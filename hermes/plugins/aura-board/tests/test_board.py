"""The board's numbers from a fixture state.db shaped like Hermes's own (T-0733).

The columns are the ones production's state.db has (checked 2026-11-09 as Hermes's uid);
the fixture carries message content and a title so a test can prove neither leaves.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import stat
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("jarvis_board_board", _HERE / "board.py")
board = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = board
_spec.loader.exec_module(board)

sys.path.insert(0, str(_HERE.parent))  # `_shared`, as Hermes's plugin loader has it
from _shared import _probes  # noqa: E402

NOW = 1_800_000_000.0
H = 3600.0


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never the real Hermes home: the probe list lives beside the board file."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    return tmp_path / "hermes"


def _db(path: Path, *, finish_reason: bool = True) -> Path:
    c = sqlite3.connect(path)
    c.executescript(
        """
        create table sessions (id text primary key, source text, model text, title text, started_at real,
            end_reason text, estimated_cost_usd real, cost_status text, last_activity_at real);
        create index idx_sessions_effective_activity on sessions(coalesce(last_activity_at, started_at) desc, started_at desc);
        create table session_model_usage (session_id text, model text, api_call_count int, input_tokens int,
            output_tokens int, estimated_cost_usd real, cost_status text, first_seen real, last_seen real);
        """
        + (
            "create table messages (id integer primary key, session_id text, role text, content text,"
            " tool_name text, finish_reason text, timestamp real);"
            if finish_reason
            else "create table messages (id integer primary key, session_id text, role text, content text,"
            " tool_name text, timestamp real);"
        )
    )
    sessions = [
        # id, source, model, started_at
        ("s1", "tui", "qwen", NOW - 2 * H),
        ("s2", "cron", "qwen", NOW - 30 * 60),
        ("s3", "api_server", "other", NOW - 10 * 60),
        ("old", "tui", "qwen", NOW - 30 * H),  # outside the 24 h window
    ]
    for sid, source, model, at in sessions:
        c.execute("insert into sessions values (?,?,?,?,?,?,?,?,?)",
                  (sid, source, model, "SECRET-TITLE", at, None, None, "unknown", None))
        c.execute("insert into session_model_usage values (?,?,?,?,?,?,?,?,?)",
                  (sid, model, 3, 1000, 200, None, "unknown", at, at + 60))
    msgs = [
        # s1: answered in 2 s, then a turn cut off after 4 s
        ("s1", "user", "SECRET-CONTENT", None, None, NOW - 2 * H + 1),
        ("s1", "assistant", "x", None, "tool_calls", NOW - 2 * H + 2),
        ("s1", "tool", "x", "terminal", None, NOW - 2 * H + 2.5),
        ("s1", "assistant", "x", None, "stop", NOW - 2 * H + 3),
        ("s1", "user", "x", None, None, NOW - 2 * H + 10),
        ("s1", "assistant", "x", None, "length", NOW - 2 * H + 14),
        # s2: a cron turn answered in 6 s with two tool calls in the last hour
        ("s2", "user", "x", None, None, NOW - 30 * 60),
        ("s2", "tool", "x", "web_search", None, NOW - 30 * 60 + 2),
        ("s2", "tool", "x", "web_search", None, NOW - 30 * 60 + 3),
        ("s2", "assistant", "x", None, "stop", NOW - 30 * 60 + 6),
        # s3: a turn with no reply, then one still running (asked 30 s ago)
        ("s3", "user", "x", None, None, NOW - 10 * 60),
        ("s3", "user", "x", None, None, NOW - 30),
        ("old", "user", "x", None, None, NOW - 30 * H),
        ("old", "assistant", "x", None, "stop", NOW - 30 * H + 1),
    ]
    for sid, role, content, tool, fin, at in msgs:
        if finish_reason:
            c.execute("insert into messages (session_id, role, content, tool_name, finish_reason, timestamp)"
                      " values (?,?,?,?,?,?)", (sid, role, content, tool, fin, at))
        else:
            c.execute("insert into messages (session_id, role, content, tool_name, timestamp) values (?,?,?,?,?)",
                      (sid, role, content, tool, at))
    c.commit()
    c.close()
    return path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return _db(tmp_path / "state.db")


def test_turns_by_model_per_hour_and_tokens(db: Path) -> None:
    b = board.collect(db, NOW)
    models = {m["model"]: m for m in b["models"]}
    assert models["qwen"] == {"model": "qwen", "turns": 3, "input_tokens": 2000, "output_tokens": 400, "calls": 6}
    assert models["other"]["turns"] == 2
    assert len(b["hours"]) == 24
    assert b["hours"][-1]["turns"] == {"qwen": 1, "other": 2}  # the hour ending now
    assert b["hours"][-2]["turns"] == {"qwen": 2}  # s1, just under two hours ago
    assert sum(sum(h["turns"].values()) for h in b["hours"]) == 5


def test_which_model_each_kind_of_task_went_to(db: Path) -> None:
    assert board.collect(db, NOW)["flows"] == [
        {"kind": "api_server", "model": "other", "turns": 2},
        {"kind": "tui", "model": "qwen", "turns": 2},
        {"kind": "cron", "model": "qwen", "turns": 1},
    ]


def test_tasks_by_kind_and_outcome(db: Path) -> None:
    kinds = {k["kind"]: k for k in board.collect(db, NOW)["kinds"]}
    assert kinds["tui"] == {"kind": "tui", "turns": 2, "answered": 1, "cut_off": 1, "no_reply": 0, "running": 0}
    assert kinds["cron"] == {"kind": "cron", "turns": 1, "answered": 1, "cut_off": 0, "no_reply": 0, "running": 0}
    assert kinds["api_server"] == {"kind": "api_server", "turns": 2, "answered": 0, "cut_off": 0,
                                   "no_reply": 1, "running": 1}


def test_latency_is_a_question_to_the_reply_that_ends_its_turn(db: Path) -> None:
    lat = board.collect(db, NOW)["latency"]
    assert lat == {"turns": 3, "p50_ms": 4000, "p95_ms": 6000}  # 2 s, 4 s, 6 s


def test_tool_calls_per_minute_over_the_last_hour(db: Path) -> None:
    tools = board.collect(db, NOW)["tools"]
    assert len(tools["per_minute"]) == 60
    assert sum(tools["per_minute"]) == 2  # s1's call was two hours ago
    assert tools["per_minute"][30] == 2  # 29.9 minutes ago, oldest first
    assert tools["top"] == [{"name": "web_search", "calls": 2}, {"name": "terminal", "calls": 1}]


def test_no_cost_is_invented_where_hermes_recorded_none(db: Path) -> None:
    assert board.collect(db, NOW)["cost"] == {"usd": None, "why": "Hermes recorded no cost for these models"}
    c = sqlite3.connect(db)
    c.execute("update session_model_usage set estimated_cost_usd = 0.25, cost_status = 'estimated' where session_id = 's3'")
    c.commit()
    c.close()
    assert board.collect(db, NOW)["cost"] == {"usd": 0.25, "why": None}


def test_counts_only_never_content_or_titles(db: Path) -> None:
    out = json.dumps(board.collect(db, NOW))
    assert "SECRET" not in out


def test_a_missing_column_takes_out_its_own_section_only(tmp_path: Path) -> None:
    b = board.collect(_db(tmp_path / "state.db", finish_reason=False), NOW)
    assert "finish_reason" in b["errors"]["turns"]
    assert b["kinds"] is None and b["latency"] is None
    assert {m["model"] for m in b["models"]} == {"qwen", "other"}


def test_no_db_says_so(tmp_path: Path) -> None:
    b = board.collect(tmp_path / "missing.db", NOW)
    assert b["errors"] == {"db": "no state.db"}


def test_bounded_by_the_caps(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board, "SESSION_CAP", 1)  # the newest session only: s3
    b = board.collect(db, NOW)
    assert [m["model"] for m in b["models"]] == ["other"]


def test_a_session_resumed_today_counts_its_turns_today_only(db: Path) -> None:
    """A console session lives for days: its turns today count, its turns before the window do not."""
    c = sqlite3.connect(db)
    c.execute("insert into sessions values (?,?,?,?,?,?,?,?,?)",
              ("resumed", "tui", "qwen", "t", NOW - 30 * H, None, None, "unknown", NOW - H))
    for role, fin, at in (("user", None, NOW - 29 * H), ("assistant", "stop", NOW - 29 * H + 1),
                          ("user", None, NOW - H), ("assistant", "stop", NOW - H + 2)):
        c.execute("insert into messages (session_id, role, content, tool_name, finish_reason, timestamp)"
                  " values (?,?,?,?,?,?)", ("resumed", role, "x", None, fin, at))
    c.commit()
    c.close()
    tui = next(k for k in board.collect(db, NOW)["kinds"] if k["kind"] == "tui")
    assert (tui["turns"], tui["answered"]) == (3, 2)  # s1's two and today's resumed one


def test_written_atomically_0640_in_a_0750_dir(db: Path, tmp_path: Path) -> None:
    out = tmp_path / "home" / "board" / "board.json"
    board.write(out, board.collect(db, NOW))
    assert json.loads(out.read_text())["schema"] == board.SCHEMA
    assert stat.S_IMODE(out.stat().st_mode) == 0o640
    assert stat.S_IMODE(out.parent.stat().st_mode) == 0o750
    assert [p.name for p in out.parent.iterdir()] == ["board.json"]


def test_refreshed_at_most_every_ten_seconds(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "state.db").write_bytes(db.read_bytes())
    monkeypatch.setattr(board, "_last", 0.0)
    assert board.refresh(NOW) is True
    assert board.refresh(NOW + 5) is False
    assert board.refresh(NOW + 10) is True
    assert (tmp_path / "board" / "board.json").exists()


def test_a_hook_inside_the_gap_is_written_when_the_gap_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reply that ends a burst lands inside the gap: dropped, the board would show it running."""
    calls: list[float | None] = []
    monkeypatch.setattr(board, "refresh", lambda now=None: calls.append(now) or True)
    monkeypatch.setattr(board, "MIN_GAP_S", 0.2)
    monkeypatch.setattr(board, "_last", time.time())
    monkeypatch.setattr(board, "_pending", None)
    board.after()
    board.after()  # one trailing write for the both of them
    assert calls == []
    time.sleep(0.5)
    assert len(calls) == 1


def test_a_recorded_probe_session_is_its_own_kind_in_tasks_and_flows(db: Path) -> None:
    """A deploy's check turn is model load (TURNS keeps it) but not the person's console (T-1142)."""
    _probes.record("s1")
    b = board.collect(db, NOW)
    kinds = {k["kind"]: k for k in b["kinds"]}
    assert kinds["probe"] == {"kind": "probe", "turns": 2, "answered": 1, "cut_off": 1, "no_reply": 0, "running": 0}
    assert "tui" not in kinds  # s1 was the only tui session in the window
    assert {"kind": "probe", "model": "qwen", "turns": 2} in b["flows"]
    assert all(f["kind"] != "tui" for f in b["flows"])
    # the model really ran: the per-hour figure is the same as without the mark
    assert sum(sum(h["turns"].values()) for h in b["hours"]) == 5
    assert b["hours"][-2]["turns"] == {"qwen": 2}


def test_without_a_record_every_session_keeps_its_own_kind(db: Path) -> None:
    kinds = {k["kind"] for k in board.collect(db, NOW)["kinds"]}
    assert kinds == {"tui", "cron", "api_server"}


@pytest.mark.parametrize("content", ["", "not json {", "[1, 2]", '{"ids": "s1"}', '{"ids": [1, null, {"a": 1}]}'])
def test_a_broken_probe_file_relabels_nothing_and_never_raises(db: Path, home: Path, content: str) -> None:
    (home / "board").mkdir(parents=True)
    _probes.path().write_text(content, encoding="utf-8")
    kinds = {k["kind"] for k in board.collect(db, NOW)["kinds"]}
    assert kinds == {"tui", "cron", "api_server"}


def test_the_probe_list_is_bounded_newest_kept_and_holds_only_ids(home: Path) -> None:
    for i in range(_probes.CAP + 25):
        _probes.record(f"sid{i}")
    kept = _probes.ids()
    assert len(kept) == _probes.CAP
    assert f"sid{_probes.CAP + 24}" in kept and "sid0" not in kept
    assert set(json.loads(_probes.path().read_text())) == {"ids"}


def test_recording_a_session_twice_writes_nothing_the_second_time(home: Path) -> None:
    _probes.record("s1")
    before = _probes.path().stat().st_mtime_ns
    _probes.record("s1")
    assert _probes.path().stat().st_mtime_ns == before
    assert _probes.ids() == {"s1"}


def test_recording_survives_a_broken_file_and_an_empty_id(home: Path) -> None:
    (home / "board").mkdir(parents=True)
    _probes.path().write_text("not json", encoding="utf-8")
    _probes.record("")
    assert _probes.ids() == set()
    _probes.record("s1")
    assert _probes.ids() == {"s1"}
