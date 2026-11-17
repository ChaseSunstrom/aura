"""departure: the leaving sweep, the empty-house signal, the forecast prep note."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.core import Jarvis  # noqa: E402
from aura.integrations import departure  # noqa: E402


async def _house(tmp_path, options=None, minute=12 * 60):
    j = Jarvis(tmp_path)
    sent: list[dict] = []

    async def notify(call):
        sent.append(dict(call.data))

    j.services.register("companion", "notify", notify)
    j.data["departure_clock"] = lambda: 1000.0
    j.data["departure_local_minutes"] = lambda now: minute
    await departure.async_setup(j, {"enabled": True, **(options or {})})
    j.states.set("person.sir", "home")
    j.states.set("light.hall", "on")
    j.states.set("lock.front", "unlocked")
    j.states.set("cover.garage_door", "open")
    j.states.set("light.den", "off")
    await j.async_block_till_done()
    return j, sent


@pytest.mark.asyncio
async def test_departure_names_what_was_left_on_once(tmp_path):
    j, sent = await _house(tmp_path)
    j.states.set("person.sir", "not_home")
    await j.async_block_till_done()
    assert len(sent) == 1
    for name in ("hall", "front", "garage door"):
        assert name in sent[0]["message"]
    assert "den" not in sent[0]["message"]
    j.states.set("person.sir", "not_home", {"x": 1})  # no new departure
    await j.async_block_till_done()
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_another_person_home_means_nobody_left(tmp_path):
    j, sent = await _house(tmp_path)
    j.states.set("person.guest", "home")
    j.states.set("person.sir", "not_home")
    await j.async_block_till_done()
    assert sent == []


@pytest.mark.asyncio
async def test_quiet_hours_hold_the_sweep(tmp_path):
    j, sent = await _house(tmp_path, {"quiet_hours": ["23:00", "07:00"]}, minute=2 * 60)
    j.states.set("person.sir", "not_home")
    await j.async_block_till_done()
    assert sent == []


@pytest.mark.asyncio
async def test_a_clean_house_says_nothing(tmp_path):
    j, sent = await _house(tmp_path)
    j.states.set("light.hall", "off")
    j.states.set("lock.front", "locked")
    j.states.set("cover.garage_door", "closed")
    j.states.set("person.sir", "not_home")
    await j.async_block_till_done()
    assert sent == []


@pytest.mark.asyncio
async def test_a_door_while_nobody_home_alerts_and_is_debounced(tmp_path):
    j, sent = await _house(tmp_path)
    j.states.set("lock.front", "locked")
    j.states.set("person.sir", "not_home")
    await j.async_block_till_done()
    sent.clear()
    door = {"device_class": "door"}
    j.states.set("binary_sensor.back_door", "off", door)
    j.states.set("binary_sensor.back_door", "on", door)
    j.states.set("binary_sensor.back_door", "off", door)
    j.states.set("binary_sensor.back_door", "on", door)
    await j.async_block_till_done()
    assert len(sent) == 1 and "Back door" in sent[0]["message"]
    assert sent[0]["importance"] == "high"
    j.states.set("lock.front", "unlocked")  # a different entity still alerts
    await j.async_block_till_done()
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_no_alert_when_someone_is_home(tmp_path):
    j, sent = await _house(tmp_path)
    j.states.set("binary_sensor.motion", "off", {"device_class": "motion"})
    j.states.set("binary_sensor.motion", "on", {"device_class": "motion"})
    await j.async_block_till_done()
    assert sent == []


@pytest.mark.asyncio
async def test_freeze_forecast_sets_the_prep_note(tmp_path):
    j, _ = await _house(tmp_path)
    j.states.set(
        "weather.home", "cloudy",
        {"temperature_unit": "°F", "forecast": [{"templow": 50}, {"templow": 28, "temperature": 40}]},
    )
    await j.async_block_till_done()
    assert "freez" in j.states.get("sensor.home_prep").state.lower()
    j.states.set(
        "weather.home", "cloudy",
        {"temperature_unit": "°C", "forecast": [{}, {"templow": 8, "temperature": 20}]},
    )
    await j.async_block_till_done()
    assert j.states.get("sensor.home_prep").state == "none"


@pytest.mark.asyncio
async def test_heat_and_storm_notes(tmp_path):
    j, _ = await _house(tmp_path)
    hot = {"temperature_unit": "°C", "forecast": [{"temperature": 38, "templow": 24}]}
    j.states.set("weather.home", "sunny", hot)
    await j.async_block_till_done()
    assert "heat" in j.states.get("sensor.home_prep").state.lower()
    wet = {"temperature_unit": "°C", "forecast": [{"condition": "lightning-rainy", "temperature": 20, "templow": 12}]}
    j.states.set("weather.home", "sunny", wet)
    await j.async_block_till_done()
    assert "storm" in j.states.get("sensor.home_prep").state.lower()
