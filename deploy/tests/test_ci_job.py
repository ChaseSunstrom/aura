"""`scripts/ci-job.py`: one pipeline job's own lines, in its own image, on a clean clone.

Nothing here starts a container. What is pinned is what the tool would run: that the lines come
from the pipeline file in order with nothing hand-copied, that a job it cannot reproduce is
refused by name with the reason, and that the container it starts is handed nothing of the
host's (T-1444; T-1442 is why it exists).
"""
from __future__ import annotations

import importlib.util
import select
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())


def _tool():
    spec = importlib.util.spec_from_file_location("ci_job_under_test", ROOT / "scripts" / "ci-job.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tool = _tool()


def _lines(text: str) -> list[str]:
    """The job's own lines: what follows the tool's preamble marker."""
    return text.split(tool.OWN_LINES + "\n", 1)[1].rstrip("\n").split("\n")


def test_a_jobs_lines_are_the_pipeline_files_own_in_order():
    image, text = tool.job_script(ROOT, "test:adapter")
    assert image == CI[".python"]["image"]
    assert _lines(text) == CI[".python"]["before_script"] + CI["test:adapter"]["script"]
    # The preamble is the runner's shell options and the checkout, nothing that installs.
    preamble = text.split(tool.OWN_LINES)[0]
    assert preamble.splitlines()[:2] == ["set -eo pipefail", "cd /builds/p"]
    assert all(line.startswith("export ") for line in preamble.splitlines()[2:])


def test_a_job_from_an_included_file_resolves_too():
    part = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-python.yml").read_text())
    image, text = tool.job_script(ROOT, "py-mypy")
    assert image.startswith("python:")
    assert _lines(text)[-len(part["py-mypy"]["script"]):] == part["py-mypy"]["script"]


def test_the_pipelines_variables_reach_the_job_and_its_image_name():
    image, text = tool.job_script(ROOT, "test:plugin-discovery")
    tag = CI["variables"]["HERMES_TAG"]
    assert image == f"nousresearch/hermes-agent:{tag}"
    assert f"export HERMES_TAG={tag}" in text
    assert "export CI_PROJECT_DIR=/builds/p" in text
    # A variable the pipeline takes from the runner (the registry) is not invented here.
    assert "IMAGE_BASE" not in text and "CI_REGISTRY" not in text


@pytest.mark.parametrize("name, word", [
    ("deploy:production", "tag"),          # this host's runner, with the Docker socket
    ("integration:smoke", "tag"),
    ("hud-a11y", "needs"),                 # another job's artifacts
    ("no-such-job", "no job"),
])
def test_a_job_it_cannot_reproduce_is_refused_with_the_reason(name, word):
    with pytest.raises(tool.CannotRun) as refused:
        tool.job_script(ROOT, name)
    assert word in str(refused.value), str(refused.value)


def test_the_python_test_jobs_are_found_by_rule_and_each_resolves():
    names = tool.python_test_jobs(ROOT)
    assert {"test:lint", "test:plugins", "test:adapter", "test:desktop", "test:config-render",
            "py-mypy", "docs-links"} <= set(names)
    assert not {"deploy:production", "integration:smoke", "hud-a11y", "web:build"} & set(names)
    for name in names:
        image, text = tool.job_script(ROOT, name)
        assert image.startswith("python:") and len(_lines(text)) >= 1, name


def test_the_whole_runnable_set_adds_the_jobs_in_other_images_and_keeps_the_python_ones(monkeypatch, capsys):
    """The Python set was T-1442's fault class; the tool can also run the untagged test-stage
    jobs in the Node and Hermes images, and three of those were checked by hand that day."""
    python, every = tool.python_test_jobs(ROOT), tool.test_jobs(ROOT)
    assert [n for n in every if n in python] == python, "the Python jobs, in the same order"
    extra = set(every) - set(python)
    assert {"web:build", "test:web", "web-lint", "test:plugin-discovery"} <= extra
    assert not {"deploy:production", "integration:smoke", "hud-a11y", "kotlin-lint"} & set(every)
    for name in extra:
        image, text = tool.job_script(ROOT, name)
        assert not image.startswith("python:") and len(_lines(text)) >= 1, name
    monkeypatch.setattr(tool, "ROOT", ROOT)
    assert tool.main(["--list"]) == 0
    assert capsys.readouterr().out.split() == python
    assert tool.main(["--list", "--all-tests"]) == 0
    assert capsys.readouterr().out.split() == every


def test_extends_variables_and_overrides_resolve_as_the_runner_does(tmp_path):
    (tmp_path / ".gitlab").mkdir()
    (tmp_path / ".gitlab" / "more.yml").write_text(yaml.safe_dump({
        "child": {"extends": [".base", ".extra"], "script": ["run $WHO"], "variables": {"WHO": "$NAME-2"}},
        "own-before": {"extends": ".base", "before_script": ["mine"], "script": ["go"]},
    }))
    (tmp_path / ".gitlab-ci.yml").write_text(yaml.safe_dump({
        "include": [{"local": ".gitlab/more.yml"}],
        "variables": {"NAME": "aura", "IMG": "python:3.12-slim"},
        "default": {"before_script": ["from default"]},
        ".base": {"image": "$IMG", "before_script": ["install"], "variables": {"A": "1"}},
        ".extra": {"variables": {"B": "2"}},
        "bare": {"image": "busybox", "script": ["true"]},
        "served": {"image": "busybox", "services": ["postgres"], "script": ["true"]},
    }))
    image, text = tool.job_script(tmp_path, "child")
    assert image == "python:3.12-slim"
    assert _lines(text) == ["install", "run $WHO"]
    assert "export WHO=aura-2" in text and "export A=1" in text and "export B=2" in text
    assert _lines(tool.job_script(tmp_path, "own-before")[1]) == ["mine", "go"]
    assert _lines(tool.job_script(tmp_path, "bare")[1]) == ["from default", "true"]
    with pytest.raises(tool.CannotRun, match="service"):
        tool.job_script(tmp_path, "served")


def test_a_pipeline_file_the_safe_loader_cannot_read_is_refused_in_words(tmp_path, monkeypatch, capsys):
    """A ``!reference`` tag is the likely one: the tool resolves none, and a traceback from the
    loader would read as the tool being broken rather than the file being outside it."""
    (tmp_path / ".gitlab-ci.yml").write_text(
        ".base:\n  script: [install]\njob:\n  image: busybox\n  script:\n    - !reference [.base, script]\n")
    with pytest.raises(tool.CannotRun, match=r"\.gitlab-ci\.yml.*reference"):
        tool.job_script(tmp_path, "job")
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    for argv in (["--list"], ["--python-tests"], ["--print", "job"], ["job"]):
        assert tool.main(argv) == 2, argv
        assert "cannot run" in capsys.readouterr().err


_DRIVER = '''
import importlib.util, subprocess, sys
spec = importlib.util.spec_from_file_location("ci_job_driven", sys.argv[1])
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
tool.shutil.which = lambda name: "/usr/bin/docker"
tool._clone = lambda root, into, ref: "abc1234"
def fake(argv, **kw):
    if argv[0] == "docker" and argv[1] == "run":
        sys.stdin.readline()            # the job "runs" until the test has read the first line
    return subprocess.CompletedProcess(argv, 0, "", "")
tool.subprocess.run = fake
sys.exit(tool.main(["test:adapter"]))
'''


def test_a_line_reaches_a_pipe_while_a_job_still_runs(tmp_path):
    """The whole set takes ten minutes. Sent to a file or through a pipe, the first run of it
    showed nothing until the last job ended, because Python holds a redirected stdout until
    exit. Here nothing real is started: Docker and git are stubbed in a child process, and the
    stubbed job does not end until this test has read the tool's first line from the pipe.
    Held lines would leave both sides waiting, and the 30 seconds below run out."""
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER)
    proc = subprocess.Popen([sys.executable, str(driver), str(ROOT / "scripts" / "ci-job.py")], text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin and proc.stdout
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 30)
        first = proc.stdout.readline() if ready else ""
    finally:
        try:
            proc.stdin.write("\n")
            proc.stdin.close()
        except OSError:
            pass
        rest, err = proc.stdout.read(), proc.stderr.read() if proc.stderr else ""
        proc.wait(timeout=30)
    assert first.startswith("ci-job: commit abc1234"), f"nothing reached the pipe while the job ran ({err[-300:]})"
    assert "ok    test:adapter" in rest and proc.returncode == 0, err[-300:]


def _docker_that_lists(monkeypatch, names: list[str]) -> list[list[str]]:
    """Docker stubbed: ``docker ps`` answers with these container names, nothing else runs.
    Returns the argv of every call made, and fails the test if the clone is reached."""
    calls: list[list[str]] = []

    def fake(argv, **_kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "\n".join(names) + "\n" if argv[:2] == ["docker", "ps"] else "", "")

    def cloned(*_a):
        raise RuntimeError("cloned")

    monkeypatch.setattr(tool.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(tool.subprocess, "run", fake)
    monkeypatch.setattr(tool, "_clone", cloned)
    return calls


def test_it_refuses_to_start_while_a_pipelines_job_runs_on_this_machine(monkeypatch, capsys):
    """The runner shares these cores, and a suite started beside its jobs fails their frame
    budgets. The rule was a sentence in docs/ci.md; the tool can see the runner's containers.
    Does not see a job on the second runner, which is another machine's Docker."""
    calls = _docker_that_lists(monkeypatch, ["jarvis", "runner-ab12cd-project-3-concurrent-0-0123abcd-build"])
    assert tool.run(ROOT, ["test:adapter"]) == 2
    err = capsys.readouterr().err
    assert "pipeline" in err and "--during-pipeline" in err and "1 " in err
    assert calls == [["docker", "ps", "--format", "{{.Names}}"]], "it looked, and started nothing"


def test_the_override_and_an_idle_machine_both_reach_the_clone(monkeypatch):
    _docker_that_lists(monkeypatch, ["jarvis", "runner-ab12cd-project-3-concurrent-0-0123abcd-build"])
    with pytest.raises(RuntimeError, match="cloned"):
        tool.run(ROOT, ["test:adapter"], during_pipeline=True)
    # Its own containers, the runner's daemon and a name that merely holds the word are not a job.
    _docker_that_lists(monkeypatch, ["jarvis", "gitlab-runner", "ci-job-x1-0", "my-runner-thing"])
    with pytest.raises(RuntimeError, match="cloned"):
        tool.run(ROOT, ["test:adapter"])
    monkeypatch.setattr(tool, "ROOT", ROOT)
    _docker_that_lists(monkeypatch, ["runner-ab12cd-project-3-concurrent-1-89ab-build"])
    with pytest.raises(RuntimeError, match="cloned"):
        tool.main(["--during-pipeline", "test:adapter"])


def test_the_container_is_handed_nothing_of_the_hosts():
    """The job installs from the network as root. It gets the clone read-only and its own
    script; no environment of the host, no Docker socket, no host network, no extra rights."""
    argv = tool.docker_argv("python:3.12-slim", Path("/x/src"), Path("/x/job.sh"))
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "/x/src:/src:ro" in argv and "/x/job.sh:/job.sh:ro" in argv
    joined = " ".join(argv)
    for never in ("-e ", "--env", "--privileged", "docker.sock", "--network", "--net", "--pid", "--cap-add",
                  "--volumes-from", "--device"):
        assert never not in joined, never
    assert sum(1 for a in argv if a in ("-v", "--volume")) == 2
    # The shell replaces the image's own entrypoint, as the runner's does (the Hermes image has one).
    assert argv[argv.index("--entrypoint") + 1] == "bash" and argv[argv.index("python:3.12-slim") + 1] == "-c"
    named = tool.docker_argv("python:3.12-slim", Path("/x/src"), Path("/x/job.sh"), "ci-job-a-0")
    assert named[named.index("--name") + 1] == "ci-job-a-0" and len(named) == len(argv) + 2


def test_make_and_the_docs_point_at_the_tool():
    mk = (ROOT / "Makefile").read_text()
    assert "scripts/ci-job.py $(JOB)" in mk and "scripts/ci-job.py --python-tests" in mk
    assert "scripts/ci-job.py --all-tests" in mk
    docs = (ROOT / "docs" / "ci.md").read_text()
    assert "make ci-job JOB=" in docs and "make ci-jobs-all" in docs
