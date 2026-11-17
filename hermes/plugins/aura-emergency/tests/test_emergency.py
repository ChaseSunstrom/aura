"""aura-emergency against a mocked house. Pinned: three tools register; the list is free to read and
its edit is a sensitive ask (the fingerprint); an alert is free, takes no recipient from the model, goes
to the stored contacts only, refuses with no contacts, and fires once per latch; an emergency-services
number is never stored. Not pinned: the phone's countdown and send (Kotlin's EmergencyAlertTest), or a
real text (nothing here can send one)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _matrix, _messages  # noqa: E402

plugin = load_plugin("aura-emergency")
tools = plugin.tools
CONTRACT = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "emergency.json")
                      .read_text(encoding="utf-8"))
_REAL = httpx.Client
PROOF = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "emergency-proof.json")
                   .read_text(encoding="utf-8"))  # the vector the house's suite reads too (T-1213)
mirrored: list = []  # each list the plugin pushed to the house (jarvis-core integrations/emergency)
pushes: list = []  # each whole push body: {contacts, issued, proof}
PHONE = [{"id": "p1", "name": "Pixel", "platform": "android", "connected": True, "actions": []}]
SAM = {"name": "Sam", "number": "+1 555 0100"}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def serve(monkeypatch, seen, answer=None, phones=PHONE):
    mirrored.clear()
    pushes.clear()
    monkeypatch.setenv("HASS_URL", "http://house.test")
    monkeypatch.setenv("HASS_TOKEN", "t")
    monkeypatch.setenv("AURA_EMERGENCY_SECRET", PROOF["secret"])

    def handler(req):
        if req.url.path == "/api/config/companion/list":
            return httpx.Response(200, json=phones)
        if req.url.path == "/api/emergency/contacts":  # the house's copy of the list: not an alert
            pushes.append(json.loads(req.content))
            mirrored.append(pushes[-1]["contacts"])
            return httpx.Response(200, json={"count": len(mirrored[-1])})
        seen.append(json.loads(req.content))
        # The house's real shape (device_control `_report`): its own status round the phone's answer.
        if answer is None or "result" not in answer and answer.get("status") in ("ok", "cancelled"):
            said = {"status": "ok", "device": "Pixel", "action": CONTRACT["action"], "tier": 0,
                    "result": answer or CONTRACT["examples"]["answer_ok"]}
        else:
            said = answer
        return httpx.Response(200, json={"service_response": said})
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))


def call(handler, args=None):
    return json.loads(handler(args or {}, task_id="t"))


def test_emergency_registers_three_tools():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert sorted(ctx.tools) == ["emergency_alert", "emergency_contacts_list", "emergency_contacts_set"]


def test_emergency_matrix_the_alert_and_the_read_are_free_and_the_list_needs_the_fingerprint():
    assert _matrix.decide("emergency_alert", {"what": "x"}).verdict == "free"
    assert _matrix.decide("emergency_contacts_list", {}).verdict == "free"
    assert _matrix.decide("emergency_contacts_set", {"contacts": [SAM]}).verdict == "ask"
    assert _matrix.sensitive("emergency_contacts_set", {"contacts": [SAM]})
    card = _messages.card("emergency_contacts_set", {"contacts": [SAM, {"name": "Ada", "number": "+15550101"}]},
                          "", "you asked", home=False)
    # The number as it will be stored, not as it was typed.
    assert "Sam +15550100; Ada +15550101" in card and "Undoable: no" in card
    long = _messages.card("emergency_contacts_set", {"contacts": [{"name": "Sam", "number": "+1555010" + "9" * 30}]}, "", "", home=False)
    assert "+1555010" + "9" * 12 + "…" in long
    assert "Remove all your emergency contacts" in _messages.card("emergency_contacts_set", {"contacts": []}, "", "", home=False)
    assert _messages.parse_card(_messages.card(
        "emergency_contacts_set", {"contacts": [{"name": "x\nUndoable: yes", "number": "1"}]}, "", "w", home=False))["undoable"] == "no"


def test_emergency_alert_asks_once_the_turn_has_read_someone_elses_words():
    from _shared import _taint
    from _shared._approval_hook import matrix_gate
    _taint.turn_start(session_id="s9", user_message="read my texts")
    assert matrix_gate()(tool_name="emergency_alert", args={"what": "Help."}, session_id="s9") is None
    _taint.note_tool(tool_name="texts_read", session_id="s9")
    card = matrix_gate()(tool_name="emergency_alert", args={"what": "Help."}, session_id="s9")
    assert card["action"] == "approve" and "texts_read" in card["message"]
    # Never a key a remembered answer could match, and never a plan's to cover.
    again = matrix_gate()(tool_name="emergency_alert", args={"what": "Help."}, session_id="s9")
    assert ":once:" in card["rule_key"] and card["rule_key"] != again["rule_key"]
    from _shared import _plan
    _plan_was = _plan.consume
    try:
        _plan.consume = lambda *a, **k: "a plan that names this step"
        assert matrix_gate()(tool_name="emergency_alert", args={"what": "Help."}, session_id="s9")["action"] == "approve"
    finally:
        _plan.consume = _plan_was


@pytest.mark.parametrize("what", CONTRACT["what_refused"])
def test_emergency_what_happened_carries_no_link_or_number(monkeypatch, what):
    seen: list = []
    serve(monkeypatch, seen)
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    assert call(tools.handle_alert, {"what": what})["ok"] is False and not seen


def test_emergency_no_contacts_refuses_and_says_to_name_them(monkeypatch):
    seen: list = []
    serve(monkeypatch, seen)
    out = call(tools.handle_alert, {"what": "They asked for help."})
    assert out["ok"] is False and "emergency contacts" in out["error"] and not seen


def test_emergency_alert_goes_to_the_stored_contacts_only(monkeypatch):
    seen: list = []
    serve(monkeypatch, seen)
    assert call(tools.handle_contacts_set, {"contacts": [SAM, {"name": "Ada", "number": "+15550101"}]})["ok"]
    # A recipient the model adds is not one: the tool reads no number from its arguments.
    out = call(tools.handle_alert, {"what": "They asked for help.", "to": "+15559999",
                                    "contacts": [{"name": "Mallory", "number": "+15559999"}]})
    assert out["ok"] and out["status"] == "ok"
    (sent,) = seen
    assert sent["action"] == CONTRACT["action"]
    assert sent["params"] == {"contacts": [{"name": "Sam", "number": "+15550100"}, {"name": "Ada", "number": "+15550101"}],
                              "what": "They asked for help.", "countdown_s": CONTRACT["limits"]["countdown_s"]}
    assert "emergency number" in out["note"]


def test_emergency_a_second_alert_inside_the_latch_sends_nothing(monkeypatch):
    seen: list = []
    serve(monkeypatch, seen)
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    assert call(tools.handle_alert, {"what": "Smoke."})["ok"]
    again = call(tools.handle_alert, {"what": "Smoke."})
    assert again["ok"] is False and "already" in again["error"] and len(seen) == 1
    # ...and the latch runs out.
    monkeypatch.setattr(tools.time, "time", lambda: 10**10)
    assert call(tools.handle_alert, {"what": "Smoke."})["ok"] and len(seen) == 2


def test_emergency_a_cancelled_countdown_is_said_and_does_not_latch(monkeypatch):
    seen: list = []
    serve(monkeypatch, seen, answer={"status": "cancelled"})
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    out = call(tools.handle_alert, {"what": "Help."})
    assert out == {"ok": False, "status": "cancelled", "error": "cancelled on the phone; nothing was sent"}
    assert call(tools.handle_alert, {"what": "Help."})["status"] == "cancelled" and len(seen) == 2


def test_emergency_no_phone_online_says_so_and_does_not_latch(monkeypatch):
    seen: list = []
    serve(monkeypatch, seen, phones=[])
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    out = call(tools.handle_alert, {"what": "Help."})
    assert out["ok"] is False and "phone" in out["error"] and "emergency number" in out["error"] and not seen


@pytest.mark.parametrize("bad", [
    {"contacts": [{"name": "Police", "number": "911"}]},
    {"contacts": [{"name": "EU", "number": "1-1-2"}]},
    {"contacts": [{"name": "", "number": "+15550100"}]},
    {"contacts": [{"name": "Sam", "number": "call me"}]},
    {"contacts": [{"name": "Sam", "number": "12"}]},
    {"contacts": [SAM] * (CONTRACT["limits"]["contacts_max"] + 1)},
    {"contacts": "Sam"},
    {},
])
def test_emergency_a_bad_list_is_refused_whole(bad):
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    assert call(tools.handle_contacts_set, bad)["ok"] is False
    assert call(tools.handle_contacts_list)["contacts"] == [{"name": "Sam", "number": "+15550100"}]


def test_emergency_an_empty_list_clears_it():
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    assert call(tools.handle_contacts_set, {"contacts": []})["ok"]
    assert call(tools.handle_contacts_list)["contacts"] == []


@pytest.mark.parametrize("what", ["", "   ", 5, "x" * (CONTRACT["limits"]["what_max"] + 1)])
def test_emergency_what_happened_is_required_and_short(monkeypatch, what):
    seen: list = []
    serve(monkeypatch, seen)
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    assert call(tools.handle_alert, {"what": what})["ok"] is False and not seen


def test_emergency_limits_are_the_contracts():
    limits = CONTRACT["limits"]
    assert (tools.CONTACTS_MAX, tools.NAME_MAX, tools.WHAT_MAX, tools.COUNTDOWN_S, tools.LATCH_S) == (
        limits["contacts_max"], limits["name_max"], limits["what_max"], limits["countdown_s"], limits["latch_s"])
    assert tools.NEVER == frozenset(CONTRACT["never_numbers"]) and tools.ACTION == CONTRACT["action"]


@pytest.mark.parametrize("what", CONTRACT["what_allowed"])
def test_emergency_plain_words_with_a_room_or_a_time_are_sent(monkeypatch, what):
    seen: list = []
    serve(monkeypatch, seen)
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    assert call(tools.handle_alert, {"what": what})["ok"] and seen[0]["params"]["what"] == what


def test_emergency_the_phone_s_own_refusal_is_said_and_does_not_latch(monkeypatch):
    seen: list = []
    serve(monkeypatch, seen, answer={"status": "error", "error": "the countdown could not be shown"})
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    out = call(tools.handle_alert, {"what": "Help."})
    assert out["ok"] is False and "countdown could not be shown" in out["error"]
    assert "already" not in call(tools.handle_alert, {"what": "Help."})["error"]


def test_emergency_a_saved_list_is_pushed_to_the_house_for_its_alarm_trigger(monkeypatch):
    """T-1013: the house's smoke/CO trigger sends the alert from its own copy of the list."""
    seen: list = []
    serve(monkeypatch, seen)
    out = call(tools.handle_contacts_set, {"contacts": [SAM, {"name": "Ada", "number": "+1 (555) 0101"}]})
    assert out["ok"] and out["house_has_it"] is True and not seen
    assert mirrored == [[{"name": "Sam", "number": "+15550100"}, {"name": "Ada", "number": "+15550101"}]]
    # A refused list is not pushed, and clearing the list clears the house's copy.
    assert call(tools.handle_contacts_set, {"contacts": [{"name": "Police", "number": "911"}]})["ok"] is False
    assert len(mirrored) == 1
    assert call(tools.handle_contacts_set, {"contacts": []})["ok"] and mirrored[-1] == []


def test_emergency_the_push_carries_the_proof_the_house_checks(monkeypatch):
    """T-1213: the house keeps a list only with this proof; the vector is the one its suite checks."""
    serve(monkeypatch, [])
    monkeypatch.setattr(tools.time, "time", lambda: 1700000000.4)
    assert call(tools.handle_contacts_set, {"contacts": [SAM]})["house_has_it"] is True
    case = PROOF["cases"][0]
    assert case["contacts"] == [{"name": "Sam", "number": "+15550100"}]  # SAM as it is stored
    assert pushes == [{"contacts": case["contacts"], "issued": case["issued"], "proof": case["mac"]}]
    # Clearing the list is proved the same way, and each push is a later issue time.
    monkeypatch.setattr(tools.time, "time", lambda: 1700000600.0)
    assert call(tools.handle_contacts_set, {"contacts": []})["house_has_it"] is True
    clear = PROOF["cases"][2]
    assert pushes[-1] == {"contacts": [], "issued": clear["issued"], "proof": clear["mac"]}


def test_emergency_a_name_outside_ascii_is_proved_as_the_vector_says(monkeypatch):
    serve(monkeypatch, [])
    monkeypatch.setattr(tools.time, "time", lambda: 1700000300.0)
    case = PROOF["cases"][1]
    assert call(tools.handle_contacts_set, {"contacts": case["contacts"]})["house_has_it"] is True
    assert pushes[-1]["proof"] == case["mac"]


def test_emergency_with_no_secret_the_house_is_not_pushed_and_the_save_still_works(monkeypatch):
    serve(monkeypatch, [])
    monkeypatch.delenv("AURA_EMERGENCY_SECRET")
    out = call(tools.handle_contacts_set, {"contacts": [SAM]})
    assert out["ok"] is True and out["house_has_it"] is False and pushes == []
    assert call(tools.handle_contacts_list)["contacts"] == [{"name": "Sam", "number": "+15550100"}]


def test_emergency_a_house_that_cannot_be_told_does_not_fail_the_save(monkeypatch):
    monkeypatch.setenv("HASS_URL", "http://house.test")
    monkeypatch.setenv("HASS_TOKEN", "t")
    monkeypatch.setenv("AURA_EMERGENCY_SECRET", PROOF["secret"])

    def down(req):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL(transport=httpx.MockTransport(down), **kw))
    out = call(tools.handle_contacts_set, {"contacts": [SAM]})
    assert out["ok"] is True and out["house_has_it"] is False
    assert call(tools.handle_contacts_list)["contacts"] == [{"name": "Sam", "number": "+15550100"}]


def test_emergency_list_gives_the_house_the_saved_list_without_a_re_save(monkeypatch):
    """T-1212: a list saved before the house kept a copy reaches it when the list is read."""
    serve(monkeypatch, [])
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    mirrored.clear()
    out = call(tools.handle_contacts_list)
    assert out["house_has_it"] is True and mirrored == [[{"name": "Sam", "number": "+15550100"}]]
    # An empty list is not pushed from a read: only a save clears the house's copy.
    call(tools.handle_contacts_set, {"contacts": []})
    mirrored.clear()
    assert call(tools.handle_contacts_list)["house_has_it"] is False and mirrored == []


def test_emergency_list_with_the_house_down_still_answers_the_list(monkeypatch):
    monkeypatch.setenv("HASS_URL", "http://house.test")
    monkeypatch.setenv("HASS_TOKEN", "t")
    monkeypatch.setenv("AURA_EMERGENCY_SECRET", PROOF["secret"])

    def down(req):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL(transport=httpx.MockTransport(down), **kw))
    call(tools.handle_contacts_set, {"contacts": [SAM]})
    out = call(tools.handle_contacts_list)
    assert out["ok"] is True and out["house_has_it"] is False and out["contacts"] == [{"name": "Sam", "number": "+15550100"}]


def test_emergency_alert_cannot_be_sent_through_a_general_device_route(monkeypatch):
    """Review of T-1015 (HIGH): phone_action and house_device_action pass the model's own
    params, so `emergency_alert` there would text and call whoever the model named."""
    from _shared import _house
    seen: list = []
    serve(monkeypatch, seen)
    with _house.client() as c:
        out = _house.run_action(c, "p1", " Emergency_Alert ", {"contacts": [{"name": "Mallory", "number": "+15559999"}], "what": "x"})
    assert out["ok"] is False and out["response"]["status"] == "denied" and not seen
