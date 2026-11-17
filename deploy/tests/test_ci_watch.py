"""`make ci-watch`: a pushed head's pipeline, read from this host without a token.

Whether a push deployed is visible here in three things Docker shows: the runner daemon's own
result lines, the job containers it starts, and the image tag of the deployed stack. The watch
was a scratch script rewritten each session, and one such wait matched its own process and sat
for 2.4 hours. These tests pin its ends with Docker, the clock and the sleep replaced, so none
of them starts a container or waits.

Not covered: the three real docker commands (run once against this host when the tool was
added), and the second runner, whose jobs this host cannot see.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("ci_watch_under_test", ROOT / "scripts" / "ci-watch.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                         # a dataclass looks its module up by name
    spec.loader.exec_module(mod)
    return mod


tool = _tool()
#: A line as the runner's daemon writes it: the project's path rides on every one.
LINE = "{word}  duration_s=61.2 job=4242 project=77 runner=abc123 repo=https://git.example.test/someone/private-repo"


class Host:
    """Docker as this host would answer, one scripted state per look; the last one repeats."""

    def __init__(self, *states: dict):
        self.states, self.looks, self.calls = list(states), 0, []

    def now(self) -> dict:
        return self.states[min(self.looks, len(self.states) - 1)]

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        s = self.now()
        if argv[0] == "logs":
            lines = [LINE.format(word="Job succeeded")] * s.get("ok", 0) + [
                LINE.format(word="WARNING: Job failed: exit code 1")] * s.get("bad", 0)
            return 0, "\n".join(lines + list(s.get("lines", [])))
        if argv[0] == "ps":
            self.looks += 1                              # the last question of each look
            return 0, "\n".join(["jarvis", "gitlab-runner"] + [f"runner-x-project-7-concurrent-{i}-ab-build"
                                                               for i in range(s.get("jobs", 0))])
        if argv[0] == "inspect" and "{{.Config.Image}}" in argv:
            return (0, f"registry.example.test:5050/someone/jarvis:{s['tag']}\n") if s.get("tag") else (1, "Error: no such object")
        if argv[0] == "inspect":
            return 0, s.get("health", "running healthy")
        raise AssertionError(argv)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _watch(host: Host, **kw):
    clock, said = Clock(), []
    opts = {"minutes": 60, "interval": 30, "quiet_minutes": 10, "from_tag": None, "sha": None, "since": "0m"}
    code = tool.watch(host, clock, clock.sleep, said.append, **{**opts, **kw})
    return code, said, clock


def test_it_ends_0_when_the_tag_moved_the_stack_is_healthy_and_the_deploy_job_is_gone():
    host = Host({"tag": "aaaa1111", "jobs": 2, "ok": 3},
                {"tag": "aaaa1111", "jobs": 1, "ok": 17},
                {"tag": "bbbb2222", "jobs": 1, "ok": 17},        # the deploy job is still at its smoke
                {"tag": "bbbb2222", "jobs": 0, "ok": 18})
    code, said, _ = _watch(host)
    assert code == 0 and said[-1].startswith("deployed bbbb2222")
    assert "18 succeeded" in said[-1] and "0 failed" in said[-1]
    assert host.looks == 4, "a moved tag is not the end while the deploy job still runs"


def test_it_ends_1_when_a_job_failed_here_and_the_runner_then_stayed_quiet():
    host = Host({"tag": "aaaa1111", "jobs": 2, "ok": 5},
                {"tag": "aaaa1111", "jobs": 1, "ok": 6, "bad": 1},
                {"tag": "aaaa1111", "jobs": 0, "ok": 7, "bad": 1})
    code, said, clock = _watch(host)
    assert code == 1 and "1 failed" in said[-1] and "aaaa1111" in said[-1]
    assert clock.t - 1000.0 >= 10 * 60, "quiet is ten minutes with no job container, not one look"


def test_a_failure_the_pipeline_allows_does_not_end_the_watch_before_the_deploy():
    host = Host({"tag": "aaaa1111", "jobs": 2, "ok": 5, "bad": 1},
                {"tag": "aaaa1111", "jobs": 0, "ok": 9, "bad": 1},   # a gap between jobs, shorter than quiet
                {"tag": "aaaa1111", "jobs": 1, "ok": 9, "bad": 1},
                {"tag": "bbbb2222", "jobs": 0, "ok": 10, "bad": 1})
    code, said, _ = _watch(host)
    assert code == 0 and "1 failed" in said[-1]


def test_it_ends_2_at_its_limit_and_says_what_it_last_saw():
    code, said, clock = _watch(Host({"tag": "aaaa1111", "jobs": 1, "ok": 2}), minutes=5)
    assert code == 2 and "5 minutes" in said[-1] and "aaaa1111" in said[-1]
    assert 5 * 60 <= clock.t - 1000.0 < 6 * 60
    assert len(said) == 2, "an unchanged state is said once, then the end"


def test_a_moved_tag_on_an_unhealthy_stack_is_not_a_deploy():
    host = Host({"tag": "aaaa1111", "jobs": 1},
                {"tag": "bbbb2222", "jobs": 0, "health": "running unhealthy"})
    code, said, _ = _watch(host, minutes=3)
    assert code == 2 and "not healthy" in said[-1]
    # A deploy that rolled back: the tag is the old one again, the deploy job failed, quiet follows.
    back = Host({"tag": "bbbb2222", "jobs": 1, "ok": 17},
                {"tag": "aaaa1111", "jobs": 0, "ok": 17, "bad": 1})
    assert _watch(back, from_tag="aaaa1111")[0] == 1


def test_with_a_commit_named_only_that_commits_tag_is_the_deploy():
    other = Host({"tag": "aaaa1111", "jobs": 1}, {"tag": "cccc3333", "jobs": 0})
    assert _watch(other, sha="bbbb2222ffff", minutes=2)[0] == 2, "somebody else's deploy is not this push's"
    mine = Host({"tag": "bbbb2222", "jobs": 0})
    code, said, _ = _watch(mine, sha="bbbb2222ffff")
    assert code == 0 and host_looks(mine) == 1, "already deployed when the watch starts: said at once"


def host_looks(host: Host) -> int:
    return host.looks


def test_nothing_of_a_log_line_reaches_the_output_and_no_process_is_listed():
    host = Host({"tag": "aaaa1111", "jobs": 2, "ok": 3, "bad": 1}, {"tag": "bbbb2222", "jobs": 0, "ok": 4, "bad": 1})
    _code, said, _ = _watch(host)
    text = "\n".join(said)
    for private in ("example.test", "someone", "private-repo", "project=", "runner=", "registry"):
        assert private not in text, private
    assert {argv[0] for argv in host.calls} == {"logs", "ps", "inspect"}, "it asks Docker and nothing else"
    assert ["logs", "--since", "0m", "gitlab-runner"] in host.calls


#: A failed job as the runner wrote two of them on 2026-11-16: the shape kept, every value made up.
#: The line after it is the runner handing the result to GitLab; it says "job failed" too.
FAILED = ("WARNING: Job failed: exit code {code}  container_name=runner-x-project-7-concurrent-0-ab-build "
          "duration_s={secs} error=exit code {code} exit_code={code} failure_reason=script_failure gitlab_user_id=2 "
          "job=4242 job-status=failed name=someone-runner namespace_id=2 pipeline_id={pipeline} project=77 "
          "repo_url=https://git.example.test/someone/private-repo.git runner=abc123")
HANDED_ON = ("WARNING: Submitting job to coordinator... job failed  bytesize=4840 code=200 job=4242 "
             "job-status=failed runner=abc123 status=200 OK")
PRIVATE = ("example.test", "someone", "private-repo", "project", "runner-x", "runner=", "registry", "repo_url",
           "abc123", "4242")


def test_a_failed_job_is_said_with_how_it_ended_exit_code_seconds_and_pipeline():
    """Two jobs failed on one night and the watch said "2 failed". That both had exited 22, one
    after 18 seconds and one after 13 minutes, in two pipelines, was read out of the runner's log
    by hand: those numbers were the diagnosis (an HTTP error at an upload, not a failing test)."""
    lines = [FAILED.format(code=22, secs="18.194977379", pipeline=910), HANDED_ON,
             FAILED.format(code=22, secs="788.445710881", pipeline=911), HANDED_ON]
    host = Host({"tag": "aaaa1111", "jobs": 1, "ok": 12, "lines": lines[:2]},
                {"tag": "aaaa1111", "jobs": 0, "ok": 12, "lines": lines})
    code, said, _ = _watch(host)
    assert code == 1
    assert "1 failed" in said[0] and "exit 22 after 18 s (pipeline 910)" in said[0]
    for line in said[1:]:                                # the changed state, then the end
        assert "2 failed" in line, "the handing-on line is not a second failure"
        assert "exit 22 after 18 s (pipeline 910), exit 22 after 788 s (pipeline 911)" in line
    assert said[-1].startswith("no deploy") and said[-1].count("curl") == 1, "what 22 usually is, said once"
    assert not [p for p in PRIVATE if p in "\n".join(said)]


def test_how_a_job_ended_is_numbers_only_also_from_a_line_unlike_the_ones_seen():
    how = tool.how_it_ended
    assert how(FAILED.format(code=1, secs="61.2", pipeline=7)) == "exit 1 after 61 s (pipeline 7)"
    # The daemon colours each field's name: read plain, the real lines gave the exit code and no more.
    coloured = "\x1b[0;33m" + FAILED.format(code=22, secs="18.19", pipeline=910).replace(
        " duration_s=", " \x1b[0;33mduration_s\x1b[0m=").replace(" pipeline_id=", " \x1b[0;33mpipeline_id\x1b[0m=") + "\x1b[0m"
    assert how(coloured) == "exit 22 after 18 s (pipeline 910)"
    # A job the runner stopped at its time limit has no exit code.
    timed_out = ("ERROR: Job failed: execution took longer than 45m0s seconds  duration_s=2700.4 job=4242 "
                 "pipeline_id=910 project=77 repo_url=https://git.example.test/someone/private-repo.git")
    assert how(timed_out) == "no exit code after 2700 s (pipeline 910)"
    # A shape never seen: a path where a number is expected, the fields renamed. Nothing is guessed
    # and nothing of the line is passed on.
    odd = ("ERROR: Job failed (system failure): prepare /someone/private-repo exit_code=/someone/x "
           "duration_s=soon pipeline_id=someone-910 took=5s")
    assert how(odd) == "no detail"
    assert how("") == "no detail"
    host = Host({"tag": "aaaa1111", "jobs": 0, "lines": [odd, timed_out] + [FAILED.format(code=137, secs="9", pipeline=3)] * 9})
    text = tool.look(host, "0m").line()
    assert "11 failed" in text and "no detail" in text and "+5 more" in text, "a long list is cut, the count is whole"
    assert not [p for p in PRIVATE if p in text]


def test_a_host_without_the_stack_or_docker_says_so_and_once_ends_at_one_look(capsys, monkeypatch):
    gone = Host({"jobs": 0})
    code, said, _ = _watch(gone, minutes=1)
    assert code == 2 and "unknown" in said[0]
    monkeypatch.setattr(tool, "_docker", Host({"tag": "aaaa1111", "jobs": 1, "ok": 2}))
    assert tool.main(["--once"]) == 0
    out = capsys.readouterr().out
    assert "2 succeeded" in out and "aaaa1111" in out and len(out.strip().splitlines()) == 1


def test_the_count_starts_at_a_fixed_moment_not_a_window_that_slides(monkeypatch):
    """Docker reads `45m` as 45 minutes before each call. Handed on as typed, the first jobs of a
    pipeline fell out of the count while the watch ran; the first real watch was started so."""
    assert tool.since_stamp("45m", 1_800_000_000.0) == "2027-01-15T07:15:00Z"
    assert tool.since_stamp("2h", 1_800_000_000.0) == "2027-01-15T06:00:00Z"
    assert tool.since_stamp(None, 1_800_000_000.0) == "2027-01-15T08:00:00Z"
    with pytest.raises(ValueError, match="45m"):
        tool.since_stamp("yesterday", 0.0)
    host = Host({"tag": "aaaa1111", "jobs": 1, "ok": 2})
    monkeypatch.setattr(tool, "_docker", host)
    monkeypatch.setattr(tool.time, "time", lambda: 1_800_000_000.0)
    assert tool.main(["--once", "--since", "45m"]) == 0
    assert ["logs", "--since", "2027-01-15T07:15:00Z", "gitlab-runner"] in host.calls


def test_make_and_the_docs_point_at_the_watcher():
    assert "scripts/ci-watch.py" in (ROOT / "Makefile").read_text()
    assert "make ci-watch" in (ROOT / "docs" / "ci.md").read_text()


@pytest.mark.parametrize("image, tag", [
    ("registry.example.test:5050/someone/jarvis:bbbb2222", "bbbb2222"),
    ("jarvis:bbbb2222", "bbbb2222"),
    ("registry.example.test:5050/someone/jarvis", ""),      # a port is not a tag
    ("", ""),
])
def test_the_tag_is_what_follows_the_last_colon_of_the_images_last_part(image, tag):
    assert tool.tag_of(image) == tag
