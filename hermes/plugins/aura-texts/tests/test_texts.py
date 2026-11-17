"""aura-texts against a mocked house. Pinned: three tools register; read and search are free and
their results are fenced as other people's words; a reply asks every time (a level Allowed lifts it
for a known conversation only), its card names the recipient and the exact text, the phone gets the
command only when the tool runs (after the approval), and the audit log keeps a hash and a length,
never the text. Not pinned: a live phone, or a real send (nothing here can send one)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _audit, _levels, _matrix, _messages, _taint  # noqa: E402

plugin = load_plugin("aura-texts")
tools = plugin.tools
CONTRACT = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "message-mirror.json")
                      .read_text(encoding="utf-8"))
EX = CONTRACT["examples"]
_REAL = httpx.Client


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def serve(monkeypatch, handler):
    monkeypatch.setenv("HASS_URL", "http://house.test")
    monkeypatch.setenv("HASS_TOKEN", "t")
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))


def test_texts_registers_three_tools():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert sorted(ctx.tools) == ["texts_read", "texts_reply", "texts_search"]


def test_texts_matrix_reads_are_free_and_a_reply_asks_every_time():
    assert _matrix.decide("texts_read", {}).verdict == "free"
    assert _matrix.decide("texts_search", {"query": "x"}).verdict == "free"
    assert _matrix.decide("texts_reply", dict(EX["reply_sms"])).verdict == "ask"


def test_texts_the_readers_are_fenced_as_untrusted_words():
    assert _taint._reads_outside("texts_read") and _taint._reads_outside("texts_search")
    assert not _taint._reads_outside("texts_reply")


def test_texts_the_card_names_the_recipient_and_the_whole_text():
    long_text = "word " * 90  # 450 chars, under the reply cap
    card = _messages.card("texts_reply", {"conversation": EX["reply_sms"]["conversation"], "text": long_text.strip()},
                          "", "you asked", home=False)
    assert EX["reply_sms"]["conversation"] in card and long_text.strip() in card


def test_texts_a_reply_longer_than_the_card_can_show_is_refused_before_anything_is_sent(monkeypatch):
    calls = []
    serve(monkeypatch, lambda req: calls.append(req.url.path) or httpx.Response(200, json=[]))
    out = json.loads(tools.handle_reply({"conversation": "sms:+15551234567", "text": "x" * (CONTRACT["limits"]["reply_card_max"] + 1)}))
    assert "error" in out and calls == []


def test_texts_allowed_level_lifts_the_ask_for_a_known_conversation_only(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _levels.set_level("texts", "allowed", actor="test")
    assert _levels.capability_of("texts_reply") == "texts"
    assert _levels.capability_of("texts_read") == "texts"
    # a conversation nobody has written to yet still asks, as a first-time mail recipient does
    assert _matrix.decide("texts_reply", dict(EX["reply_sms"])).verdict == "ask"
    _audit.append(_audit.KIND_OUTBOUND, actor="model", tool="texts_reply",
                  args={"to": [EX["reply_sms"]["conversation"]]}, outcome="sent")
    assert _matrix.decide("texts_reply", dict(EX["reply_sms"])).verdict == "free"
    _levels.set_level("texts", "read", actor="test")
    assert _matrix.decide("texts_reply", dict(EX["reply_sms"])).verdict == "deny"
    _levels.set_level("texts", "off", actor="test")
    assert _matrix.decide("texts_read", {}).verdict == "deny"


def test_texts_read_asks_the_house_and_marks_the_result_untrusted(monkeypatch):
    seen = []

    def handler(req):
        seen.append((req.url.path, dict(req.url.params)))
        return httpx.Response(200, json={"trust": "untrusted", "conversations": [],
                                         "messages": [EX["message_mirror"]]})

    serve(monkeypatch, handler)
    out = json.loads(tools.handle_read({"conversation": "sms:+15551234567", "limit": 5}))
    assert seen == [("/api/messages", {"conversation": "sms:+15551234567", "limit": "5"})]
    assert out["trust"] == "untrusted" and out["messages"][0]["text"] == "Lunch at 1?"


def test_texts_search_needs_a_query(monkeypatch):
    serve(monkeypatch, lambda req: httpx.Response(200, json={"messages": []}))
    assert "error" in json.loads(tools.handle_search({"query": "  "}))


def test_texts_reply_goes_to_the_one_online_phone_as_the_message_reply_command(monkeypatch):
    seen = []

    def handler(req):
        if req.url.path == "/api/config/companion/list":
            return httpx.Response(200, json=[{"id": "p1", "name": "Pixel", "platform": "android", "connected": True, "actions": []},
                                             {"id": "d1", "name": "Desk", "platform": "desktop", "connected": True}])
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"service_response": {"status": "ok", "result": {"sent": True}}})

    serve(monkeypatch, handler)
    out = json.loads(tools.handle_reply(dict(EX["reply_sms"])))
    assert out["ok"] is True
    assert seen[0]["device_id"] == "p1" and seen[0]["action"] == CONTRACT["reply_action"]
    assert seen[0]["params"] == EX["reply_sms"]


def test_texts_reply_with_no_phone_online_says_so_and_sends_nothing(monkeypatch):
    serve(monkeypatch, lambda req: httpx.Response(200, json=[]))
    out = json.loads(tools.handle_reply(dict(EX["reply_sms"])))
    assert out["ok"] is False and "phone" in out["error"]


def test_texts_reply_needs_a_conversation_and_text(monkeypatch):
    serve(monkeypatch, lambda req: httpx.Response(200, json=[]))
    assert "error" in json.loads(tools.handle_reply({"conversation": "sms:+1555"}))
    assert "error" in json.loads(tools.handle_reply({"text": "hi"}))


def test_texts_the_audit_log_keeps_the_recipient_and_a_hash_never_the_text(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    rec = _audit.append("tool_call", tool="texts_reply", args=dict(EX["reply_sms"]), verdict="ask")
    blob = json.dumps(rec)
    assert EX["reply_sms"]["text"] not in blob
    assert EX["reply_sms"]["conversation"] in blob
    assert rec["args"]["text"].startswith("[private ") and "len=" in rec["args"]["text"]
    assert EX["reply_sms"]["text"] not in (Path(_audit.audit_path()).read_text(encoding="utf-8") if Path(_audit.audit_path()).exists() else "")


@pytest.mark.parametrize("tool", ["texts_reply"])
def test_texts_a_first_time_conversation_counts_as_a_recipient(tool):
    from _shared import _approval_hook

    assert _approval_hook.recipients(tool, dict(EX["reply_sms"])) == [EX["reply_sms"]["conversation"]]
