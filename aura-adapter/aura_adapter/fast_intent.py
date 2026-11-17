"""Answers that do not wait for the model: the time, the date, the phone's music.

While llama-swap is loading the model (up to ~3 min, `docs/hermes-hud-protocol.md`)
"what time is it" sat behind the load like any other turn, and with the model warm it
still paid a whole turn (the car's took 7-14 s to a first word, T-0186). The relay
answers a small closed grammar itself, warm or cold, and adds that the big model is
still waking up only while it is.

The grammar is closed on purpose and the match is a whole-sentence one, so "what
time is it in Tokyo" or "play some Radiohead" still go to the model. It holds
only what is FREE in ``hermes/plugins/_shared/_matrix.py``: reading the clock,
and ``phone_media`` (play/pause/next/previous on the person's own phone, sent as
the same ``hud.phone.media`` event that tool emits). Lights are not here:
``ha_call_service`` is an ASK row, and a fast path that switched them without
the approval would be a way round the guard. Timers are not here either: no
FREE tool sets one. Both are relayed and wait for the model as before.

A model server that is *down* is said at once for every prompt, not only the
grammar: on 2026-10-06 the ai host fell off the tailnet and each turn sat 180 s on
a provider refusing connections. ``SwapProbe`` reports ``DOWN`` only for a
llama-swap it has seen answer since the relay started, and only on a transport
failure -- a deployment whose model is not behind llama-swap never reached it,
and refusing every prompt there would be a lie, so it relays as before.

Does not cover: a cold model behind a clean ``ready`` (a remote provider), a
model server that answers ``/running`` but fails completions, or speaking the
line -- the apps speak ``message.complete`` themselves, as they do for any reply.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

#: Said after every local answer, so the person knows why the rest is slow.
WAKING = "The main model is still waking up."

#: Said after a grammar answer while the model server is down.
GONE = "My main model is unreachable right now."

#: The whole reply to anything else while it is down.
UNREACHABLE_LINE = ("I can't reach my main model right now, so I can't work on that. "
                    "I can still tell you the time or the date.")

#: ``SwapProbe``'s word for a llama-swap that answered before and now cannot be reached.
DOWN = "unreachable"

#: llama-swap states (``status.summarize_llama_swap``) in which a turn would wait for a load.
COLD_STATES = frozenset({"starting", "loading", "unloaded", "other-model", "stopping"})

#: The surface name the phone reports (``tests/contracts/surface-capabilities.json``);
#: only that client acts on ``hud.phone.media``, so music is answered only for it.
PHONE_SURFACE = "android"

_FILLER = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis\s+)?(?:please\s+)?|\s+please$")

_GRAMMAR: tuple[tuple[str, re.Pattern[str]], ...] = tuple((kind, re.compile(rx)) for kind, rx in (
    ("time", r"(?:what(?: i|')?s the time|what time is it(?: now)?|tell me the time)"),
    ("date", r"(?:what(?: i|')?s (?:the date|today(?:'s date)?)(?: today)?"
             r"|what day is it(?: today)?|what is today(?:'s date)?)"),
    ("media:pause", r"pause(?: the)?(?: music| song)?|stop(?: the)? music"),
    ("media:play", r"(?:resume|unpause|play)(?: the)? music|resume|unpause"),
    ("media:next", r"(?:skip|next)(?: this)?(?: song| track)?"),
    ("media:previous", r"(?:previous|last|go back a)(?: song| track)"),
))


def _normalise(text: str) -> str:
    words = re.sub(r"[^\w\s']", " ", text.lower().replace("’", "'"))
    return _FILLER.sub("", " ".join(words.split())).strip()


def match(text: str) -> str | None:
    """The intent a whole sentence is, or None for anything outside the grammar."""
    said = _normalise(text)
    return next((kind for kind, rx in _GRAMMAR if rx.fullmatch(said)), None)


def is_cold(state: str) -> bool:
    """True only for a state in which llama-swap is known to have to load the model."""
    return state in COLD_STATES


class SwapProbe:
    """llama-swap's model state, or ``DOWN`` for a server once seen answering that now
    refuses or never accepts the connection. Anything else it raises, which relays the prompt."""

    def __init__(self, fetch: Callable[[], Awaitable[str]]) -> None:
        self._fetch = fetch
        self._seen = False

    async def __call__(self) -> str:
        try:
            state = await self._fetch()
        # Connect failures only: a read timeout is a server that took the connection and
        # is slow, and telling the person the model is gone would be wrong.
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if self._seen:
                return DOWN
            raise
        self._seen = True
        return state


def answer(kind: str, now: datetime, down: bool = False, cold: bool = False) -> str:
    """The local reply; why the rest is slow is said only while it is (cold) or cannot come (down)."""
    if kind == "time":
        said = f"It's {now.strftime('%I:%M %p').lstrip('0')}."
    elif kind == "date":
        said = f"It's {now.strftime('%A, %B')} {now.day}, {now.year}."
    else:
        said = {"media:pause": "Pausing your music.", "media:play": "Resuming your music.",
                "media:next": "Skipping ahead.", "media:previous": "Going back a track."}[kind]
    return f"{said} {GONE}" if down else f"{said} {WAKING}" if cold else said


def _frame(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))


def frames(kind: str | None, req_id: object, session_id: str, now: datetime,
           down: bool = False, cold: bool = False) -> list[str]:
    """The reply, as the frames Hermes sends for a turn: the result, then ``message.complete``.

    No new frame type: the console and the phone already end a turn on
    ``message.complete`` and play ``hud.phone.media``.
    """
    out = [_frame({"jsonrpc": "2.0", "id": req_id, "result": {"status": "streaming"}})]

    def event(type_: str, payload: dict) -> str:
        return _frame({"jsonrpc": "2.0", "method": "event",
                       "params": {"type": type_, "session_id": session_id, "payload": payload}})

    if kind and kind.startswith("media:"):
        # The shape places.media_payload gives a bare play/pause/next/previous.
        out.append(event("hud.phone.media", {"action": kind.split(":", 1)[1], "query": "", "kind": "any"}))
    text = answer(kind, now, down, cold) if kind else UNREACHABLE_LINE
    out.append(event("message.complete", {"text": text, "status": "complete"}))
    return out


async def intercept(frame: str, model_state: Callable[[], Awaitable[str]],
                    surface: Callable[[str], str | None] = lambda _sid: None,
                    now: datetime | None = None,
                    photos: set[str] | None = None) -> list[str] | None:
    """The frames that answer ``frame`` locally, or None to relay it as always.

    ``model_state`` is asked for every prompt -- one ``/running`` on the LAN, so a
    dead server is caught before the turn waits on it; any failure asking it relays.

    ``photos`` holds the sessions with an ``image.attach_bytes`` relayed and no prompt
    since: Hermes queues the photo for that session's next ``prompt.submit``, so that
    prompt is always relayed, or the photo would ride whatever is said after it. An
    attach Hermes refused still costs the next prompt its fast path, once.
    """
    try:
        msg = json.loads(frame)
    except ValueError:
        return None
    if not isinstance(msg, dict):
        return None
    params = msg.get("params")
    if not isinstance(params, dict):
        return None
    session_id = str(params.get("session_id") or "")
    if msg.get("method") == "image.attach_bytes" and photos is not None:
        photos.add(session_id)
        return None
    if msg.get("method") != "prompt.submit":
        return None
    if photos is not None and session_id in photos:
        photos.discard(session_id)
        return None
    kind = match(str(params.get("text") or ""))
    if kind and kind.startswith("media:") and surface(session_id) != PHONE_SURFACE:
        kind = None
    try:
        state = await model_state()
    except Exception:  # noqa: BLE001 - a status we cannot read is not a reason to answer
        return None
    when = now or datetime.now().astimezone()
    if state == DOWN:
        return frames(kind, msg.get("id"), session_id, when, down=True)
    # Warm or cold (T-0186): the grammar needs no tool and no approval, and a warm model
    # still spent a whole turn (7-14 s in the car) on "what time is it".
    if kind is None:
        return None
    return frames(kind, msg.get("id"), session_id, when, cold=is_cold(state))
