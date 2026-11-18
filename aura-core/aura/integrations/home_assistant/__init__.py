"""The stack's Home Assistant, set up and wired in by AURA itself (T-1523).

``home_assistant:`` in configuration.yaml names the HA (``url``, default the stack's own on the
loopback) and, optionally, a ``token`` the owner made. On start, and every ten minutes after,
:class:`~.setup.HaSetup` makes sure AURA holds a token HA accepts (onboarding a fresh HA itself),
and :class:`~.bridge.HaBridge` mirrors HA's devices into the house and sends their commands back.

Services: ``home_assistant.status`` (what was found, what is mirrored; no secret),
``home_assistant.setup`` (check and redo it now; owner only) and
``home_assistant.call_service`` (an HA service by name, for one the house's domain table does
not have; owner only; only a (domain, service) pair on :data:`ALLOWED_CALLS`, with its own data
keys, on entities the bridge mirrors).

Not covered: an HA the owner runs somewhere else that AURA has no token for (the status says to
set ``home_assistant.token``); HA add-ons (a container HA has no Supervisor).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ...automation.reach import INDIRECT_DOMAINS
from ...const import GATED_DOMAINS
from .bridge import _SERVICES, MIRRORED_DOMAINS, HaBridge
from .setup import HaSetup

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "home_assistant"
DEPENDENCIES = ["domains"]
#: How often the credentials are checked again: a reset HA volume is noticed within this.
INTERVAL_S = 600.0
#: How soon after an HA that did not answer: on a fresh stack HA takes a minute to start, and a
#: ten-minute wait left it unwired that long on every first start.
RETRY_S = 30.0
#: States after which the next check can wait the full interval.
SETTLED = frozenset({"adopted", "provisioned", "renewed", "owned_elsewhere"})
#: HA domains the house holds for a human (``lock``, ``notify``) or treats as reaching anything
#: (a script, a scene, an automation), and the mirrored ones as wide (a disarmed alarm). Taken out
#: of :data:`ALLOWED_DOMAINS`, which is the list that decides.
REFUSED_DOMAINS = GATED_DOMAINS | INDIRECT_DOMAINS | frozenset({"alarm_control_panel"})
#: The only HA domains ``call_service`` reaches, as the call's domain and as every target's: the
#: device domains the house mirrors, less :data:`REFUSED_DOMAINS`. An allow-list, because HA's
#: integrations add domains by the hundred (``shell_command``, ``rest_command``,
#: ``homeassistant.restart``, a cloud brand's own verbs) and a deny-list misses the next one;
#: ``call_service`` runs for a routine with no identity, so a miss runs unasked.
ALLOWED_DOMAINS = MIRRORED_DOMAINS - REFUSED_DOMAINS
#: HA services the house's domain table does not name, each with the plain-value keys it takes:
#: what ``call_service`` is for. None takes a URL, a path or another entity to act on.
EXTRA_CALLS: dict[tuple[str, str], frozenset[str]] = {
    ("cover", "open_cover"): frozenset(), ("cover", "close_cover"): frozenset(),
    ("cover", "open_cover_tilt"): frozenset(), ("cover", "close_cover_tilt"): frozenset(),
    ("cover", "set_cover_tilt_position"): frozenset({"tilt_position"}),
    ("vacuum", "start"): frozenset(), ("vacuum", "pause"): frozenset(), ("vacuum", "stop"): frozenset(),
    ("vacuum", "return_to_base"): frozenset(), ("vacuum", "locate"): frozenset(),
    ("vacuum", "clean_spot"): frozenset(), ("vacuum", "set_fan_speed"): frozenset({"fan_speed"}),
    ("light", "turn_on"): frozenset({"brightness_pct", "brightness_step", "brightness_step_pct", "hs_color",
                                     "xy_color", "rgbw_color", "rgbww_color", "color_name", "white",
                                     "effect", "flash"}),
    ("climate", "set_preset_mode"): frozenset({"preset_mode"}),
    ("climate", "set_swing_mode"): frozenset({"swing_mode"}),
    ("climate", "set_humidity"): frozenset({"humidity"}),
    ("climate", "set_temperature"): frozenset({"target_temp_high", "target_temp_low", "hvac_mode"}),
    ("climate", "turn_on"): frozenset(), ("climate", "turn_off"): frozenset(),
    ("fan", "set_percentage"): frozenset({"percentage"}), ("fan", "set_preset_mode"): frozenset({"preset_mode"}),
    ("fan", "oscillate"): frozenset({"oscillating"}), ("fan", "set_direction"): frozenset({"direction"}),
    ("fan", "increase_speed"): frozenset({"percentage_step"}),
    ("fan", "decrease_speed"): frozenset({"percentage_step"}),
    ("media_player", "volume_mute"): frozenset({"is_volume_muted"}),
    ("media_player", "volume_up"): frozenset(), ("media_player", "volume_down"): frozenset(),
    ("media_player", "media_seek"): frozenset({"seek_position"}),
    ("media_player", "select_source"): frozenset({"source"}),
    ("media_player", "select_sound_mode"): frozenset({"sound_mode"}),
    ("media_player", "shuffle_set"): frozenset({"shuffle"}), ("media_player", "repeat_set"): frozenset({"repeat"}),
    ("humidifier", "turn_on"): frozenset(), ("humidifier", "turn_off"): frozenset(),
    ("humidifier", "toggle"): frozenset(), ("humidifier", "set_humidity"): frozenset({"humidity"}),
    ("humidifier", "set_mode"): frozenset({"mode"}),
    ("water_heater", "set_temperature"): frozenset({"temperature"}),
    ("water_heater", "set_operation_mode"): frozenset({"operation_mode"}),
    ("water_heater", "turn_on"): frozenset(), ("water_heater", "turn_off"): frozenset(),
    ("valve", "open_valve"): frozenset(), ("valve", "close_valve"): frozenset(),
    ("valve", "stop_valve"): frozenset(), ("valve", "set_valve_position"): frozenset({"position"}),
    ("lawn_mower", "start_mowing"): frozenset(), ("lawn_mower", "pause"): frozenset(),
    ("lawn_mower", "dock"): frozenset(),
    ("select", "select_first"): frozenset(), ("select", "select_last"): frozenset(),
    ("select", "select_next"): frozenset({"cycle"}), ("select", "select_previous"): frozenset({"cycle"}),
    ("siren", "turn_on"): frozenset({"tone", "volume_level", "duration"}),
}
#: Services left out though their domain is allowed: HA fetches the URL ``play_media`` is given,
#: from inside the house network (T-1525 review).
WITHHELD_CALLS = frozenset({("media_player", "play_media")})


def _allowed_calls() -> dict[tuple[str, str], frozenset[str]]:
    calls: dict[tuple[str, str], set[str]] = {}
    for (domain, _method), (service, params) in _SERVICES.items():
        keys = calls.setdefault((domain, service), set())
        for param in params:
            keys.update({param.key, *getattr(param, "aliases", ())})
    for pair, extra in EXTRA_CALLS.items():
        calls.setdefault(pair, set()).update(extra)
    return {pair: frozenset(keys | {"entity_id"}) for pair, keys in calls.items()
            if pair[0] in ALLOWED_DOMAINS and pair not in WITHHELD_CALLS}


#: The only HA calls ``call_service`` makes: (domain, service) -> the data keys it may carry. The
#: house's own domain table (each entity method's HA service and keys) plus :data:`EXTRA_CALLS`,
#: on :data:`ALLOWED_DOMAINS`, less :data:`WITHHELD_CALLS`. An allowed domain is not enough:
#: ``camera.snapshot`` writes a path and ``play_media`` fetches a URL (T-1525).
ALLOWED_CALLS = _allowed_calls()


class HaRefused(PermissionError):
    """A ``home_assistant.call_service`` the house would hold for a human, or never run."""


def _entity_ids(value: Any) -> list[str] | None:
    """The ids as HA will read them (HA lowercases them), or None for a shape that is not ids."""
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip().lower() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return [v.strip().lower() for v in value if v.strip()]
    return None


def refusal(domain: str, service: str, data: dict[str, Any], ids: list[str], mirrored: Any) -> str:
    """Why this call may not go to HA, or "" when it may. ``domain``, ``service`` and ``ids``
    come normalised.

    The pair must be on :data:`ALLOWED_CALLS` and every data key one it lists. Every target must
    be an entity of the call's own domain that the bridge mirrors (``mirrored``): HA's config and
    diagnostic entities (a hub's restart button, a firmware switch) are kept out of the mirror on
    purpose and stay out of reach here too, and a call with no target is refused rather than left
    to HA's reading of "every entity".

    Not covered: a value outside its key's range (HA's own schema refuses it); what a device does
    with a value it accepts.
    """
    if domain not in ALLOWED_DOMAINS:
        return f"home_assistant.call_service does not reach HA's {domain or 'unnamed'} services"
    keys = ALLOWED_CALLS.get((domain, service))
    if keys is None:
        return f"home_assistant.call_service does not make {domain}.{service} calls"
    for key in ("area_id", "device_id", "floor_id", "label_id"):
        if data.get(key):  # HA resolves these to entities the house never sees
            return f"home_assistant.call_service takes entity ids, not {key}"
    extra = sorted(set(data) - keys)
    if extra:
        return f"{domain}.{service} through home_assistant.call_service takes no {', '.join(extra)}"
    if not ids:
        return "home_assistant.call_service needs the entity_id of a device the house mirrors"
    for eid in ids:
        if eid.split(".", 1)[0] != domain or eid not in mirrored:
            return (f"{eid} is not one home_assistant.call_service reaches: a lock, a script or "
                    "a scene goes through the house's own service, which asks a human")
    return ""


async def _keep(setup: HaSetup, bridge: HaBridge, jarvis: "Jarvis", interval: float, retry: float) -> None:
    bridge_task: asyncio.Task | None = None
    while True:
        out = await setup.ensure()
        if setup.token and (bridge_task is None or bridge_task.done()):
            bridge_task = jarvis.async_create_task(bridge.run())
        # A bind just staged must be promoted inside HA's five-minute trial, so come back soon.
        settled = out.get("state") in SETTLED and out.get("http") != "restarting"
        await asyncio.sleep(interval if settled else min(retry, interval))


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}
    url = str(options.get("url") or "").strip().rstrip("/")
    store = jarvis.data.setdefault(DOMAIN, {})
    if not url:
        _LOGGER.info("home_assistant: no url; off")
        return True
    setup = HaSetup(jarvis, url, token=str(options.get("token") or ""),
                    transport=store.get("transport"), connect=store.get("connect"),
                    bind=str(options.get("bind") or ""))
    bridge = HaBridge(jarvis, url, token=lambda: setup.token, connect=store.get("connect"))
    store.update({"setup": setup, "bridge": bridge})
    keeper = jarvis.async_create_task(_keep(setup, bridge, jarvis, float(options.get("interval") or INTERVAL_S),
                                            float(options.get("retry") or RETRY_S)))

    async def _stop(*_: Any) -> None:
        keeper.cancel()

    jarvis.register_shutdown(_stop)

    async def status(call: Any) -> dict[str, Any]:
        return {**setup.status(), "bridge": bridge.status()}

    async def redo(call: Any) -> dict[str, Any]:
        out = await setup.ensure()
        return {**out, "bridge": bridge.status()}

    async def call_service(call: Any) -> dict[str, Any]:
        data = dict(call.data or {})
        domain = str(data.get("domain") or "").strip().lower()
        service = str(data.get("service") or "").strip().lower()
        if not domain or not service:
            raise ValueError("home_assistant.call_service needs a domain and a service")
        payload = dict(data.get("data") or {})
        target_ids, inner_ids = _entity_ids(data.get("entity_id")), _entity_ids(payload.get("entity_id"))
        if target_ids is None or inner_ids is None:
            raise HaRefused("home_assistant.call_service takes entity_id as an id or a list of ids")
        why = refusal(domain, service, payload, target_ids + inner_ids, bridge.entities)
        if why:
            raise HaRefused(why)
        # What was checked is what is sent: HA lowercases ids itself, so a raw "LOCK.door" would
        # have passed a check on the raw string and still reached the lock.
        if inner_ids:
            payload["entity_id"] = inner_ids
        target = {"entity_id": target_ids} if target_ids else None
        result = await bridge.call_service(domain, service, payload, target)
        return {"ok": True, "result": result}

    jarvis.services.register(DOMAIN, "status", status, supports_response=True,
                             description="Whether AURA holds a token the stack's Home Assistant accepts, "
                                         "and how many of its entities are mirrored into the house.")
    jarvis.services.register(DOMAIN, "setup", redo, supports_response=True, owner_only=True,
                             description="Check Home Assistant's setup now: onboard it, adopt or renew "
                                         "AURA's token.")
    jarvis.services.register(
        DOMAIN, "call_service", call_service, supports_response=True, owner_only=True,
        description="Call a Home Assistant service by name, for one the house's own services "
                    "do not cover (open_cover, vacuum start, a light's colour name, a media "
                    "player's source …), on a device the house mirrors. Only modelled services "
                    "and their own keys; locks, notify, scripts, scenes, automations, alarms, "
                    "cameras, play_media and HA itself are refused.",
        fields={"domain": {"description": "HA domain, e.g. scene", "required": True},
                "service": {"description": "HA service, e.g. turn_on", "required": True},
                "entity_id": {"description": "HA entity id to target", "required": False},
                "data": {"description": "service data", "required": False}})
    return True
