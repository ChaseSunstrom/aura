"""Hermes as the house pipeline's conversation agent.

The house has had no agent of its own since the Hermes rebuild (ADR-0012), so
a voice run that reached it — a phone paired to the house with the console's
pairing code, a satellite, a channel — answered every question with
:data:`~aura.integrations.voice.NO_AGENT_REPLY` (the owner, 2026-09-25:
"why am I getting 'Sorry, no conversation agent is configured yet' on the
mobile app?"). This hands the sentence to Hermes instead, through the
adapter's OpenAI-compatible ``/v1/chat/completions`` — a forwarder, not a
second agent loop.

What it does **not** give: approvals. ``/v1`` is Hermes' unattended route, so
a tool that asks is refused and Hermes says so in the reply ("this session is
running unattended… approve it from a connected surface"); a turn that should
act needs the phone's own Hermes link, which answers approvals. Nor streaming
or the HUD's events: the pipeline wants one sentence back.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

import httpx

_LOGGER = logging.getLogger(__name__)

#: A cold model start behind the adapter is slow; a voice turn waits this long.
TIMEOUT_SECONDS = 300.0

#: Said when Hermes cannot be reached, instead of a traceback or silence.
UNREACHABLE_REPLY = "Sorry, I can't reach Hermes right now."

Converse = Callable[..., Awaitable[str]]

#: The word each persona is woken by: the openWakeWord model's name (its file's stem in the
#: wake service's custom model folder, or a built-in). The adapter's ``PERSONAS`` is the
#: other half; a persona this table lacks keeps the house's configured word.
PERSONA_WAKE_WORDS = {"aura": "aura", "jarvis": "hey_jarvis"}

#: A persona switch is rare and a wake stage starts per utterance: ask the adapter this seldom.
PERSONA_TTL_SECONDS = 60.0


def persona_wake_word(
    url: str,
    key: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], Awaitable[str | None]] | None:
    """A ``() -> wake word`` that follows the adapter's primary persona, or None when unset.

    The persona lives in Hermes' volume, not the house's, so the adapter's ``/api/status`` is
    the one place the house can read it. Returns None (use the configured word) when the
    adapter is unreachable or names a persona with no word here; the last good answer is not
    kept past its minute, so a switch is heard within one.
    """
    base = (url or "").strip().rstrip("/")
    key = (key or "").strip()
    if not base or not key:
        return None
    cached: tuple[float, str | None] | None = None

    async def word() -> str | None:
        nonlocal cached
        now = clock()
        if cached is not None and now - cached[0] < PERSONA_TTL_SECONDS:
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
                response = await client.get(
                    f"{base}/api/status", headers={"Authorization": f"Bearer {key}"}
                )
            response.raise_for_status()
            active = str(response.json()["persona"]["active"]).strip().lower()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as err:
            _LOGGER.warning("voice: the adapter at %s did not name the persona: %s", base, err)
            return None  # not cached: the next wake stage asks again
        cached = (now, PERSONA_WAKE_WORDS.get(active))
        return cached[1]

    return word


def hermes_agent(
    url: str,
    key: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Converse | None:
    """A ``converse(text, conversation_id)`` that asks Hermes, or None when unset.

    ``conversation_id`` becomes Hermes' session (``X-Hermes-Session-Id``), so a
    follow-up in the same pipeline conversation is the same session there.
    """
    base = (url or "").strip().rstrip("/")
    key = (key or "").strip()
    if not base or not key:
        return None
    endpoint = f"{base}/v1/chat/completions"

    async def converse(text: str, conversation_id: str | None = None, **_: Any) -> str:
        headers = {"Authorization": f"Bearer {key}"}
        if conversation_id:
            headers["X-Hermes-Session-Id"] = f"house-{conversation_id}"
        body = {"model": "hermes", "messages": [{"role": "user", "content": text}]}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=transport) as client:
                response = await client.post(endpoint, json=body, headers=headers)
            response.raise_for_status()
            reply = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as err:
            _LOGGER.warning("voice: Hermes at %s did not answer: %s", base, err)
            return UNREACHABLE_REPLY
        return str(reply or "").strip()

    return converse
