"""The guest gates find a voice turn's speaker under the id the adapter bound it to (T-0792).

The adapter binds who spoke to ``prompt.submit``'s short TUI session id; a tool call on the
gateway carries the durable ``session_key`` (the agent is built with it). Asked by the key
alone, the adapter has no record, the lookup fails open to the key holder, and a guest's
voice turn gets the owner's tools. The fake table here is shaped like
``tui_gateway.server._sessions``: short sid -> {"session_key": …}.

Not covered: the live gateway, which ``scripts/verify`` probes after a deploy.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import _speaker  # noqa: E402
from _shared._approval_hook import matrix_gate  # noqa: E402

UNKNOWN = {"label": None, "level": "owner", "source": "none", "at": None}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _speaker._cache.clear()
    yield
    _speaker._cache.clear()


def _adapter(monkeypatch, records: dict, table: dict | None) -> list[str]:
    asked: list[str] = []

    def fetch(sid: str):
        asked.append(sid)
        return {"session_id": sid, **records.get(sid, UNKNOWN)}

    monkeypatch.setattr(_speaker, "fetch", fetch)
    monkeypatch.setattr(_speaker, "sessions", lambda: table)
    return asked


def _guest(at: float) -> dict:
    return {"label": "Visitor", "level": "guest", "source": "voice", "at": at}


def _owner_typed(at: float) -> dict:
    return {"label": None, "level": "owner", "source": "key", "at": at}


def test_a_tool_call_by_the_durable_key_reads_the_speaker_bound_to_the_short_id(monkeypatch):
    _adapter(monkeypatch, {"tui-1": _guest(100.0)}, {"tui-1": {"session_key": "20261109_1_abc"}})
    who = _speaker.speaker_of("20261109_1_abc")
    assert who["level"] == "guest" and who["label"] == "Visitor"


def test_the_gate_refuses_a_guest_whose_turn_the_tool_call_names_by_key(monkeypatch):
    _adapter(monkeypatch, {"tui-1": _guest(100.0)}, {"tui-1": {"session_key": "20261109_1_abc"}})
    out = matrix_gate()(tool_name="terminal", args={"command": "ls"}, session_id="20261109_1_abc")
    assert out and out["action"] == "block"


@pytest.mark.parametrize("guest_at, owner_at", [
    (200.0, 100.0),
    # The owner types on the other screen while the guest's turn still runs, however long it
    # runs: its tool calls name only the key, so the newer owner binding must not lift them.
    (100.0, 105.0),
    (100.0, 100.0 + 3600.0),
])
def test_two_screens_on_one_key_the_strictest_live_binding_wins(monkeypatch, guest_at, owner_at):
    table = {"phone": {"session_key": "k"}, "console": {"session_key": "k"}}
    _adapter(monkeypatch, {"phone": _guest(guest_at), "console": _owner_typed(owner_at)}, table)
    assert _speaker.speaker_of("k")["level"] == "guest"


def test_a_screen_that_left_the_gateway_no_longer_counts(monkeypatch):
    # The phone's session closed (gone from the gateway's table), so only the console's
    # binding is live; the adapter may still remember the phone's.
    _adapter(monkeypatch, {"phone": _guest(100.0), "console": _owner_typed(105.0)},
             {"console": {"session_key": "k"}})
    assert _speaker.speaker_of("k")["level"] == "owner"


def test_a_short_id_is_still_asked_as_itself(monkeypatch):
    asked = _adapter(monkeypatch, {"tui-1": _guest(100.0)}, {"tui-1": {"session_key": "k"}})
    assert _speaker.speaker_of("tui-1")["level"] == "guest"
    assert asked == ["tui-1"]


def test_an_id_nobody_bound_is_still_the_key_holders(monkeypatch):
    _adapter(monkeypatch, {}, {"tui-1": {"session_key": "k"}})
    who = _speaker.speaker_of("api-session")
    assert who == {"label": None, "level": "owner", "source": "none"}


def test_off_the_gateway_the_id_is_asked_as_given(monkeypatch):
    asked = _adapter(monkeypatch, {"k": _guest(100.0)}, None)
    assert _speaker.speaker_of("k")["level"] == "guest"
    assert asked == ["k"]
