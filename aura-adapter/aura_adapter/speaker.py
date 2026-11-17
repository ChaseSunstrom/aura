"""Who is speaking, carried from the microphone to the tool call.

The brief (the v3 brief PART 2): verification on every
utterance *before the transcript reaches the agent*, several people each
with an authorization level, and the level applied in the approval matrix.
jarvis-core's speaker gate answers *who* (``/api/voice/speaker/verify``);
this module is the rest of the path, and it lives in the adapter because the
adapter is the one place every app's audio passes through on its way to
``prompt.submit``.

Three pieces, all pure so the tests can drive them without a socket:

* :func:`speaker_of` turns the gate's verdict into the ``speaker`` object the
  transcribe reply carries, and says whether the transcript is dropped.
* A **ticket** (:func:`issue_ticket` / :func:`open_ticket`): the transcript
  goes back to the client and comes in again as a prompt, so the identity has
  to survive that round trip without trusting the client to restate it. The
  ticket is the verdict signed with the apps' key, short-lived, and it names
  the person and the level. A client holds the key anyway, so this is not a
  defence against the client; it is what stops a *page* — a HUD panel showing
  untrusted content, a script — from claiming an owner's voice for a typed
  line, and it is what makes "this prompt came from a verified voice" a fact
  the adapter recorded rather than a field it was told.
* :class:`Speakers`: the record. ``prompt.submit`` with a ticket binds that
  session to the person; without one, to the key holder — the console
  password or the phone's token, which is the owner's. The guard asks
  ``/api/speaker/current`` for a session and reads the level.

What this does not do: it never sees audio from a client that sends a format
jarvis-core cannot embed (the gate wants WAV or raw PCM), and a turn like that
is the key holder's, as every turn was before this existed. Nor does it make
the gate stricter: whether an unrecognised voice is dropped is jarvis-core's
``mode`` and ``on_reject``, read off the verdict as ``would_block``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

#: How long a ticket stays good. A transcript is submitted within a second or
#: two of coming back; two minutes covers a slow hand on a slow phone and
#: nothing else, and a ticket replayed after that is refused as stale.
TICKET_TTL_S = 120.0

#: The level a session has when nobody's voice is on record for it: the
#: holder of the key. Mirrors ``LEVEL_OWNER`` in jarvis-core's ``voice/speaker.py``.
KEY_HOLDER_LEVEL = "owner"
LEVELS = ("owner", "member", "guest")

#: Sessions remembered at most; a socket that creates sessions in a loop
#: must not grow this without bound. Oldest out.
MAX_SESSIONS = 512


def speaker_of(payload: Any) -> dict[str, Any] | None:
    """The ``speaker`` object for a transcribe reply, from jarvis-core's verify reply.

    ``None`` when the reply is not a verdict (the gate is not there, nobody is
    enrolled, the audio was not something it could embed) — the turn is then
    the key holder's, as it always was.
    """
    if not isinstance(payload, dict):
        return None
    verdict = payload.get("verdict")
    if not isinstance(verdict, dict):
        return None
    accepted = bool(verdict.get("accepted"))
    level = verdict.get("level")
    return {
        "accepted": accepted,
        "label": verdict.get("label") if accepted else None,
        "level": level if accepted and level in LEVELS else None,
        "reason": str(verdict.get("reason") or ""),
        "nearest": verdict.get("nearest"),
        # The gate's own policy: an ``enforce`` gate drops what it refuses.
        # ``observe`` scores and lets it through, which is how a threshold is
        # found without being locked out while looking for it.
        "dropped": bool(payload.get("would_block")),
        # How close the nearest enrolled voice was (cosine), so a refusal can say whether
        # it was nearly the owner -- and a claim can be bounded by it (Unrecognised).
        "similarity": verdict.get("similarity"),
    }


class Unrecognised:
    """Utterances the gate refused, kept two hours for the owner to claim as theirs.

    The owner, 2026-10-07: enrolled on the console, then refused on the phone and in
    the car. A microphone the voice was never enrolled on sounds different enough to
    fall under the line; the cure is a print from that microphone. Each refusal close
    enough to someone (``FLOOR``) is held here with the surface it came from, and
    "That was me" on the console files it as a print for that surface (jarvis-core's
    enrol, ``source=``). A voice nowhere near anyone is never held, so a stranger's
    words cannot be claimed into the owner's prints by a tap.

    Held in memory only, never written to disk: a refused utterance close to an enrolled
    voice (``FLOOR`` or above) is kept for ``TTL_S`` (two hours, pruned on the next hold,
    list or claim after that), at most ``KEEP`` at a time, and a restart empties it. A voice
    nowhere near anyone is not kept at all.
    """

    #: CAM++ puts one speaker's clips around 0.6-0.8 and different speakers under 0.4
    #: (jarvis-core voice/neural.py); 0.3 holds a familiar voice on a new microphone and
    #: still not most strangers. A claim also needs MIN_PRINTS from a microphone to count.
    FLOOR = 0.3
    #: Two hours, not fifteen minutes: a voice refused in the car is claimed from the phone's
    #: tray at the next stop (VoiceClaim), and a drive is longer than fifteen minutes.
    TTL_S = 2 * 60 * 60
    KEEP = 10

    def __init__(self) -> None:
        self._held: dict[str, dict[str, Any]] = {}

    def hold(self, audio: bytes, content_type: str, verdict: dict[str, Any] | None, surface: str,
             now: float | None = None) -> str | None:
        """Keep a refused utterance; its id, or None when it is not close enough to anyone."""
        if not verdict or not verdict.get("dropped") or not verdict.get("nearest"):
            return None
        raw = verdict.get("similarity")
        if not isinstance(raw, (int, float, str)):
            return None
        try:
            similarity = float(raw)
        except ValueError:
            return None
        if similarity < self.FLOOR:
            return None
        at = time.time() if now is None else now
        self._prune(at)
        claim = secrets.token_urlsafe(9)
        self._held[claim] = {"audio": audio, "content_type": content_type, "nearest": str(verdict["nearest"]),
                             "similarity": round(similarity, 3), "surface": surface, "at": at}
        while len(self._held) > self.KEEP:
            self._held.pop(next(iter(self._held)))
        return claim

    def listing(self, now: float | None = None) -> list[dict[str, Any]]:
        self._prune(time.time() if now is None else now)
        return [{"id": k, **{f: v[f] for f in ("nearest", "similarity", "surface", "at")}}
                for k, v in reversed(self._held.items())]

    def take(self, claim: str, now: float | None = None) -> dict[str, Any] | None:
        self._prune(time.time() if now is None else now)
        return self._held.pop(claim, None)

    def _prune(self, now: float) -> None:
        for k in [k for k, v in self._held.items() if now - v["at"] > self.TTL_S]:
            del self._held[k]


def issue_ticket(key: str, label: str, level: str, now: float | None = None) -> str:
    """A signed, short-lived statement that ``label`` (a ``level``) just spoke."""
    body = json.dumps({"label": label, "level": level, "iat": round(now if now is not None else time.time(), 3)},
                      separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + mac


def open_ticket(key: str, ticket: Any, now: float | None = None) -> dict[str, Any] | None:
    """The person a ticket names, or ``None`` for anything forged, stale or malformed."""
    if not isinstance(ticket, str) or "." not in ticket:
        return None
    encoded, _, mac = ticket.rpartition(".")
    try:
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError):
        return None
    expected = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return None
    try:
        claims = json.loads(body)
    except ValueError:
        return None
    if not isinstance(claims, dict):
        return None
    label, level, iat = claims.get("label"), claims.get("level"), claims.get("iat")
    if not isinstance(label, str) or level not in LEVELS or not isinstance(iat, (int, float)):
        return None
    at = now if now is not None else time.time()
    if not (at - TICKET_TTL_S <= iat <= at + 5):
        return None
    return {"label": label, "level": level, "iat": float(iat)}


@dataclass
class Speakers:
    """Who each session's latest prompt came from."""

    key: str
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def bind(self, session_id: str, ticket: Any, now: float | None = None) -> dict[str, Any]:
        """Record who a ``prompt.submit`` on ``session_id`` came from.

        A good ticket binds the person it names. No ticket — a typed line, or
        a voice the gate could not judge — binds the key holder, so a guest's
        earlier turn never outlives the next thing the owner types. A bad
        ticket is treated as no ticket and noted, never as the person it
        claims: a forged or stale ticket must not lower the bar either, since
        the key holder typing is the default.
        """
        at = now if now is not None else time.time()
        person = open_ticket(self.key, ticket, at) if ticket is not None else None
        if person is not None:
            record = {"label": person["label"], "level": person["level"], "source": "voice", "at": at}
        else:
            record = {"label": None, "level": KEY_HOLDER_LEVEL, "source": "key", "at": at,
                      **({"rejected_ticket": True} if ticket is not None else {})}
        if session_id in self.sessions:
            del self.sessions[session_id]
        elif len(self.sessions) >= MAX_SESSIONS:
            oldest = next(iter(self.sessions))
            del self.sessions[oldest]
        self.sessions[session_id] = record
        return record

    def current(self, session_id: str) -> dict[str, Any]:
        """The record for a session, or the key holder's for one never seen."""
        known = self.sessions.get(session_id)
        if known is not None:
            return {"session_id": session_id, **known}
        return {"session_id": session_id, "label": None, "level": KEY_HOLDER_LEVEL, "source": "none", "at": None}


def rewrite_prompt(frame: str, speakers: Speakers, now: float | None = None) -> str:
    """Bind a ``prompt.submit`` frame's session and strip the ticket before Hermes sees it.

    Any other frame, and anything that is not JSON-RPC, passes through untouched
    — byte for byte, which is what the relay promises the apps.
    """
    try:
        msg = json.loads(frame)
    except ValueError:
        return frame
    if not isinstance(msg, dict) or msg.get("method") != "prompt.submit":
        return frame
    params = msg.get("params")
    if not isinstance(params, dict):
        return frame
    ticket = params.pop("speaker_ticket", None)
    # ``voice`` was read by effort.Efforts.before, which runs first; Hermes never sees it.
    spoken = params.pop("voice", None)
    session_id = str(params.get("session_id") or "")
    if session_id:
        speakers.bind(session_id, ticket, now)
    if ticket is None and spoken is None:
        return frame
    return json.dumps(msg, separators=(",", ":"))
