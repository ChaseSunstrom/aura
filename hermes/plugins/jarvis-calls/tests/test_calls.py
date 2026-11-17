"""jarvis-calls against a mocked house. Pinned: eight tools register; status is free and fenced as a
stranger's words; place, answer, decline, back and the screening rule ask (a rule is never lifted by
Allowed, a first call to someone new still asks); the phone gets the contract's command only when the
tool runs; a bad reply or rule is refused before anything is sent. Not pinned: a live phone, or a real
call (nothing here can place one)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _levels, _matrix, _messages, _taint  # noqa: E402

plugin = load_plugin("jarvis-calls")
tools = plugin.tools
CONTRACT = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "call-event.json")
                       .read_text(encoding="utf-8"))
ACTIONS = CONTRACT["actions"]
_REAL = httpx.Client
PHONE = [{"id": "p1", "name": "Pixel", "platform": "android", "connected": True, "actions": []}]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def serve(monkeypatch, handler):
    monkeypatch.setenv("HASS_URL", "http://house.test")
    monkeypatch.setenv("HASS_TOKEN", "t")
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))


def phone(seen):
    def handler(req):
        if req.url.path == "/api/config/companion/list":
            return httpx.Response(200, json=PHONE)
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"service_response": {"status": "ok"}})
    return handler


def test_calls_registers_eight_tools():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert sorted(ctx.tools) == ["calls_answer", "calls_back", "calls_decline", "calls_later", "calls_place",
                                 "calls_rule", "calls_status", "calls_summary"]


def test_calls_matrix_status_and_notes_are_free_and_the_rest_ask():
    for tool in ("calls_status", "calls_later", "calls_summary"):
        assert _matrix.decide(tool, {}).verdict == "free", tool
    for tool in ("calls_place", "calls_answer", "calls_decline", "calls_back", "calls_rule"):
        assert _matrix.decide(tool, {}).verdict == "ask", tool
    assert _taint._reads_outside("calls_status") and not _taint._reads_outside("calls_answer")


def test_calls_every_asking_tool_has_a_card_naming_what_happens():
    card = _messages.card("calls_place", {"to": "Sam"}, "", "you asked", home=False)
    assert "Call Sam" in card
    for tool in ("calls_answer", "calls_decline", "calls_back", "calls_rule"):
        assert tool in _messages._CARD


def test_calls_allowed_lifts_answering_but_never_the_screening_rule(tmp_path):
    _levels.set_level("calls", "allowed", actor="test")
    assert _levels.capability_of("calls_answer") == "calls"
    assert _matrix.decide("calls_answer", {}).verdict == "free"
    assert _matrix.decide("calls_rule", {"screen_unknown": "reject"}).verdict == "ask"
    # a number nobody has rung before still asks
    assert _matrix.decide("calls_place", {"to": "+15551234567"}).verdict == "ask"
    _levels.set_level("calls", "off", actor="test")
    assert _matrix.decide("calls_status", {}).verdict == "deny"


def test_calls_status_asks_the_house_and_marks_the_result_untrusted(monkeypatch):
    seen = []

    def handler(req):
        seen.append((req.url.path, dict(req.url.params)))
        return httpx.Response(200, json={"rule": "ring", "calls": [{"from": "Sam"}]})

    serve(monkeypatch, handler)
    out = json.loads(tools.handle_status({"limit": 5}))
    assert seen == [("/api/calls", {"limit": "5"})] and out["trust"] == "untrusted" and out["calls"][0]["from"] == "Sam"


def test_calls_commands_reach_the_one_online_phone_with_the_contracts_action_names(monkeypatch):
    seen = []
    serve(monkeypatch, phone(seen))
    assert json.loads(tools.handle_answer({}))["ok"] is True
    assert json.loads(tools.handle_decline({"reply": "driving"}))["ok"] is True
    assert json.loads(tools.handle_back({"call_id": "call-7"}))["ok"] is True
    assert json.loads(tools.handle_place({"to": "Sam"}))["ok"] is True
    assert [(s["device_id"], s["action"], s["params"]) for s in seen] == [
        ("p1", ACTIONS["answer"], {}),
        ("p1", ACTIONS["decline"], {"reply": "driving"}),
        ("p1", ACTIONS["back"], {"call_id": "call-7"}),
        ("p1", "place_call", {"to": "Sam"}),
    ]


def test_calls_a_bad_reply_or_rule_or_missing_argument_sends_nothing(monkeypatch):
    seen = []
    serve(monkeypatch, phone(seen))
    for out in (tools.handle_decline({"reply": "I am at the pub"}), tools.handle_back({}), tools.handle_place({"to": " "}),
                tools.handle_rule({"screen_unknown": "block"}), tools.handle_rule({}), tools.handle_summary({"call_id": "x"}),
                tools.handle_later({})):
        assert "error" in json.loads(out)
    assert seen == []


def test_calls_with_no_phone_online_says_so(monkeypatch):
    serve(monkeypatch, lambda req: httpx.Response(200, json=[]))
    out = json.loads(tools.handle_answer({}))
    assert out["ok"] is False and "phone" in out["error"]


def test_calls_the_rule_and_the_notes_go_to_the_house(monkeypatch):
    seen = []

    def handler(req):
        seen.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"rule": "reject"})

    serve(monkeypatch, handler)
    tools.handle_rule({"screen_unknown": "reject", "auto_reply": True})
    tools.handle_summary({"call_id": "c1", "notes": "Dinner at 8", "follow_ups": ["book table"]})
    tools.handle_later({"call_id": "c1"})
    assert seen == [("/api/calls/rule", {"rule": "reject", "auto_reply": True}),
                    ("/api/calls/summary", {"call_id": "c1", "notes": "Dinner at 8", "follow_ups": ["book table"]}),
                    ("/api/calls/callback", {"call_id": "c1"})]
