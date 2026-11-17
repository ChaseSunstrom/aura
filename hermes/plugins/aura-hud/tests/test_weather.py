"""weather_forecast: the forecast as numbers in one call, and its chart on the session's HUD.

The owner, 2026-11-08: asked the temperature, and Jarvis searched the web, read two
pages and never answered; "it didn't popup charts ... for example with a weather chart".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-hud")
hud = plugin.hud
projections = plugin.projections
weather = plugin.weather

HOUSE = {"latitude": 41.881832, "longitude": -87.623177, "unit_system": "imperial"}


def _hours(start_day: str, temps: list[float]) -> dict:
    times = [f"{start_day}T{h:02d}:00" for h in range(24)] + [f"2026-11-09T{h:02d}:00" for h in range(24)]
    return {"time": times, "temperature_2m": temps + temps,
            "precipitation_probability": [10] * 48, "weather_code": [3] * 48}


TEMPS = [30.0 + h for h in range(24)]
BODY = {
    "current": {"time": "2026-11-08T16:15", "temperature_2m": 41.2, "apparent_temperature": 36.0,
                "relative_humidity_2m": 80, "wind_speed_10m": 9.1, "precipitation": 0.0, "weather_code": 3},
    "hourly": _hours("2026-11-08", TEMPS),
    "daily": {"time": ["2026-11-08", "2026-11-09", "2026-11-10"], "temperature_2m_max": [45.0, 50.0, 47.0],
              "temperature_2m_min": [33.0, 35.0, 31.0], "precipitation_probability_max": [40, 5, 70],
              "weather_code": [61, 1, 95], "sunrise": ["2026-11-08T06:30"] * 3, "sunset": ["2026-11-08T16:35"] * 3},
}


class Api:
    def __init__(self, geocode: dict | None = None):
        self.asked: list[tuple[str, dict]] = []
        self.geocode = geocode if geocode is not None else {"results": [
            {"name": "Oslo", "country": "Norway", "admin1": "Oslo", "latitude": 59.91, "longitude": 10.75}]}

    def __call__(self, url: str, params: dict) -> dict:
        self.asked.append((url, params))
        return self.geocode if url == weather.GEOCODE else BODY


class Recorder:
    def __init__(self):
        self.frames: list[tuple[str, str, dict]] = []

    def __call__(self, state_key: str, event: str, payload: dict) -> bool:
        self.frames.append((state_key, event, payload))
        return True


@pytest.fixture
def rec(monkeypatch) -> Recorder:
    r = Recorder()
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=r))
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    return r


def _call(args: dict, api: Api, house=lambda: HOUSE) -> dict:
    return json.loads(weather.handle_weather_forecast(args, fetch=api, house=house, session_id="s"))


def test_the_house_point_rounded_in_the_house_units(rec):
    api = Api()
    out = _call({}, api)
    url, params = api.asked[0]
    assert url == weather.FORECAST
    # The public API sees a point about a kilometre wide, not the house's door.
    assert (params["latitude"], params["longitude"]) == (41.9, -87.6)
    assert params["temperature_unit"] == "fahrenheit" and params["wind_speed_unit"] == "mph"
    assert out["ok"] and out["place"] == "home"
    assert out["units"]["temperature"] == "°F"
    assert out["now"]["temperature"] == 41.2 and out["now"]["sky"] == "overcast"
    assert out["days"][0] == {"date": "2026-11-08", "high": 45.0, "low": 33.0, "rain_chance": 40,
                              "sky": "light rain", "sunrise": "06:30", "sunset": "16:35"}
    # From the current hour on, 24 of them.
    assert len(out["hours"]) == 24 and out["hours"][0]["time"] == "16:00"
    assert out["hours"][0]["temperature"] == 46.0
    assert out["said"].startswith("41°F and overcast at home")


def test_metric_when_the_house_says_metric_or_says_nothing(rec):
    for config in ({**HOUSE, "unit_system": "metric"}, {**HOUSE, "unit_system": {"temperature": "°C"}}):
        api = Api()
        out = _call({}, api, house=lambda c=config: c)
        assert api.asked[0][1]["temperature_unit"] == "celsius"
        assert out["units"]["temperature"] == "°C"


def test_a_named_place_is_geocoded_first(rec):
    api = Api()
    out = _call({"place": "Oslo"}, api)
    assert api.asked[0] == (weather.GEOCODE, {"name": "Oslo", "count": 1, "language": "en", "format": "json"})
    assert (api.asked[1][1]["latitude"], api.asked[1][1]["longitude"]) == (59.9, 10.8)
    assert out["place"] == "Oslo, Norway"
    # Still in the owner's units: they asked in theirs.
    assert api.asked[1][1]["temperature_unit"] == "fahrenheit"


def test_a_point_given_is_used_as_is(rec):
    api = Api()
    out = _call({"lat": 42.0, "lon": -87.7}, api)
    assert len(api.asked) == 1 and (api.asked[0][1]["latitude"], api.asked[0][1]["longitude"]) == (42.0, -87.7)
    assert out["place"] == "42, -87.7"
    # The model sends numbers as strings at times; they still count.
    strings = Api()
    _call({"lat": "42.0", "lon": "-87.7", "days": "3"}, strings)
    assert strings.asked[0][1]["latitude"] == 42.0 and strings.asked[0][1]["forecast_days"] == 3


def test_it_projects_the_next_24_hours_on_the_asking_session(rec):
    _call({}, Api())
    [(key, event, payload)] = rec.frames
    assert key == "s" and event == projections.EVENT
    assert payload["id"] == "weather" and payload["type"] == "line" and payload["origin"] == projections.ORIGIN
    data = payload["data"]
    assert projections.validate("line", data) is None
    assert data["unit"] == "°F" and data["title"] == "Next 24 hours · home"
    assert data["x"][0] == "4 PM" and len(data["x"]) == 24
    assert data["series"][0]["points"][0] == 46.0
    # A temperature's zero is arbitrary: an area chart pinned the axis at 0 °F and flattened the day.
    assert "area" not in data


def test_it_projects_highs_and_lows_when_asked_for_days(rec):
    out = _call({"days": 3}, Api())
    [(_, _, payload)] = rec.frames
    data = payload["data"]
    assert projections.validate("line", data) is None
    assert [s["name"] for s in data["series"]] == ["High", "Low"]
    assert data["x"] == ["Sun 8", "Mon 9", "Tue 10"] and data["series"][1]["points"] == [33.0, 35.0, 31.0]
    assert len(out["days"]) == 3


def test_a_failure_is_one_answer_and_projects_nothing(rec):
    def down(url, params):
        raise OSError("unreachable")

    out = json.loads(weather.handle_weather_forecast({}, fetch=down, house=lambda: HOUSE, session_id="s"))
    assert out["ok"] is False and "Open-Meteo" in out["error"]
    assert rec.frames == []
    nowhere = json.loads(weather.handle_weather_forecast({}, fetch=Api(), house=lambda: {}, session_id="s"))
    assert nowhere["ok"] is False and "place" in nowhere["error"]
    unknown = _call({"place": "Nowhereville"}, Api(geocode={}))
    odd = json.loads(weather.handle_weather_forecast({}, fetch=lambda u, p: {"current": {}}, house=lambda: HOUSE,
                                                    session_id="s"))
    assert odd["ok"] is False and "shape" in odd["error"] and rec.frames == []
    assert unknown["ok"] is False and "Nowhereville" in unknown["error"]


def test_the_house_being_unreachable_still_answers_a_named_place(rec):
    def gone():
        raise OSError("no house")

    out = _call({"place": "Oslo"}, Api(), house=gone)
    assert out["ok"] and out["units"]["temperature"] == "°C"


QUIET = "The house is set to make no calls out on its own, so there is no forecast."


def _quiet(monkeypatch, on: bool) -> None:
    monkeypatch.setenv("AURA_QUIET_HOUSE", "true" if on else "false")


def _as(session_id: str, api: Api) -> dict:
    return json.loads(weather.handle_weather_forecast({}, fetch=api, house=lambda: HOUSE, session_id=session_id))


def test_privacy_quiet_house_makes_a_cron_started_forecast_no_request(rec, monkeypatch):
    """T-0837: the 07:00 brief is the house calling out on its own; with the switch on it asks nothing."""
    _quiet(monkeypatch, True)
    api = Api()
    out = _as("cron_ab12cd34_20261111_070000", api)
    assert api.asked == [] and rec.frames == []
    assert out == {"ok": False, "quiet": True, "said": QUIET}


def test_privacy_quiet_house_still_answers_a_person_who_asks(rec, monkeypatch):
    _quiet(monkeypatch, True)
    api = Api()
    out = _as("20261111_070500_abc123", api)
    assert out["ok"] and api.asked and rec.frames


def test_privacy_quiet_house_off_a_cron_turn_fetches_as_before(rec, monkeypatch):
    _quiet(monkeypatch, False)
    api = Api()
    assert _as("cron_ab12cd34_20261111_070000", api)["ok"] and api.asked


def test_privacy_quiet_house_knows_a_cron_turn_by_the_platform_that_opened_it(rec, monkeypatch):
    """The scheduler's own session id is the first signal; the opening platform is the second."""
    _quiet(monkeypatch, True)
    from _shared import _taint

    # Fresh module tables for this test only; monkeypatch puts the real ones back.
    monkeypatch.setattr(_taint, "_OPENED", {})
    monkeypatch.setattr(_taint, "_TURN", {})
    _taint.turn_start(session_id="s-brief", user_message="Do the 07:00 brief", platform="cron")
    api = Api()
    assert _as("s-brief", api)["quiet"] is True and api.asked == []
    _taint.turn_start(session_id="s-chat", user_message="weather?", platform="tui")
    assert _as("s-chat", api)["ok"] and api.asked


def test_privacy_quiet_house_unreadable_settings_never_stop_a_person_asking(rec, monkeypatch):
    """from_env raises on any malformed setting; that must not cost a person their forecast, and a
    scheduled one, unable to read the switch, stays quiet."""
    def broken():
        raise ValueError("a setting that will not parse")

    monkeypatch.setattr(weather, "from_env", broken)
    api = Api()
    assert _as("20261111_070500_abc123", api)["ok"] and api.asked
    api = Api()
    assert _as("cron_ab12cd34_20261111_070000", api) == {"ok": False, "quiet": True, "said": QUIET}
    assert api.asked == []


def test_the_forecast_is_asked_for_a_ten_kilometre_cell_not_the_house():
    """T-0765: the house's own coordinates went to Open-Meteo to six decimals, the house's front
    door. A forecast is the same across a 0.1 degree cell (about 10 km), so that is all that leaves."""
    asked: list[tuple[str, dict]] = []

    def fetch(url: str, params: dict) -> dict:
        asked.append((url, params))
        return BODY

    out = weather.forecast({}, fetch=fetch, house=lambda: HOUSE)
    assert out.get("ok", True) is not False, out
    _, params = asked[-1]
    assert (params["latitude"], params["longitude"]) == (41.9, -87.6)
    weather.forecast({"lat": 41.881832, "lon": -87.623177}, fetch=fetch, house=lambda: {})  # the car's fix
    assert (asked[-1][1]["latitude"], asked[-1][1]["longitude"]) == (41.9, -87.6)


def test_the_chart_reaches_the_owners_other_screens_and_a_members_stays_put(monkeypatch):
    """T-1471: a forecast asked for by voice runs in the voice client's own session; sent only
    there, the chart never reached the console being looked at (the protocol's T-0785 rule)."""
    r = Recorder()
    monkeypatch.setattr(hud, "STORE", hud.HudStore(
        emitter=r, live=lambda: ["voice", "console", "member"], owner=lambda sid: sid in ("voice", "console")))
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    out = json.loads(weather.handle_weather_forecast({}, fetch=Api(), house=lambda: HOUSE, session_id="voice"))
    assert out["projected"] is True
    assert [key for key, _, _ in r.frames] == ["voice", "console"]
    del r.frames[:]
    json.loads(weather.handle_weather_forecast({}, fetch=Api(), house=lambda: HOUSE, session_id="member"))
    assert [key for key, _, _ in r.frames] == ["member"]
