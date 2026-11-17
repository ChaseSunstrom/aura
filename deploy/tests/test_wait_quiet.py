"""deploy/wait_quiet.py: a deploy waits for an open voice turn to end, up to a cap (T-0577).

A fake adapter answers /api/status. Pinned: it waits while a turn is open and says why, goes on
the moment it is quiet, gives up at the cap, and never waits on an adapter that is down or has
no "busy". Not covered: scripts/deploy-here.sh calling it (read as text below, not run).
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))
import wait_quiet  # noqa: E402


@pytest.fixture
def adapter():
    """A status route that plays ``answers`` in order, then repeats the last."""
    answers: list[dict] = []
    asked: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            asked.append(self.headers.get("Authorization", ""))
            body = json.dumps(answers.pop(0) if len(answers) > 1 else answers[0]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", answers, asked
    server.shutdown()


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _wait(base: str, clock: Clock, cap: float = 180.0) -> float:
    return wait_quiet.wait(base, "k", cap, 5.0, sleep=clock.sleep, clock=lambda: clock.now)


def test_it_waits_while_a_turn_is_open_says_why_and_goes_on_when_quiet(adapter, capsys):
    base, answers, asked = adapter
    answers += [{"busy": {"turns": 1, "car": False}}, {"busy": {"turns": 0, "car": True}}, {"busy": {"turns": 0, "car": False}}]
    clock = Clock()
    assert _wait(base, clock) == 10.0 and clock.slept == [5.0, 5.0]
    said = capsys.readouterr().err
    assert "waiting: 1 voice turn(s) open (0 of 180 s)" in said
    # A drive with no open turn is waited on too, and is named for what it is.
    assert "waiting: the car spoke in the last ten minutes (5 of 180 s)" in said and "quiet after 10 s" in said
    assert asked[0] == "Bearer k"


def test_it_gives_up_at_the_cap(adapter, capsys):
    base, answers, _ = adapter
    answers.append({"busy": {"turns": 2, "car": False}})
    clock = Clock()
    assert _wait(base, clock, cap=20.0) == 20.0 and len(clock.slept) == 4
    assert "still busy after 20 s; deploying anyway" in capsys.readouterr().err


def test_a_quiet_house_an_old_adapter_and_a_dead_one_are_not_waited_on(adapter, capsys):
    base, answers, _ = adapter
    clock = Clock()
    for answer in ({"busy": {"turns": 0, "car": False}}, {"ok": True}, {"busy": "yes"}):
        answers[:] = [answer]
        assert _wait(base, clock) == 0.0
    assert _wait("http://127.0.0.1:9", clock) == 0.0
    assert clock.slept == [] and "cannot ask the adapter" in capsys.readouterr().err


def test_deploy_here_asks_before_the_recreate_and_cannot_fail_on_it():
    script = (ROOT / "scripts/deploy-here.sh").read_text()
    ask, up = script.index("wait_quiet.py"), script.index("docker compose $files up -d --no-deps")
    assert ask < up
    assert "wait_quiet.py" in script[ask:script.index("\n", ask)] and "|| " in script[ask:script.index("\n", ask)]


def test_ci_deploy_asks_before_the_recreate_and_cannot_fail_on_it_either():
    """scripts/deploy.sh recreates jarvis and the adapter in rollout(); a recreate cuts a reply
    in mid-sentence, so the wait runs on the host, from the deploy dir, before that compose up.
    The wait's failure (missing file, no key, adapter down) must never be the deploy's (T-1099).
    Not covered: whether the adapter actually reports busy on the host (needs the stack)."""
    script = (ROOT / "scripts/deploy.sh").read_text()
    rollout = script.index("rollout() {")
    up = script.index("$COMPOSE up -d --remove-orphans", rollout)
    ask = script.index("wait_quiet.py", rollout)
    assert rollout < ask < up, "the wait must run inside rollout(), before the recreating up"
    line = script[script.rindex("\n", 0, ask) + 1:script.index("\n", ask)]
    assert "cd $REMOTE_DIR" in line, "wait_quiet.py ships to the deploy dir; run it there"
    assert "|| " in line, line
    assert "$TAILSCALE_IP:8643" in line, "the adapter the deploy checks in step 5 is on the same port"
