"""The stack's Home Assistant, set up and wired in by AURA itself (T-1523).

``home_assistant:`` in configuration.yaml names the HA (``url``, default the stack's own on the
loopback) and, optionally, a ``token`` the owner made. On start, and every ten minutes after,
:class:`~.setup.HaSetup` makes sure AURA holds a token HA accepts (onboarding a fresh HA itself),
and :class:`~.bridge.HaBridge` mirrors HA's devices into the house and sends their commands back.

Services: ``home_assistant.status`` (what was found, what is mirrored; no secret),
``home_assistant.setup`` (check and redo it now; owner only) and
``home_assistant.call_service`` (an HA service by name, for one the house's domain table does
not have; owner only; only a domain in :data:`ALLOWED_DOMAINS`).

Not covered: an HA the owner runs somewhere else that AURA has no token for (the status says to
set ``home_assistant.token``); HA add-ons (a container HA has no Supervisor).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ...automation.reach import INDIRECT_DOMAINS
from ...const import GATED_DOMAINS
from .bridge import MIRRORED_DOMAINS, HaBridge
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


def refusal(domain: str, data: dict[str, Any], ids: list[str], mirrored: Any) -> str:
    """Why this call may not go to HA, or "" when it may. ``domain`` and ``ids`` come normalised.

    Every target must be an entity the bridge mirrors (``mirrored``): HA's config and diagnostic
    entities (a hub's restart button, a firmware switch) are kept out of the mirror on purpose and
    stay out of reach here too, and a call with no target is refused rather than left to HA's
    reading of "every entity".

    Not covered: what an allowed service does with its own data (``media_player.play_media``
    makes HA fetch the URL it is given; ``camera.snapshot`` writes where HA's own
    ``allowlist_external_dirs`` lets it).
    """
    if domain not in ALLOWED_DOMAINS:
        return f"home_assistant.call_service does not reach HA's {domain or 'unnamed'} services"
    for key in ("area_id", "device_id", "floor_id", "label_id"):
        if data.get(key):  # HA resolves these to entities the house never sees
            return f"home_assistant.call_service takes entity ids, not {key}"
    if not ids:
        return "home_assistant.call_service needs the entity_id of a device the house mirrors"
    for eid in ids:
        if eid.split(".", 1)[0] not in ALLOWED_DOMAINS or eid not in mirrored:
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
        why = refusal(domain, payload, target_ids + inner_ids, bridge.entities)
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
                    "do not cover, on a device domain the house mirrors. Locks, notify, "
                    "scripts, scenes, automations, alarms and HA itself are refused.",
        fields={"domain": {"description": "HA domain, e.g. scene", "required": True},
                "service": {"description": "HA service, e.g. turn_on", "required": True},
                "entity_id": {"description": "HA entity id to target", "required": False},
                "data": {"description": "service data", "required": False}})
    return True
