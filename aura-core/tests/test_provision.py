"""AURA sets its Home Assistant up itself (T-1523): onboarding, a long-lived token, a renewal.

Run against :mod:`fake_ha`, which copies what Home Assistant 2026.11.2 answered on a throwaway
instance.
"""
from __future__ import annotations

import json
import logging
import stat
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.core import Jarvis  # noqa: E402
from aura.integrations.home_assistant import setup as ha_setup  # noqa: E402
from fake_ha import URL, FakeHA  # noqa: E402


@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path / "config")


def _setup(jarvis, ha: FakeHA, token: str = "", bind: str = "") -> ha_setup.HaSetup:
    return ha_setup.HaSetup(jarvis, URL, token=token, transport=httpx.MockTransport(ha.http),
                            connect=ha.connect, bind=bind)


async def test_provision_a_fresh_home_assistant_is_onboarded_and_gives_a_long_lived_token(jarvis, caplog):
    ha = FakeHA()
    caplog.set_level(logging.DEBUG)
    setup = _setup(jarvis, ha)

    out = await setup.ensure()

    assert out["state"] == "provisioned", out
    assert all(ha.steps.values()), "every onboarding step is done, so HA's own page shows no wizard"
    assert setup.token in ha.long_lived
    store = setup.sealed_path
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert setup.token.encode() not in store.read_bytes(), "the token is sealed at rest"
    secrets = [setup.token, *ha.users.values()]
    shown = caplog.text + json.dumps(out) + json.dumps(setup.status())
    assert not any(s in shown for s in secrets)


async def test_provision_drops_what_onboarding_added_that_calls_out_but_not_the_owners(jarvis):
    """HA's core_config step sets up radio_browser and google_translate by itself; both reach the
    internet with no setting saying so (T-0343). An entry the owner added later is theirs."""
    ha = FakeHA()
    ha.entries.append({"entry_id": "e4", "domain": "radio_browser", "source": "user"})

    await _setup(jarvis, ha).ensure()
    await _setup(jarvis, ha).ensure()  # the step may add them after the first run: every run checks

    assert sorted((e["domain"], e["source"]) for e in ha.entries) == [
        ("radio_browser", "user"), ("shopping_list", "onboarding")]


async def test_provision_a_second_run_adopts_the_token(jarvis):
    ha = FakeHA()
    await _setup(jarvis, ha).ensure()

    again = _setup(jarvis, ha)  # a restart: the sealed file is read back
    out = await again.ensure()

    assert out["state"] == "adopted", out
    assert ha.minted == 1 and len(ha.users) == 1


async def test_provision_a_refused_token_is_renewed_with_the_login_aura_made(jarvis):
    ha = FakeHA()
    setup = _setup(jarvis, ha)
    await setup.ensure()
    ha.long_lived.clear()  # the token was revoked on HA's profile page

    out = await setup.ensure()

    assert out["state"] == "renewed", out
    assert ha.minted == 2 and setup.token == "long-lived-2"


async def test_provision_a_home_assistant_someone_else_onboarded_is_left_alone(jarvis):
    ha = FakeHA()
    ha.steps = dict.fromkeys(ha.steps, True)
    ha.users["owner"] = "theirs"

    out = await _setup(jarvis, ha).ensure()

    assert out["state"] == "owned_elsewhere", out
    assert "token" in out["detail"]
    assert ha.minted == 0


async def test_provision_a_token_the_owner_set_is_adopted_and_never_replaced(jarvis):
    ha = FakeHA()
    ha.steps = dict.fromkeys(ha.steps, True)
    ha.long_lived.add("owner-made")

    setup = _setup(jarvis, ha, token="owner-made")
    out = await setup.ensure()

    assert out["state"] == "adopted" and setup.token == "owner-made"
    assert not setup.sealed_path.exists(), "a token from settings is not copied into the store"
    assert len(ha.entries) == 3, "an HA someone else onboarded keeps what its onboarding set up"


async def test_provision_home_assistant_down_is_reported_not_raised(jarvis):
    ha = FakeHA()
    ha.up = False

    out = await _setup(jarvis, ha).ensure()

    assert out["state"] == "unreachable"
    assert not _setup(jarvis, ha).sealed_path.exists()


async def test_provision_the_integration_onboards_mirrors_and_reports_without_a_secret(tmp_path):
    import asyncio

    from aura.integrations import domains as domains_integration
    from aura.integrations import home_assistant

    jarvis = Jarvis(tmp_path)
    for registry in (jarvis.areas, jarvis.devices, jarvis.entities):
        await registry.load()
    await domains_integration.async_setup(jarvis, None)
    ha = FakeHA()
    jarvis.data["home_assistant"] = {"transport": httpx.MockTransport(ha.http), "connect": ha.connect}

    assert await home_assistant.async_setup(jarvis, {"url": URL})
    for _ in range(200):
        if jarvis.states.get("light.kitchen") is not None:
            break
        await asyncio.sleep(0.01)
    status = await jarvis.async_call_service("home_assistant", "status", {}, return_response=True)

    assert status["state"] == "provisioned" and status["has_token"] is True
    assert status["bridge"]["connected"] is True and status["bridge"]["by_domain"] == {"light": 1}
    shown = json.dumps(status)
    assert not any(s in shown for s in [*ha.long_lived, *ha.users.values()])
    for task in list(asyncio.all_tasks()):
        if task is not asyncio.current_task():
            task.cancel()


async def test_provision_the_keeper_tries_again_soon_when_home_assistant_was_still_starting(tmp_path):
    import asyncio

    from aura.integrations import domains as domains_integration
    from aura.integrations import home_assistant

    jarvis = Jarvis(tmp_path)
    for registry in (jarvis.areas, jarvis.devices, jarvis.entities):
        await registry.load()
    await domains_integration.async_setup(jarvis, None)
    ha = FakeHA()
    ha.up = False  # a fresh stack: HA takes a minute to answer
    jarvis.data["home_assistant"] = {"transport": httpx.MockTransport(ha.http), "connect": ha.connect}

    assert await home_assistant.async_setup(jarvis, {"url": URL, "interval": 600, "retry": 0.05})
    await asyncio.sleep(0.02)
    ha.up = True
    for _ in range(100):
        if jarvis.data["home_assistant"]["setup"].status()["state"] == "provisioned":
            break
        await asyncio.sleep(0.01)

    assert jarvis.data["home_assistant"]["setup"].status()["state"] == "provisioned"
    for task in list(asyncio.all_tasks()):
        if task is not asyncio.current_task():
            task.cancel()


@pytest.mark.parametrize(("domain", "service", "entity_id"), [
    ("lock", "unlock", "lock.front_door"),
    ("notify", "notify", None),
    ("script", "turn_on", "script.anything"),
    ("scene", "turn_on", "scene.evening"),
    ("automation", "trigger", "automation.any"),
    ("alarm_control_panel", "alarm_disarm", "alarm_control_panel.house"),
    ("shell_command", "anything", None),
    ("rest_command", "anything", None),
    ("homeassistant", "turn_on", "lock.front_door"),
    ("homeassistant", "restart", None),
    ("light", "turn_on", "lock.front_door"),
    ("light", "turn_on", "LOCK.Front_Door"),  # HA lowercases ids: checked as HA will read them
    ("light", "turn_on", " lock.front_door"),
    ("LOCK", "unlock", None),
    ("light", "turn_on", "ALL"),
    ("zwave_js", "set_config_parameter", None),  # an integration's own verb: not on the allow-list
    ("tesla_custom", "remote_start", None),
    ("button", "press", "button.hub_restart"),  # a config entity HA has; the mirror skips it
    ("light", "turn_on", None),  # no target: HA's "every entity" is not this service's to say
])
async def test_provision_call_service_refuses_what_the_house_holds_for_a_human(tmp_path, domain, service, entity_id):
    """``home_assistant.call_service`` reaches any HA service by name, and a routine reaches it with
    no identity. Without this an automation could unlock a door, message someone or run a script
    through HA while ``lock.unlock`` itself is held for a human (T-1523 review)."""
    import asyncio

    from aura.integrations import domains as domains_integration
    from aura.integrations import home_assistant

    jarvis = Jarvis(tmp_path)
    for registry in (jarvis.areas, jarvis.devices, jarvis.entities):
        await registry.load()
    await domains_integration.async_setup(jarvis, None)
    ha = FakeHA()
    jarvis.data["home_assistant"] = {"transport": httpx.MockTransport(ha.http), "connect": ha.connect}
    assert await home_assistant.async_setup(jarvis, {"url": URL})
    for _ in range(200):
        if jarvis.data["home_assistant"]["bridge"].connected:
            break
        await asyncio.sleep(0.01)
    data = {"domain": domain, "service": service}
    if entity_id:
        data["entity_id"] = entity_id
    try:
        with pytest.raises(home_assistant.HaRefused):
            await jarvis.async_call_service("home_assistant", "call_service", data, return_response=True)
        with pytest.raises(home_assistant.HaRefused):  # the same id inside the service data
            await jarvis.async_call_service("home_assistant", "call_service",
                                            {"domain": domain, "service": service,
                                             "data": {"entity_id": [entity_id or "lock.x"]}},
                                            return_response=True)
        assert not [c for c in ha.calls if c.get("type") == "call_service"]
        ok = await jarvis.async_call_service("home_assistant", "call_service",
                                             {"domain": "light", "service": "turn_on",
                                              "entity_id": "light.kitchen"}, return_response=True)
        assert ok["ok"] is True, "an ordinary call still goes through"
        sent = [c for c in ha.calls if c.get("type") == "call_service"]
        assert sent[-1]["target"] == {"entity_id": ["light.kitchen"]}, "the checked ids are what HA gets"
    finally:
        for task in list(asyncio.all_tasks()):
            if task is not asyncio.current_task():
                task.cancel()


async def test_provision_an_home_assistant_someone_else_onboarded_is_logged_as_a_warning(jarvis, caplog):
    """On a page reachable from the LAN, someone else onboarding HA first is the race the bind
    default exists to stop; when it happens the log says so at warning level, not info."""
    ha = FakeHA()
    ha.steps = dict.fromkeys(ha.steps, True)
    ha.users["owner"] = "theirs"
    caplog.set_level(logging.INFO)

    await _setup(jarvis, ha).ensure()

    assert any(r.levelno >= logging.WARNING and "owned_elsewhere" in r.getMessage() for r in caplog.records)


async def test_provision_promotes_the_bind_home_assistant_is_trying_on_its_first_start(jarvis):
    """HA 2026.11 runs the YAML `server_host` as a five-minute trial and then reverts to every
    interface unless an admin promotes it. On production nobody did: the page answered on the LAN
    five minutes after AURA onboarded it. AURA is that admin."""
    ha = FakeHA()
    ha.first_boot_with_yaml_bind("127.0.0.1")

    out = await _setup(jarvis, ha, bind="127.0.0.1").ensure()

    assert out["state"] == "provisioned" and out["http"] == "promoted", out
    assert ha.http_active == "stable" and ha.http_stable["server_host"] == ["127.0.0.1"]
    assert ha.http_pending is None and ha.restarts == 0


async def test_provision_a_reverted_bind_is_set_again_and_promoted_after_the_restart(jarvis):
    ha = FakeHA()
    ha.first_boot_with_yaml_bind("127.0.0.1")
    ha.trial_reverted()  # jarvis-core was down for the five minutes
    setup = _setup(jarvis, ha, bind="127.0.0.1")

    first = await setup.ensure()
    assert first["http"] == "restarting", first
    assert ha.http_pending["server_host"] == ["127.0.0.1"]
    assert ha.http_pending["cors_allowed_origins"] == [] and ha.http_pending["ip_ban_enabled"] is True, \
        "the rest of HA's stable config is kept"
    second = await setup.ensure()

    assert second["http"] == "promoted", second
    assert ha.bound() == ["127.0.0.1"] and ha.http_active == "stable"
    third = await setup.ensure()
    assert third["http"] == "held" and ha.restarts == 2, "a held bind is left alone"


async def test_provision_never_touches_the_http_config_of_an_home_assistant_it_did_not_onboard(jarvis):
    ha = FakeHA()
    ha.steps = dict.fromkeys(ha.steps, True)
    ha.long_lived.add("owner-made")
    before = dict(ha.http_stable)

    out = await _setup(jarvis, ha, token="owner-made", bind="127.0.0.1").ensure()

    assert out["state"] == "adopted" and "http" not in out
    assert ha.http_stable == before and ha.http_pending is None and ha.restarts == 0


async def test_provision_the_keeper_comes_back_soon_after_asking_home_assistant_to_restart(tmp_path):
    import asyncio

    from aura.integrations import domains as domains_integration
    from aura.integrations import home_assistant

    jarvis = Jarvis(tmp_path)
    for registry in (jarvis.areas, jarvis.devices, jarvis.entities):
        await registry.load()
    await domains_integration.async_setup(jarvis, None)
    ha = FakeHA()
    ha.first_boot_with_yaml_bind("127.0.0.1")
    ha.trial_reverted()
    jarvis.data["home_assistant"] = {"transport": httpx.MockTransport(ha.http), "connect": ha.connect}

    assert await home_assistant.async_setup(jarvis, {"url": URL, "bind": "127.0.0.1",
                                                     "interval": 600, "retry": 0.05})
    try:
        for _ in range(200):
            if ha.http_active == "stable" and ha.bound() == ["127.0.0.1"]:
                break
            await asyncio.sleep(0.01)
        assert ha.bound() == ["127.0.0.1"] and ha.http_active == "stable", \
            "promoted within the trial's five minutes, not after the ten-minute interval"
    finally:
        for task in list(asyncio.all_tasks()):
            if task is not asyncio.current_task():
                task.cancel()
