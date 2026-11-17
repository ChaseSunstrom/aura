"""GET /api/board: the jarvis-board plugin's counts, with their age, and the models' live load (T-0733).

The plugin writes board.json inside the jarvis container (hermes/plugins/jarvis-board, its own
tests pin the numbers); here only what the route adds is checked: each source's status and age,
a missing or broken file named as unavailable, llama-swap's and the manager's live load, the key.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aura_adapter import board
from aura_adapter.app import create_app
from aura_adapter.config import Settings

KEY = "app-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}
GPUS = {"ok": True, "gpus": [{"index": 0, "name": "RTX", "total_mib": 24576, "used_mib": 21650,
                              "users": [{"who": "models", "mib": 20890}, {"who": "voice", "mib": 760}]}]}
RUNNING = {"running": [{"model": "qwen", "state": "ready", "cmd": "vllm serve --secret-flag", "proxy": "http://x"}]}
UP = {"down": False}


class _Upstream(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if UP["down"]:
            self.send_response(503)
            self.end_headers()
            return
        body = GPUS if self.path == "/api/models/gpus" else RUNNING if self.path == "/running" else {}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def _written(path: Path, age_s: float, **over) -> None:
    data = {"schema": 1, "at": time.time() - age_s, "window_s": 86400, "models": [{"model": "qwen", "turns": 3}],
            "hours": [], "kinds": [], "latency": {"turns": 0, "p50_ms": None, "p95_ms": None},
            "tools": {"per_minute": [0] * 60, "top": []}, "cost": {"usd": None, "why": "x"}, "errors": {}}
    data.update(over)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def client(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    UP["down"] = False
    monkeypatch.setattr(board, "BOARD_FILE", tmp_path / "board" / "board.json")
    url = f"http://127.0.0.1:{srv.server_port}"
    settings = Settings(api_key=KEY, llama_swap_url=url, models_manager_url=url, models_api_key="mkey")
    with TestClient(create_app(settings)) as c:
        yield c
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=10)
    # A thread still alive here keeps serving into the next module's tests.
    assert not thread.is_alive(), "board: the fake upstream thread outlived its teardown"


def test_the_key_is_required(client):
    assert client.get("/api/board").status_code == 401


def test_a_fresh_board_is_ok_with_its_age_and_the_live_load(client):
    _written(board.BOARD_FILE, 4)
    r = client.get("/api/board", headers=AUTH).json()
    assert r["sources"]["hermes"]["status"] == "ok"
    assert 3 <= r["sources"]["hermes"]["age_s"] < 30
    assert r["hermes"]["models"] == [{"model": "qwen", "turns": 3}]
    assert r["sources"]["models"] == {"status": "ok", "age_s": 0, "why": None}
    # Only the model and its state: llama-swap's command line and proxy stay on the server.
    assert r["models"] == [{"model": "qwen", "state": "ready"}]
    assert r["sources"]["gpus"]["status"] == "ok"
    assert r["gpus"] == GPUS["gpus"]


def test_an_old_board_is_stale_and_still_served(client):
    _written(board.BOARD_FILE, board.STALE_S + 60)
    r = client.get("/api/board", headers=AUTH).json()
    assert r["sources"]["hermes"]["status"] == "stale"
    assert r["sources"]["hermes"]["age_s"] >= board.STALE_S
    assert r["hermes"]["models"]


def test_no_file_or_a_broken_one_is_unavailable_with_the_reason(client):
    r = client.get("/api/board", headers=AUTH).json()
    assert r["sources"]["hermes"]["status"] == "unavailable"
    assert "not written yet" in r["sources"]["hermes"]["why"]
    assert r["hermes"] is None
    board.BOARD_FILE.parent.mkdir(parents=True)
    board.BOARD_FILE.write_text("{not json")
    r = client.get("/api/board", headers=AUTH).json()
    assert r["sources"]["hermes"]["status"] == "unavailable"
    _written(board.BOARD_FILE, 1, schema=99)
    assert "schema" in client.get("/api/board", headers=AUTH).json()["sources"]["hermes"]["why"]
    board.BOARD_FILE.write_text(json.dumps({"schema": board.SCHEMA, "at": "soon"}))  # not a 500
    assert client.get("/api/board", headers=AUTH).json()["sources"]["hermes"]["status"] == "unavailable"


def test_a_section_the_plugin_could_not_read_says_so(client):
    _written(board.BOARD_FILE, 1, kinds=None, errors={"turns": "no such column: finish_reason"})
    r = client.get("/api/board", headers=AUTH).json()
    assert r["sources"]["hermes"]["status"] == "ok"
    assert r["hermes"]["errors"] == {"turns": "no such column: finish_reason"}


def test_the_models_server_down_is_unavailable_and_the_board_still_answers(client):
    _written(board.BOARD_FILE, 1)
    UP["down"] = True
    r = client.get("/api/board", headers=AUTH).json()
    assert r["sources"]["models"]["status"] == "unavailable"
    assert r["sources"]["gpus"]["status"] == "unavailable"
    assert r["models"] is None and r["gpus"] is None
    assert r["sources"]["hermes"]["status"] == "ok"
