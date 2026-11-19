"""The house (jarvis-core) as the plugins reach it: one client, the device routes.

``HASS_URL`` / ``HASS_TOKEN`` are the pair the built-in homeassistant toolset
uses (``aura_schema`` ``house_url`` / ``house_token``). Two plugins need the
paired devices — ``smart-home`` for "what is paired, run an action, tell the
user", ``aura-desktop`` for computer use on the owner's desktop — and one
client here is what keeps them from carrying two.

The device routes, on jarvis-core's REST:

* ``GET  /api/config/companion/list`` — paired devices with presence and the
  actions each declares.
* ``POST /api/services/device_control/run`` with ``return_response`` — one
  ``device_command`` to the device, and its ``device_result`` back as the
  service response: ``status`` (``ok`` / ``denied`` / ``error`` /
  ``unsupported`` / ``approval_required``), ``result``, ``tier``, and
  ``trust: untrusted`` with a ``note`` when the device says what came back
  was written by somebody other than the user (a screenshot, the clipboard).

The device applies its own tier policy to every command and may ask the
person at the machine or refuse; nothing here lowers that.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from aura_schema import from_env


def base_url() -> str:
    return from_env().house_url.rstrip("/")


def client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(base_url=base_url(), headers={"Authorization": f"Bearer {from_env().house_token}"}, timeout=timeout)


def configured() -> bool:
    return bool(from_env().house_token)


def body_of(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return r.text[:500]


def devices(c: httpx.Client) -> list[dict[str, Any]]:
    """The paired devices, each with ``device_id``, ``name``, ``kind``, ``online``, ``presence``, ``actions``."""
    data = c.get("/api/config/companion/list").raise_for_status().json()
    items = data if isinstance(data, list) else data.get("devices", data.get("companions", []))
    out = []
    for d in items:
        out.append({"device_id": d.get("id") or d.get("device_id"), "name": d.get("name"), "kind": d.get("kind") or d.get("platform"),
                    "online": d.get("online", d.get("connected")), "presence": d.get("presence"),
                    # A device's manifest names each action by `id`; the older
                    # shape used `name`. Either, never the whole dict as text.
                    "actions": sorted(str(a.get("id") or a.get("name") or "") if isinstance(a, dict) else str(a)
                                      for a in (d.get("actions") or [])),
                    # Each action's published parameters: without them "turn my torch off"
                    # goes out as {} and the phone runs its default, which is on.
                    "params": {str(a.get("id") or a.get("name")): a["params"] for a in (d.get("actions") or [])
                               if isinstance(a, dict) and a.get("params")}})
    return out


_HOME_CACHE: dict[str, Any] = {"at": float("-inf"), "home": False}
_HOME_TTL = 15.0


def owner_home() -> bool:
    """Is a paired phone at home right now? Unknown is False, the safe side (T-0606).

    A grant "allow when I'm home" is keyed on this, so a wrong True lets a remembered yes cover a
    request made while he is away; every failure (no token, house down, no phone reporting a
    zone, a phone not heard from lately) therefore reads as away. Only a phone counts: a
    desktop's zone says where the machine is. Cached for 15 s, so a burst of asks is one call.
    """
    now = time.monotonic()
    if now - _HOME_CACHE["at"] < _HOME_TTL:
        return bool(_HOME_CACHE["home"])
    home = False
    if configured():
        try:
            with client(timeout=2.0) as c:
                home = any(d.get("kind") == "android" and d.get("online") is not False
                           and isinstance(d.get("presence"), dict)
                           and d["presence"].get("zone") == "home"
                           and d["presence"].get("reach") != "ABSENT" for d in devices(c))
        except Exception:  # noqa: BLE001 - presence is never load-bearing: away
            home = False
    _HOME_CACHE.update(at=now, home=home)
    return home


def pick_phone(c: httpx.Client, session: str) -> tuple[str, str, str]:
    """The phone a phone-only tool acts on, as ``(device_id, device_token, problem)``: the one
    online phone by its id; with several, the phone this turn came from by its house token, for
    the house to resolve (T-1576, as ``device_action`` does, T-1555); else ``problem`` is
    ``"none"`` or ``"several"`` and the tool says so in its own words.

    Strict (``_surface.turn_device``): a second screen's prompt sent mid-turn must not redirect a
    call or an alert. Not covered: a desktop's turn, the console's or a scheduled job's names no
    phone, so with two online it still refuses; a phone paired before its key carried a house
    token has none to name."""
    phones = [d for d in devices(c) if d.get("kind") == "android" and d.get("online")]
    if len(phones) == 1:
        return str(phones[0]["device_id"]), "", ""
    if not phones:
        return "", "", "none"
    from . import _surface  # lazy: _surface reads the adapter, which a house-only caller never needs
    turn = _surface.turn_device(session, strict=True) if session else None
    token = turn.get("house_token") if isinstance(turn, dict) and turn.get("kind") == "android" else None
    if isinstance(token, str) and token:
        return "", token, ""
    return "", "", "several"


#: Device actions only their own tool may send. ``emergency_alert`` carries its recipients as
#: parameters, so through a general route (phone_action, house_device_action) the model could
#: text and call anyone in the owner's name; the emergency tool reads them from its own store.
#: ``set_island_settings`` (T-0984) is asked about by the ``phone_island_set`` row while
#: ``phone_action`` is free: reached through it, the change would skip the ask. Its read,
#: ``get_island_settings``, is open to both. ``emergency_contacts_sync`` (T-1262) is the house
#: handing the phone the owner's saved list for the fall watch: a model that could send it would
#: choose who a fall later texts, so only the house does.
RESERVED_ACTIONS = frozenset({"emergency_alert", "set_island_settings", "emergency_contacts_sync"})


def names_reserved(thing: Any) -> str | None:
    """The reserved action a nested definition (an automation, a script) names, or None.

    Any string equal to one, wherever it sits: an automation's steps are free-form, and a
    saved one fires with nobody asked. Not covered: a templated action name the house
    renders later.
    """
    if isinstance(thing, str):
        return thing if thing.strip().lower() in RESERVED_ACTIONS else None
    if isinstance(thing, dict):
        thing = list(thing.values())
    if isinstance(thing, (list, tuple)):
        return next((hit for hit in map(names_reserved, thing) if hit), None)
    return None


def run_action(c: httpx.Client, device_id: str, action: str, params: dict | None = None, reason: str = "",
               *, reserved: bool = False, device_token: str = "") -> dict[str, Any]:
    """One action on one device, and the device's answer.

    ``ok`` is the HTTP verdict; the device's own is ``response.status``. A
    ``denied`` is the person at the machine saying no, and is the answer.
    An action in :data:`RESERVED_ACTIONS` is refused here, unsent, unless its own
    tool says ``reserved=True``; ``phone_action`` refuses one before either of its routes
    and ``automation_create`` refuses a definition that names one. Not covered:
    ``ha_call_service`` on the house's ``device_control`` domain, which the matrix asks
    the owner about every time.

    ``device_token`` (with ``device_id`` empty) is the house token id of the device the turn
    came from; the house runs the action there when a connected link holds it (T-1555).
    """
    if str(action).strip().lower() in RESERVED_ACTIONS and not reserved:
        return {"ok": False, "http": 403, "device_id": device_id, "action": action,
                "response": {"status": "denied", "error": f"{action} is sent only by its own tool"}}
    body: dict[str, Any] = {"device_id": device_id, "action": action, "params": params or {}, "return_response": True}
    if reason:
        body["reason"] = reason
    if device_token and not device_id:
        body["device_token"] = device_token
    r = c.post("/api/services/device_control/run", json=body)
    answer = body_of(r)
    response = answer.get("service_response") if isinstance(answer, dict) and "service_response" in answer else answer
    return {"ok": r.status_code < 400, "http": r.status_code, "device_id": device_id, "action": action, "response": response}
