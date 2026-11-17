"""Home Assistant's entities become the house's (T-1523): one socket, a mirror, calls sent back.

:class:`HaBridge` holds one websocket to the stack's Home Assistant. ``get_states`` seeds a house
entity per HA device entity (:class:`HaEntity`, unique id ``ha:<entity_id>``, HA's object id
kept), ``state_changed`` events keep each one current, and a house service on one of them goes
back to HA as ``call_service``. So everything HA can reach (its integrations, Zigbee, Matter,
the cloud devices) is a light, a switch or a sensor the house, the console and the model already
know how to use. A dropped socket marks the mirror unavailable and reconnects with backoff.

Not covered: HA's own bookkeeping entities (sun, zones, updates, backups, diagnostics and config
entities are skipped; the house has its own); a service HA has but the house's domain table does
not name (``home_assistant.call_service`` reaches those on a mirrored domain); an entity HA removed stays here,
unavailable, until the house's registry drops it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from ...entity import Entity, EntityPlatform
from ..domains import build_specs
from .setup import TIMEOUT, Connect, ws_auth, ws_connect, ws_url

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

PLATFORM = "home_assistant"

#: Domains mirrored: the ones the house's domain table drives, plus the read-only ones.
READ_ONLY_DOMAINS = frozenset({
    "sensor", "binary_sensor", "weather", "camera", "humidifier", "water_heater", "valve",
    "alarm_control_panel", "lawn_mower", "device_tracker", "event",
})
#: HA integrations whose entities describe HA itself, not the house.
SKIPPED_PLATFORMS = frozenset({"backup", "sun", "hassio", "homeassistant", "google_translate",
                               "shopping_list", "hacs", "assist_pipeline", "wake_word"})
#: Attributes HA puts on a state that are credentials, never mirrored.
SECRET_ATTRIBUTES = frozenset({"access_token", "entity_picture", "token"})
#: Seconds between reconnects, the last one repeating.
BACKOFF = (2.0, 5.0, 15.0, 30.0, 60.0)
#: A session that lasted this long was a working one: the next drop starts the backoff over.
#: Shorter, and an HA that closes the socket straight after login would be dialled every 2 s.
STEADY_S = 60.0


def _service_table() -> dict[tuple[str, str], tuple[str, tuple[Any, ...]]]:
    """(domain, entity method) -> (HA service, the params to send back under their HA keys)."""
    specs, toggles = build_specs()
    table = {(s.domain, s.method): (s.service, s.params) for s in specs}
    for toggle in toggles:
        table.setdefault((toggle.domain, "async_toggle"), ("toggle", ()))
    return table


_SERVICES = _service_table()
MIRRORED_DOMAINS = frozenset(d for d, _ in _SERVICES) | READ_ONLY_DOMAINS


class HaEntity(Entity):
    """One HA entity, as the house sees it. Its state is HA's; its commands go back to HA."""

    def __init__(self, bridge: "HaBridge", state: dict[str, Any]) -> None:
        self.bridge = bridge
        self.ha_entity_id = str(state["entity_id"])
        self.ha_domain, self.suggested_object_id = self.ha_entity_id.split(".", 1)
        self._attr_unique_id = f"ha:{self.ha_entity_id}"
        self._ha: dict[str, Any] = state
        self._attr_name = str((state.get("attributes") or {}).get("friendly_name") or self.suggested_object_id)

    def update_from(self, state: dict[str, Any]) -> None:
        self._ha = state
        self._attr_available = True

    @property
    def state(self) -> Any:
        return self._ha.get("state")

    def state_attributes(self) -> dict[str, Any]:
        # A camera's state carries a live access token, and its picture URL the same token in the
        # query. House states reach every paired screen, the event log and the model; HA's own
        # proxy is the only place that may hold either.
        attrs = {k: v for k, v in (self._ha.get("attributes") or {}).items()
                 if k not in SECRET_ATTRIBUTES and not (isinstance(v, str) and "token=" in v)}
        attrs.setdefault("friendly_name", self.name)
        return attrs

    def __getattr__(self, name: str) -> Any:
        # Only reached for a name the class lacks: the domain table's entity methods
        # (async_turn_on, async_set_temperature …) become HA service calls; anything else
        # stays an AttributeError, so a capability probe still reads "not supported".
        found = _SERVICES.get((self.__dict__.get("ha_domain", ""), name))
        if found is None:
            raise AttributeError(name)
        service, params = found

        async def call(**kwargs: Any) -> None:
            data = {p.key: list(v) if isinstance(v, tuple) else v
                    for p in params if (v := kwargs.get(p.name)) is not None}
            await self.bridge.call_service(self.ha_domain, service, data,
                                           {"entity_id": self.ha_entity_id})

        return call


class HaBridge:
    """The socket, the mirror, and the calls that go back over it."""

    def __init__(self, jarvis: "Jarvis", url: str, *, token: Callable[[], str],
                 connect: Connect | None = None, backoff: tuple[float, ...] = BACKOFF) -> None:
        self.jarvis = jarvis
        self.url = url.strip().rstrip("/")
        self.token = token
        self.connect = connect or ws_connect
        self.backoff = backoff or BACKOFF
        self.entities: dict[str, HaEntity] = {}
        self.platforms: dict[str, EntityPlatform] = {}
        self.connected = False
        self.last_error = ""
        self._ws: Any = None
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._skip: set[str] = set()

    # --- the socket ----------------------------------------------------------------------------
    async def _request(self, msg: dict[str, Any]) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("Home Assistant is not connected")
        self._next += 1
        mid = self._next
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = future
        try:
            await self._ws.send(json.dumps({"id": mid, **msg}))
            return await asyncio.wait_for(future, TIMEOUT)
        finally:
            self._pending.pop(mid, None)

    async def _read(self, ws: Any, events: asyncio.Queue) -> None:
        try:
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "result":
                    future = self._pending.get(msg.get("id"))
                    if future is not None and not future.done():
                        future.set_result(msg)
                elif msg.get("type") == "event":
                    data = (msg.get("event") or {}).get("data") or {}
                    events.put_nowait(data)
        finally:
            events.put_nowait(None)
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Home Assistant closed the socket"))

    async def _session(self) -> None:
        token = self.token()
        if not token:
            raise RuntimeError("no Home Assistant token yet")
        ws = await self.connect(ws_url(self.url))
        events: asyncio.Queue = asyncio.Queue()
        reader: asyncio.Task | None = None
        try:
            await ws_auth(ws, token)
            self._ws, self._pending = ws, {}
            reader = asyncio.create_task(self._read(ws, events))
            registry = await self._request({"type": "config/entity_registry/list"})
            self._skip = {row["entity_id"] for row in (registry.get("result") or [])
                          if isinstance(row, dict) and row.get("entity_id") and (
                              row.get("entity_category") or row.get("hidden_by") or row.get("disabled_by")
                              or row.get("platform") in SKIPPED_PLATFORMS)}
            await self._request({"type": "subscribe_events", "event_type": "state_changed"})
            states = await self._request({"type": "get_states"})
            self.connected, self.last_error = True, ""
            # Events queue while the snapshot is applied, so an older snapshot row never lands
            # over a newer event.
            for state in states.get("result") or []:
                await self._mirror(state)
            _LOGGER.info("home_assistant: connected, %d entities mirrored", len(self.entities))
            while (data := await events.get()) is not None:
                if isinstance(data.get("new_state"), dict):
                    await self._mirror(data["new_state"])
                elif data.get("entity_id") in self.entities:  # removed in HA
                    self._unavailable([self.entities[data["entity_id"]]])
        finally:
            self.connected, self._ws = False, None
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            self._unavailable(list(self.entities.values()))
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - closing a dead socket may fail; it is closed either way
                pass

    async def run(self) -> None:
        """Hold the socket for good: reconnect with backoff; a session that held a minute resets it."""
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                await self._session()
                if time.monotonic() - started >= STEADY_S:
                    attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure here means "try again later"
                text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                if text != self.last_error:
                    _LOGGER.warning("home_assistant: not connected (%s)", text)
                self.last_error = text
            await asyncio.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
            attempt += 1

    # --- the mirror ----------------------------------------------------------------------------
    def _unavailable(self, entities: list[HaEntity]) -> None:
        for entity in entities:
            entity._attr_available = False
            entity.async_write_state()

    async def _mirror(self, state: dict[str, Any]) -> None:
        entity_id = str(state.get("entity_id") or "")
        domain = entity_id.split(".", 1)[0]
        if domain not in MIRRORED_DOMAINS or entity_id in self._skip or "." not in entity_id:
            return
        entity = self.entities.get(entity_id)
        if entity is not None:
            entity.update_from(state)
            entity.async_write_state()
            return
        entity = HaEntity(self, state)
        self.entities[entity_id] = entity
        platform = self.platforms.get(domain)
        if platform is None:
            platform = self.platforms[domain] = EntityPlatform(self.jarvis, domain, PLATFORM)
        await platform.async_add_entities([entity])

    # --- calls back to HA ----------------------------------------------------------------------
    async def call_service(self, domain: str, service: str, data: dict[str, Any] | None = None,
                           target: dict[str, Any] | None = None) -> Any:
        msg: dict[str, Any] = {"type": "call_service", "domain": domain, "service": service,
                               "service_data": data or {}}
        if target:
            msg["target"] = target
        answer = await self._request(msg)
        if not answer.get("success"):
            error = answer.get("error") or {}
            raise RuntimeError(f"Home Assistant refused {domain}.{service}: "
                               f"{error.get('message') or error.get('code') or 'no reason given'}")
        return answer.get("result")

    def status(self) -> dict[str, Any]:
        by_domain: dict[str, int] = {}
        for entity_id in self.entities:
            domain = entity_id.split(".", 1)[0]
            by_domain[domain] = by_domain.get(domain, 0) + 1
        return {"connected": self.connected, "entities": len(self.entities), "by_domain": by_domain,
                "error": self.last_error}
