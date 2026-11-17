"""A routine the Hermes plugin compiles is an automation this house accepts (T-0185).

The fixture is tests/contracts/compiled-routine.json; the plugin's suite proves it emits exactly
that automation, this proves jarvis-core's own validator takes it, so the two cannot drift.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.automation.authored import validate  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import automation as automation_integration  # noqa: E402
from test_automation import FakeClock, FakeLight, _settle  # noqa: E402

FIX = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "compiled-routine.json")
                 .read_text(encoding="utf-8"))


def test_compiled_routine_contract_the_house_accepts_the_plugins_automation():
    out = validate(FIX["automation"])
    assert out["trigger"] == FIX["automation"]["trigger"]
    assert out["action"][0]["service"] == FIX["call"]["service"]


class OneTick(FakeClock):
    """Reaches the trigger time once, then waits: FakeClock alone runs on day after day."""

    async def sleep(self, seconds):
        if self.slept:
            await asyncio.Event().wait()
        await super().sleep(seconds)


@pytest.mark.parametrize("day, fires", [(datetime(2024, 1, 1, 19, 29, 59), True),    # a Monday
                                        (datetime(2024, 1, 2, 19, 29, 59), False)])  # a Tuesday
async def test_compiled_routine_fires_in_the_house_engine_on_its_time_and_days(tmp_path, day, fires):
    jarvis = Jarvis(tmp_path)
    light = FakeLight(jarvis)
    jarvis.data["automation_clock"] = OneTick(day)
    config = [validate(FIX["automation"])]
    jarvis.config = {"automation": config}
    await automation_integration.async_setup(jarvis, config)
    for _ in range(5):
        await asyncio.sleep(0)
    await _settle(jarvis)
    assert light.actions == (["turn_on"] if fires else [])
    if fires:
        assert light.data_for()["entity_id"] == FIX["call"]["entity_ids"]
        assert light.data_for()["brightness_pct"] == FIX["call"]["data"]["brightness_pct"]


# --- the away-lock (T-0332) --------------------------------------------------------------------
AWAY = FIX["away_lock"]


def test_compiled_routine_contract_the_house_accepts_the_away_lock():
    out = validate(AWAY["automation"])
    assert out["trigger"] == AWAY["automation"]["trigger"] and out["mode"] == "restart"


@pytest.mark.parametrize("answer, told", [
    ({"status": "ok", "result": {"locked": True}}, False),
    ({"status": "error", "error": "in use: the last input was 42 s ago, so it was not locked"}, True),
])
async def test_compiled_routine_the_away_lock_runs_in_the_house_engine(tmp_path, answer, told):
    jarvis = Jarvis(tmp_path)
    ran, notices, logged = [], [], []

    async def run(call):
        ran.append(dict(call.data))
        return answer

    async def notify(call):
        notices.append(dict(call.data))
        return {"status": "sent"}

    async def log(call):
        logged.append(dict(call.data))

    jarvis.services.register("device_control", "run", run, supports_response=True)
    jarvis.services.register("companion", "notify", notify, supports_response=True)
    jarvis.services.register("logbook", "log", log)
    auto = json.loads(json.dumps(validate(AWAY["automation"])))
    auto["action"][1]["timeout"] = 0.2  # the fixture's five minutes, shortened for the test
    jarvis.config = {"automation": [auto]}
    await automation_integration.async_setup(jarvis, [auto])
    await jarvis.bus.async_fire("jarvis_device_event", {
        "device_id": "phone-1", "event": "routine_place", "trust": "trusted",
        "data": {"place": "p-home", "edge": "leave"}})
    await asyncio.sleep(0.4)
    await _settle(jarvis)
    assert logged and "unless back" in logged[0]["message"]
    assert ran == [AWAY["automation"]["action"][3]["data"]]
    assert bool(notices) == told
    if told:
        assert "in use" in notices[0]["message"] and notices[0]["title"] == "Your PC was not locked"


@pytest.mark.parametrize("sender, edge", [("pc-1", "leave"), ("pc-1", "arrive")])
async def test_compiled_routine_the_away_lock_hears_only_the_owners_phone(tmp_path, sender, edge):
    """A leave from another paired device starts nothing; an arrive from it cancels nothing."""
    jarvis = Jarvis(tmp_path)
    ran = []

    async def run(call):
        ran.append(dict(call.data))
        return {"status": "ok"}

    async def quiet(call):
        return None

    jarvis.services.register("device_control", "run", run, supports_response=True)
    jarvis.services.register("companion", "notify", quiet, supports_response=True)
    jarvis.services.register("logbook", "log", quiet)
    auto = json.loads(json.dumps(validate(AWAY["automation"])))
    auto["action"][1]["timeout"] = 0.2
    jarvis.config = {"automation": [auto]}
    await automation_integration.async_setup(jarvis, [auto])

    async def report(device, what):
        await jarvis.bus.async_fire("jarvis_device_event", {
            "device_id": device, "event": "routine_place", "trust": "trusted",
            "data": {"place": "p-home", "edge": what}})

    if edge == "leave":
        await report(sender, "leave")
    else:
        await report("phone-1", "leave")
        await asyncio.sleep(0.05)
        await report(sender, "arrive")
    await asyncio.sleep(0.4)
    await _settle(jarvis)
    assert ran == ([] if edge == "leave" else [AWAY["automation"]["action"][3]["data"]])
