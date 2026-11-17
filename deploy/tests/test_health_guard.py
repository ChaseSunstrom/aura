"""deploy/health_guard.py: one notice per incident, one when it clears (T-0344)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("health_guard", Path(__file__).resolve().parents[1] / "health_guard.py")
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def box(name: str = "jarvis-reranker", **kw) -> dict:
    c = {"name": name, "project": "jarvis-core", "state": "running", "health": "healthy",
         "exit_code": 0, "restart_policy": "unless-stopped", "restarts": 0}
    c.update(kw)
    return c


def seen(c: dict, times: int = 2, state: dict | None = None):
    """The notices after `times` identical checks."""
    state, notices = state or {}, []
    for _ in range(times):
        notices, state = guard.decide([c], state)
    return notices


def test_a_crash_loop_is_one_notice_and_its_recovery_another():
    state: dict = {}
    first, state = guard.decide([box(state="restarting", restarts=40)], state)
    assert first == [], "one bad check is not yet an incident"
    notices, state = guard.decide([box(state="restarting", restarts=55)], state)
    assert notices == [("jarvis-reranker", "jarvis-reranker keeps restarting")]
    again, state = guard.decide([box(state="restarting", restarts=70)], state)
    assert again == [], "still the same incident: no second notice"
    for _ in range(2):
        quiet, state = guard.decide([box(restarts=70)], state)
        assert quiet == []
    cleared, state = guard.decide([box(restarts=70)], state)
    assert cleared == [("jarvis-reranker", "jarvis-reranker is running normally again")]
    assert guard.decide([box(restarts=70)], state)[0] == []


def test_unhealthy_and_a_failed_exit_under_a_restart_policy_are_incidents():
    assert seen(box(health="unhealthy"))[0][1] == "jarvis-reranker is unhealthy"
    died = box(state="exited", exit_code=137)
    assert seen(died)[0][1] == "jarvis-reranker stopped with exit code 137"
    # A one-shot that finished, or a container nobody asked Docker to keep up, is not.
    assert guard.decide([box("jarvis-config-init", state="exited", exit_code=0)], {})[0] == []
    assert guard.decide([box(state="exited", exit_code=1, restart_policy="no")], {})[0] == []


def test_restarts_that_happen_between_checks_count():
    """A crash loop with long backoff can look 'running' at each check; the count gives it away."""
    _, state = guard.decide([box(restarts=2)], {})
    first, state2 = guard.decide([box(restarts=5)], state)
    notices, _ = guard.decide([box(restarts=8)], state2)
    assert first == []
    assert notices == [("jarvis-reranker", "jarvis-reranker restarted 3 times since the last check")]
    assert guard.decide([box(restarts=4)], state)[0] == []


def test_only_the_production_projects_are_watched():
    assert guard.decide([box("runner-x", project="", state="restarting")], {})[0] == []
    assert guard.decide([box("ci-smoke", project="smoke-123", state="restarting")], {})[0] == []
    assert seen(box("jarvis", project="jarvis", state="restarting")) != []


def test_a_notice_that_could_not_be_sent_is_tried_again_next_run():
    c = box(state="restarting", restarts=9)
    _, first = guard.decide([c], {})
    notices, state = guard.decide([c], first)
    assert notices
    kept = guard.settle(notices, state, first, sent={})
    assert guard.decide([c], kept)[0] == notices
    delivered = guard.settle(notices, state, first, sent={"jarvis-reranker": True})
    assert guard.decide([c], delivered)[0] == []
    # a container that was not in the old state keeps its bad count, so the next check retries
    assert guard.decide([c], guard.settle(notices, state, {}, sent={}))[0] == notices


def test_an_image_that_did_not_ship_is_one_notice_naming_the_job(monkeypatch):
    """T-0419: the deploy calls this once per allow_failure image job that failed. A house
    that cannot take the notice must not fail the deploy: production keeps the old image either way."""
    sent = []

    def house_down(title, name, body=None, source="health_guard"):
        sent.append((title, name, body, source))
        return False

    monkeypatch.setattr(guard, "notify", house_down)
    url = "https://gitlab.example/jobs/20347"
    assert guard.main(["--image-failed", "jarvis-models", url]) == 0
    [(title, name, body, source)] = sent
    assert "jarvis-models" in title and name == "jarvis-models" and source == "deploy"
    assert url in (body or ""), "the notice says where the failure is explained"


def test_an_image_notice_without_a_readable_token_file_does_not_fail_the_deploy(monkeypatch, tmp_path):
    """The deploy runs --image-failed on the host; a missing /opt/jarvis-host/.env must be a
    notice not recorded, not an exit 1 (T-0419 review)."""
    monkeypatch.setattr(guard, "HOST_ENV", tmp_path / "missing.env")
    monkeypatch.setattr(guard, "HOUSE", "http://127.0.0.1:9")
    assert guard.notify("t", "jarvis-models") is False
    assert guard.main(["--image-failed", "jarvis-models", "https://gitlab.example/jobs/1"]) == 0


def _verify(monkeypatch, rc: int = 0, out: str = "", exc: Exception | None = None) -> None:
    import subprocess

    def run(cmd, **kw):
        assert cmd[:3] == ["docker", "exec", guard.RUNNER]
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, rc, out, "")

    monkeypatch.setattr(guard.subprocess, "run", run)


def test_the_runner_is_judged_by_its_own_verify(monkeypatch):
    """T-0424: on 2026-10-27 the runner container was up but its url named the old tailnet
    host, so it took no job for two days. Container state cannot see that; its verify can."""
    import subprocess

    _verify(monkeypatch, out="Verifying runner... is valid  runner=dhw6XRqXy")
    assert guard.runner_problem() is None
    _verify(monkeypatch, out="ERROR: Verifying runner... is not valid  runner=dhw6XRqXy")
    assert guard.runner_problem() == "cannot reach GitLab"
    _verify(monkeypatch, rc=1, out="Error response from daemon: No such container: gitlab-runner")
    assert guard.runner_problem() == "is not running"
    _verify(monkeypatch, rc=1, out="ERROR: Verifying runner... is not valid")
    assert guard.runner_problem() == "cannot reach GitLab", "a verify that exits 1 still answered"
    _verify(monkeypatch, exc=subprocess.TimeoutExpired("docker", 90))
    assert guard.runner_problem() == "cannot reach GitLab"
    monkeypatch.setattr(guard, "RUNNER", "")
    assert guard.runner_problem() is None, "HEALTH_GUARD_RUNNER='' turns the check off"


def test_a_runner_blip_is_quiet_and_an_outage_is_one_notice_then_its_recovery():
    title, state = guard.ci_decide("cannot reach GitLab", None)
    assert title is None, "one failed check can be GitLab restarting: no notice yet"
    blip, after_blip = guard.ci_decide(None, state)
    assert blip is None and after_blip["strikes"] == 0 and not after_blip["incident"]
    _, state = guard.ci_decide("cannot reach GitLab", None)
    title, state = guard.ci_decide("cannot reach GitLab", state)
    assert title and "runner" in title and "cannot reach GitLab" in title
    assert guard.ci_decide("cannot reach GitLab", state)[0] is None, "same outage: no second notice"
    for _ in range(2):
        title, state = guard.ci_decide(None, state)
        assert title is None
    title, state = guard.ci_decide(None, state)
    assert title and "again" in title
    assert guard.ci_decide(None, state)[0] is None


def test_a_container_flap_is_quiet_and_a_held_failure_is_announced_then_cleared_late():
    bad, ok = box(health="unhealthy"), box()
    out = []
    state: dict = {}
    for c in [bad, ok, bad, bad, ok, ok, bad, ok, ok, ok, ok]:
        notices, state = guard.decide([c], state)
        out.append([t for _, t in notices])
    assert out[:3] == [[], [], []], "bad, good, bad: a flap"
    assert out[3] == ["jarvis-reranker is unhealthy"]
    assert out[4:9] == [[]] * 5, "never three good in a row yet"
    assert out[9] == ["jarvis-reranker is running normally again"] and out[10] == []


def test_the_runner_flap_is_quiet_and_clears_only_after_three_good_checks():
    state = None
    out = []
    for why in ["x", None, "x", "x", None, None, "x", None, None, None, None]:
        title, state = guard.ci_decide(why, state)
        out.append(title)
    assert out[:3] == [None] * 3 and out[3] and "runner" in out[3]
    assert out[4:9] == [None] * 5
    assert out[9] and "taking jobs again" in out[9] and out[10] is None


def test_a_state_file_from_before_the_hysteresis_still_works():
    old = {"jarvis-reranker": {"restarts": 0, "incident": "is unhealthy"}}
    state = old
    out = []
    for _ in range(3):
        notices, state = guard.decide([box()], state)
        out.append(notices)
    assert out == [[], [], [("jarvis-reranker", "jarvis-reranker is running normally again")]]
    assert guard.ci_decide(None, {"strikes": 0, "incident": "cannot reach GitLab"})[0] is None


def test_a_runner_notice_the_house_did_not_take_is_tried_again(monkeypatch, tmp_path):
    tries = []
    monkeypatch.setattr(guard, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(guard, "containers", lambda: [])
    monkeypatch.setattr(guard, "runner_problem", lambda: "is not running")
    monkeypatch.setattr(guard, "notify", lambda title, name, body=None, source="health_guard":
                        tries.append(title) or False)
    for _ in range(4):
        assert guard.main([]) == 0
    assert len(tries) == 3, "notified from the second run on, and again each run until it lands"
    monkeypatch.setattr(guard, "notify", lambda *a, **k: tries.append(a[0]) or True)
    guard.main([])
    guard.main([])
    assert len(tries) == 4, "once the house has it, the same outage is not announced again"
