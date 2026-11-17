"""House-service mode: `llm: {enabled: false}` leaves no model client behind.

The Hermes rebuild makes Hermes the only agent loop. jarvis-core keeps the
house (entities, automations, MQTT, device channel) and runs no model loop of
its own, so the conversation entry points answer 501 whether the llm block is
on or off; off additionally registers nothing at all.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura.api import common
from aura.integrations import llm


class _Services:
    def __init__(self):
        self.registered = []

    def has_service(self, domain, service):
        return (domain, service) in self.registered

    def async_register(self, domain, service, *a, **kw):
        self.registered.append((domain, service))


async def test_disabled_llm_registers_nothing_and_still_reports_success():
    jarvis = SimpleNamespace(services=_Services(), data={}, config={})
    assert await llm.async_setup(jarvis, {"enabled": False, "url": "http://127.0.0.1:1"}) is True
    assert jarvis.services.registered == []
    assert not jarvis.services.has_service("conversation", "process")


@pytest.mark.parametrize("value", ["false", "0", "no", "off", False])
async def test_the_switch_accepts_the_usual_spellings(value):
    jarvis = SimpleNamespace(services=_Services(), data={}, config={})
    assert await llm.async_setup(jarvis, {"enabled": value}) is True
    assert jarvis.services.registered == []


async def test_conversation_process_answers_501_when_there_is_no_agent():
    jarvis = SimpleNamespace(services=_Services())
    with pytest.raises(common.ApiError) as err:
        await common.async_conversation_process(jarvis, "turn on the lights")
    assert err.value.status == 501 and err.value.code == "agent_not_found"
