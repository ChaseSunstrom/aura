"""deploy/cold_start_drill.py: time each service to healthy, fail on a late one or a wrong start order (T-0585)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cold_start_drill", ROOT / "deploy" / "cold_start_drill.py")
drill = importlib.util.module_from_spec(spec)
sys.modules["cold_start_drill"] = drill
spec.loader.exec_module(drill)

# The shape `docker compose config --format json` gives: depends_on as a dict with a condition,
# a one-shot init (restart "no", no healthcheck).
CONFIG = {"name": "house", "services": {
    "init": {"container_name": "init", "restart": "no"},
    "db": {"container_name": "db", "healthcheck": {"test": ["CMD", "true"]}, "restart": "unless-stopped"},
    "core": {"container_name": "core", "healthcheck": {"test": ["CMD", "true"]}, "restart": "unless-stopped",
             "depends_on": {"db": {"condition": "service_healthy", "required": True},
                            "init": {"condition": "service_completed_successfully"}}},
    "plain": {"restart": "unless-stopped"},
}}
SERVICES = drill.read_compose(CONFIG)


def times(**ready):
    """started, ready per service: seconds after the drill began."""
    return {name: {"started": s, "ready": r, "state": "healthy" if r is not None else "unhealthy"} for name, (s, r) in ready.items()}


CLEAN = dict(init=(0.0, 1.0), db=(0.5, 8.0), core=(8.5, 20.0), plain=(0.5, 10.5))


def test_the_reader_keeps_what_the_judge_needs_and_a_switched_on_profile_service():
    assert list(SERVICES) == ["init", "db", "core", "plain"]
    assert SERVICES["core"].deps == {"db": "service_healthy", "init": "service_completed_successfully"}
    assert SERVICES["init"].oneshot and not SERVICES["init"].healthcheck
    assert SERVICES["plain"].container == "house-plain-1" and not SERVICES["plain"].oneshot
    # Compose's config lists a profile service only when its profile is on, and `stop` then
    # takes it down with the rest: the first real run restarted two the drill had not timed.
    on = drill.read_compose({"name": "house", "services": {"broker": {"profiles": ["mqtt"], "restart": "unless-stopped"}}})
    assert list(on) == ["broker"]


def test_a_clean_start_passes_and_prints_each_service():
    rows, problems = drill.judge(SERVICES, times(**CLEAN), {}, default=60)
    assert problems == []
    assert [(r["service"], r["seconds"]) for r in rows] == [("init", 1.0), ("db", 8.0), ("core", 20.0), ("plain", 10.5)]
    assert "core" in drill.table(rows) and "20.0" in drill.table(rows)


def test_a_service_past_its_budget_fails_by_name():
    _, problems = drill.judge(SERVICES, times(**CLEAN), {"core": 15}, default=60)
    assert problems == ["core: healthy after 20.0 s, budget 15 s"]


def test_a_service_that_never_got_there_fails_with_its_state():
    _, problems = drill.judge(SERVICES, times(**{**CLEAN, "db": (0.5, None)}), {}, default=60)
    assert any(p.startswith("db: never healthy (unhealthy)") for p in problems)


def test_order_a_dependent_started_before_its_dependency_was_healthy_fails():
    _, problems = drill.judge(SERVICES, times(**{**CLEAN, "core": (3.0, 20.0)}), {}, default=60)
    assert problems == ["core: started at 3.0 s, before db was healthy (not before 8.0 s)"]


def test_order_a_dependency_without_its_condition_fails_before_anything_runs():
    loose = json.loads(json.dumps(CONFIG))
    loose["services"]["core"]["depends_on"]["db"] = {"condition": "service_started"}
    loose["services"]["core"]["depends_on"]["init"] = {"condition": "service_started"}
    loose["services"]["core"]["depends_on"]["ghost"] = {"condition": "service_started"}
    assert drill.static_order(drill.read_compose(loose)) == [
        "core: depends on db, which has a healthcheck, without condition: service_healthy",
        "core: depends on init, a one-shot, without condition: service_completed_successfully",
        "core: depends on ghost, which is not a service here",
    ]
    assert drill.static_order(SERVICES) == []


def test_the_watcher_reads_docker_through_what_it_is_given():
    """healthy by healthcheck; a one-shot by exit 0; no healthcheck by running, unrestarted, for a while."""
    clock = {"t": 0.0}
    def inspect(container):
        t = clock["t"]
        return {
            "init": {"Status": "exited", "ExitCode": 0, "RestartCount": 0, "started": 0.0},
            "db": {"Status": "running", "Health": "healthy" if t >= 4 else "starting", "RestartCount": 0, "started": 0.5},
            "core": {"Status": "running", "Health": "healthy" if t >= 9 else "starting", "RestartCount": 0, "started": 4.5},
            "house-plain-1": {"Status": "running", "RestartCount": 0, "started": 1.0},
        }[container]
    def sleep(s):
        clock["t"] += s
    got = drill.watch(SERVICES, inspect, now=lambda: clock["t"], sleep=sleep, give_up=60, settle=5)
    assert {n: got[n]["ready"] for n in got} == {"init": 0.0, "db": 4.0, "core": 9.0, "plain": 6.0}
    assert drill.judge(SERVICES, got, {}, default=60)[1] == []


def test_order_a_dependent_started_the_moment_its_dependency_was_ready_passes_though_the_poll_saw_it_late():
    """Compose starts core 0.2 s after init exits; the one-second poll sees that exit 0.8 s later."""
    clock = {"t": 0.0}
    def inspect(container):
        t = clock["t"]
        if container == "init":
            return {"Status": "exited" if t >= 3.2 else "running", "ExitCode": 0, "RestartCount": 0, "started": 0.0}
        if container == "core" and t < 3.4:
            raise LookupError("not started again yet")
        return {"Status": "running", "Health": "healthy", "RestartCount": 0, "started": 3.4 if container == "core" else 0.0}
    got = drill.watch(SERVICES, inspect, now=lambda: clock["t"],
                      sleep=lambda s: clock.__setitem__("t", clock["t"] + s), give_up=60, settle=1)
    assert got["init"]["ready"] == 4.0 and got["core"]["started"] == 3.4
    assert drill.judge(SERVICES, got, {}, default=60)[1] == []
    # A start well before the dependency was done is still caught through the same watcher.
    got["core"]["started"] = 1.5
    assert drill.judge(SERVICES, got, {}, default=60)[1] == ["core: started at 1.5 s, before init was done (not before 3.0 s)"]


def test_docker_inspect_is_read_with_nanoseconds_no_fraction_and_a_never_started_container():
    import pytest

    began = drill.datetime.fromisoformat("2026-01-05T10:00:00+00:00").timestamp()
    def info(started_at, **state):
        return {"RestartCount": 2, "State": {"Status": "running", "StartedAt": started_at, "ExitCode": 0, **state}}
    got = drill.read_inspect(info("2026-01-05T10:00:03.123456789Z", Health={"Status": "starting"}), began)
    assert got == {"Status": "running", "Health": "starting", "ExitCode": 0, "RestartCount": 2, "started": pytest.approx(3.123456)}
    assert drill.read_inspect(info("2026-01-05T10:00:07Z"), began)["started"] == pytest.approx(7.0)
    assert drill.read_inspect(info("2026-01-05T10:00:07Z"), began)["Health"] is None
    for before in ("0001-01-01T00:00:00Z", "2026-01-05T09:59:00.5Z"):
        with pytest.raises(LookupError):
            drill.read_inspect(info(before), began)


def test_a_failed_stop_or_no_docker_is_exit_2_and_a_misordered_file_is_never_stopped(capsys):
    def boom(*a, **k):
        raise RuntimeError("stop failed: no such project")
    assert drill.main(["-f", "x.yml", "--yes"], config=CONFIG, run=boom) == 2
    assert "stop failed" in capsys.readouterr().err
    loose = json.loads(json.dumps(CONFIG))
    loose["services"]["core"]["depends_on"]["db"] = {"condition": "service_started"}
    assert drill.main(["-f", "x.yml", "--yes"], config=loose, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran"))) == 1
    assert "without condition: service_healthy" in capsys.readouterr().out


def test_the_watcher_stops_at_once_on_a_crash_loop_or_a_failed_one_shot():
    states = {"init": {"Status": "exited", "ExitCode": 3, "RestartCount": 0, "started": 0.0},
              "db": {"Status": "running", "Health": "unhealthy", "RestartCount": 0, "started": 0.0},
              "core": {"Status": "running", "Health": "starting", "RestartCount": 0, "started": 0.0},
              "house-plain-1": {"Status": "restarting", "RestartCount": 4, "started": 0.0}}
    clock = {"t": 0.0}
    got = drill.watch(SERVICES, states.__getitem__, now=lambda: clock["t"],
                      sleep=lambda s: clock.__setitem__("t", clock["t"] + s), give_up=30, settle=5)
    assert got["init"]["state"] == "exited 3" and got["db"]["state"] == "unhealthy" and got["plain"]["state"] == "restarted 4 times"
    assert got["core"]["ready"] is None and got["core"]["state"] == "starting"
    assert clock["t"] <= 31
    assert len(drill.judge(SERVICES, got, {}, default=60)[1]) == 4


def test_a_dry_run_of_the_house_stack_lists_every_service_and_touches_nothing():
    import shutil

    import pytest

    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("needs docker compose to read the file")
    done = subprocess.run([sys.executable, str(ROOT / "deploy" / "cold_start_drill.py"), "--dry", "-f", str(ROOT / "aura-core" / "docker-compose.yml")],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "jarvis-core" in done.stdout and "dry run" in done.stdout


def test_a_real_run_needs_yes_and_never_takes_the_assistants_own_stack(capsys):
    assert drill.main(["-f", "x.yml"], config=CONFIG, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran"))) == 2
    hermes = {"name": "deploy", "services": {"jarvis": {"restart": "unless-stopped"}}}
    assert drill.main(["-f", "x.yml", "--yes"], config=hermes, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran"))) == 2
    assert "only through a deploy" in capsys.readouterr().err
