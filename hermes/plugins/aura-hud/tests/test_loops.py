"""Open loops (aura-hud/loops.py): saved on disk, listed by the gate, closed by a tool."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

hud = load_plugin("aura-hud")
loops, promises = hud.loops, hud.promises
SAID = "I can keep an eye out for her reply and add it the moment it lands."


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def history(*names):
    return [{"role": "user", "content": "x"},
            {"role": "assistant", "tool_calls": [{"function": {"name": n, "arguments": "{}"}} for n in names]}]


def test_an_unbacked_promise_and_an_aside_are_stored_and_survive_a_restart(capsys):
    promises.after_turn(session_id="s", assistant_response=SAID, conversation_history=history("send_sms"),
                        user_message="Remind me to call the plumber. Thanks.", platform="tui")
    promises.before_turn(session_id="s")  # the session note is memory; the store is not
    promises._PENDING.clear()
    items = json.loads(loops.handle_loops_list({}))["items"]
    assert sorted(i["kind"] for i in items) == ["aside", "promise"]
    assert loops.main() == 0
    out = capsys.readouterr().out
    assert "call the plumber" in out and "wakeAgent" not in out


def test_the_gate_stays_asleep_with_nothing_open(capsys):
    assert loops.main() == 0
    assert json.loads(capsys.readouterr().out) == {"wakeAgent": False}


def test_loops_close_closes_one_and_a_backing_tool_closes_the_session():
    loops.add("I'll let you know when it has fired.", "promise", "s1")
    loops.add("I'll remind you at six.", "promise", "s2")
    first, second = (i["id"] for i in json.loads(loops.handle_loops_list({}))["items"])
    assert json.loads(loops.handle_loops_close({"id": first}))["ok"]
    assert not json.loads(loops.handle_loops_close({"id": first}))["ok"]
    promises.after_turn(session_id="s2", assistant_response="Done.", conversation_history=history("later_add"))
    assert json.loads(loops.handle_loops_list({}))["items"] == []
    assert second  # closed by the backing call, not by id


def test_a_stale_loop_is_not_listed_and_duplicates_are_kept_once():
    loops.add("I'll circle back on that.", "promise", "s", now=1000.0)
    loops.add("I'll circle back on that.", "promise", "s", now=1001.0)
    assert len(loops.load()) == 1
    assert json.loads(loops.handle_loops_list({}, now=1000.0 + loops.STALE_AFTER + 1))["items"] == []


def test_a_cron_or_event_turn_never_leaves_a_loop():
    # Commit review, 2026-10-09: the evening sweep reads the loops back, and its own reply
    # would be saved as a new loop every night; a job's or an event's words are not Sir's.
    promises.after_turn(session_id="c", assistant_response=SAID, user_message="Remind me to call the plumber.",
                        conversation_history=history("send_sms"), platform="cron")
    promises.after_turn(session_id="e", assistant_response=SAID, conversation_history=history("send_sms"),
                        user_message="[event: message] remind me to wire money", platform="tui")
    assert json.loads(loops.handle_loops_list({}))["items"] == []
