"""Who the current turn came from, as the approval matrix needs it: a level.

The adapter records, per session, who the latest ``prompt.submit`` came
from — a voice the house's speaker gate recognised (with that person's
level), or the key holder (``aura-adapter/aura_adapter/speaker.py``).
The matrix gate asks here before it ASKs or DENIES, and before a free row off the guest list, so the brief's tiering
lands in the one place it said it should: *"the tiering lives in the approval
matrix rather than in advice"* (the v3 brief PART 2).

What a level means at the gate (``aura-core/aura/voice/speaker.py`` LEVELS):

* ``owner``  — the matrix as written.
* ``member`` — the matrix as written. Every gated row already asks on the
  owner's HUD, and that HUD is the second factor: a member's voice can start
  a destructive action, and the owner's screen is where it is allowed.
* ``guest``  — a gated row is refused outright, and so is a free row off
  ``_matrix.GUEST_FREE`` (the owner's mail, files, facts, memory, past talks,
  the shell). A guest may ask about the world and use the screen, the voice,
  timers and media; nothing that would need the owner's approval is even put
  to the owner. Not covered: what the persona's own prompt already holds (its
  MEMORY.md and USER.md are in every turn's system prompt).

The adapter shares the gateway's network namespace, so the question is one
loopback GET with the apps' key, which this process has in its environment
(the gateway loads the host ``.env``). It fails **open** to the key holder:
an adapter that is down or slow must not stop every tool call — the matrix
still ASKs on everything gated, so the failure costs a guest's refusal (their
turn is the key holder's, the free reads included), never the owner's own use. Answers are cached per session for a moment, because a
turn makes many tool calls and the answer does not change inside one.
"""
from __future__ import annotations

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

KEY_HOLDER_LEVEL = "owner"
LEVELS = ("owner", "member", "guest")

#: How long an answer is believed, per session. A guest cannot become the
#: owner inside a turn; the owner typing a new prompt is a new binding the
#: adapter records at once, and this only delays the gate seeing it.
CACHE_S = 2.0
TIMEOUT_S = 1.5
_RANK = {level: i for i, level in enumerate(("guest", "member", "owner"))}

_log = logging.getLogger(__name__)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _endpoint() -> tuple[str, str] | None:
    """The adapter's loopback address and the apps' key, from the one schema."""
    try:
        from aura_schema import from_env

        cfg = from_env()
    except Exception:  # the schema is not importable in every test process
        return None
    key = str(getattr(cfg, "apps_api_key", "") or "")
    port = int(getattr(cfg, "adapter_port", 0) or 0)
    if not key or not port:
        return None
    return f"http://127.0.0.1:{port}", key


def _fetch(session_id: str) -> Optional[dict[str, Any]]:
    where = _endpoint()
    if where is None:
        return None
    base, key = where
    url = f"{base}/api/speaker/current?" + urllib.parse.urlencode({"session": session_id})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 — loopback, our own adapter
            body = json.loads(resp.read().decode("utf-8"))
    # HTTPException: an adapter restarting mid-answer raises IncompleteRead, which is
    # not an OSError, and an exception out of the gate blocks the owner's call.
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        _log.debug("speaker lookup failed (%s); treating the turn as the key holder's", exc)
        return None
    return body if isinstance(body, dict) else None


#: Swapped by the tests for a fake; production leaves it alone.
fetch: Callable[[str], Optional[dict[str, Any]]] = _fetch


def _gateway_sessions() -> Optional[dict[str, Any]]:
    """The gateway's live short-sid -> session map, or None off the gateway (the tests).

    A Hermes that moved the table would quietly bring T-0792 back (every voice turn the key
    holder's), so the miss is logged; CI's hermes-imports check runs this import in each image.
    """
    try:
        from tui_gateway.server import _sessions
    except Exception as exc:
        _log.debug("no gateway session table (%s); asking by the id as given", exc)
        return None
    return _sessions


#: Swapped by the tests for a fake table; production reads the gateway's.
sessions: Callable[[], Optional[dict[str, Any]]] = _gateway_sessions


def _bound_ids(session_id: str) -> list[str]:
    """The ids the adapter may have bound this session's speaker under (T-0792).

    The adapter binds ``prompt.submit``'s short sid; a tool call carries the durable
    ``session_key``. Asked by the key alone the adapter has no record and the lookup fails
    open, so a guest's voice turn got the owner's tools. Two screens can share one key.
    """
    ids = [session_id]
    for sid, entry in list((sessions() or {}).items()):
        if sid != session_id and isinstance(entry, dict) and entry.get("session_key") == session_id:
            ids.append(sid)
    return ids


def _strictest(answers: list[Optional[dict[str, Any]]]) -> Optional[dict[str, Any]]:
    """Of the adapter's answers, the strictest real binding, else the first answer at all.

    Two screens on one key: a guest's turn still running names only the key in its tool
    calls, so the owner typing on the other screen must not lift it, however long the turn
    runs. The guest's screen counts while its session is live in the gateway; the cost is
    the owner's second screen read as a guest until the first is closed or spoken on again.
    """
    got = [a for a in answers if isinstance(a, dict)]
    bound = [a for a in got if a.get("source") not in (None, "none")]
    if not bound:
        return got[0] if got else None
    return min(bound, key=lambda a: (_RANK.get(str(a.get("level")), len(_RANK)), -float(a.get("at") or 0)))


def speaker_of(session_id: str, now: float | None = None) -> dict[str, Any]:
    """The adapter's record for a session, or the key holder's when it cannot say."""
    at = now if now is not None else time.time()
    if not session_id:
        return {"label": None, "level": KEY_HOLDER_LEVEL, "source": "none"}
    cached = _cache.get(session_id)
    if cached is not None and at - cached[0] < CACHE_S:
        return cached[1]
    answer = _strictest([fetch(sid) for sid in _bound_ids(session_id)])
    level = answer.get("level") if isinstance(answer, dict) else None
    record = {
        "label": answer.get("label") if isinstance(answer, dict) else None,
        "level": level if level in LEVELS else KEY_HOLDER_LEVEL,
        "source": str(answer.get("source") or "none") if isinstance(answer, dict) else "none",
    }
    if len(_cache) > 1024:
        _cache.clear()
    _cache[session_id] = (at, record)
    return record


def level_of(session_id: str) -> str:
    return str(speaker_of(session_id)["level"])
