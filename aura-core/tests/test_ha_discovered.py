"""AURA lists what Home Assistant discovered and finishes a confirm-only one (T-1543).

HA finds devices on the network itself (zeroconf, SSDP, DHCP, Bluetooth) and holds each as a
config flow in progress, waiting for someone on HA's own page to press Submit. The owner never
opens that page (T-1523), so :class:`HaFlows` reads the list and, asked to, submits a flow whose
next step is a bare confirmation. A step that wants input (a pairing code, a password) is named
and left alone. Run against :mod:`fake_ha`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.integrations.home_assistant.flows import HaFlows, HaFlowRefused  # noqa: E402
from fake_ha import URL, FakeHA  # noqa: E402

HUE = {"flow_id": "0f1e2d3c", "handler": "hue", "step_id": "link",
       "context": {"source": "zeroconf", "title_placeholders": {"name": "Hue Bridge"}}}
CAST = {"flow_id": "a1b2c3d4", "handler": "cast", "step_id": "confirm",
        "context": {"source": "zeroconf", "title_placeholders": {"name": "Living Room TV"}}}
MINE = {"flow_id": "99887766", "handler": "mqtt", "step_id": "user", "context": {"source": "user"}}


@pytest.fixture
def ha() -> FakeHA:
    fake = FakeHA()
    fake.long_lived.add("tok")
    fake.flows = [HUE, CAST, MINE]
    fake.flow_steps = {
        "0f1e2d3c": {"type": "form", "flow_id": "0f1e2d3c", "handler": "hue", "step_id": "link",
                     "data_schema": [{"name": "api_key", "type": "string"}]},
        "a1b2c3d4": {"type": "form", "flow_id": "a1b2c3d4", "handler": "cast", "step_id": "confirm",
                     "data_schema": []},
        "99887766": {"type": "form", "flow_id": "99887766", "handler": "mqtt", "step_id": "user",
                     "data_schema": []},
    }
    return fake


def _flows(ha: FakeHA, token: str = "tok") -> HaFlows:
    return HaFlows(URL, token=lambda: token, transport=httpx.MockTransport(ha.http), connect=ha.connect)


async def test_ha_discovered_lists_what_home_assistant_found_and_not_a_flow_someone_started(ha):
    found = await _flows(ha).discovered()
    assert [f["flow_id"] for f in found] == ["0f1e2d3c", "a1b2c3d4"]
    assert found[1] == {"flow_id": "a1b2c3d4", "integration": "cast", "found_by": "zeroconf",
                        "name": "Living Room TV", "step": "confirm"}


async def test_ha_discovered_confirms_a_confirm_only_flow_and_the_integration_is_added(ha):
    out = await _flows(ha).confirm("a1b2c3d4", "cast")
    assert out == {"ok": True, "integration": "cast", "title": "Living Room TV"}
    assert ha.flow_posts == [("a1b2c3d4", {})]
    assert any(e["domain"] == "cast" for e in ha.entries)


async def test_ha_discovered_a_step_that_wants_input_is_named_and_left_alone(ha):
    with pytest.raises(HaFlowRefused, match="api_key"):
        await _flows(ha).confirm("0f1e2d3c", "hue")
    assert ha.flow_posts == []


async def test_ha_discovered_only_a_discovered_flow_is_confirmed(ha):
    # A flow someone started on HA's page is theirs; an id HA does not hold is not guessed at.
    for flow_id in ("99887766", "deadbeef"):
        with pytest.raises(HaFlowRefused, match="not one Home Assistant discovered"):
            await _flows(ha).confirm(flow_id, "cast")
    assert ha.flow_posts == []


@pytest.mark.parametrize("flow_id", ["", "../entry", "a" * 65, "a b"])
async def test_ha_discovered_a_flow_id_that_is_not_one_is_refused_before_home_assistant(ha, flow_id):
    with pytest.raises(HaFlowRefused, match="not a flow id"):
        await _flows(ha).confirm(flow_id, "cast")
    assert ha.flow_posts == []


async def test_ha_discovered_a_flow_home_assistant_aborts_says_why(ha):
    ha.flow_results["a1b2c3d4"] = {"type": "abort", "reason": "already_configured"}
    with pytest.raises(HaFlowRefused, match="already_configured"):
        await _flows(ha).confirm("a1b2c3d4", "cast")


async def test_ha_discovered_no_token_or_no_answer_says_so(ha):
    with pytest.raises(HaFlowRefused, match="no token"):
        await _flows(ha, token="").discovered()
    ha.up = False
    with pytest.raises(HaFlowRefused, match="did not answer"):
        await _flows(ha).discovered()


async def test_ha_discovered_the_house_services_list_and_confirm_and_answer_refusals(tmp_path, ha):
    import asyncio

    from aura.core import Jarvis
    from aura.integrations import domains as domains_integration
    from aura.integrations import home_assistant

    jarvis = Jarvis(tmp_path)
    for registry in (jarvis.areas, jarvis.devices, jarvis.entities):
        await registry.load()
    await domains_integration.async_setup(jarvis, None)
    jarvis.data["home_assistant"] = {"transport": httpx.MockTransport(ha.http), "connect": ha.connect}
    assert await home_assistant.async_setup(jarvis, {"url": URL, "token": "tok"})
    try:
        listed = await jarvis.async_call_service("home_assistant", "discovered", {}, return_response=True)
        assert listed["ok"] is True and [f["integration"] for f in listed["discovered"]] == ["hue", "cast"]
        added = await jarvis.async_call_service("home_assistant", "confirm_discovered",
                                                {"flow_id": "a1b2c3d4", "integration": "cast"}, return_response=True)
        assert added == {"ok": True, "integration": "cast", "title": "Living Room TV"}
        left = await jarvis.async_call_service("home_assistant", "confirm_discovered",
                                               {"flow_id": "0f1e2d3c", "integration": "hue"}, return_response=True)
        assert left["ok"] is False and "api_key" in left["error"]
    finally:
        for task in list(asyncio.all_tasks()):
            if task is not asyncio.current_task():
                task.cancel()


async def test_ha_discovered_the_integration_named_on_the_card_must_be_the_flow_s(ha):
    """T-1549: the owner's yes is for the integration the card named; a flow of another one
    (or an integration that is not a name) is refused before anything is submitted."""
    for integration in ("hue", "", "../cast", "Cast"):
        with pytest.raises(HaFlowRefused):
            await _flows(ha).confirm("a1b2c3d4", integration)
    assert ha.flow_posts == []


def test_ha_discovered_an_automation_that_adds_a_discovered_device_is_held():
    """T-1549: an automation reaching confirm_discovered would add a device with no one asked."""
    from aura.automation.reach import gated_reach
    from aura.const import GATED_SERVICES

    assert "home_assistant.confirm_discovered" in GATED_SERVICES
    assert gated_reach([{"service": "home_assistant.confirm_discovered"}])
