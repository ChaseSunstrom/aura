"""The adapter relays /api/models/* to the model manager, behind the bearer key.

The manager's own behaviour is pinned in deploy/tests/test_models_manager.py against the same
contract file; here only the relay is checked: method, path, query and body reach the upstream
and its status and JSON come back, and a call without the key never reaches it.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aura_adapter.app import create_app
from aura_adapter.config import Settings

KEY = "app-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}
CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/models-api.json").read_text())
SEEN: list[tuple[str, str, bytes]] = []
SEEN_AUTH: list = []


class _Upstream(BaseHTTPRequestHandler):
    def _go(self):
        n = int(self.headers.get("content-length") or 0)
        SEEN.append((self.command, self.path, self.rfile.read(n)))
        SEEN_AUTH.append(self.headers.get("authorization"))
        if self.path.startswith("/api/models/nope"):
            code, body = 404, {"ok": False, "error": "No such model."}
        else:
            code, body = 200, CONTRACT["endpoints"]["list"]["example"]
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PATCH = do_DELETE = _go

    def log_message(self, *a):
        pass


@pytest.fixture
def client():
    srv = HTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    SEEN.clear()
    SEEN_AUTH.clear()
    settings = Settings(api_key=KEY, models_manager_url=f"http://127.0.0.1:{srv.server_port}", models_api_key="mkey")
    with TestClient(create_app(settings)) as c:
        yield c
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=10)
    # A thread still alive here keeps serving into the next module's tests.
    assert not thread.is_alive(), "relay: the fake upstream thread outlived its teardown"


def test_every_contract_endpoint_is_relayed_with_its_method_path_and_body(client):
    for name, ep in CONTRACT["endpoints"].items():
        path = CONTRACT["base"] + ep["path"].replace("{id}", "m1").replace("{query}", "qwen")
        body = ep.get("body")
        r = client.request(ep["method"], path, headers=AUTH, json=body)
        assert r.status_code == 200 and r.json()["ok"] is True, name
        method, seen_path, seen_body = SEEN[-1]
        assert (method, seen_path) == (ep["method"], path), name
        assert (json.loads(seen_body) if seen_body else None) == body, name


def test_an_upstream_error_keeps_its_status_and_sentence(client):
    r = client.delete("/api/models/nope", headers=AUTH)
    assert r.status_code == 404 and r.json()["error"] == "No such model."


def test_without_the_key_nothing_reaches_the_manager(client):
    for method in ("GET", "POST", "DELETE"):
        assert client.request(method, "/api/models/x").status_code == 401
    assert client.get("/api/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert SEEN == []


def test_the_manager_gets_its_own_key_never_the_apps(client):
    # Commit review, 2026-10-09: the manager refuses calls without MODELS_API_KEY, and the
    # apps' bearer key has no business leaving the adapter.
    client.get(CONTRACT["base"], headers={"Authorization": f"Bearer {KEY}"})
    assert SEEN_AUTH and all(a == "Bearer mkey" for a in SEEN_AUTH)


def test_with_no_manager_key_the_panel_is_off_not_open():
    with TestClient(create_app(Settings(api_key=KEY, models_manager_url="http://127.0.0.1:9"))) as c:
        r = c.get(CONTRACT["base"], headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 503


def test_with_no_llama_swap_url_the_panel_is_off_not_a_request_to_none():
    with TestClient(create_app(Settings(api_key=KEY, models_api_key="m", llama_swap_url=""))) as c:
        r = c.get(CONTRACT["base"], headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 503 and "LLAMA_SWAP_URL" in r.text
