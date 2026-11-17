"""The throwaway demo house: the guard, the skip, the wiring and the teardown.

Offline by construction. The core and the gateway here are a few lines of
Python the test starts itself; no model is called, no Hermes starts, nothing
binds anything but 127.0.0.1, and every key is drawn at run time.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from testing.live import LiveError, runner as runner_module, throwaway  # noqa: E402
from testing.live.runner import Runner, _target_label  # noqa: E402
from testing.live.scenario import SCENARIO_DIR, load_scenario  # noqa: E402

SCENARIO = SCENARIO_DIR / "interactions-thread-continuity.yaml"
#: Every credential in this file is drawn at run time; none is written down.
KEY = secrets.token_hex(8)

#: A core that says who it is, and a stand-in for the gateway that only stays alive.
FAKE_CORE = """\
import http.server, json, sys
marker, port = sys.argv[1], int(sys.argv[2])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"location_name": marker} if self.path == "/api/config" else {"state": "off"}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""
FAKE_GATEWAY = "import time; time.sleep(600)"


@pytest.fixture(autouse=True)
def _no_owner_voice(monkeypatch):
    """`Runner()` loads the owner's voice model, which none of this needs."""
    monkeypatch.setattr(runner_module, "Mouth", lambda *a, **k: object())
    monkeypatch.setattr(runner_module, "Ears", lambda *a, **k: object())


def _boot_fake_core(tw):
    tw.spawn("core", [sys.executable, "-c", FAKE_CORE, tw.marker, str(tw.core_port)])
    tw.wait_until_listening(tw.core_port, 20.0)


def _boot_fake_gateway(tw):
    tw.spawn("gateway", [sys.executable, "-c", FAKE_GATEWAY])


def _fake_throwaway(tmp_path, **kwargs):
    return throwaway.Throwaway(
        boot_core=_boot_fake_core,
        boot_gateway=_boot_fake_gateway,
        pid_file=tmp_path / "pids.json",
        **kwargs,
    )


def _alive(pid: int) -> bool:
    """Running, not merely a zombie its parent has yet to collect."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


# --- the scenario says it needs the demo house -------------------------------


def test_demo_house_flag_is_read_from_the_scenario():
    assert load_scenario(SCENARIO).needs_demo_house is True


def test_demo_house_requirement_must_be_one_the_rig_knows(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(
        "name: x\nrequires: [a_bigger_house]\nturns:\n  - say: hi\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="a_bigger_house"):
        load_scenario(path)


def test_demo_house_scenario_is_skipped_by_name_with_reason_on_the_stack():
    scenario = load_scenario(SCENARIO)
    runner = Runner([scenario], target="stack")
    run, skipped = runner._split()
    assert run == []
    assert [s["name"] for s in skipped] == [scenario.name]
    assert "demo house" in skipped[0]["reason"]
    # No services, no stack, no lock: a run that is all skips never starts one.
    assert asyncio.run(runner.run()) == []
    assert runner.skipped == skipped


def test_demo_house_scenario_runs_where_there_is_a_demo_house():
    scenario = load_scenario(SCENARIO)
    run, skipped = Runner([scenario], target="harness")._split()
    assert run == [scenario] and skipped == []


# --- the throwaway as the report's target ------------------------------------


def test_throwaway_core_url_is_the_reports_target(tmp_path):
    with _fake_throwaway(tmp_path) as tw:
        ground = tw.ground()
        assert ground.base_url == tw.base_url == f"http://127.0.0.1:{tw.core_port}"
        runner = Runner([load_scenario(SCENARIO)], ground=ground)
        # The scenario is not skipped there, and it runs on this ground.
        assert runner._split()[1] == []
        assert runner._make_ground(runner._ground_for(runner.scenarios[0])) is ground
        assert _target_label("stack", ground) == tw.base_url
    assert _target_label("stack", None) == "stack"


def test_throwaway_gateway_is_given_the_throwaway_core_and_nothing_else(tmp_path):
    env = throwaway.gateway_env(Path(tmp_path), core_url="http://127.0.0.1:45000",
                                core_token=KEY, port=45001, key=KEY)
    assert env["HASS_URL"] == "http://127.0.0.1:45000" and env["HASS_TOKEN"] == KEY
    assert env["API_SERVER_HOST"] == "127.0.0.1" and env["API_SERVER_PORT"] == "45001"
    assert env["HERMES_HOME"] == str(tmp_path)
    # A caller's own chat tokens never reach a gateway that is not theirs.
    os.environ["TELEGRAM_BOT_TOKEN"] = KEY
    try:
        assert "TELEGRAM_BOT_TOKEN" not in throwaway.gateway_env(
            Path(tmp_path), core_url="http://127.0.0.1:45000", core_token=KEY, port=45001, key=KEY
        )
    finally:
        del os.environ["TELEGRAM_BOT_TOKEN"]


def test_throwaway_core_config_names_the_marker_and_the_gateway():
    import yaml

    from testing.harness.harness import build_config

    config = yaml.safe_load(build_config(
        45000, "127.0.0.1", "http://127.0.0.1:1", 1, 2, 3, name="throwaway-m",
        agent_url="http://127.0.0.1:45001", agent_key=KEY,
    ))
    assert config["jarvis"]["name"] == "throwaway-m"
    assert config["voice"]["agent"] == {"url": "http://127.0.0.1:45001", "key": KEY}
    plain = yaml.safe_load(build_config(45000, "127.0.0.1", "http://127.0.0.1:1", 1, 2, 3))
    assert "agent" not in plain["voice"]  # the harness's own callers are unchanged


def test_throwaway_keys_are_drawn_at_run_time(tmp_path):
    with _fake_throwaway(tmp_path) as first:
        a = (first.token, first.gateway_key, first.marker)
    with _fake_throwaway(tmp_path) as second:
        b = (second.token, second.gateway_key, second.marker)
    assert len(set(a) | set(b)) == 6 and all(len(x) >= 16 for x in a)


# --- the guard ---------------------------------------------------------------


@pytest.mark.parametrize("port", [8080, 8642, 8643, 9119])
def test_throwaway_refuses_production_ports(port, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(throwaway, "http_json", lambda *a, **k: sent.append(a[0]))
    ground = throwaway.DemoHouse(f"http://127.0.0.1:{port}", KEY, marker="m")
    with pytest.raises(LiveError, match="refus"):
        ground.start()
    assert sent == []  # refused before a single request left


@pytest.mark.parametrize("url", [
    "http://192.0.2.7:45123",       # a LAN-shaped address, not loopback
    "http://localhost:45123",       # a name, which resolves to whatever the host says
    "http://0.0.0.0:45123",
    "http://127.0.0.1",             # no port: the scheme's default, which is not ours
    "https://127.0.0.1:45123",      # not plain http on loopback
])
def test_throwaway_refuses_production_shaped_targets(url, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(throwaway, "http_json", lambda *a, **k: sent.append(a[0]))
    with pytest.raises(LiveError, match="refus"):
        throwaway.DemoHouse(url, KEY, marker="m").start()
    assert sent == []


def test_throwaway_refuses_production_deployed_core_address(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(throwaway, "http_json", lambda *a, **k: sent.append(a[0]))
    monkeypatch.setenv("AURA_URL", "http://127.0.0.1:45123/")
    with pytest.raises(LiveError, match="deployed"):
        throwaway.DemoHouse("http://127.0.0.1:45123", KEY, marker="m").start()
    assert sent == []


def test_throwaway_refuses_production_before_anything_is_booted(tmp_path):
    booted: list[str] = []
    tw = throwaway.Throwaway(
        boot_core=lambda t: booted.append("core"),
        boot_gateway=lambda t: booted.append("gateway"),
        pick_port=lambda: 8080,
        pid_file=tmp_path / "pids.json",
    )
    with pytest.raises(LiveError, match="refus"):
        tw.__enter__()
    assert booted == []


def test_throwaway_refuses_a_core_that_is_not_the_one_it_started(tmp_path, monkeypatch):
    """The marker is the proof: a listener that does not know it is refused."""
    monkeypatch.setattr(throwaway, "http_json", lambda *a, **k: {"location_name": "Home"})
    with pytest.raises(LiveError, match="not the throwaway"):
        throwaway.DemoHouse("http://127.0.0.1:45123", KEY, marker="throwaway-abc").start()


def test_throwaway_refuses_a_house_without_the_demo_bed_light(monkeypatch):
    def reply(url, *a, **k):
        if url.endswith("/api/config"):
            return {"location_name": "throwaway-abc"}
        raise OSError("404")

    monkeypatch.setattr(throwaway, "http_json", reply)
    with pytest.raises(LiveError, match="bed_light"):
        throwaway.DemoHouse("http://127.0.0.1:45123", KEY, marker="throwaway-abc").start()


def test_throwaway_accepts_the_core_it_started(tmp_path):
    with _fake_throwaway(tmp_path) as tw:
        assert tw.ground().start().base_url == tw.base_url


# --- teardown ----------------------------------------------------------------


def test_throwaway_teardown_after_a_clean_run(tmp_path):
    with _fake_throwaway(tmp_path) as tw:
        pids = list(tw.pids)
        assert len(pids) == 2 and all(_alive(p) for p in pids)
    assert not any(_alive(p) for p in pids)
    assert not Path(tw.work_dir).exists()


def test_throwaway_teardown_when_the_scenario_raises(tmp_path, monkeypatch):
    seen: dict[str, list[int]] = {}

    async def boom(args, ground=None):
        seen["pids"] = list(tw_holder[0].pids)
        assert all(_alive(p) for p in seen["pids"])
        raise RuntimeError("the scenario blew up")

    tw_holder: list = []

    def make():
        tw_holder.append(_fake_throwaway(tmp_path))
        return tw_holder[0]

    monkeypatch.setattr(runner_module, "_main", boom)
    with pytest.raises(RuntimeError, match="blew up"):
        throwaway.launch([], make_throwaway=make)
    assert len(seen["pids"]) == 2
    assert not any(_alive(p) for p in seen["pids"])


def test_throwaway_teardown_when_interrupted(tmp_path):
    pids: list[int] = []
    with pytest.raises(KeyboardInterrupt):
        with _fake_throwaway(tmp_path) as tw:
            pids.extend(tw.pids)
            raise KeyboardInterrupt
    assert pids and not any(_alive(p) for p in pids)


def test_throwaway_teardown_when_the_gateway_fails_to_start(tmp_path):
    def broken(tw):
        raise LiveError("the gateway never came up")

    tw = throwaway.Throwaway(boot_core=_boot_fake_core, boot_gateway=broken,
                             pid_file=tmp_path / "pids.json")
    with pytest.raises(LiveError, match="never came up"):
        tw.__enter__()
    assert tw.pids and not any(_alive(p) for p in tw.pids)


def test_throwaway_teardown_reaps_what_a_killed_launcher_left(tmp_path):
    """A launcher that died without finalizers leaves a pid file; the next run reaps it."""
    pid_file = tmp_path / "pids.json"
    tw = _fake_throwaway(tmp_path)
    tw.__enter__()
    orphans = list(tw.pids)
    keep = throwaway.subprocess.Popen([sys.executable, "-c", FAKE_GATEWAY])
    # A live process of ours whose recorded fingerprint does not match must be left alone.
    data = json.loads(pid_file.read_text(encoding="utf-8"))
    data.append({"pid": keep.pid, "start": 1, "cmdline": ["not", "this"]})
    pid_file.write_text(json.dumps(data), encoding="utf-8")
    try:
        throwaway.reap_stale(pid_file)
        assert _alive(keep.pid)
        assert not any(_alive(p) for p in orphans)
    finally:
        keep.kill()
        keep.wait()
        tw.close()


def test_throwaway_reaps_a_matching_stale_process(tmp_path):
    pid_file = tmp_path / "pids.json"
    proc = throwaway.subprocess.Popen([sys.executable, "-c", FAKE_GATEWAY],
                                      start_new_session=True)
    pid_file.write_text(json.dumps([throwaway.fingerprint(proc.pid)]), encoding="utf-8")
    throwaway.reap_stale(pid_file)
    deadline = time.monotonic() + 10
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proc.poll() is not None
