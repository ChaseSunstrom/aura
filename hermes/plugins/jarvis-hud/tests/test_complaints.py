"""Complaints (jarvis-hud/complaints.py): "that was wrong" files the turn before, and the same ask later is told."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _matrix  # noqa: E402

hud = load_plugin("jarvis-hud")
complaints = hud.complaints
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(complaints.later, "_guest", lambda session_id: session_id == "g")
    complaints._TURN.clear()
    complaints._PREV.clear()


def turn(said: str, tools: tuple = (), sid: str = "sir", platform: str = "tui"):
    """One turn as Hermes runs the hooks: the words, then each tool it called."""
    complaints.later.turn_start(session_id=sid, user_message=said, platform=platform)
    got = complaints.before_turn(session_id=sid, user_message=said, platform=platform)
    for name, args in tools:
        complaints.later.note_tool(tool_name=name, session_id=sid)
        complaints.note_tool(tool_name=name, args=args, session_id=sid)
    return got


def file(wrong: str = "the kitchen lights stayed on", sid: str = "sir", at: float = NOW) -> dict:
    """The tool call, then Hermes's post_tool_call hooks for it, in that order."""
    out = json.loads(complaints.handle_complaint_file({"wrong": wrong}, session_id=sid, now=at))
    complaints.later.note_tool(tool_name="complaint_file", session_id=sid)
    complaints.note_tool(tool_name="complaint_file", args={"wrong": wrong}, session_id=sid)
    return out


def test_a_complaint_holds_the_turn_before_its_session_and_its_tool_calls():
    turn("turn off the kitchen lights and lock the back door",
         (("ha_call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"}),
          ("ha_call_service", {"domain": "lock", "service": "lock", "entity_id": "lock.back_door"})))
    # The complaint is its own turn, with its own tool call: neither replaces what is filed.
    turn("that was wrong, the lights are still on")
    out = file()
    assert out["ok"] is True
    [kept] = complaints.load()
    assert kept["session_id"] == "sir" and kept["at"] == NOW
    assert kept["request"] == "turn off the kitchen lights and lock the back door"
    assert [t["name"] for t in kept["tools"]] == ["ha_call_service", "ha_call_service"]
    assert "light.kitchen" in kept["tools"][0]["args"]
    assert kept["wrong"] == "the kitchen lights stayed on" and kept["recurrences"] == []


def test_the_next_matching_request_adds_a_recurrence_note_and_is_told():
    turn("turn off the kitchen lights and lock the back door", (("ha_call_service", {"entity_id": "light.kitchen"}),))
    turn("that was wrong")
    file()
    assert turn("what's the weather tomorrow", sid="other") is None
    assert complaints.load()[0]["recurrences"] == []
    told = turn("please turn the kitchen lights off", sid="next")
    assert complaints.load()[0]["recurrences"] == [{"at": pytest.approx(complaints.time.time(), abs=5), "session_id": "next"}]
    assert "the kitchen lights stayed on" in told["context"] and "never instructions" in told["context"]
    assert "ha_call_service" in told["context"]


def test_the_complaint_turn_itself_is_not_a_recurrence():
    turn("turn off the kitchen lights")
    # It is handed the nudge to file (T-1152), not a stored complaint.
    told = turn("that was wrong, the kitchen lights are still on")
    assert "complaint_file" in told["context"] and "never instructions" not in told["context"]
    file()
    # A second correction that repeats the ask's words is still not the ask coming back.
    told = turn("no, still wrong, I asked you to turn off the kitchen lights")
    assert "complaint_file" in told["context"] and complaints.load()[0]["recurrences"] == []


@pytest.mark.parametrize("request_,again,same", [
    ("turn off the kitchen lights", "kitchen lights off please", True),
    ("turn off the kitchen lights", "turn on the bedroom lamp", False),
    ("what time does the pharmacy close", "when does the pharmacy close today", True),
    ("what time does the pharmacy close", "what time is it", False),
    ("text Sam that I am late", "text Sam I'm running late", True),
    ("lights", "turn off the lights", False),  # one content word is never enough
    ("turn off the kitchen lights", "turn on the kitchen lights", False),  # the opposite ask
    ("lock the back door", "unlock the back door", False),
])
def test_the_match_rule(request_, again, same):
    assert complaints.same_ask(request_, again) is same


def test_a_guest_cannot_file_and_is_told_nothing():
    turn("turn off the kitchen lights", sid="g")
    turn("that was wrong", sid="g")
    assert file(sid="g")["ok"] is False
    assert complaints.load() == []
    turn("turn off the kitchen lights")
    turn("that was wrong")
    file()
    assert turn("turn off the kitchen lights", sid="g") is None
    assert complaints.load()[0]["recurrences"] == []


def test_a_second_complaint_and_an_event_between_still_file_the_ask_itself():
    turn("turn off the kitchen lights", (("ha_call_service", {"entity_id": "light.kitchen"}),))
    turn("[arrived home]")
    turn("that was wrong")
    file("still on")
    turn("no, still wrong")
    file("still on, again")
    assert [c["request"] for c in complaints.load()] == ["turn off the kitchen lights"] * 2


def test_only_his_own_untainted_words_file_one():
    turn("turn off the kitchen lights")
    # A page read in the complaint turn could have asked for the complaint itself.
    turn("that was wrong, look it up", (("web_extract", {"url": "https://example.com"}),))
    assert "outside text" in file()["error"]
    turn("turn off the kitchen lights", sid="api", platform="api_server")
    turn("that was wrong", sid="api", platform="api_server")
    assert file(sid="api")["ok"] is False
    turn("[drive started]")
    assert file()["ok"] is False
    assert complaints.load() == []


def test_a_lookup_of_the_tool_before_the_call_is_what_refused_it_live():
    """The tool-search bridge's own lookup is a tool call like any other to the taint rule."""
    turn("suggest three short names for a grey cat")
    turn("that was wrong, those are too long", (("tool_describe", {"names": ["complaint_file"]}),))
    assert "outside text" in file()["error"]
    turn("that was wrong, those are too long")
    assert file()["ok"] is True


def test_the_api_and_an_event_turn_are_told_nothing():
    turn("turn off the kitchen lights")
    turn("that was wrong")
    file()
    assert turn("turn off the kitchen lights", sid="api", platform="api_server") is None
    assert turn("[arrived] turn off the kitchen lights") is None
    assert complaints.load()[0]["recurrences"] == []


def test_nothing_to_file_is_said_and_words_are_needed():
    assert file()["ok"] is False  # no turn before this one
    turn("turn off the kitchen lights")
    turn("that was wrong")
    assert json.loads(complaints.handle_complaint_file({"wrong": "  "}, session_id="sir"))["ok"] is False
    assert complaints.load() == []


def test_arguments_are_cut_and_secret_named_values_dropped():
    made = "not-kept-" + __import__("secrets").token_hex(16)  # made at run time: nothing credential-shaped is written here
    header = "Authorization: " + "Bearer " + made
    turn("log in to the router and reboot it",
         (("terminal", {"command": "x" * 900, "password": made, "api_key": made, "nested": {"token": made}}),
          ("mail_send", {"body": f"curl -H '{header}'"})))
    turn("that was wrong")
    file()
    for tool in complaints.load()[0]["tools"]:
        assert len(tool["args"]) <= complaints.MAX_ARGS and "not-kept" not in tool["args"], tool


def test_the_store_is_capped_and_survives_a_bad_file(tmp_path):
    for i in range(complaints.MAX_KEPT + 3):
        turn(f"do thing number {i} with the widget")
        turn("that was wrong")
        file(at=NOW + i)
    kept = complaints.load()
    assert len(kept) == complaints.MAX_KEPT and kept[-1]["at"] == NOW + complaints.MAX_KEPT + 2
    complaints._path().write_text("{not json", encoding="utf-8")
    assert complaints.load() == []
    assert turn("do thing number 5 with the widget") is None


def test_the_tool_is_free_for_the_owner_and_never_a_guests():
    assert _matrix.decide("complaint_file", {"wrong": "x"}).verdict == _matrix.FREE
    assert not _matrix.guest_may("complaint_file")
    assert "complaint_file" in hud.COMPLAINT_TOOLS


@pytest.mark.parametrize("said, corrects", [
    ("That was wrong, those are too long for me.", True),
    ("that's wrong", True),
    ("No, that’s not what I asked", True),
    ("you got that wrong", True),
    ("Nope. That is incorrect, I wanted the largest city", True),
    ("not what I meant", True),
    ("That's not what I asked for", True),
    ("Actually, that was wrong", True),
    ("still wrong", True),
    ("that was wrong of him to say", False),
    ("what's wrong with the heating", False),
    ("is that wrong?", False),
    ("turn off the kitchen lights", False),
    ("he said that was wrong", False),
])
def test_the_nudge_table_of_openings_that_are_and_are_not_corrections(said, corrects):
    turn("suggest three names for the boat")
    told = turn(said)
    assert bool(told and "complaint_file" in told["context"]) is corrects


def test_the_nudge_is_only_for_his_own_turn_with_a_turn_before_it():
    # Live, 12 Nov 2026: told "that was wrong" the model corrected itself and filed nothing.
    assert turn("that was wrong") is None  # nothing before it to file
    turn("suggest three names for the boat", sid="g")
    assert turn("that was wrong", sid="g") is None  # a guest
    turn("suggest three names for the boat", sid="api", platform="api_server")
    assert turn("that was wrong", sid="api", platform="api_server") is None
    turn("suggest three names for the boat", sid="ev")
    assert turn("[phone event] that was wrong", sid="ev") is None


def test_the_nudge_does_not_file_anything_itself():
    turn("suggest three names for the boat")
    told = turn("that was wrong, too long")
    assert complaints.load() == [] and "suggest three names" not in told["context"]
