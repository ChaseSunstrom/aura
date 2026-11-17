"""scripts/verify/aura_recent.py finds a loop, a tool error and a silent turn in a made-up store (T-0910).

Does not cover: the deployed store's real contents; the script is run on it by hand.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify" / "aura_recent.py"
spec = importlib.util.spec_from_file_location("aura_recent", SCRIPT)
assert spec and spec.loader
aura_recent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aura_recent)


def _call(name: str, **args: object) -> str:
    return json.dumps([{"id": "c", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}])


def _store(path: Path, now: float) -> None:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, "
        "tool_calls TEXT, tool_name TEXT, timestamp REAL)"
    )
    db.executemany("INSERT INTO sessions VALUES (?, ?)", [("s1", "tui"), ("s2", "cron"), ("old", "tui")])
    rows: list[tuple] = []

    def add(session: str, role: str, content: str | None = None, calls: str | None = None, tool: str | None = None, ago: float = 60) -> None:
        rows.append((session, role, content, calls, tool, now - ago))

    # A loop: the same call four times, each answered with an error.
    add("s1", "user", "turn the made-up lamp on, please, and then tell me a very long story about why it would not turn on at all")
    for _ in range(4):
        add("s1", "assistant", None, _call("lamp_set", on=True))
        add("s1", "tool", json.dumps({"error": "no such lamp"}), tool="lamp_set")
    add("s1", "assistant", "I could not.")
    # A healthy turn: three different calls, then a reply.
    add("s1", "user", "what is on")
    for n in range(3):
        add("s1", "assistant", None, _call("lamp_get", n=n))
        add("s1", "tool", json.dumps({"on": True}), tool="lamp_get")
    add("s1", "assistant", "The lamp.")
    # A turn that ended on a tool result, then a newer one that is still running (not silent).
    add("s2", "user", "first")
    add("s2", "assistant", None, _call("clock"))
    add("s2", "tool", "12:00", tool="clock")
    add("s2", "user", "second")
    add("s2", "assistant", None, _call("clock"))
    add("s2", "tool", "12:01", tool="clock")
    # Outside the window: never reported.
    add("old", "user", "long ago", ago=90_000)
    for _ in range(3):
        add("old", "assistant", "same", ago=90_000)
    db.executemany("INSERT INTO messages (session_id, role, content, tool_calls, tool_name, timestamp) VALUES (?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def test_aura_recent_finds_the_loop_the_errors_and_the_silent_turn_and_nothing_else(tmp_path: Path) -> None:
    now = time.time()
    _store(tmp_path / "state.db", now)
    db = sqlite3.connect(f"file:{tmp_path / 'state.db'}?mode=ro", uri=True)
    found = aura_recent.findings(db, now - 86_400)
    got = sorted((f["kind"], f["session"], f["what"], f["count"]) for f in found)
    assert got == [("errors", "s1", "lamp_set", 4), ("loop", "s1", "lamp_set", 4), ("silent", "s2", "clock", 1)]


def test_aura_recent_prints_no_words_unless_asked_and_then_at_most_80_characters(tmp_path: Path, capsys) -> None:
    _store(tmp_path / "state.db", time.time())
    assert aura_recent.main([str(tmp_path / "state.db")]) == 1
    quiet = capsys.readouterr().out
    assert "lamp_set" in quiet and "made-up lamp" not in quiet
    assert quiet.strip().endswith("1 loop(s), 1 turn(s) with tool errors, 1 silent turn(s)")
    aura_recent.main([str(tmp_path / "state.db"), "--text"])
    loud = capsys.readouterr().out
    line = next(ln for ln in loud.splitlines() if " loop " in ln)
    assert len(line.split("| ", 1)[1]) == 80


def test_a_denied_call_is_a_decision_not_a_tool_error() -> None:
    # The deploy smoke asks for a gated call and answers deny on purpose: 31 of the first run's
    # 126 "error" turns were that (T-1228).
    denied = json.dumps({"error": "BLOCKED: Action denied by user. The user has NOT consented to this."})
    assert not aura_recent._is_error(denied)
    assert aura_recent._is_error(json.dumps({"error": "BLOCKED: something else"}))


def test_aura_recent_exits_zero_on_a_quiet_store(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL)")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL)")
    db.commit()
    db.close()
    assert aura_recent.main([str(tmp_path / "state.db")]) == 0
