"""What the house remembers about a turn does not outlive one call (T-1489).

The stores in `aura/api/devices.py` (what the user said, the turn's conversation and speaker,
whether the turn has read text a stranger wrote) are keyed by the context's id. The in-process
agent that owned a turn made one context and used it for every tool call of that turn; it was
removed on 2026-09-22 (acc05134a). Since then every REST request and every socket message
builds its own context, and Hermes reaches the house one request per call. So a mark made by
one call is invisible to the next, and nothing records what was said or who spoke.

These tests pin that shape, because several house checks read those stores (the tool gate's
hold on a turn that read outside text, the refusals in memory and undo, the lock tool's verb
check) and the suites that prove them reuse one context across calls, which no production
path does. The live gate on a turn is the Hermes guard's (`hermes/plugins/_shared/_taint.py`).

If one of these fails because the house was given a turn that spans calls (a turn id carried
from Hermes, say), that is the intended alarm: update `docs/research/house-turn-memory.md`
and the claims rows it names, then change the test.

Not covered: the Hermes guard's own record; what a single call does with a mark it made
itself; the console's test-run of one tool, which is one call by design.
"""

from __future__ import annotations

from aura.api import common
from aura.api.devices import (
    mark_untrusted,
    remember_speaker,
    remember_turn,
    remember_utterance,
    speaker_of,
    turn_facts_of,
    turn_is_untrusted,
    utterance_of,
)


class FakeJarvis:
    def __init__(self) -> None:
        self.data: dict = {}


def test_each_request_gets_a_context_of_its_own():
    first, second = common.api_context(), common.api_context("someone")
    assert first.id and second.id and first.id != second.id


def test_a_mark_made_by_one_request_is_not_seen_by_the_next():
    jarvis = FakeJarvis()
    reading, acting = common.api_context(), common.api_context()
    mark_untrusted(jarvis, reading)
    assert turn_is_untrusted(jarvis, reading) is True   # inside the call that read: seen
    assert turn_is_untrusted(jarvis, acting) is False   # the next call of the same turn: not


def test_what_one_request_recorded_about_the_turn_is_not_there_for_the_next():
    jarvis = FakeJarvis()
    first, second = common.api_context(), common.api_context()
    remember_utterance(jarvis, first, "lock the front door")
    remember_turn(jarvis, first, "conv-1", spoken=True)
    remember_speaker(jarvis, first, "sam")
    assert utterance_of(jarvis, first) == "lock the front door"
    assert (utterance_of(jarvis, second), speaker_of(jarvis, second)) == ("", "")
    assert turn_facts_of(jarvis, second) == (None, False)


def test_a_call_with_no_context_remembers_and_sees_nothing():
    """`POST /api/tools/call` hands the registry no context at all."""
    jarvis = FakeJarvis()
    mark_untrusted(jarvis, None)
    remember_utterance(jarvis, None, "unlock the door")
    assert turn_is_untrusted(jarvis, None) is False
    assert utterance_of(jarvis, None) == "" and speaker_of(jarvis, None) == ""
    assert turn_facts_of(jarvis, None) == (None, False)
