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


@pytest.mark.parametrize(("entity", "house", "data", "ha_service", "ha_data"), [
    # The house names these by an alias too (cover.turn_on opens); HA has no such service, and
    # the alias, written last in the domain table, was what the bridge sent (T-1529).
    ("cover.garage", "open_cover", {}, "open_cover", {}),
    ("cover.garage", "close_cover", {}, "close_cover", {}),
    ("cover.garage", "turn_on", {}, "open_cover", {}),
    ("vacuum.robot", "start", {}, "start", {}),
    ("vacuum.robot", "return_to_base", {}, "return_to_base", {}),
    ("vacuum.robot", "turn_off", {}, "return_to_base", {}),
    # HA's play_media takes media_content_type/_id; the house's own keys were sent as they were.
    ("media_player.den", "play_media", {"media_type": "music", "media_id": "song-1"}, "play_media",
     {"media_content_type": "music", "media_content_id": "song-1"}),
])
async def test_ha_bridge_sends_the_service_and_keys_home_assistant_has(
        running, entity, house, data, ha_service, ha_data):
    jarvis, ha, _ = running
    ha.push_state(entity, "closed", {"friendly_name": entity})
    await _until(lambda: jarvis.states.get(entity) is not None, entity)

    await jarvis.async_call_service(entity.split(".")[0], house, {"entity_id": entity, **data})

    assert ha.calls[-1] == {"type": "call_service", "domain": entity.split(".")[0],
                            "service": ha_service, "service_data": ha_data,
                            "target": {"entity_id": entity}}


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


async def test_ha_bridge_registry_an_internal_entity_ha_adds_later_is_never_mirrored(running):
    """The skip list was read once at connect, so a config switch or a hidden entity an
    integration added afterwards was mirrored, and so reachable by call_service (T-1525)."""
    jarvis, ha, bridge = running

    ha.register("switch.hub_firmware", entity_category="config", platform="zwave_js")
    ha.push_state("switch.hub_firmware", "off", {"friendly_name": "Hub firmware"})
    ha.register("switch.hidden_relay", hidden_by="user")
    ha.push_state("switch.hidden_relay", "off", {})
    ha.register("switch.kettle")
    ha.push_state("switch.kettle", "off", {"friendly_name": "Kettle"})
    await _until(lambda: jarvis.states.get("switch.kettle") is not None, "the ordinary switch after them")

    assert jarvis.states.get("switch.hub_firmware") is None
    assert jarvis.states.get("switch.hidden_relay") is None
    assert {"switch.hub_firmware", "switch.hidden_relay"} <= set(ha.registry_lookups), \
        "an entity unseen at connect is looked up before it is mirrored"
    assert set(bridge.entities) == {"light.kitchen", "switch.kettle"}


async def test_ha_bridge_registry_an_entity_hidden_after_connect_leaves_the_mirror(running):
    jarvis, ha, bridge = running
    ha.register("switch.kettle")
    ha.push_state("switch.kettle", "off", {"friendly_name": "Kettle"})
    await _until(lambda: "switch.kettle" in bridge.entities, "the switch mirrored")

    ha.update_registry("switch.kettle", entity_category="diagnostic")
    await _until(lambda: "switch.kettle" not in bridge.entities, "the switch dropped from the mirror")
    assert jarvis.states.get("switch.kettle") is None

    ha.push_state("switch.kettle", "on", {"friendly_name": "Kettle"})
    ha.push_state("light.kitchen", "on", {"friendly_name": "Kitchen"})
    await _until(lambda: jarvis.states.get("light.kitchen").state == "on", "a later state")
    assert jarvis.states.get("switch.kettle") is None, "a later state does not bring it back"


async def test_ha_bridge_registry_an_entity_hidden_while_disconnected_leaves_the_mirror(running):
    """The mirror outlives a session, so an entity HA hid while the socket was down was skipped
    by the next session's registry read but still mirrored, and still reachable (T-1526)."""
    jarvis, ha, bridge = running
    ha.register("switch.kettle")
    ha.push_state("switch.kettle", "off", {"friendly_name": "Kettle"})
    await _until(lambda: "switch.kettle" in bridge.entities, "the switch mirrored")

    ha.subscribers.clear()  # HA restarting: nobody hears the registry change
    ha.update_registry("switch.kettle", hidden_by="user")
    ha.sockets[-1].drop()
    await _until(lambda: len(ha.sockets) >= 2 and bridge.connected, "the next session")

    assert "switch.kettle" not in bridge.entities
    assert jarvis.states.get("switch.kettle") is None


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
