"""service_setup_status / service_setup: AURA's own view of, and hand on, the stack's setup (T-1523)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import FakeCtx, load_plugin  # noqa: E402

from _shared import _n8n_setup  # noqa: E402

plugin = load_plugin("aura-integrations")


def test_service_setup_tools_are_registered():
    ctx = FakeCtx()
    plugin.register(ctx)
    assert {"service_setup_status", "service_setup"} <= set(ctx.tools)


def test_service_setup_status_reports_each_service_without_a_secret(monkeypatch):
    monkeypatch.setattr(_n8n_setup, "status", lambda home=None: {"state": "provisioned", "detail": "d",
                                                                  "login_file": "/h/.storage/n8n-owner.json"})
    out = json.loads(plugin.services.handle_status({}))
    assert out["ok"] is True
    assert out["services"]["n8n"]["state"] == "provisioned"
    assert "login_file" in out["services"]["n8n"]


def test_service_setup_runs_the_named_service_and_refuses_an_unknown_one(monkeypatch):
    ran = []
    monkeypatch.setattr(_n8n_setup, "ensure", lambda **kw: ran.append(kw) or {"state": "adopted", "detail": "ok"})

    out = json.loads(plugin.services.handle_setup({"service": "n8n"}))
    assert out == {"ok": True, "service": "n8n", "state": "adopted", "detail": "ok"}
    assert ran == [{}]

    bad = json.loads(plugin.services.handle_setup({"service": "printer"}))
    assert bad["ok"] is False and "n8n" in bad["error"]


def _house(monkeypatch, answer):
    import httpx

    from _shared import _house

    seen = []

    def core(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return answer(request)

    monkeypatch.setattr(_house, "client", lambda timeout=20.0: httpx.Client(
        base_url="http://house.test", transport=httpx.MockTransport(core)))
    return seen


def test_service_setup_home_assistant_is_read_and_redone_through_the_house(monkeypatch):
    import httpx

    body = {"service_response": {"state": "provisioned", "detail": "d", "url": "http://127.0.0.1:8123",
                                 "has_token": True, "bridge": {"connected": True, "entities": 3}}}
    seen = _house(monkeypatch, lambda r: httpx.Response(200, json=body))

    status = json.loads(plugin.services.handle_status({}))["services"]["home_assistant"]
    redo = json.loads(plugin.services.handle_setup({"service": "home_assistant"}))

    assert status["state"] == "provisioned" and status["bridge"]["entities"] == 3
    assert redo == {"ok": True, "service": "home_assistant", "state": "provisioned", "detail": "d"}
    assert seen == ["/api/services/home_assistant/status", "/api/services/home_assistant/setup"]


def test_service_setup_home_assistant_off_or_down_is_a_state_not_an_error(monkeypatch):
    import httpx

    _house(monkeypatch, lambda r: httpx.Response(400, json={"message": "Service not found"}))
    off = json.loads(plugin.services.handle_status({}))["services"]["home_assistant"]
    assert off["state"] == "off"

    _house(monkeypatch, lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    down = json.loads(plugin.services.handle_status({}))["services"]["home_assistant"]
    assert down["state"] == "unreachable"
