"""The forecast as numbers in one call, and its chart on the session's HUD.

The owner, 2026-11-08: asked the temperature, and Jarvis searched the web, read two
pages and never answered; "it didn't popup charts ... for example with a weather
chart". ``weather_forecast`` asks Open-Meteo (keyless) for the house's point (the
house's own ``/api/config``), a place by name (Open-Meteo's geocoder) or a point the
turn carries (the car), each rounded to one decimal so the public API sees a cell about
10 km wide, never the house or the car (T-0765). Units are the house's ``unit_system``. The tool
projects its own line chart (id ``weather``, so asking again replaces it) where every
projection goes (``projections.send``: the session that asked and, for the owner, the
owner's other screens): the chart no longer waits on the model remembering ``hud_project``.

Does not cover: alerts or warnings (Open-Meteo has none keyless), radar, or a
forecast past seven days; the house's ``weather.*`` entities the briefing reads are
not fed from here.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Callable

import httpx
from aura_schema import from_env

from . import hud, projections

from _shared import _taint

_LOGGER = logging.getLogger(__name__)

FORECAST = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT_S = 6.0
USER_AGENT = "Jarvis-home-assistant/1.0 (self-hosted weather_forecast)"
PROJECTION_ID = "weather"

Fetch = Callable[[str, dict], Any]
HouseConfig = Callable[[], dict]

#: WMO weather interpretation codes, as Open-Meteo documents them.
SKY = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}

WEATHER_FORECAST = {
    "name": "weather_forecast",
    "description": (
        "The weather: now, the next 24 hours and up to 7 days, as numbers, at home by default. "
        "One call answers 'what's the temperature', 'will it rain', 'the weather in Oslo this "
        "week'; it also puts the chart on Sir's screen, so do not search the web for the weather "
        "or project it again. Say the answer from `said` and the numbers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "place": {"type": "string", "description": "A town or city by name; leave out for home."},
            "lat": {"type": "number", "description": "With lon: a point (the car's, from the request) instead of home."},
            "lon": {"type": "number"},
            "days": {"type": "integer", "description": "1 (default) for today by the hour, up to 7 for the days ahead."},
        },
        "required": [],
    },
}


def _get(url: str, params: dict) -> Any:
    response = httpx.get(url, params=params, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def _house_config() -> dict:
    from _shared import _house

    with _house.client(timeout=5.0) as c:
        return c.get("/api/config").raise_for_status().json()


def _imperial(units: Any) -> bool:
    """The house says ``imperial``/``metric``, or HA's dict with a temperature unit."""
    if isinstance(units, dict):
        return str(units.get("temperature") or "").endswith("F")
    return str(units or "").strip().lower() == "imperial"


def _hour(stamp: str) -> str:
    """'2026-11-08T16:00' -> '4 PM'."""
    h = int(stamp[11:13])
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def _day(stamp: str) -> str:
    """'2026-11-08' -> 'Sun 8'."""
    d = date.fromisoformat(stamp[:10])
    return f"{d.strftime('%a')} {d.day}"


def _num(v: Any) -> float | None:
    """A number, or one the model sent as a string ("3", "41.9"); never a bool."""
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _where(args: dict, config: dict, fetch: Fetch) -> tuple[float, float, str] | str:
    """The point and what to call it, or why there is none."""
    lat, lon = _num(args.get("lat")), _num(args.get("lon"))
    if lat is not None and lon is not None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return "lat/lon is out of range"
        return lat, lon, f"{lat:g}, {lon:g}"
    place = str(args.get("place") or "").strip()[:80]
    if place:
        found = (fetch(GEOCODE, {"name": place, "count": 1, "language": "en", "format": "json"}) or {}).get("results") or []
        if not found:
            return f"no place called {place!r} found"
        hit = found[0]
        # The geocoder's words reach the model's sentence and the chart: plain, and short.
        label = ", ".join(str(p) for p in (hit.get("name"), hit.get("country") or hit.get("admin1")) if p)
        return float(hit["latitude"]), float(hit["longitude"]), label[:60] or place
    lat, lon = _num(config.get("latitude")), _num(config.get("longitude"))
    if lat is None or lon is None:
        return "the house has no location set: name a place, or set the house's latitude and longitude"
    return lat, lon, "home"


def forecast(args: dict, fetch: Fetch = _get, house: HouseConfig = _house_config) -> dict:
    try:
        config = house() or {}
    except Exception as exc:  # noqa: BLE001 - a named place still answers without the house
        _LOGGER.info("weather: the house's config is out of reach (%s); metric, no home point", exc)
        config = {}
    imperial = _imperial(config.get("unit_system"))
    try:
        where = _where(args, config, fetch)
    except Exception as exc:  # noqa: BLE001 - a geocoder failure is one answer
        return {"ok": False, "error": f"Open-Meteo's geocoder failed: {exc.__class__.__name__}"}
    if isinstance(where, str):
        return {"ok": False, "error": where}
    lat, lon, name = where
    days = max(1, min(7, int(_num(args.get("days")) or 1)))
    params: dict[str, Any] = {
        # A 0.1 degree cell (about 10 km): a forecast is the same across it, and the house's
        # own coordinates are its front door (T-0765).
        "latitude": round(lat, 1), "longitude": round(lon, 1), "timezone": "auto", "forecast_days": max(2, days),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,precipitation,weather_code",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
        "precipitation_unit": "inch" if imperial else "mm",
    }
    try:
        body = fetch(FORECAST, params)
    except Exception as exc:  # noqa: BLE001 - any forecast failure is one answer
        return {"ok": False, "error": f"Open-Meteo did not answer: {exc.__class__.__name__}"}
    try:
        return _shape(body, name, imperial, days)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError) as exc:  # round(Infinity)
        return {"ok": False, "error": f"Open-Meteo answered in a shape this tool does not read ({exc.__class__.__name__})"}


def _shape(body: dict, name: str, imperial: bool, days: int) -> dict:
    now, hourly, daily = body["current"], body["hourly"], body["daily"]
    units = {"temperature": "°F" if imperial else "°C", "wind": "mph" if imperial else "km/h",
             "precipitation": "in" if imperial else "mm"}
    times = hourly.get("time") or []
    first = next((i for i, t in enumerate(times) if t[:13] >= str(now.get("time", ""))[:13]), 0)
    hours = [{"time": times[i][11:16], "temperature": hourly["temperature_2m"][i],
              "rain_chance": hourly["precipitation_probability"][i],
              "sky": SKY.get(hourly["weather_code"][i], "")} for i in range(first, min(first + 24, len(times)))]
    day_rows = [{"date": daily["time"][i], "high": daily["temperature_2m_max"][i], "low": daily["temperature_2m_min"][i],
                 "rain_chance": daily["precipitation_probability_max"][i], "sky": SKY.get(daily["weather_code"][i], ""),
                 "sunrise": str(daily["sunrise"][i])[11:16], "sunset": str(daily["sunset"][i])[11:16]}
                for i in range(min(days, len(daily.get("time") or [])))]
    current = {"time": now.get("time"), "temperature": now.get("temperature_2m"),
               "feels_like": now.get("apparent_temperature"), "humidity": now.get("relative_humidity_2m"),
               "wind": now.get("wind_speed_10m"), "precipitation": now.get("precipitation"),
               "sky": SKY.get(now.get("weather_code"), "")}
    deg = units["temperature"]
    said = f"{round(current['temperature'])}{deg} and {current['sky']} at {name}" if name == "home" else \
        f"{round(current['temperature'])}{deg} and {current['sky']} in {name}"
    if round(current["feels_like"]) != round(current["temperature"]):
        said += f", feels like {round(current['feels_like'])}"
    if day_rows:
        today = day_rows[0]
        said += f". High {round(today['high'])}, low {round(today['low'])}, {today['rain_chance']}% chance of rain."
    return {"ok": True, "place": name, "units": units, "now": current, "hours": hours, "days": day_rows,
            "said": said}


def chart(out: dict) -> dict:
    """The line chart for an answer: the next 24 hours, or the days' highs and lows."""
    unit = out["units"]["temperature"]
    if len(out["days"]) > 1:
        rows = out["days"]
        return {"title": f"Next {len(rows)} days · {out['place']}"[:80], "unit": unit, "x": [_day(r["date"]) for r in rows],
                "series": [{"name": "High", "points": [r["high"] for r in rows], "tone": "accent"},
                           {"name": "Low", "points": [r["low"] for r in rows], "tone": "violet"}]}
    rows = out["hours"]
    # No `area`: it fills to zero and pins the axis there, so 41-46 °F drew as a flat line.
    return {"title": f"Next 24 hours · {out['place']}"[:80], "unit": unit,
            "x": [_hour(f"0000-00-00T{r['time']}") for r in rows],
            "series": [{"name": "Temperature", "points": [r["temperature"] for r in rows], "tone": "accent"}]}


QUIET_SAID = "The house is set to make no calls out on its own, so there is no forecast."


def _quiet_and_unasked(session_id: Any) -> bool:
    """The privacy switch is on and no person asked: a cron job's turn (the 07:00 brief) is the house
    calling out by itself. The scheduler names its sessions `cron_<job>_<time>` and opens them on the
    `cron` platform; either says so, and a missing id says nothing, so a person's question is never
    refused by a guess.

    A setting that will not parse (from_env raises on any malformed one) must not stop a person's
    forecast, so an asked turn fetches as before; a cron turn cannot read the switch, so it stays quiet."""
    sid = str(session_id or "")
    unasked = sid.startswith("cron_") or _taint.opened_by(sid)[1] == "cron"
    if not unasked:
        return False
    try:
        return bool(from_env().privacy_quiet_house)
    except Exception:  # noqa: BLE001 - any malformed setting; the switch is unreadable
        _LOGGER.warning("weather: the settings could not be read; a scheduled forecast stays quiet", exc_info=True)
        return True


def handle_weather_forecast(args: dict, fetch: Fetch = _get, house: HouseConfig = _house_config, **kwargs: Any) -> str:
    args = args if isinstance(args, dict) else {}
    if _quiet_and_unasked(kwargs.get("session_id")):
        return json.dumps({"ok": False, "quiet": True, "said": QUIET_SAID})
    out = forecast(args, fetch, house)
    if not out.get("ok"):
        return json.dumps(out)
    # Shared, not sent to the asker alone: a forecast asked for by voice runs in the voice client's
    # own session, and the chart belongs on the screen the owner is looking at (T-1471).
    out["projected"], problem = projections.send(
        hud._state_key(kwargs.get("session_id")), PROJECTION_ID, "line", chart(out))
    if problem:
        _LOGGER.warning("weather: the chart was refused (%s); the numbers still answer", problem)
    return json.dumps(out)
