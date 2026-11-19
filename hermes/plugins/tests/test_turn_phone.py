"""The phone-only tools with two phones online (T-1576): calls, a text reply and the emergency
alert run on the phone the turn came from, named to the house by its house token as the timer and
``device_action`` already are (T-1498, T-1555), and refuse when the turn names no phone.

Pinned: a phone's turn sends ``device_token`` with no ``device_id``; a desktop's turn, a turn the
adapter cannot place (the console, a scheduled job) and an unsure one (``strict``) send nothing;
one phone online is still the one phone, whatever the turn. Not pinned: the house resolving the
token (aura-core's device_control suite) or a live phone."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _surface  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PROOF = json.loads((ROOT / "tests" / "contracts" / "emergency-proof.json").read_text(encoding="utf-8"))
_REAL = httpx.Client
PIXEL = {"id": "p1", "name": "Pixel", "platform": "android", "connected": True, "actions": []}
TABLET = {"id": "p2", "name": "Tablet", "platform": "android", "connected": True, "actions": []}
DESK = {"id": "d1", "name": "Desk", "platform": "desktop", "connected": True, "actions": []}
# The adapter's record of each conversation's device (T-1498), by the session a tool call carries.
TURNS: dict[str, dict] = {
    "from-tablet": {"kind": "android", "house_token": "tok-tablet"},
    "from-desk": {"kind": "desktop", "house_token": "tok-desk"},
}

calls = load_plugin("aura-calls").tools
texts = load_plugin("aura-texts").tools
emergency = load_plugin("aura-emergency").tools


def _place(session: str) -> dict:
    return json.loads(calls.handle_place({"to": "Sam"}, task_id="t", session_id=session))


def _reply(session: str) -> dict:
    return json.loads(texts.handle_reply({"conversation": "sms:1", "text": "on my way"}, task_id="t",
                                         session_id=session))


def _alert(session: str) -> dict:
    assert json.loads(emergency.handle_contacts_set({"contacts": [{"name": "Sam", "number": "+15550100"}]},
                                                    task_id="t"))["ok"]
    return json.loads(emergency.handle_alert({"what": "They asked for help."}, task_id="t", session_id=session))


TOOLS: dict[str, Callable[[str], dict]] = {"calls_place": _place, "texts_reply": _reply, "emergency_alert": _alert}


@pytest.fixture
def house(tmp_path, monkeypatch) -> Callable[[list[dict]], list[dict]]:
    """A house with the given devices online; returns the list of commands it was sent."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HASS_URL", "http://house.test")
    monkeypatch.setenv("HASS_TOKEN", "t")
    monkeypatch.setenv("AURA_EMERGENCY_SECRET", PROOF["secret"])
    monkeypatch.setattr(_surface, "turn_device",
                        lambda session, read=None, *, strict=False: TURNS.get(session) if strict else None)

    def online(devices: list[dict]) -> list[dict]:
        sent: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/config/companion/list":
                return httpx.Response(200, json=devices)
            if req.url.path == "/api/emergency/contacts":
                return httpx.Response(200, json={"count": 1})
            sent.append(json.loads(req.content))
            result: dict[str, Any] = {"status": "ok", "result": {"status": "ok", "sent": True}}
            return httpx.Response(200, json={"service_response": result})
        monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))
        return sent
    return online


@pytest.mark.parametrize("tool", TOOLS)
def test_two_phones_the_turns_phone_is_named_by_its_house_token(house, tool):
    sent = house([PIXEL, TABLET, DESK])
    out = TOOLS[tool]("from-tablet")
    assert out["ok"] is True, out
    (command,) = sent
    assert command["device_id"] == "" and command["device_token"] == "tok-tablet"


@pytest.mark.parametrize("session", ["from-desk", "", "console-turn"])
@pytest.mark.parametrize("tool", TOOLS)
def test_two_phones_and_no_phone_turn_refuses_and_sends_nothing(house, tool, session):
    sent = house([PIXEL, TABLET, DESK])
    out = TOOLS[tool](session)
    assert out["ok"] is False and "phone" in out["error"]
    assert sent == [], "a desktop's or an unplaced turn must never pick one of two phones"


@pytest.mark.parametrize("tool", TOOLS)
def test_one_phone_online_is_the_phone_whatever_the_turn(house, tool):
    sent = house([PIXEL, DESK])
    assert TOOLS[tool]("from-tablet")["ok"] is True
    (command,) = sent
    assert command["device_id"] == "p1" and "device_token" not in command
