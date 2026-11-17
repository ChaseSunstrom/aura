"""The Hermes memory layers, read from a fixture home.

The point of these is the failure modes, not the happy path: this walks files
and two SQLite databases that a live agent is writing to, and the console asking
for its graph must never be the thing that fails. Every reader here is allowed
to return nothing; none is allowed to raise.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aura_adapter.memory_layers import collect


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "memories").mkdir()
    (tmp_path / "SOUL.md").write_text("# SOUL\nYou are JARVIS.\n")
    (tmp_path / "memories" / "USER.md").write_text("# USER\nName: Casey.\n")
    # Hermes separates entries with a lone section mark.
    (tmp_path / "memories" / "MEMORY.md").write_text("first fact\n§\nsecond fact\n§\n  \n")

    db = sqlite3.connect(tmp_path / "memory_store.db")
    db.execute(
        "create table facts (fact_id integer, content text, category text, tags text, created_at text)"
    )
    db.execute(
        "insert into facts values (1, 'the greenhouse wants 24C', 'user_pref', 'greenhouse,climate', '2026-09-06 15:20:00')"
    )
    db.commit()
    db.close()

    state = sqlite3.connect(tmp_path / "state.db")
    state.execute("create table sessions (id text, title text, started_at real, message_count int, hidden int)")
    state.execute("insert into sessions values ('s1', 'A morning brief', 1788900000.0, 4, 0)")
    state.execute("insert into sessions values ('s2', 'Misleading one', 1788900001.0, 2, 1)")
    state.commit()
    state.close()

    skills = tmp_path / "skills"
    (skills / "jarvis-voice").mkdir(parents=True)
    (skills / "jarvis-voice" / "SKILL.md").write_text("---\nname: jarvis-voice\n---\n\n# JARVIS's voice\n")
    # A category directory: DESCRIPTION.md and no SKILL.md. Not a skill.
    (skills / "creative").mkdir()
    (skills / "creative" / "DESCRIPTION.md").write_text("things that are creative\n")
    (skills / ".usage.json").write_text(json.dumps({"jarvis-voice": {"use_count": 12}}))
    return tmp_path


def test_reads_every_layer(home: Path) -> None:
    out = collect(home)
    assert out["counts"] == {
        "hermes-layer": 4,  # SOUL, USER, and MEMORY's two non-empty entries
        "hermes-facts": 1,
        "hermes-skills": 1,
        "hermes-sessions": 1,
    }


def test_memory_md_splits_but_soul_and_user_do_not(home: Path) -> None:
    """SOUL and USER are documents; MEMORY is a list of things chosen separately.

    Splitting SOUL into paragraphs would put a dozen meaningless nodes in the
    graph where one belongs.
    """
    ids = {e["id"] for e in collect(home)["entries"]}
    # memory:2 is the blank chunk after the last separator, dropped by design.
    assert {"soul:SOUL.md", "user:USER.md", "memory:0", "memory:1"} <= ids
    assert not any(i.startswith("soul:") and i != "soul:SOUL.md" for i in ids)


def test_blank_entries_between_separators_are_dropped(home: Path) -> None:
    texts = [e["text"] for e in collect(home)["entries"] if e["id"].startswith("memory:")]
    assert texts == ["first fact", "second fact"]


def test_a_fact_keeps_its_category_and_tags(home: Path) -> None:
    fact = next(e for e in collect(home)["entries"] if e["id"] == "fact:1")
    assert fact["tags"] == ["fact", "user_pref", "greenhouse", "climate"]
    assert fact["created"] > 0, "an ISO created_at must become an epoch, not zero"


def test_a_category_directory_is_not_a_skill(home: Path) -> None:
    """13 of the directories under skills/ hold only a DESCRIPTION.md."""
    skills = [e for e in collect(home)["entries"] if e["id"].startswith("skill:")]
    assert [s["id"] for s in skills] == ["skill:jarvis-voice"]
    assert skills[0]["uses"] == 12


def test_hidden_sessions_stay_hidden(home: Path) -> None:
    """Phase 0a hid two sessions whose content is now misleading."""
    ids = {e["id"] for e in collect(home)["entries"]}
    assert "session:s1" in ids
    assert "session:s2" not in ids


def test_a_session_carries_its_conversation_id(home: Path) -> None:
    """It is what lets the graph say two entries came out of the same turn."""
    s = next(e for e in collect(home)["entries"] if e["id"] == "session:s1")
    assert s["conversation_id"] == "s1"


def test_an_empty_home_is_an_empty_graph_not_an_error(tmp_path: Path) -> None:
    out = collect(tmp_path)
    assert out["entries"] == []
    assert out["counts"] == {}


def test_a_corrupt_database_costs_its_rows_and_nothing_else(home: Path) -> None:
    """A live agent is writing to these; a bad read must not fail the request."""
    (home / "memory_store.db").write_bytes(b"this is not a database")
    out = collect(home)
    assert out["counts"].get("hermes-facts") is None
    assert out["counts"]["hermes-layer"] == 4, "the other layers still came through"


def test_a_corrupt_usage_file_costs_the_counts_and_nothing_else(home: Path) -> None:
    (home / "skills" / ".usage.json").write_text("{ not json")
    skills = [e for e in collect(home)["entries"] if e["id"].startswith("skill:")]
    assert len(skills) == 1
    assert "uses" not in skills[0]


def test_a_layer_it_may_not_list_costs_that_layer_and_says_so(home: Path) -> None:
    """The adapter is another uid on a cd-through mount: it can enter skills/ and not
    list it. One PermissionError took the whole endpoint to a 500 in production."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root can list anything; the mode below would not bite")
    # The adapter's view of a 0701 directory it does not own: enter, no listing.
    # The test owns the fixture, so the owner's read bit is what comes off.
    skills = home / "skills"
    skills.chmod(0o300)
    try:
        out = collect(home)
    finally:
        skills.chmod(0o755)
    assert [u["layer"] for u in out["unreadable"]] == ["skills"]
    assert out["counts"].get("hermes-skill") is None
    assert out["counts"]["hermes-layer"] == 4, "the other layers still came through"
    assert out["counts"]["hermes-facts"] >= 1


def test_a_layer_file_it_may_not_open_is_named_not_blanked(home: Path) -> None:
    """MEMORY.md is 0600 to Hermes and the adapter is another uid: in production it
    came back as an empty layer, which reads as "nothing remembered"."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    memory = home / "memories" / "MEMORY.md"
    memory.chmod(0o000)
    try:
        out = collect(home)
    finally:
        memory.chmod(0o644)
    assert [u["layer"] for u in out["unreadable"]] == ["memory"]
    assert out["counts"]["hermes-layer"] == 2, "SOUL and USER still came through"


@pytest.mark.parametrize(("name", "layer"), [("state.db", "sessions"), ("memory_store.db", "facts")])
def test_a_store_it_may_not_open_is_named_not_logged(
    home: Path, caplog: pytest.LogCaptureFixture, name: str, layer: str
) -> None:
    """Hermes keeps state.db 0600 (hermes_state._secure_state_db_files) and the adapter is
    another uid: sqlite's "unable to open database file" was a warning on every read of
    the graph while the layer came back empty, unlike every other layer it cannot open."""
    import logging
    import os

    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    db = home / name
    if not db.exists():
        sqlite3.connect(db).close()
    db.chmod(0o000)
    try:
        with caplog.at_level(logging.INFO, logger="aura_adapter.memory_layers"):
            out = collect(home)
    finally:
        db.chmod(0o644)
    assert layer in [u["layer"] for u in out["unreadable"]]
    assert not caplog.records
