"""The deploy's smoke asks the two erase doors for a window of 0 and erases nothing (T-1412).

Fake servers on loopback stand in for the adapter and the house: nothing here touches a real
stack. A door that erases on a window of 0, one that answers without a key and one that is not
deployed each fail the step; and no request, whatever the door answers, carries a window that a
door would take (a whole number from 1 to 1440).
"""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("erase_doors_check", ROOT / "scripts/erase_doors_check.py")
assert _spec and _spec.loader
edc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edc)

KEY = "adapter-key"
TOKEN = "house-token"


class _Door(BaseHTTPRequestHandler):
    """One door. `mode` picks how it misbehaves; `seen` records every request it got."""

    mode = "good"
    path_ok = ""
    key = KEY
    seen: list[dict] = []

    def log_message(self, *_):
        pass

    def _send(self, status, body=b"{}"):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        auth = self.headers.get("Authorization", "")
        type(self).seen.append({"path": self.path, "auth": auth, "body": json.loads(raw or b"null")})
        if self.path != self.path_ok or self.mode == "missing":
            return self._send(404, b'{"detail":"Not Found"}')
        keyed = auth == f"Bearer {self.key}"
        if self.mode == "open":
            return self._send(200, b'{"minutes":0,"artifacts":0}')
        if not keyed:
            return self._send(401, b'{"detail":"Unauthorized"}')
        if self.mode == "erases":
            return self._send(200, b'{"minutes":0,"artifacts":3}')
        if self.mode == "no_window_check":
            return self._send(500, b"{}")
        return self._send(400, b'{"detail":"minutes is a whole number from 1 to 1440"}')


def _serve(path, key, mode="good"):
    handler = type("H", (_Door,), {"mode": mode, "path_ok": path, "key": key, "seen": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, handler, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def doors():
    started = []

    def start(adapter="good", house="good"):
        a = _serve(edc.ADAPTER_PATH, KEY, adapter)
        h = _serve(edc.HOUSE_PATH, TOKEN, house)
        started.extend([a[0], h[0]])
        env = {"AURA_BASE": a[2], "AURA_API_KEY": KEY, "HASS_URL": h[2], "HASS_TOKEN": TOKEN}
        return env, a[1], h[1]

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def _run(env, capsys):
    code = edc.main([], env)
    return code, capsys.readouterr().out


def _could_erase(body):
    minutes = body.get("minutes") if isinstance(body, dict) else None
    return type(minutes) is int and 1 <= minutes <= 1440 or not isinstance(body, dict) or "minutes" not in body


def test_erase_doors_guarded_pass_and_say_what_answered(doors, capsys):
    env, adapter, house = doors()
    code, out = _run(env, capsys)
    assert code == 0, out
    assert out.strip() == "adapter 401/400; house 401/400"
    # Each door was asked twice: once with no key, once with the key.
    assert [s["auth"] for s in adapter.seen] == ["", f"Bearer {KEY}"]
    assert [s["auth"] for s in house.seen] == ["", f"Bearer {TOKEN}"]


@pytest.mark.parametrize("adapter,house", [
    ("good", "good"), ("erases", "good"), ("good", "erases"), ("open", "good"), ("good", "open"),
    ("missing", "good"), ("good", "missing"), ("no_window_check", "good"), ("good", "no_window_check"),
])
def test_erase_doors_never_send_a_window_that_could_erase(doors, capsys, adapter, house):
    """Whatever a door answers, every request carries minutes 0 and nothing else."""
    env, a, h = doors(adapter, house)
    _run(env, capsys)
    sent = a.seen + h.seen
    assert sent, "the check asked nobody"
    for request in sent:
        assert request["body"] == {"minutes": 0}, request
        assert not _could_erase(request["body"]), request


@pytest.mark.parametrize("body", [
    {"minutes": 1}, {"minutes": 60}, {"minutes": 1440}, {"minutes": True}, {"minutes": 0.0},
    {"minutes": "5"}, {"minutes": -1}, {"minutes": 1441}, {}, {"minutes": 0, "extra": 1},
])
def test_erase_door_post_refuses_to_send_any_other_window(doors, body):
    env, adapter, _ = doors()
    with pytest.raises(ValueError):
        edc.post(env["AURA_BASE"] + edc.ADAPTER_PATH, KEY, body, 2)
    assert adapter.seen == []


def test_erase_door_that_erases_on_a_window_of_zero_fails(doors, capsys):
    env, _, _ = doors(adapter="erases")
    code, out = _run(env, capsys)
    assert code == 1 and "answered 200, not 400" in out and "adapter" in out
    env, _, _ = doors(house="no_window_check")
    code, out = _run(env, capsys)
    assert code == 1 and "house" in out and "500" in out


def test_erase_door_that_answers_without_a_key_fails(doors, capsys):
    env, _, _ = doors(adapter="open")
    code, out = _run(env, capsys)
    assert code == 1 and "not guarded" in out and "adapter" in out
    env, _, _ = doors(house="open")
    code, out = _run(env, capsys)
    assert code == 1 and "not guarded" in out and "house" in out


def test_erase_door_that_is_not_deployed_fails(doors, capsys):
    env, _, _ = doors(adapter="missing")
    code, out = _run(env, capsys)
    assert code == 1 and "is not there (404)" in out and "adapter" in out
    env, _, _ = doors(house="missing")
    code, out = _run(env, capsys)
    assert code == 1 and "is not there (404)" in out and "house" in out


def test_erase_door_that_does_not_answer_fails(doors, capsys):
    env, _, _ = doors()
    env["AURA_BASE"] = "http://127.0.0.1:1"
    code, out = _run(env, capsys)
    assert code == 1 and "did not answer" in out


def test_erase_door_of_the_house_without_a_token_is_asked_only_without_a_key(doors, capsys):
    env, _, house = doors()
    del env["HASS_TOKEN"]
    code, out = _run(env, capsys)
    assert code == 0 and out.strip() == "adapter 401/400; house 401 unkeyed (no key to ask with)"
    assert [s["auth"] for s in house.seen] == [""]


def test_erase_door_of_the_house_is_left_out_when_the_smoke_has_no_address(doors, capsys):
    env, _, house = doors()
    del env["HASS_URL"]
    code, out = _run(env, capsys)
    assert code == 0 and out.strip() == "adapter 401/400; house not asked (HASS_URL unset)"
    assert house.seen == []


def test_erase_door_check_needs_the_adapter_key(capsys):
    assert edc.main([], {"AURA_BASE": "http://127.0.0.1:1"}) == 1
    assert "no adapter base or key" in capsys.readouterr().out


def test_erase_door_arguments_beat_the_environment(doors, capsys):
    env, adapter, _ = doors()
    assert edc.main([env["AURA_BASE"] + "/", KEY], {"AURA_BASE": "http://127.0.0.1:1", "AURA_API_KEY": "x"}) == 0
    assert [s["auth"] for s in adapter.seen] == ["", f"Bearer {KEY}"]
    capsys.readouterr()


def test_erase_door_step_is_in_the_smoke_the_deploys_and_the_board():
    smoke = (ROOT / "scripts/smoke.sh").read_text()
    assert '"$(dirname "$0")/erase_doors_check.py"' in smoke
    assert 'echo "== 7. ' in smoke
    for deploy in ("scripts/deploy.sh", "scripts/deploy-here.sh"):
        assert '/scripts/erase_doors_check.py"' in (ROOT / deploy).read_text(), deploy
    contract = json.loads((ROOT / "tests/contracts/verified.json").read_text())
    row = next(c for c in contract["checks"] if c["id"] == "erase")
    assert smoke.count(f'pass "{row["prefix"]}') == 1 and row["section"] == "7."
