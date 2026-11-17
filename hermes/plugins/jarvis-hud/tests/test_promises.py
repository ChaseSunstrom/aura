"""A promise of later is saved, or raised on the next turn (jarvis-hud/promises.py).

The 2026-10-08 drive: "I can keep an eye out for her reply and add it the moment it lands",
with nothing saved; the owner's next words were "Yeah, can you do that?".
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

promises = load_plugin("jarvis-hud").promises

LORI = ("Done, Sir. The calendar entry: I'd create it as soon as she sends the address. "
        "I can keep an eye out for her reply and add it the moment it lands.")


def turn(*calls):
    """A history whose last turn made these (name, arguments) calls."""
    return [{"role": "user", "content": "text grandma"},
            {"role": "assistant", "tool_calls": [{"type": "function", "function": {"name": n, "arguments": a}}
                                                 for n, a in calls]},
            {"role": "tool", "content": "{}"}]


def test_the_promises_jarvis_actually_made_are_seen_and_plain_answers_are_not():
    assert promises.promise_in(LORI).startswith("The calendar entry: I'd create it as soon as she sends")
    assert promises.promise_in("Scheduled for 00:41, one-shot. I'll wait for it to fire and then verify the file.")
    assert promises.promise_in("I'll let you know when it has fired, in case you ask.")
    assert promises.promise_in("Shall I remind you at six?")
    for plain in ("The kitchen lights are on, Sir.", "Let me know if you want it louder.",
                  "Texted Lori: on my way.", "n8n has been down since the 19th.",
                  "Both are your gated calls, so I'll wait for your OK first."):
        assert promises.promise_in(plain) is None, plain


def test_an_unbacked_promise_is_handed_back_once_on_the_next_turn():
    promises.after_turn(session_id="s1", assistant_response=LORI, conversation_history=turn(("send_sms", "{}")))
    note = promises.before_turn(session_id="s1")
    assert note and "routine_add" in note["context"] and "keep an eye out" in note["context"]
    assert promises.before_turn(session_id="s1") is None  # said once
    assert promises.before_turn(session_id="other") is None


def test_a_promise_the_turn_saved_is_left_alone():
    for calls in ((("routine_add", '{"when": {"event": "message"}}'),),
                  (("watch_add", "{}"),),
                  (("phone_action", '{"action": "set_reminder", "params": {}}'),)):
        promises.after_turn(session_id="s2", assistant_response=LORI, conversation_history=turn(*calls))
        assert promises.before_turn(session_id="s2") is None, calls
    # A call in an EARLIER turn does not back this one.
    history = turn(("routine_add", "{}")) + [{"role": "user", "content": "and?"}, {"role": "assistant", "content": LORI}]
    promises.after_turn(session_id="s3", assistant_response=LORI, conversation_history=history)
    assert promises.before_turn(session_id="s3") is not None
