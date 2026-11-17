"""Ask and answer (M66): one voice, a clock a question can live on, and an
answer that can be said.

The operator reported three things on the same evening. When Jarvis asked
something, the voice said the reply AND the question. A question expired on the
five-minute approval clock, and the answer that came a little after it got
"unknown, expired or already-used approval request". And there was no way to
say "yes" — a held thing could only be tapped.

Each is a claim below, at the layer that owns it:

* the registry puts a question on its own clock (`question_ttl`) and, when one
  has lapsed, says so in words when the answer arrives;
* the registry stamps a request with the conversation and whether the turn is
  spoken, from what the caller recorded, so the phone can be told not to read
  a spoken question out again.

What is NOT here: the rules themselves (`test_spoken_answers.py` runs the
contract), and the half that resolved a held question from the next thing said
in the conversation, which was jarvis-core's own agent loop and went with it
(ADR-0012 addendum) — Hermes answers its own questions.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aura.api.devices import remember_turn  # noqa: E402
from aura.bus import Context  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.tools import (
    TIER_APPROVAL,  # noqa: E402
    DEFAULT_APPROVAL_TTL,
    DEFAULT_QUESTION_TTL,
    EVENT_APPROVAL_EXPIRED,
    ToolRegistry,
    expired_sentence,
    register_builtin_tools,
    schema_object,
)
from aura.voice.pipeline import PipelineRun  # noqa: E402
from test_llm import (  # noqa: E402
    call_tool,
)

pytestmark = pytest.mark.asyncio


# ===========================================================================
# the registry: a question's clock, and a late answer told the truth
# ===========================================================================
@pytest.fixture
async def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    await box.async_setup({})
    yield box
    await box.async_stop()


async def test_a_question_waits_thirty_minutes_and_an_action_five(jarvis):
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    now = time.time()

    question = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    action = await registry.call("lock_control", {"action": "lock", "name": "front door"}, None)

    assert question["expires_at"] - now == pytest.approx(DEFAULT_QUESTION_TTL, abs=2)
    assert action["expires_at"] - now == pytest.approx(DEFAULT_APPROVAL_TTL, abs=2)
    assert DEFAULT_QUESTION_TTL == 1800.0
    # The clock travels with the request, so the banner and the voice count
    # the same number down.
    by_tool = {r["tool"]: r for r in registry.pending_requests()}
    assert by_tool["ask_user"]["ttl"] == DEFAULT_QUESTION_TTL
    assert by_tool["lock_control"]["ttl"] == DEFAULT_APPROVAL_TTL
    # ...and the model is told how long, so it can say so.
    assert "30 minutes" in question["message"]
    assert "5 minutes" in action["message"]
    assert question["waits_seconds"] == DEFAULT_QUESTION_TTL


async def test_the_question_clock_is_its_own_and_not_derived(jarvis):
    """An operator shortening approvals must not shorten every question."""
    registry = ToolRegistry(jarvis, approval_ttl=60.0, question_ttl=900.0)
    register_builtin_tools(registry)
    now = time.time()
    held = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    assert held["expires_at"] - now == pytest.approx(900.0, abs=2)


async def test_a_lapsed_question_is_answered_in_words(jarvis):
    """The operator's report: answer after the clock, get the three-way guess."""
    registry = ToolRegistry(jarvis, question_ttl=0.0)
    register_builtin_tools(registry)
    lapsed: list[dict] = []
    jarvis.bus.listen(EVENT_APPROVAL_EXPIRED, lambda event: lapsed.append(event.data))

    held = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    late = await registry.approve_request(held["request_id"], True, "the corner one")

    assert late["status"] == "error"
    assert late["expired"] is True
    assert late["error"] == "That question expired after 0 seconds; ask again and I'll wait."
    assert late["tool"] == "ask_user"
    # The surfaces are told the same thing, once, when the registry notices.
    assert len(lapsed) == 1
    assert lapsed[0]["request_id"] == held["request_id"]
    assert lapsed[0]["expired"] is True
    assert lapsed[0]["answerable"] == "answer"


async def test_a_lapsed_action_names_the_tool_and_its_clock(jarvis):
    registry = ToolRegistry(jarvis, approval_ttl=0.0)
    register_builtin_tools(registry)
    held = await registry.call("lock_control", {"action": "lock", "name": "front door"}, None)
    late = await registry.approve_request(held["request_id"], True)
    assert late["expired"] is True
    assert late["error"] == (
        "That request to lock_control expired after 0 seconds; ask again and I'll hold it for you."
    )


async def test_the_expiry_sentence_says_minutes_when_it_is_minutes():
    assert expired_sentence("ask_user", 1800, True) == (
        "That question expired after 30 minutes; ask again and I'll wait."
    )
    assert expired_sentence("lock_control", 300, False) == (
        "That request to lock_control expired after 5 minutes; ask again and I'll hold it for you."
    )
    assert "90 seconds" in expired_sentence("ask_user", 90, True)


async def test_an_id_the_registry_never_held_still_gets_the_honest_guess(jarvis):
    """The three-way sentence stays for what it is true of: an id nobody
    raised, or one spent so long ago that it fell out of the lapsed memory."""
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    unknown = await registry.approve_request("never-raised", True)
    assert unknown["status"] == "error"
    assert "expired" not in unknown
    assert unknown["error"] == "unknown, expired or already-used approval request"

    # Spent, not lapsed: answered once, then again.
    held = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    assert (await registry.approve_request(held["request_id"], True, "desk"))["status"] == "executed"
    again = await registry.approve_request(held["request_id"], True, "desk")
    assert "expired" not in again


async def test_a_lapsed_request_never_runs(jarvis):
    """Lapsing is remembered, not queued: the answer that comes late runs
    nothing, however affirmative it is."""
    registry = ToolRegistry(jarvis, approval_ttl=0.0)
    ran: list[dict] = []

    async def _handler(args, context):
        ran.append(args)
        return {"status": "ok"}

    registry.register(
        name="unlock_gate",
        description="Unlock the gate.",
        parameters=schema_object({"target": {"type": "string"}}),
        handler=_handler,
        tier=3,
    )
    held = await registry.call("unlock_gate", {"target": "lock.gate"}, None)
    late = await registry.approve_request(held["request_id"], True)
    assert late["expired"] is True
    assert ran == []


async def test_the_lapsed_memory_is_bounded(jarvis):
    from aura.tools import MAX_LAPSED

    registry = ToolRegistry(jarvis, question_ttl=0.0)
    register_builtin_tools(registry)
    first = await registry.call("ask_user", {"question": "first?"}, None)
    for _ in range(MAX_LAPSED + 5):
        await registry.call("ask_user", {"question": "again?"}, None)
    registry.purge_expired()
    assert len(registry._lapsed) == MAX_LAPSED
    # The oldest fell out and is back to the honest guess.
    assert "expired" not in await registry.approve_request(first["request_id"], True, "x")


# ===========================================================================
# the registry: what the agent recorded is stamped on the request
# ===========================================================================
async def test_a_request_is_stamped_with_its_conversation_and_whether_it_is_spoken(jarvis):
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    spoken_turn = Context(origin="llm")
    typed_turn = Context(origin="llm")
    remember_turn(jarvis, spoken_turn, "conv-voice", spoken=True)
    remember_turn(jarvis, typed_turn, "conv-typed", spoken=False)

    voiced = await registry.call("ask_user", {"question": "Which lamp?"}, spoken_turn)
    typed = await registry.call("ask_user", {"question": "Which URL?"}, typed_turn)
    nobodys = await registry.call("ask_user", {"question": "Whose?"}, None)

    by_id = {r["request_id"]: r for r in registry.pending_requests()}
    assert by_id[voiced["request_id"]]["conversation_id"] == "conv-voice"
    assert by_id[voiced["request_id"]]["spoken"] is True
    assert by_id[typed["request_id"]]["conversation_id"] == "conv-typed"
    assert by_id[typed["request_id"]]["spoken"] is False
    # A request raised outside any turn belongs to no conversation and is not
    # spoken — the reading under which nothing resolves by accident.
    assert by_id[nobodys["request_id"]]["conversation_id"] is None
    assert by_id[nobodys["request_id"]]["spoken"] is False

    # Listing by conversation: what was stamped with it, plus the QUESTIONS the
    # house raised with no conversation — a notice's offer belongs to whoever
    # answers it (M86). The typed conversation's question stays its own.
    listed = [r["request_id"] for r in registry.pending_for_conversation("conv-voice")]
    assert listed == [voiced["request_id"], nobodys["request_id"]]
    assert registry.pending_for_conversation(None) == []
    # An ACTION held with no conversation is still nobody's to approve by voice.
    registry.register(
        name="house_action",
        description="a scripted action that needs a human",
        handler=lambda args, ctx: {"status": "ok"},
        tier=TIER_APPROVAL,
    )
    held = await registry.call("house_action", {}, None)
    assert held["status"] == "approval_required"
    assert held["request_id"] not in [
        r["request_id"] for r in registry.pending_for_conversation("conv-voice")
    ]
    # A conversation with nothing of its own still sees the house's question:
    # whoever answers it answers it.
    assert [r["request_id"] for r in registry.pending_for_conversation("conv-nobody")] == [
        nobodys["request_id"]
    ]


async def test_the_phone_is_told_a_spoken_question_is_already_spoken(jarvis):
    """The single voice: the reply says the question; the phone shows it."""
    from aura.integrations.llm import _bridge_questions_to_the_phone

    asked: list[dict] = []

    async def _ask(call):
        asked.append(dict(call.data))
        return {"answer": None}

    jarvis.services.register("companion", "ask", _ask, supports_response=True)
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    _bridge_questions_to_the_phone(jarvis, registry)

    spoken_turn = Context(origin="llm")
    remember_turn(jarvis, spoken_turn, "conv-voice", spoken=True)
    typed_turn = Context(origin="llm")
    remember_turn(jarvis, typed_turn, "conv-typed", spoken=False)

    await registry.call("ask_user", {"question": "Which lamp?"}, spoken_turn)
    await registry.call("ask_user", {"question": "Which URL?"}, typed_turn)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(asked) == 2:
            break

    by_question = {a["question"]: a for a in asked}
    assert by_question["Which lamp?"]["spoken"] is True
    assert by_question["Which URL?"]["spoken"] is False
    # And the phone is asked for as long as the question lives, not the
    # action clock.
    assert by_question["Which lamp?"]["timeout"] == pytest.approx(DEFAULT_QUESTION_TTL, abs=5)


async def test_the_companion_puts_spoken_on_the_wire(tmp_path):
    from aura.integrations.companion import CompanionManager
    from aura.presence import PresenceRegistry

    box = Jarvis(tmp_path)
    await box.async_setup({"companion": {}})
    try:
        manager: CompanionManager = box.data["companion"]
        presence: PresenceRegistry = box.data["presence"]
        presence.register("phone", "Pixel", "android", ["ask"])
        phone = presence.devices["phone"]
        phone.last_seen = time.time()
        phone.screen_on = True
        phone.locked = False
        sent: list[dict] = []

        async def transport(device_id, payload):
            sent.append(payload)
            return True

        manager.set_transport(transport)
        task = asyncio.create_task(
            box.services.async_call(
                "companion",
                "ask",
                {"question": "Which lamp?", "spoken": True, "timeout": 5},
                blocking=True,
                return_response=True,
            )
        )
        for _ in range(50):
            await asyncio.sleep(0.01)
            if sent:
                break
        assert sent and sent[0]["spoken"] is True
        manager.on_device_answer(sent[0]["message_id"], "desk")
        await task
        # The default is the old wire, so an older caller changes nothing.
        result = await manager.send("Plain?", kind="notify")
        assert result["status"] == "delivered"
        assert sent[-1]["spoken"] is False
    finally:
        await box.async_stop()


# ===========================================================================
# the pipeline says whether it will speak
# ===========================================================================
async def test_the_pipeline_tells_the_agent_whether_the_reply_is_spoken(tmp_path):
    box = Jarvis(tmp_path)
    await box.async_setup({})
    try:
        seen: list[dict] = []

        async def converse(text, conversation_id=None, **kwargs):
            seen.append(kwargs)
            return "Very good, Sir."

        class _Tts:
            def synthesize(self, text, voice=None):
                return b"\x00\x00" * 160, 16000, 2, 1

        spoken = PipelineRun(
            box, converse=converse, tts=_Tts(), start_stage="intent", end_stage="tts"
        )
        await spoken.execute_text("hello")
        typed = PipelineRun(box, converse=converse, start_stage="intent", end_stage="intent")
        await typed.execute_text("hello")

        assert seen[0]["spoken"] is True
        assert seen[1]["spoken"] is False

        # A two-argument stand-in is called as it always was.
        plain: list[str] = []

        async def two_args(text, conversation_id=None):
            plain.append(text)
            return "Very good, Sir."

        run = PipelineRun(box, converse=two_args, start_stage="intent", end_stage="intent")
        await run.execute_text("hello")
        assert plain == ["hello"]
    finally:
        await box.async_stop()


# ===========================================================================
# the agent: the next thing said is the answer
# ===========================================================================
ASK = call_tool("ask_user", {"question": "Which lamp?", "choices": ["Desk lamp", "Corner lamp"]})
LOCK = call_tool("lock_control", {"action": "lock", "name": "front door"})
