"""A routine that is a plain house action, compiled into a jarvis-core automation (T-0185).

A routine normally fires by prompting the model ("[routine: …] Do this now: …"), which costs a
model turn, can be misread, and does nothing while the model loads. When the routine's trigger
has a house-side equivalent (tests/contracts/routine-triggers.json: today only ``time``, with
its days and after/before window) and what it does is one service call on entities that exist,
the house runs it instead: ``compile_routine`` builds the automation jarvis-core's own validator
accepts (``aura/automation/authored.py``), and the routine keeps its id.

The rule for what compiles is fixed here, not the model's call: only the services in
``SERVICES`` (switching lights, switches, fans, closing covers, a few media and climate
calls), on entities the house knows now, with no text, message, call or search. Anything else
stays on the model path. One leave routine compiles too, the away-lock (T-0332): with
``away_min`` and ``device_control.run lock_if_idle`` the house waits that long for the owner to
come back, then asks the PC to lock if nobody is using it (``_away_lock``); the phone reports
place edges for the places such a routine names (``house_places`` in its copy). Not covered:
any other arrive/leave routine, one-off dated routines (the house has no one-shot date
trigger), and a service whose entity is renamed later (the automation follows jarvis-core's
own renames).
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

#: The services a routine may compile to. Each switches or sets one thing in the house and
#: nothing the approval matrix asks about; a text, a call, a purchase never compiles. Not a
#: script or scene: either can do anything (unlock a door, send a message), so they stay with
#: the model and its approvals. Not opening a cover: that cover may be the garage door.
SERVICES = frozenset({
    "light.turn_on", "light.turn_off", "light.toggle",
    "switch.turn_on", "switch.turn_off", "switch.toggle",
    "fan.turn_on", "fan.turn_off", "fan.toggle",
    "cover.close_cover", "cover.stop_cover",
    "input_boolean.turn_on", "input_boolean.turn_off", "input_boolean.toggle",
    "media_player.media_pause", "media_player.media_play", "media_player.volume_set",
    "climate.set_temperature",
})
#: Data keys a compiled call may carry, per service; anything else is refused.
DATA = {
    "light.turn_on": {"brightness_pct", "color_temp_kelvin"},
    "media_player.volume_set": {"volume_level"},
    "climate.set_temperature": {"temperature"},
}
#: What a leave routine may do on a paired device (T-0332): only an action that cannot interrupt
#: anyone. lock_if_idle locks a PC nobody has used for idle_s; lock_screen (which can) never
#: compiles, so it keeps asking every time.
DEVICE_ACTIONS = frozenset({"lock_if_idle"})
AWAY_MIN = (1, 60)
#: The device kinds that report the owner's place: their routine_place events start and end the
#: away-lock's wait, and no other paired device's do.
PHONE_KINDS = frozenset({"android", "phone", "ios"})
_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def compile_routine(routine: dict, call: Any, entities: set[str],
                    devices: Optional[Mapping[str, str]] = None) -> tuple[Optional[dict], str]:
    """(the automation, "") or (None, why it stays on the model path)."""
    when = routine.get("when") or {}
    if when.get("event") == "leave":
        return _away_lock(routine, call, devices or {})
    if when.get("event") != "time":
        return None, f"the {when.get('event') or 'routine'} trigger has no house-side equivalent"
    if when.get("date") or routine.get("once"):
        # The phone reports a once routine spent; a house automation would fire every day.
        return None, "a one-off routine stays with the model (no one-shot trigger in the house)"
    if not _HHMM.match(str(when.get("at") or "")):
        return None, "when.at is not HH:MM"
    if not isinstance(call, dict):
        return None, "no house action given"
    service = str(call.get("service") or "")
    if service not in SERVICES:
        return None, f"{service or 'that'} is not a house action a routine may run on its own"
    ids = call.get("entity_ids")
    ids = [ids] if isinstance(ids, str) else list(ids or [])
    domain = service.split(".", 1)[0]
    if not ids or any(not isinstance(e, str) or e.split(".", 1)[0] != domain for e in ids):
        return None, f"{service} needs {domain} entities"
    unknown = [e for e in ids if e not in entities]
    if unknown:
        return None, f"the house has no {', '.join(unknown)}"
    data = call.get("data") or {}
    if not isinstance(data, dict) or set(data) - DATA.get(service, set()):
        return None, f"{service} takes only {', '.join(sorted(DATA.get(service, set()))) or 'no data'}"
    conditions = _window(when)
    action: dict[str, Any] = {"service": service, "target": {"entity_id": ids}}
    if data:
        action["data"] = dict(data)
    return {
        "alias": f"Routine: {str(routine.get('name') or routine['id'])[:100]}",
        "description": f"jarvis routine {routine['id']} (compiled; remove the routine to remove this)",
        "mode": "single",
        "trigger": [{"platform": "time", "at": f"{when['at']}:00"}],
        "condition": conditions,
        "action": [action],
    }, ""


def _window(when: dict) -> list[dict]:
    """The routine's days and after/before hours as the house's time conditions."""
    conditions: list[dict] = []
    days = [d for d in when.get("days") or [] if d in _DAYS]
    if days:
        conditions.append({"condition": "time", "weekday": days})
    window = {k: f"{when[k]}:00" for k in ("after", "before") if when.get(k)}
    if window:
        conditions.append({"condition": "time", **window})
    return conditions


def _place_edge(place: str, edge: str, phone: str) -> dict:
    """The owner's phone's own report of a place edge (routine-triggers.json): from that phone
    and trusted only, so neither another paired device nor text another app wrote can start the
    wait or cancel it."""
    return {"platform": "event", "event_type": "jarvis_device_event",
            "event_data": {"event": "routine_place", "device_id": phone, "trust": "trusted",
                           "data": {"place": place, "edge": edge}}}


def _away_lock(routine: dict, call: Any, devices: Mapping[str, str]) -> tuple[Optional[dict], str]:
    """A leave routine that locks a PC once the owner has stayed gone away_min (T-0332).

    The guard against a GPS or Wi-Fi blip is the wait itself: an arrive at the same place inside
    away_min ends the run, mode restart keeps one wait per routine. The PC then locks only if
    nobody has used it since about when the owner left, and a refusal (in use, or it cannot tell)
    is told to the owner -- someone at the PC while they are out is the news. Does not cover a
    house restart inside the window: the wait is dropped and nothing locks (the logbook line shows
    it began).
    """
    when = routine.get("when") or {}
    raw = when.get("away_min")
    if isinstance(raw, bool) or not isinstance(raw, int) or not AWAY_MIN[0] <= raw <= AWAY_MIN[1]:
        return None, (f"a leave routine runs in the house only as an away-lock with away_min "
                      f"({AWAY_MIN[0]}-{AWAY_MIN[1]} minutes)")
    if not isinstance(call, dict) or call.get("service") != "device_control.run":
        return None, "a leave routine runs in the house only to lock a PC (device_control.run lock_if_idle)"
    action, device = str(call.get("action") or ""), str(call.get("device_id") or "")
    if action not in DEVICE_ACTIONS:
        return None, f"{action or 'that'} is not something a device may do unasked"
    if device not in devices:
        return None, f"no paired device {device!r}"
    phones = sorted(d for d, kind in devices.items() if str(kind).lower() in PHONE_KINDS)
    if not phones:
        return None, "no paired phone to report where the owner is"
    place, label = str(when.get("place") or ""), str(when.get("place_label") or "there")
    name = str(routine.get("name") or routine["id"])[:100]
    return {
        "alias": f"Routine: {name}",
        "description": f"jarvis routine {routine['id']} (compiled; remove the routine to remove this)",
        "mode": "restart",
        "trigger": [_place_edge(place, "leave", phone) for phone in phones],
        "condition": _window(when),
        "action": [
            {"service": "logbook.log", "data": {
                "name": "Routine",
                "message": f"{name}: away from {label}, {action} on {device} in {raw} min unless back"}},
            {"wait_for_trigger": [_place_edge(place, "arrive", phone) for phone in phones],
             "timeout": raw * 60, "continue_on_timeout": True},
            {"condition": "template", "value_template": "{{ wait.trigger is none }}"},
            {"service": "device_control.run",
             "data": {"device_id": device, "action": action, "params": {"idle_s": max(120, raw * 60 - 30)},
                      "reason": f"routine: {name} (away from {label} {raw} min)"},
             "response_variable": "result"},
            {"if": [{"condition": "template", "value_template": "{{ result.status != 'ok' }}"}],
             "then": [{"service": "companion.notify", "data": {
                 "title": "Your PC was not locked",
                 "message": f"{name}: {{{{ result.error or result.status }}}}"}}]},
        ],
    }, ""
