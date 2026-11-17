"""Home Assistant's entities become the house's (T-1523): a mirrored state, a forwarded call.

The bridge holds one websocket to the stack's Home Assistant: ``get_states`` seeds a house entity
per HA entity, ``state_changed`` events keep them current, and a house service on one of them
(``light.turn_on``) goes back to HA as ``call_service``. Run against :mod:`fake_ha`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.core import Jarvis  # noqa: E402
from aura.integrations import domains as domains_integration  # noqa: E402
from aura.integrations.home_assistant.bridge import HaBridge  # noqa: E402
from fake_ha import URL, FakeHA  # noqa: E402


async def _jarvis(tmp_path) -> Jarvis:
    jarvis = Jarvis(tmp_path)
    await jarvis.areas.load()
    await jarvis.devices.load()
    await jarvis.entities.load()
    await domains_integration.async_setup(jarvis, None)
    return jarvis


async def _until(check, what: str) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
async def running(tmp_path):
    jarvis = await _jarvis(tmp_path)
    ha = FakeHA()
    ha.long_lived.add("tok")
    bridge = HaBridge(jarvis, URL, token=lambda: "tok", connect=ha.connect, backoff=(0.01,))
    task = asyncio.create_task(bridge.run())
    await _until(lambda: jarvis.states.get("light.kitchen") is not None, "the mirrored light")
    yield jarvis, ha, bridge
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_ha_bridge_mirrors_a_state_and_follows_its_changes(running):
    jarvis, ha, _ = running

    seeded = jarvis.states.get("light.kitchen")
    assert seeded.state == "off" and seeded.attributes["friendly_name"] == "Kitchen"

    ha.push_state("light.kitchen", "on", {"friendly_name": "Kitchen", "brightness": 128})
    await _until(lambda: jarvis.states.get("light.kitchen").state == "on", "the pushed state")
    assert jarvis.states.get("light.kitchen").attributes["brightness"] == 128


async def test_ha_bridge_forwards_a_house_service_call_to_home_assistant(running):
    jarvis, ha, _ = running

    result = await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.kitchen", "brightness": 200}, return_response=True)

    assert result == {"changed": ["light.kitchen"], "failed": {}}
    assert ha.calls == [{"type": "call_service", "domain": "light", "service": "turn_on",
                         "service_data": {"brightness": 200},
                         "target": {"entity_id": "light.kitchen"}}]


async def test_ha_bridge_a_new_entity_in_home_assistant_appears_in_the_house(running):
    jarvis, ha, _ = running

    ha.push_state("switch.kettle", "off", {"friendly_name": "Kettle"})

    await _until(lambda: jarvis.states.get("switch.kettle") is not None, "the new switch")


async def test_ha_bridge_reconnects_after_home_assistant_restarts(running):
    jarvis, ha, _ = running
    first = ha.sockets[-1]

    first.drop()
    await _until(lambda: len(ha.sockets) >= 2 and ha.sockets[-1].authed, "a second socket")
    ha.push_state("light.kitchen", "on", {"friendly_name": "Kitchen"})

    await _until(lambda: jarvis.states.get("light.kitchen").state == "on", "a state after the restart")
    assert len([e for e in jarvis.entities.entities.values() if e.platform == "home_assistant"]) == 1, \
        "a reconnect re-uses the mirrored entity, it does not add a second one"


async def test_ha_bridge_a_socket_that_keeps_dropping_backs_off(tmp_path):
    jarvis = await _jarvis(tmp_path)
    ha = FakeHA()
    ha.long_lived.add("tok")
    bridge = HaBridge(jarvis, URL, token=lambda: "tok", connect=ha.connect, backoff=(0.01, 5.0))

    async def drop_soon():
        while True:
            for ws in ha.sockets:
                if ws.authed and not getattr(ws, "dropped", False):
                    ws.dropped = True
                    ws.drop()
            await asyncio.sleep(0.005)

    dropper = asyncio.create_task(drop_soon())
    task = asyncio.create_task(bridge.run())
    await asyncio.sleep(0.4)
    task.cancel()
    dropper.cancel()
    await asyncio.gather(task, dropper, return_exceptions=True)

    assert len(ha.sockets) == 2, "after a session that lasted no time, the next wait is the longer one"


async def test_ha_bridge_does_not_mirror_a_secret_home_assistant_puts_in_attributes(running):
    """A camera's state carries a live access token and a picture URL with it in the query. The
    house's states reach every paired screen, the event log and the model: none may hold it."""
    jarvis, ha, _ = running

    ha.push_state("camera.door", "idle", {
        "friendly_name": "Door", "access_token": "cam-secret",
        "entity_picture": "/api/camera_proxy/camera.door?token=cam-secret"})
    await _until(lambda: jarvis.states.get("camera.door") is not None, "the mirrored camera")

    attrs = jarvis.states.get("camera.door").attributes
    assert attrs["friendly_name"] == "Door"
    assert "cam-secret" not in repr(attrs)
