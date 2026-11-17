"""Device status and console handoff on the device channel (T-0617).

The real :class:`WebSocketHandler` on a fake socket, as in ``test_api_companion``: what is
asserted is what the console's devices view reads and the frame the desktop agent parses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the sibling test module

from test_api_companion import DEVICE_ID, Session, jarvis, session, token  # noqa: E402,F401

from aura.api import common  # noqa: E402
from aura.api.devices import EVENT_DEVICE_EVENT, presence_signals  # noqa: E402

DESKTOP_ID = "desk-41a0"
# The session-id rule all three ends check (core, the desktop agent, the app's Rust).
SESSION_IDS = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/contracts/handoff-session.json").read_text()
)


def _row(jarvis_, device_id: str = DEVICE_ID) -> dict:
    return next(r for r in common.companion_list_payload(jarvis_) if r["device_id"] == device_id)


# --- status -------------------------------------------------------------------
def test_device_status_foreground_app_is_a_short_clean_name():
    assert presence_signals({"foreground_app": "  firefox  "})["foreground_app"] == "firefox"
    long = presence_signals({"foreground_app": "x" * 500})["foreground_app"]
    assert len(long) == 64
    # A window class is the app's own choice: no control characters reach a console row.
    assert presence_signals({"foreground_app": "evil\x1b[2J\napp"})["foreground_app"] == "evil[2Japp"
    for junk in (None, 7, ["code"], {"name": "code"}, "   ", "\x00\x07"):
        assert "foreground_app" not in presence_signals({"foreground_app": junk}), junk


async def test_device_status_row_carries_online_battery_app_and_activity(jarvis, session):
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    session.push({"type": "device_event", "event": "presence",
                  "data": {"battery": 64, "charging": False, "foreground_app": "code"}})
    session.push({"type": "device_event", "event": "activity", "data": {"activity": "typing"}})
    await session.settle()

    status = _row(jarvis, DESKTOP_ID)["status"]
    assert status["online"] is True
    assert status["battery"] == 64 and status["charging"] is False
    assert status["foreground_app"] == "code"
    assert status["activity"] == "typing"
    assert status["last_seen"] > 0


async def test_device_status_an_unknown_activity_is_ignored(jarvis, session):
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    session.push({"type": "device_event", "event": "activity", "data": {"activity": "clicking"}})
    session.push({"type": "device_event", "event": "activity", "data": {"activity": "rm -rf"}})
    session.push({"type": "device_event", "event": "activity", "data": {"activity": 3}})
    await session.settle()
    assert _row(jarvis, DESKTOP_ID)["status"]["activity"] == "clicking"


async def test_device_status_offline_says_nothing_about_what_jarvis_is_doing(jarvis, session):
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    session.push({"type": "device_event", "event": "activity", "data": {"activity": "typing"}})
    await session.settle()
    jarvis.data["presence"].devices[DESKTOP_ID].connected = False
    status = common.companion_list_payload(jarvis)
    row = next((r for r in status if r["device_id"] == DESKTOP_ID), None)
    if row is not None:  # the hub may already have dropped the link
        assert row["status"]["activity"] is None


async def test_device_status_only_the_pause_edges_reach_the_bus(jarvis, session):
    """A run is several changes a second; the logbook wants "paused" and "resumed", not those."""
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_DEVICE_EVENT, lambda event: seen.append(event.data))
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    for value in ("moving", "clicking", "paused", "paused", "idle", "typing"):
        session.push({"type": "device_event", "event": "activity", "data": {"activity": value}})
    await session.settle(20)

    activity = [e["data"]["activity"] for e in seen if e.get("event") == "activity"]
    assert activity == ["paused", "idle"]


# --- handoff ------------------------------------------------------------------
async def test_handoff_pushes_the_session_to_the_named_desktop(jarvis, session):
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    result = await session.command({"id": 7, "type": "jarvis/device/handoff",
                                    "target": "workshop", "session_id": "20261108_abc.1"})
    assert result["success"] is True, result
    assert result["result"] == {"device_id": DESKTOP_ID, "name": "Workshop",
                                "session_id": "20261108_abc.1"}
    frames = [f for f in session.ws.sent if f.get("type") == "jarvis_handoff"]
    assert frames == [{"type": "jarvis_handoff", "session_id": "20261108_abc.1"}]


async def test_handoff_to_an_unknown_device_is_not_found(jarvis, session):
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    result = await session.command({"id": 8, "type": "jarvis/device/handoff",
                                    "target": "the tv", "session_id": "abc"})
    assert result["success"] is False
    assert result["error"]["code"] == "not_found"


async def test_handoff_refuses_a_device_that_cannot_open_the_console(jarvis, session):
    await session.register()  # the phone: it has no handler for the frame yet
    result = await session.command({"id": 9, "type": "jarvis/device/handoff",
                                    "target": DEVICE_ID, "session_id": "abc"})
    assert result["success"] is False
    assert result["error"]["code"] == "not_supported"
    assert not [f for f in session.ws.sent if f.get("type") == "jarvis_handoff"]


@pytest.mark.parametrize("good", SESSION_IDS["valid"])
async def test_handoff_takes_every_session_id_the_contract_allows(jarvis, session, good):
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    result = await session.command({"id": 11, "type": "jarvis/device/handoff",
                                    "target": DESKTOP_ID, "session_id": good})
    assert result["success"] is True, result
    assert result["result"]["session_id"] == good


@pytest.mark.parametrize("bad", SESSION_IDS["invalid"])
async def test_handoff_refuses_a_malformed_session_id(jarvis, session, bad):
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    result = await session.command({"id": 10, "type": "jarvis/device/handoff",
                                    "target": DESKTOP_ID, "session_id": bad})
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_format"
    assert not [f for f in session.ws.sent if f.get("type") == "jarvis_handoff"]


async def test_handoff_service_lets_hermes_move_the_view(jarvis, session):
    """Hermes reaches the house through services: companion.handoff with a session id."""
    await session.register(DESKTOP_ID, name="Workshop", platform="desktop")
    reply = await jarvis.services.async_call(
        "companion", "handoff", {"device_id": "Workshop", "session_id": "s-1"},
        blocking=True, return_response=True,
    )
    assert reply["ok"] is True and reply["device_id"] == DESKTOP_ID
    await session.settle()  # the push is queued on the socket's writer
    assert [f for f in session.ws.sent if f.get("type") == "jarvis_handoff"] == [
        {"type": "jarvis_handoff", "session_id": "s-1"}]
    reply = await jarvis.services.async_call(
        "companion", "handoff", {"device_id": "Workshop", "session_id": "../x"},
        blocking=True, return_response=True,
    )
    assert reply["ok"] is False and reply["error"] == "invalid_format"
