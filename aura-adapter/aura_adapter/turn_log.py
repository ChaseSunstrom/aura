"""One log line when a turn starts and one when it ends, keyed by the session id.

The adapter logged nothing per turn, so "Jarvis didn't answer at 17:42" from the phone or
the car could not be found anywhere on this side. The session id is already the
correlation key -- the apps send it, the relay passes it on, Hermes logs it -- so no new id
is minted: these two lines put it, the surface, the elapsed time and how the turn ended
into the adapter's log, where ``docker logs jarvis-adapter | grep <session>`` finds them.

Each voice hop also has a deadline (T-0589, [Deadlines]): one past it is a warning line here
and a count on /api/status (with the last hour's share and the last breach's age, T-1431),
so a slow card or model shows before the owner notices it.

Does not cover: turns Hermes starts on its own (cron, goals), or what happened inside the
turn -- that is Hermes' log, under the same session id.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from collections.abc import Callable

log = logging.getLogger("aura_adapter.turns")

#: Each voice hop's deadline, ms, keyed as /api/status's "voice" timings are (T-0589).
#: Transcribe: the card's Whisper answers in ~0.7 s, Hermes' CPU one in ~3 s (2026-10-08), so a
#: breach is mostly the card down. First token: prompt.submit to Hermes' first streamed text
#: (first words measured 6.2-8.1 s whole, docs/verification.md). First audio: a line made now
#: to its first PCM.
DEADLINES_MS: dict[str, float] = {"transcribe_ms": 2500.0, "first_token_ms": 6000.0, "first_pcm_ms": 1500.0}
#: What /api/status calls recent: a breach older than this is history, not a present fault.
RECENT_S = 3600.0
#: Breach times kept per hop. A card that is down breaches on every turn for days; the count
#: goes on, the times do not, so the adapter's memory does not grow with the outage.
BREACH_TIMES_KEPT = 256
#: The hops' names on /api/status and in the log.
HOP_NAMES = {"transcribe_ms": "transcribe", "first_token_ms": "first_token", "first_pcm_ms": "first_audio"}  # pragma: allowlist secret (hop names)


#: Every relay connection's open turns, (connection, session id): what /api/status's "busy"
#: counts, so a deploy can wait for a reply to finish before it recreates this process (T-0577).
_OPEN: set[tuple[int, str]] = set()
#: When a turn last started from the car's surface (monotonic), and how long after it a drive
#: is taken to be still going. ponytail: a drive with ten silent minutes is not seen; the phone
#: reporting its car link to the adapter would be the real signal.
_car_at: float | None = None
CAR_ACTIVE_S = 600.0


def busy(now: float | None = None) -> dict[str, object]:
    """Open turns across every connection, and whether the car spoke in the last ten minutes.
    Does not cover a turn Hermes started itself (cron, a goal) or one through the API server.
    A turn that never ends on a socket that stays open (an approval left waiting) counts until
    it does."""
    now = time.monotonic() if now is None else now
    return {"turns": len(_OPEN), "car": _car_at is not None and now - _car_at < CAR_ACTIVE_S}


class Deadlines:
    """Breaches per hop since the adapter started. Does not cover a hop that never finished: a
    turn with no first token ends as the turn log's error or socket-closed line."""

    def __init__(self, limits: dict[str, float] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = dict(DEADLINES_MS if limits is None else limits)
        self.breaches = {hop: 0 for hop in self.limits}
        self._clock = clock
        #: When each hop's newest breaches happened (the clock's seconds), oldest first.
        self._times: dict[str, deque[float]] = {hop: deque(maxlen=BREACH_TIMES_KEPT) for hop in self.limits}

    def check(self, hop: str, ms: float, what: str = "-") -> bool:
        """Count and log [hop] taking [ms] when past its deadline; True when it was."""
        limit = self.limits.get(hop)
        if limit is None or ms <= limit:
            return False
        self.breaches[hop] += 1
        self._times[hop].append(self._clock())
        log.warning("voice deadline breached hop=%s ms=%d deadline_ms=%d %s",
                    HOP_NAMES.get(hop, hop), ms, limit, what)
        return True

    def status(self) -> dict[str, dict[str, int | None]]:
        """Per hop: its deadline, the breaches since the adapter started, those of the last hour
        and the seconds since the last one (None when there was none). Without the last two a
        deploy's warm-up breaches read as a present fault until the next deploy (T-1431).
        ``last_hour`` stops at BREACH_TIMES_KEPT; ``breaches`` does not."""
        now = self._clock()
        out: dict[str, dict[str, int | None]] = {}
        for hop, ms in self.limits.items():
            times = self._times[hop]
            out[HOP_NAMES.get(hop, hop)] = {
                "deadline_ms": round(ms),
                "breaches": self.breaches[hop],
                "last_hour": sum(1 for t in times if now - t <= RECENT_S),
                "last_breach_s": round(now - times[-1]) if times else None,
            }
        return out


class TurnLog:
    """Per relay connection: ``sent`` sees every frame to Hermes, ``received`` every frame back."""

    def __init__(self, surface: Callable[[str], str | None] = lambda _sid: None,
                 deadlines: Deadlines | None = None) -> None:
        self._surface = surface
        self._deadlines = deadlines
        #: session id -> (monotonic start, request id)
        self._open: dict[str, tuple[float, object]] = {}
        #: Open turns whose first streamed text has come.
        self._spoke: set[str] = set()

    def sent(self, frame: str, local: bool = False) -> None:
        if '"prompt.submit"' not in frame:
            return
        try:
            msg = json.loads(frame)
            sid = str(msg["params"].get("session_id") or "")
        except (ValueError, KeyError, TypeError, AttributeError):
            return
        log.info("turn start session=%s id=%s surface=%s%s", sid, msg.get("id"),
                 self._surface(sid) or "-", " answered-locally" if local else "")
        if not local:
            self._open[sid] = (time.monotonic(), msg.get("id"))
            _OPEN.add((id(self), sid))
            if self._surface(sid) == "car":
                global _car_at
                _car_at = time.monotonic()

    def received(self, frame: str) -> None:
        if '"message.delta"' in frame:
            self._first_token(frame)
            return
        if '"message.complete"' not in frame and '"error"' not in frame:
            return
        try:
            msg = json.loads(frame)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        if params.get("type") == "message.complete":
            payload = params.get("payload")
            status = payload.get("status") if isinstance(payload, dict) else None
            self._end(str(params.get("session_id") or ""), str(status or "complete"))
        elif "error" in msg and "id" in msg:
            # A JSON-RPC error answering the prompt itself: find the turn by its request id.
            owner = next((s for s, (_t, rid) in self._open.items() if rid == msg["id"]), None)
            if owner is not None:
                err = msg["error"] if isinstance(msg["error"], dict) else {}
                self._end(owner, f"error {err.get('code', '')} {str(err.get('message', ''))[:120]}".strip())

    def _first_token(self, frame: str) -> None:
        if not self._open.keys() - self._spoke:
            return  # every open turn has had its first token: no frame to parse
        try:
            params = json.loads(frame).get("params") or {}
            sid = str(params.get("session_id") or "")
        except (ValueError, AttributeError):
            return
        if sid not in self._open or sid in self._spoke:
            return
        self._spoke.add(sid)
        if self._deadlines is not None:
            self._deadlines.check("first_token_ms", (time.monotonic() - self._open[sid][0]) * 1000,
                                  f"session={sid}")

    def _end(self, sid: str, outcome: str) -> None:
        self._spoke.discard(sid)
        _OPEN.discard((id(self), sid))
        opened = self._open.pop(sid, None)
        if opened is None:
            return
        log.info("turn end session=%s id=%s outcome=%s elapsed_ms=%d", sid, opened[1], outcome,
                 int((time.monotonic() - opened[0]) * 1000))

    def close(self) -> None:
        """The app's socket went away: a turn still open never ended where anyone saw it."""
        for sid in list(self._open):
            self._end(sid, "socket-closed")
