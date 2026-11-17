"""The cold-model fast path: a closed grammar answered without the LLM."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from aura_adapter import fast_intent as fi

NOON = datetime(2026, 10, 6, 14, 5)


@pytest.mark.parametrize("text,kind", [
    ("What time is it?", "time"),
    ("Jarvis, what's the time", "time"),
    ("hey jarvis what time is it please", "time"),
    ("what's the date", "date"),
    ("What day is it today?", "date"),
    ("pause the music", "media:pause"),
    ("Pause.", "media:pause"),
    ("resume music", "media:play"),
    ("skip this song", "media:next"),
    ("previous track", "media:previous"),
])
def test_the_grammar_matches(text, kind):
    assert fi.match(text) == kind


@pytest.mark.parametrize("text", [
    "",
    "what time is it in Tokyo",          # needs the model: a zone
    "turn off the lights",               # ha_call_service is ASK: never acted on here
    "set a timer for five minutes",      # no FREE timer tool exists
    "play some Radiohead",               # a query: the model picks the fields
    "pause the lights",
    "stop",                              # a barge-in word, not a music command
    "write a poem about what time it is",
])
def test_everything_else_is_left_to_the_model(text):
    assert fi.match(text) is None


@pytest.mark.parametrize("state,cold", [
    ("starting", True), ("loading", True), ("unloaded", True), ("other-model", True),
    ("stopping", True), ("ready", False), ("unreachable", False), ("unknown", False),
])
def test_only_a_known_cold_state_is_cold(state, cold):
    assert fi.is_cold(state) is cold


def test_the_answer_says_the_model_is_waking_up():
    assert fi.answer("time", NOON).startswith("It's 2:05 PM.")
    assert fi.answer("date", NOON).startswith("It's Tuesday, October 6, 2026.")
    for kind in ("time", "date", "media:pause"):
        # The waking line is said only while the model is loading (T-0186).
        assert fi.WAKING in fi.answer(kind, NOON, cold=True)
        assert fi.WAKING not in fi.answer(kind, NOON) and fi.GONE not in fi.answer(kind, NOON)


def test_frames_are_ones_the_apps_already_render():
    out = [json.loads(f) for f in fi.frames("media:pause", 9, "s1", NOON, cold=True)]
    assert out[0] == {"jsonrpc": "2.0", "id": 9, "result": {"status": "streaming"}}
    media = out[1]["params"]
    assert media["type"] == "hud.phone.media" and media["session_id"] == "s1"
    assert media["payload"] == {"action": "pause", "query": "", "kind": "any"}
    done = out[2]["params"]
    assert done["type"] == "message.complete" and done["session_id"] == "s1"
    assert done["payload"]["status"] == "complete" and fi.WAKING in done["payload"]["text"]
    assert len(fi.frames("time", 1, "s1", NOON)) == 2


def test_intercept_answers_the_grammar_warm_or_cold_and_relays_the_rest():
    """T-0186: the car's turns took 7-14 s to a first word; with the model warm, 'what time
    is it' still paid a whole model turn. The grammar needs no tool and no approval."""
    frame = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "prompt.submit",
                        "params": {"session_id": "s", "text": "what time is it"}})

    def run(state, f=frame):
        async def status():
            return state
        return asyncio.run(fi.intercept(f, status, now=NOON))

    warm = run("ready")
    assert warm[0] == '{"jsonrpc":"2.0","id":4,"result":{"status":"streaming"}}'
    said = json.loads(warm[-1])["params"]["payload"]["text"]
    assert said == "It's 2:05 PM." and fi.WAKING not in said
    assert fi.WAKING in json.loads(run("starting")[-1])["params"]["payload"]["text"]
    assert run("ready", frame.replace("what time is it", "tell me a joke")) is None
    other = frame.replace("what time is it", "tell me a joke")
    assert run("starting", other) is None
    assert run("starting", '{"method":"session.create","params":{}}') is None
    assert run("starting", "not json") is None

    music = frame.replace("what time is it", "pause the music")
    assert run("starting", music) is None  # not the phone: nothing would play the event

    async def cold():
        return "starting"
    on_phone = asyncio.run(fi.intercept(music, cold, lambda sid: "android", now=NOON))
    assert json.loads(on_phone[1])["params"]["type"] == "hud.phone.media"

    async def broken():
        raise RuntimeError("llama-swap down")
    assert asyncio.run(fi.intercept(frame, broken, now=NOON)) is None


def test_a_down_model_server_is_said_at_once_for_any_prompt():
    """2026-10-06: the ai host fell off the tailnet and every turn sat 180 s on a provider
    that refused connections, with the console still showing the model as ready."""
    frame = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "prompt.submit",
                        "params": {"session_id": "s", "text": "tell me a joke"}})

    async def down():
        return fi.DOWN
    out = [json.loads(f) for f in asyncio.run(fi.intercept(frame, down, now=NOON))]
    assert out[0]["result"] == {"status": "streaming"}
    assert out[-1]["params"]["type"] == "message.complete"
    assert out[-1]["params"]["payload"]["text"] == fi.UNREACHABLE_LINE
    # The grammar still answers, with the true reason after it.
    clock = asyncio.run(fi.intercept(frame.replace("tell me a joke", "what time is it"), down, now=NOON))
    text = json.loads(clock[-1])["params"]["payload"]["text"]
    assert text.startswith("It's 2:05 PM.") and fi.WAKING not in text and fi.GONE in text


def test_the_probe_says_down_only_for_a_server_it_has_seen_answer():
    """A deployment whose model is not behind llama-swap never reached it: refusing every
    prompt there would be the lie the old docstring warned about, so it relays as before."""
    import httpx

    calls = iter(["ready", httpx.ConnectError("refused"), httpx.ConnectTimeout("t"),
                  httpx.ReadTimeout("slow"), httpx.HTTPStatusError("500", request=None, response=None)])

    async def fetch():
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    async def fetch_error():
        raise httpx.ConnectError("refused")
    never = fi.SwapProbe(fetch_error)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(never())

    probe = fi.SwapProbe(fetch)
    assert asyncio.run(probe()) == "ready"
    assert asyncio.run(probe()) == fi.DOWN
    assert asyncio.run(probe()) == fi.DOWN
    with pytest.raises(httpx.ReadTimeout):  # it took the connection: slow, not gone
        asyncio.run(probe())
    with pytest.raises(httpx.HTTPStatusError):  # it answered: not down, just odd
        asyncio.run(probe())


def test_a_prompt_with_a_photo_waiting_goes_to_the_model():
    """T-0497 review: Hermes queues an attached photo for the session's next prompt.submit.
    A "what time is it" answered here never reached it, so the photo rode the next,
    unrelated prompt. With a photo waiting, the next prompt is relayed, once."""
    def frame(method, sid, text="what time is it"):
        return json.dumps({"jsonrpc": "2.0", "id": 7, "method": method,
                           "params": {"session_id": sid, "text": text}})

    async def warm():
        return "ready"

    async def down():
        return fi.DOWN
    photos: set[str] = set()

    def run(f, state=warm):
        return asyncio.run(fi.intercept(f, state, now=NOON, photos=photos))
    assert run(frame("image.attach_bytes", "s")) is None
    assert run(frame("prompt.submit", "other")) is not None  # another session is untouched
    assert run(frame("prompt.submit", "s"), down) is None  # relayed: Hermes says the failure
    assert run(frame("prompt.submit", "s")) is not None  # once: the next is answered here again
