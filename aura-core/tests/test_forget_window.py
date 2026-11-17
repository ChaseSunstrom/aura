"""The house erases what it recorded and remembered in the last N minutes (T-1396).

A real sqlite file and a real memory file, a made-up clock (rows stamped now minus a number of
minutes), and the REST door driven in-process. Nothing here reaches a network.
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.api.server import create_app  # noqa: E402
from aura.auth import DATA_AUTH, ENV_TOKEN, AuthManager  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import recorder as recorder_integration  # noqa: E402
from aura.integrations.memory import MemoryEntry, MemoryStore  # noqa: E402

MIN = 60.0


async def _house(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    box = Jarvis(tmp_path)
    box.data[DATA_AUTH] = AuthManager()
    options = {"db_file": "test.db", "commit_interval": 0, "auto_purge": False}
    assert await recorder_integration.async_setup(box, options) is True
    memory = MemoryStore(box)
    await memory.async_load()
    box.data["memory"] = memory
    return box


def _fill(box, now):
    """An hour-old state, event and note; and the same made two minutes ago."""
    recorder = box.data["recorder"]
    recorder._write_sync(
        [("light.hall", "on", "{}", now - 60 * MIN, now - 60 * MIN, "c1"),
         ("light.kitchen", "on", "{}", now - 2 * MIN, now - 2 * MIN, "c2")],
        [("old_event", "{}", now - 60 * MIN, "c3", "LOCAL"),
         ("new_event", "{}", now - 2 * MIN, "c4", "LOCAL")],
    )
    memory = box.data["memory"]
    memory.entries = [MemoryEntry(id="old1", text="the spare key is in the shed", created=now - 60 * MIN),
                      MemoryEntry(id="new1", text="the wifi guest room is upstairs", created=now - 2 * MIN),
                      MemoryEntry(id="pin1", text="a pinned note made just now", created=now - 1 * MIN, pinned=True)]


def _rows(path):
    with sqlite3.connect(path) as conn:
        return ([r[0] for r in conn.execute("SELECT entity_id FROM states ORDER BY 1")],
                [r[0] for r in conn.execute("SELECT event_type FROM events ORDER BY 1")])


async def test_forget_window_erases_recorder_rows_and_notes_in_the_window_only(tmp_path, monkeypatch):
    box = await _house(tmp_path, monkeypatch)
    now = time.time()
    _fill(box, now)
    await box.data["memory"].async_save()

    recorder_out = await box.data["recorder"].forget_window(now - 10 * MIN)
    memory_out = await box.data["memory"].async_forget_since(now - 10 * MIN)

    assert recorder_out == {"states": 1, "events": 1}
    assert memory_out["count"] == 2  # the pinned note too: it was said in the window
    assert _rows(tmp_path / "test.db") == (["light.hall"], ["old_event"])
    kept = json.loads((tmp_path / ".storage" / "memory.json").read_text())
    assert [e["id"] for e in kept["data"]["entries"]] == ["old1"]
    await box.async_stop()


async def test_forget_window_sees_rows_still_queued_for_the_disk(tmp_path, monkeypatch):
    """A state recorded a second ago may not be committed yet; the erase must not miss it."""
    box = await _house(tmp_path, monkeypatch)
    recorder = box.data["recorder"]
    box.states.set("light.porch", "on")
    out = await recorder.forget_window(time.time() - 5 * MIN)
    assert out["states"] == 1
    assert "light.porch" not in _rows(tmp_path / "test.db")[0]
    await box.async_stop()


async def test_forget_window_leaves_the_forgotten_text_out_of_the_vector_sidecar_and_notes_it(tmp_path, monkeypatch):
    box = await _house(tmp_path, monkeypatch)
    now = time.time()
    _fill(box, now)
    memory = box.data["memory"]
    await memory.async_forget_since(now - 10 * MIN)
    # Reflection must not learn the erased fact back from the day's turns.
    assert memory.was_forgotten("the wifi guest room is upstairs")
    assert not memory.was_forgotten("the spare key is in the shed")
    await box.async_stop()


# --- the door ------------------------------------------------------------------
async def _call(box, token, body, tmp_path):
    transport = httpx.ASGITransport(app=create_app(box, static_dir=tmp_path / "no-www"))
    async with httpx.AsyncClient(transport=transport, base_url="http://house") as client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return await client.post("/api/config/forget_window", json=body, headers=headers)


async def test_forget_window_door_erases_for_the_owner_token_and_answers_per_store(tmp_path, monkeypatch):
    box = await _house(tmp_path, monkeypatch)
    now = time.time()
    _fill(box, now)
    _, secret = await box.data[DATA_AUTH].create_token("console")

    answer = await _call(box, secret, {"minutes": 10}, tmp_path)

    assert answer.status_code == 200
    assert answer.json() == {"minutes": 10, "recorder": {"states": 1, "events": 1}, "memory": {"count": 2}}
    assert _rows(tmp_path / "test.db") == (["light.hall"], ["old_event"])
    await box.async_stop()


async def test_forget_window_leaves_none_of_the_erased_words_in_the_recorder(tmp_path, monkeypatch):
    """The memory erase tells the bus it happened, and the recorder stores every event: that
    event once carried each erased note whole, so the words came back a moment after the
    recorder's own erase (T-1408)."""
    box = await _house(tmp_path, monkeypatch)
    now = time.time()
    _fill(box, now)
    _, secret = await box.data[DATA_AUTH].create_token("console")

    assert (await _call(box, secret, {"minutes": 10}, tmp_path)).status_code == 200
    await box.async_block_till_done()
    await box.data["recorder"].async_commit()

    with sqlite3.connect(tmp_path / "test.db") as conn:
        rows = conn.execute("SELECT event_type, data FROM events").fetchall()
    assert not [kind for kind, data in rows if "wifi guest room" in (data or "") or "pinned note" in (data or "")]
    # History still says a forget happened, with a count and no note.
    assert [json.loads(data)["action"] for kind, data in rows if kind == "memory_changed"] == ["forgotten"]
    await box.async_stop()


async def test_forget_window_paired_token_is_denied_and_nothing_is_erased(tmp_path, monkeypatch):
    box = await _house(tmp_path, monkeypatch)
    _fill(box, time.time())
    _, phone = await box.data[DATA_AUTH].create_token("Pixel", paired=True)

    answer = await _call(box, phone, {"minutes": 10}, tmp_path)

    assert answer.status_code == 403
    assert _rows(tmp_path / "test.db") == (["light.hall", "light.kitchen"], ["new_event", "old_event"])
    assert len(box.data["memory"].entries) == 3
    await box.async_stop()


async def test_forget_window_denied_without_a_valid_token(tmp_path, monkeypatch):
    box = await _house(tmp_path, monkeypatch)
    _fill(box, time.time())
    for token in (None, "not-a-token"):
        assert (await _call(box, token, {"minutes": 10}, tmp_path)).status_code == 401
    assert len(box.data["memory"].entries) == 3
    await box.async_stop()


@pytest.mark.parametrize("minutes", [0, 1441, -5, "10", 2.5, True, None])
async def test_forget_window_door_refuses_a_window_outside_one_to_1440_whole_minutes(tmp_path, monkeypatch, minutes):
    box = await _house(tmp_path, monkeypatch)
    _fill(box, time.time())
    _, secret = await box.data[DATA_AUTH].create_token("console")
    assert (await _call(box, secret, {"minutes": minutes}, tmp_path)).status_code == 400
    assert len(box.data["memory"].entries) == 3
    await box.async_stop()


async def test_forget_window_door_says_which_store_is_missing_instead_of_claiming_it(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    box = Jarvis(tmp_path)
    box.data[DATA_AUTH] = AuthManager()
    _, secret = await box.data[DATA_AUTH].create_token("console")
    answer = await _call(box, secret, {"minutes": 10}, tmp_path)
    assert answer.status_code == 200
    assert answer.json() == {"minutes": 10, "recorder": None, "memory": None}
    await box.async_stop()
