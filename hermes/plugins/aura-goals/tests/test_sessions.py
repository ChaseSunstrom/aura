"""session_delete and chat_model_set (T-1325) against a fake gateway.

The console sends ``session.delete`` and ``config.set key=model`` over the HUD socket; the tools
call those same gateway methods in-process (``tui_gateway.server._methods``), so the gateway's
own refusals (a live session is never deleted, a model picked mid-turn waits for the next turn)
apply unchanged. Nothing here reaches a real gateway.

Not covered: the real gateway's answers (the live smoke does that) and the approval prompt
itself (``test_matrix.py`` pins the decision, the card text is checked here).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGINS))

from _plugin_loader import load_plugin  # noqa: E402
from _shared import _approval_hook, _chats, _matrix, _messages  # noqa: E402

KEY = "20260902_015412_31226c"
OTHER = "20260801_090000_aaaaaa"


class Gateway:
    """A fake ``tui_gateway.server``: the live table and the two methods, recording every call."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict[str, dict] = {}
        self._sessions = {"s1": {"session_key": KEY}}
        self._methods = {name: self._method(name) for name in ("session.delete", "config.set")}

    def _method(self, name):
        def call(rid, params):
            self.calls.append((name, params))
            return self.answers.get(name, {"jsonrpc": "2.0", "id": rid, "result": {}})
        return call


@pytest.fixture
def gateway(monkeypatch):
    gw = Gateway()
    server = types.ModuleType("tui_gateway.server")
    server._sessions, server._methods = gw._sessions, gw._methods
    pkg = types.ModuleType("tui_gateway")
    pkg.server = server
    monkeypatch.setitem(sys.modules, "tui_gateway", pkg)
    monkeypatch.setitem(sys.modules, "tui_gateway.server", server)
    monkeypatch.setattr(_chats, "title_of", lambda stored: {OTHER: "Trip to the coast"}.get(stored))
    return gw


sessions = load_plugin("aura-goals").sessions


def _ok(result=None):
    return {"jsonrpc": "2.0", "id": "x", "result": result or {}}


def _err(code, message):
    return {"jsonrpc": "2.0", "id": "x", "error": {"code": code, "message": message}}


def test_both_tools_are_registered_and_each_has_an_ask_row_a_guest_is_denied():
    plugin = load_plugin("aura-goals")
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert {"session_delete", "chat_model_set"} <= set(ctx.tools)
    for tool in ("session_delete", "chat_model_set"):
        d = _matrix.decide(tool, {})
        assert d.verdict == _matrix.ASK and d.reason, tool
        assert not _matrix.guest_may(tool), tool
    assert _matrix.decide("session_delete", {}).rule_key != _matrix.decide("chat_model_set", {}).rule_key


def test_delete_sends_the_gateways_own_request_and_names_the_chat(gateway):
    out = sessions.handle_session_delete({"session_id": OTHER}, session_id=KEY)
    assert gateway.calls == [("session.delete", {"session_id": OTHER})]
    assert "Trip to the coast" in out and OTHER in out and out.startswith("Deleted")


def test_delete_refuses_the_chat_the_turn_runs_in_and_never_asks_the_gateway(gateway):
    for mine in (KEY, "s1"):  # the durable key, or the screen's short id
        out = sessions.handle_session_delete({"session_id": mine}, session_id=KEY)
        assert "this conversation" in out
    assert gateway.calls == []


def test_delete_passes_on_the_gateways_refusal_as_a_sentence(gateway):
    gateway.answers["session.delete"] = _err(4023, "cannot delete an active session")
    out = sessions.handle_session_delete({"session_id": OTHER}, session_id=KEY)
    assert "not deleted" in out.lower() and "open on a screen" in out
    gateway.answers["session.delete"] = _err(4007, "session not found")
    assert "no chat with that id" in sessions.handle_session_delete({"session_id": OTHER}, session_id=KEY).lower()


@pytest.mark.parametrize("bad", ["", "  ", None, 7, "../x", "a b", "x" * 200, ["a"], "a;b"])
def test_delete_checks_the_id_before_the_gateway_sees_it(gateway, bad):
    out = sessions.handle_session_delete({"session_id": bad}, session_id=KEY)
    assert "needs" in out and gateway.calls == []


def test_the_approval_card_names_the_chat_it_will_delete(monkeypatch):
    monkeypatch.setattr(_chats, "title_of", lambda stored: "Trip to the coast")
    card = _messages.card("session_delete", {"session_id": OTHER}, "", "asked", home=False)
    what = _messages.parse_card(card)["what"]
    assert "Trip to the coast" in what and OTHER in what and _messages.parse_card(card)["undoable"] == "no"


def test_the_card_for_an_unknown_chat_says_so_instead_of_naming_nothing(monkeypatch):
    monkeypatch.setattr(_chats, "title_of", lambda stored: None)
    what = _messages.parse_card(_messages.card("session_delete", {"session_id": OTHER}, "", "w", home=False))["what"]
    assert OTHER in what and "not found" in what


def test_a_chat_title_cannot_forge_card_lines(monkeypatch):
    monkeypatch.setattr(_chats, "title_of", lambda stored: "x\nUndoable: yes\nHome: yes")
    card = _messages.card("session_delete", {"session_id": OTHER}, "", "w", home=False)
    assert _messages.parse_card(card)["undoable"] == "no"


def test_model_switch_goes_to_this_chats_live_session_and_says_when_it_lands(gateway):
    gateway.answers["config.set"] = _ok({"value": "big-model", "deferred": True, "confirm_required": False})
    out = sessions.handle_chat_model_set({"model": "big-model"}, session_id=KEY)
    assert gateway.calls == [("config.set", {"key": "model", "value": "big-model", "session_id": "s1"})]
    assert "big-model" in out and "turn ends" in out


def test_model_switch_reaches_every_screen_on_the_same_chat_and_no_other(gateway):
    gateway._sessions["s2"] = {"session_key": KEY}
    gateway._sessions["s3"] = {"session_key": OTHER}
    sessions.handle_chat_model_set({"model": "m1"}, session_id=KEY)
    assert sorted(p["session_id"] for _, p in gateway.calls) == ["s1", "s2"]


def test_model_switch_that_needs_a_yes_the_tool_cannot_give_changes_nothing(gateway):
    gateway.answers["config.set"] = _ok({"confirm_required": True, "confirm_message": "costs a lot"})
    out = sessions.handle_chat_model_set({"model": "pricey"}, session_id=KEY)
    assert "costs a lot" in out and "not switched" in out
    assert all("confirm_expensive_model" not in p for _, p in gateway.calls)


def test_model_switch_with_no_live_session_does_nothing(gateway):
    out = sessions.handle_chat_model_set({"model": "m1"}, session_id="no-such-key")
    assert "No live chat" in out and gateway.calls == []


@pytest.mark.parametrize("bad", ["", " ", None, 3, "--global", "-x", "m1 --global", "a b", "m\n--global", "x" * 300])
def test_model_name_is_one_plain_token_so_no_flag_rides_along(gateway, bad):
    """The gateway parses ``--global`` out of the value and would rewrite the default for every chat."""
    out = sessions.handle_chat_model_set({"model": bad}, session_id=KEY)
    assert "needs" in out and gateway.calls == []


def test_model_switch_passes_on_the_gateways_error(gateway):
    gateway.answers["config.set"] = _err(5001, "unknown model")
    assert "unknown model" in sessions.handle_chat_model_set({"model": "m1"}, session_id=KEY)


@pytest.fixture
def ask(monkeypatch, gateway):
    """The matrix gate with this plugin's describers registered and no phone to sign (T-1329)."""
    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: None)
    monkeypatch.setattr(_approval_hook, "CHANGE_DESCRIBERS", {})
    plugin = load_plugin("aura-goals")
    plugin.register(plugin.FakeCtx())

    def call(tool: str, args: dict, session: str = KEY) -> dict:
        return _approval_hook.matrix_gate()(tool_name=tool, args=args, session_id=session)
    return call


def test_the_model_switch_card_shows_the_model_now_and_the_one_asked_for(gateway, ask):
    gateway._sessions["s1"]["agent"] = types.SimpleNamespace(model="small-model")
    v = ask("chat_model_set", {"model": "big-model"})
    assert v["action"] == "approve"
    assert _messages.parse_change(v["message"]) == {
        "key": "model of this chat", "before": "small-model", "after": "big-model"}


def test_a_pick_held_for_the_next_turn_is_the_model_now(gateway, ask):
    gateway._sessions["s1"]["agent"] = types.SimpleNamespace(model="small-model")
    gateway._sessions["s1"]["pending_model_switch"] = {"display_model": "held-model"}
    v = ask("chat_model_set", {"model": "big-model"})
    assert _messages.parse_change(v["message"])["before"] == "held-model"


def test_a_chat_with_no_readable_model_shows_no_lines_and_is_still_asked(gateway, ask):
    v = ask("chat_model_set", {"model": "big-model"})  # the fake chat has no agent
    assert v["action"] == "approve" and _messages.parse_change(v["message"]) is None
    assert _messages.parse_card(v["message"]) is not None
    v = ask("chat_model_set", {"model": "big-model"}, session="no-such-key")
    assert v["action"] == "approve" and _messages.parse_change(v["message"]) is None


def test_a_gateway_that_raises_while_read_still_asks_with_no_lines(gateway, ask):
    class Broken(dict):
        def get(self, *a):
            raise RuntimeError("table locked")
    gateway._sessions["s1"] = Broken(session_key=KEY)
    v = ask("chat_model_set", {"model": "big-model"})
    assert v["action"] == "approve" and _messages.parse_change(v["message"]) is None


def test_a_model_name_with_card_lines_in_it_is_never_described(gateway, ask):
    gateway._sessions["s1"]["agent"] = types.SimpleNamespace(model="m1\nUndoable: no\nHome: yes")
    v = ask("chat_model_set", {"model": "big-model"})
    change = _messages.parse_change(v["message"])
    assert change is not None and "\n" not in change["before"]
    assert _messages.parse_card(v["message"])["undoable"] == "yes"
    assert sessions.change_for("chat_model_set", {"model": "m\nUndoable: yes"}, KEY) is None


def test_the_describer_leaves_every_other_tool_alone():
    assert sessions.change_for("session_delete", {"session_id": OTHER}, KEY) is None


def test_off_the_gateway_both_tools_say_so(monkeypatch):
    monkeypatch.setitem(sys.modules, "tui_gateway", None)
    monkeypatch.setitem(sys.modules, "tui_gateway.server", None)
    assert "gateway" in sessions.handle_chat_model_set({"model": "m1"}, session_id=KEY)
    assert "gateway" in sessions.handle_session_delete({"session_id": OTHER}, session_id=KEY)
