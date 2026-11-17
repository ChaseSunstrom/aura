"""The house pipeline's agent is Hermes, through the adapter (voice/hermes.py)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from aura.integrations.voice import NO_AGENT_REPLY, resolve_conversation_agent
from aura.integrations.voice.hermes import UNREACHABLE_REPLY, hermes_agent


def _adapter(seen: list[httpx.Request], reply: str = "The bed light is on, Sir.", status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": reply}}]})

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_a_turn_goes_to_the_adapter_with_the_key_and_the_session() -> None:
    seen: list[httpx.Request] = []
    converse = hermes_agent("http://100.64.0.51:8643/", "k", transport=_adapter(seen))
    assert await converse("Turn on the bed light", "abc") == "The bed light is on, Sir."
    (request,) = seen
    assert str(request.url) == "http://100.64.0.51:8643/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer k"
    assert request.headers["x-hermes-session-id"] == "house-abc"
    assert json.loads(request.content)["messages"] == [{"role": "user", "content": "Turn on the bed light"}]


@pytest.mark.asyncio
async def test_an_unreachable_hermes_is_said_not_raised() -> None:
    converse = hermes_agent("http://h:8643", "k", transport=_adapter([], status=502))
    assert await converse("Hello") == UNREACHABLE_REPLY


def test_no_url_or_no_key_is_no_agent() -> None:
    assert hermes_agent("", "k") is None
    assert hermes_agent("http://h:8643", "") is None


@pytest.mark.asyncio
async def test_resolution_uses_hermes_when_configured_and_the_stand_in_when_not() -> None:
    def house(agent: dict) -> SimpleNamespace:
        voice = SimpleNamespace(config={"agent": agent}, think=True)
        return SimpleNamespace(
            data={"voice": voice},
            services=SimpleNamespace(has_service=lambda *_: False),
        )

    assert await resolve_conversation_agent(house({}))("Hello") == NO_AGENT_REPLY
    converse = resolve_conversation_agent(house({"url": "http://h:8643", "key": "k"}))
    assert converse is not None and converse.__name__ == "converse"
