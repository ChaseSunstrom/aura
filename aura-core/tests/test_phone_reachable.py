"""Is a phone on the house's device channel (T-1227): the read a scheduled gate asks.

The leave-by job woke the model every half hour to ask an asleep phone for its calendar and
wait 45 s. The gate it now has needs one yes or no from the house; this is that read, on the
real :class:`WebSocketHandler` over a fake socket as in ``test_api_companion``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the sibling test module

from test_api_companion import DEVICE_ID, Session, jarvis, session, token  # noqa: E402,F401

from aura.api import common  # noqa: E402


def _reachable(jarvis_, now: float | None = None) -> dict:
    return common.companion_reachable_payload(jarvis_, now)


async def test_no_phone_has_ever_connected_is_not_reachable(jarvis):
    assert _reachable(jarvis) == {"reachable": False}


async def test_a_phone_on_the_channel_is_reachable(jarvis, session):
    await session.register()
    assert _reachable(jarvis) == {"reachable": True}


async def test_a_phone_that_dropped_its_socket_a_minute_ago_is_still_reachable(jarvis, session):
    # Doze drops the socket and the phone is back within seconds of a network wake.
    await session.register()
    await session.close()
    assert _reachable(jarvis, time.time() + 60) == {"reachable": True}


async def test_a_phone_gone_for_longer_than_the_window_is_not_reachable(jarvis, session):
    await session.register()
    await session.close()
    past = time.time() + common.PHONE_REACH_WINDOW_S + 5
    assert _reachable(jarvis, past) == {"reachable": False}


async def test_a_desktop_on_the_channel_is_not_a_phone(jarvis, session):
    await session.register("desk-41a0", name="Workshop", platform="desktop")
    assert _reachable(jarvis) == {"reachable": False}


async def test_the_answer_names_no_device(jarvis, session):
    await session.register()
    body = _reachable(jarvis)
    assert list(body) == ["reachable"] and isinstance(body["reachable"], bool)
    assert DEVICE_ID not in str(body)


async def test_the_route_is_read_only_and_behind_the_token():
    from aura.api.rest import api_router

    [route] = [r for r in api_router.routes if r.path == "/api/config/companion/reachable"]
    assert route.methods == {"GET"}
    assert any(d.dependency.__name__ == "require_token" for d in api_router.dependencies)
