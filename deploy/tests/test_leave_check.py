"""The leave-by gate: the model wakes only when the phone is reachable (T-1227).

``hermes/cron/scripts/leave-check.py`` run as the scheduler runs it (a subprocess with a
scheduler's environment) against a fake adapter. What the runner honours is the last line:
``{"wakeAgent": false}`` skips the model, anything else wakes it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "hermes/cron/scripts/leave-check.py"
KEY = "gate-key-0123456789"
SKIP = '{"wakeAgent": false}'


class _Adapter(BaseHTTPRequestHandler):
    reply: tuple[int, bytes] = (200, b'{"reachable": false}')
    seen: list[str | None] = []

    def do_GET(self):  # noqa: N802 - the http.server name
        _Adapter.seen.append(f"{self.path} {self.headers.get('Authorization')}")
        code, body = _Adapter.reply
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def adapter():
    server = HTTPServer(("127.0.0.1", 0), _Adapter)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Adapter.seen = []
    yield server
    server.shutdown()
    server.server_close()


def _run(tmp_path, url: str, key: str | None = KEY) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AURA_", "JARVIS_"))}
    env.update(HERMES_HOME=str(tmp_path), AURA_ADAPTER_URL=url)
    if key is not None:
        env["AURA_ADAPTER_KEY"] = key
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True,
                          timeout=60, cwd=tmp_path)


def _wakes(done: subprocess.CompletedProcess) -> bool:
    assert done.returncode == 0, done.stderr
    return done.stdout.strip().splitlines()[-1] != SKIP


def test_leave_gate_skips_the_model_when_the_phone_is_not_reachable(tmp_path, adapter):
    _Adapter.reply = (200, b'{"reachable": false}')
    done = _run(tmp_path, f"http://127.0.0.1:{adapter.server_port}")
    assert not _wakes(done)
    assert done.stdout.strip().splitlines()[-1] == SKIP and "not on the house's device channel" in done.stdout
    # The adapter was asked with the key and nothing else of ours.
    assert _Adapter.seen == [f"/api/phone/reachable Bearer {KEY}"]


def test_leave_gate_wakes_the_model_when_the_phone_is_reachable(tmp_path, adapter):
    _Adapter.reply = (200, b'{"reachable": true}')
    done = _run(tmp_path, f"http://127.0.0.1:{adapter.server_port}")
    assert _wakes(done) and SKIP not in done.stdout


@pytest.mark.parametrize("code,body,why", [
    (401, b'{"detail": "Unauthorized"}', "HTTP 401"),
    (502, b'{"detail": "the house is unreachable"}', "HTTP 502"),
    (200, b"not json", "JSONDecodeError"),
    (200, b'{"reachable": "yes"}', "yes or no"),
    (200, b"[true]", "yes or no"),
])
def test_leave_gate_fails_open_when_the_adapter_refuses_or_is_confused(tmp_path, adapter, code, body, why):
    _Adapter.reply = (code, body)
    done = _run(tmp_path, f"http://127.0.0.1:{adapter.server_port}")
    assert _wakes(done) and SKIP not in done.stdout
    assert why in done.stdout and KEY not in done.stdout + done.stderr


def test_leave_gate_fails_open_when_the_adapter_does_not_answer(tmp_path, adapter):
    port = adapter.server_port
    adapter.shutdown()
    adapter.server_close()  # nothing listens there now: a refused connection
    done = _run(tmp_path, f"http://127.0.0.1:{port}")
    assert _wakes(done) and "could not run" in done.stdout


def test_leave_gate_fails_open_without_a_key(tmp_path, adapter):
    done = _run(tmp_path, f"http://127.0.0.1:{adapter.server_port}", key=None)
    assert _wakes(done) and "no adapter key" in done.stdout
    assert _Adapter.seen == []  # nothing was sent unauthenticated


def test_the_leave_by_row_is_gated_by_the_script():
    rows = [r.split("\t") for r in (ROOT / "hermes/cron/jobs.tsv").read_text().splitlines()
            if r.startswith("Leave-by nudge\t")]
    assert len(rows) == 1 and rows[0][3] == "leave-check.py"
    assert "phone_action read_calendar" in rows[0][4]
    assert json.loads(SKIP) == {"wakeAgent": False}
