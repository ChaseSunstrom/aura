"""Departure: what was left on when you went out, and who came in while you were away.

    departure:
      enabled: true                    # opt-in: nothing runs without this key
      quiet_hours: ["23:00", "07:00"]  # holds the leaving sweep, not the alarm
      min_interval: 600                # seconds before one entity can alert again
      freeze_c: 0                      # forecast low at or under this -> freeze note
      heat_c: 35                       # forecast high at or over this -> heat note

* When the last person at home goes ``not_home``, the house checks lights on,
  locks unlocked, covers open (garage doors, gates) and media playing, and says
  what is still going once through ``companion.notify``. A clean house says
  nothing.
* While nobody is home, a lock unlocking, a door/window/opening sensor opening
  or a motion sensor firing alerts (importance ``high``) once per entity per
  ``min_interval``. Quiet hours do not hold this: an unexpected door at 03:00
  is the case it exists for.
* ``sensor.home_prep`` holds a one-line note ("A freeze is forecast, ...") from
  the weather entity's forecast for tomorrow, or ``none``; the evening brief
  job reads it.

Not covered: "home" is only what ``person.*`` says, so a house with no person
entity never sweeps or alerts; the forecast needs a weather entity that carries
a ``forecast`` attribute; the alert debounce is per entity, so a burst across
many sensors is still one notice each.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ...const import EVENT_STATE_CHANGED, STATE_HOME, STATE_NOT_HOME
from ..narrate.limits import in_window, local_minutes, parse_window

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "departure"
PREP_ENTITY = "sensor.home_prep"
WATCHED_CLASSES = {"door", "garage_door", "window", "opening", "motion", "occupancy"}
STORM = {"lightning", "lightning-rainy", "hail", "exceptional"}


def _name(entity_id: str) -> str:
    return entity_id.split(".", 1)[1].replace("_", " ")


def _celsius(value: Any, unit: str) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return (v - 32) * 5 / 9 if "F" in unit.upper() else v


def prep_note(attrs: dict[str, Any], freeze_c: float, heat_c: float) -> str:
    """One line for tomorrow's weather, or ``none``. Reads the second forecast
    entry (tomorrow) when there is one, since the note is read the evening before."""
    forecast = [f for f in attrs.get("forecast") or [] if isinstance(f, dict)]
    if not forecast:
        return "none"
    day = forecast[1] if len(forecast) > 1 else forecast[0]
    unit = str(attrs.get("temperature_unit") or "°C")
    low = _celsius(day.get("templow", day.get("native_templow")), unit)
    high = _celsius(day.get("temperature", day.get("native_temperature")), unit)
    notes = []
    if low is not None and low <= freeze_c:
        notes.append("a freeze is forecast, so cover or drain outdoor taps and bring plants in")
    if high is not None and high >= heat_c:
        notes.append("heat is forecast, so close blinds early and check the cooling")
    if str(day.get("condition") or "") in STORM:
        notes.append("a storm is forecast, so secure outdoor items and charge devices")
    if not notes:
        return "none"
    return notes[0].capitalize() + "".join(f"; also {n}" for n in notes[1:]) + "."


class Departure:
    def __init__(self, jarvis: "Jarvis", options: dict[str, Any]) -> None:
        self.jarvis = jarvis
        self.quiet = parse_window(options.get("quiet_hours"))
        self.min_interval = float(options.get("min_interval") or 600)
        self.freeze_c = float(options.get("freeze_c", 0))
        self.heat_c = float(options.get("heat_c", 35))
        self.clock = jarvis.data.get("departure_clock") or time.time
        self.minutes = jarvis.data.get("departure_local_minutes") or local_minutes
        self._last: dict[str, float] = {}

    def nobody_home(self) -> bool:
        people = self.jarvis.states.all("person")
        return bool(people) and all(p.state != STATE_HOME for p in people)

    async def on_state(self, event: Any) -> None:
        try:
            await self._handle(event.data)
        except Exception:  # a notice must never break the state machine
            _LOGGER.exception("departure: could not handle a state change")

    async def _handle(self, data: dict[str, Any]) -> None:
        new, old = data.get("new_state"), data.get("old_state")
        if new is None:
            return
        eid = new.entity_id
        domain = eid.split(".", 1)[0]
        if domain == "weather":
            note = prep_note(new.attributes, self.freeze_c, self.heat_c)
            self.jarvis.states.set(PREP_ENTITY, note, {"friendly_name": "Home prep"})
        elif old is None or old.state == new.state:
            return
        elif domain == "person":
            if old.state == STATE_HOME and new.state == STATE_NOT_HOME and self.nobody_home():
                await self.sweep()
        elif self.nobody_home() and self._is_intrusion(domain, new):
            now = self.clock()
            if now - self._last.get(eid, -1e9) >= self.min_interval:
                self._last[eid] = now
                verb = self._verb(domain, new)
                await self.notify(f"{_name(eid).capitalize()} {verb} while nobody is home.", "high")

    @staticmethod
    def _is_intrusion(domain: str, new: Any) -> bool:
        if domain == "lock":
            return new.state == "unlocked"
        return (
            domain == "binary_sensor"
            and new.state == "on"
            and new.attributes.get("device_class") in WATCHED_CLASSES
        )

    @staticmethod
    def _verb(domain: str, new: Any) -> str:
        if domain == "lock":
            return "was unlocked"
        if new.attributes.get("device_class") in ("motion", "occupancy"):
            return "detected motion"
        return "opened"

    async def sweep(self) -> None:
        left = []
        for s in self.jarvis.states.all():
            d = s.entity_id.split(".", 1)[0]
            if d == "light" and s.state == "on":
                left.append(f"{_name(s.entity_id)} is on")
            elif d == "media_player" and s.state == "playing":
                left.append(f"{_name(s.entity_id)} is playing")
            elif d == "lock" and s.state == "unlocked":
                left.append(f"{_name(s.entity_id)} is unlocked")
            elif d == "cover" and s.state == "open":
                left.append(f"{_name(s.entity_id)} is open")
        if not left or in_window(self.minutes(self.clock()), self.quiet):
            return
        await self.notify("You've left with: " + "; ".join(left) + ".", "normal")

    async def notify(self, message: str, importance: str) -> None:
        if not self.jarvis.services.has_service("companion", "notify"):
            _LOGGER.warning("departure: companion.notify is not available; %r not sent", message)
            return
        await self.jarvis.services.async_call(
            "companion", "notify",
            {"message": message, "importance": importance, "kind": "notify"}, blocking=True,
        )


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}
    if options.get("enabled", True) is False:
        return True
    unsub = jarvis.bus.listen(EVENT_STATE_CHANGED, Departure(jarvis, options).on_state)
    jarvis.register_shutdown(unsub)
    return True
