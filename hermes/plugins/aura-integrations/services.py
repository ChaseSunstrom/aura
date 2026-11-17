"""The stack's services as AURA sets them up itself (T-1523): what the last check found, and a redo.

The keepers do the work unasked (n8n's in the n8n plugin, ``_shared/_n8n_setup.py``; Home
Assistant's in the house, ``aura-core/aura/integrations/home_assistant``); these two
tools are how AURA answers "is n8n wired up?" and does it again on request. ``service_setup`` asks
(``_shared/_matrix.py``): it can create an owner or mint a key inside a service.

Neither answer carries a secret: the key and the login AURA made stay in their files; the status
names the login file, so the owner can be told where to find it.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from _shared import _house, _n8n_setup

#: What a house answer may pass on: the token and the login stay in the house.
_HOUSE_KEYS = ("state", "detail", "checked", "url", "has_token", "bridge")


def _home_assistant(service: str) -> dict[str, Any]:
    """``home_assistant.<service>`` on the house; off or down is a state, never a raise."""
    try:
        with _house.client(timeout=60.0) as c:  # a first onboarding takes HA a while
            r = c.post(f"/api/services/home_assistant/{service}", json={"return_response": True})
    except Exception as exc:  # noqa: BLE001 - the house is down: say so
        return {"state": "unreachable", "detail": f"the house could not be reached ({type(exc).__name__})"}
    if r.status_code >= 400:
        return {"state": "off", "detail": f"the house has no Home Assistant bridge ({r.status_code}): "
                                          "configuration.yaml needs its home_assistant block"}
    answer = _house.body_of(r)
    if isinstance(answer, dict) and "service_response" in answer:
        answer = answer["service_response"]
    if not isinstance(answer, dict):
        return {"state": "failed", "detail": "the house gave no Home Assistant answer"}
    return {k: answer[k] for k in _HOUSE_KEYS if k in answer}


#: Each service AURA sets up: (its status, its setup run).
SERVICES: dict[str, tuple[Callable[[], dict], Callable[[], dict]]] = {
    "n8n": (lambda: _n8n_setup.status(), lambda: _n8n_setup.ensure()),
    "home_assistant": (lambda: _home_assistant("status"), lambda: _home_assistant("setup")),
}

SERVICE_SETUP_STATUS = {
    "name": "service_setup_status",
    "description": (
        "What AURA found the last time it checked the stack's own services (n8n, Home Assistant): "
        "whether it set each one up itself, adopted a key it was given, or could not (and why), and "
        "for Home Assistant how many of its devices are mirrored into the house. Read-only. Use it "
        "for 'is n8n connected?', 'is Home Assistant set up?' or 'where is the n8n login?'."
    ),
    "parameters": {"type": "object", "properties": {}},
}

SERVICE_SETUP = {
    "name": "service_setup",
    "description": (
        "Set one of the stack's services up for AURA now, as the background check does: on a fresh "
        "service, create its owner and an API key (n8n) or onboard it and mint a token (Home "
        "Assistant); on a refused key, mint a new one with AURA's own login. Asks the person first."
    ),
    "parameters": {
        "type": "object",
        "properties": {"service": {"type": "string", "enum": sorted(SERVICES),
                                   "description": "Which service."}},
        "required": ["service"],
    },
}


def handle_status(args: dict, **_kwargs: Any) -> str:
    return json.dumps({"ok": True, "services": {name: st() for name, (st, _run) in SERVICES.items()}})


def handle_setup(args: dict, **_kwargs: Any) -> str:
    name = str((args or {}).get("service") or "")
    if name not in SERVICES:
        return json.dumps({"ok": False, "error": f"unknown service {name!r}: one of {', '.join(sorted(SERVICES))}"})
    out = SERVICES[name][1]()
    return json.dumps({"ok": True, "service": name, "state": out.get("state"), "detail": out.get("detail")})
