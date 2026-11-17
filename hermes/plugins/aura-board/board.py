"""The board's numbers, from Hermes's own state.db, for the adapter to serve (T-0733).

The adapter cannot open state.db: Hermes keeps it 0600 and the adapter runs as another uid
(group hermes). So this plugin, inside the jarvis container as Hermes's uid, reads it
read-only and writes counts to ``$HERMES_HOME/board/board.json`` (0640, the adapter's
group may read it), at most every :data:`MIN_GAP_S`, off the turn's path.

What leaves state.db: counts, durations, model names, session sources and tool names.
Never a message's content, a title or a tool's arguments.

A session a deploy's own smoke or golden turn ran in (``_shared/_probes``, recorded by
aura-hud) is kind ``probe`` in tasks and flows; its turns still count in the hourly figure,
because the model really ran. A turn recorded while the adapter could not answer stays a
console turn.

A session is in the window by its last activity, so a console session resumed today counts
its turns today; only its messages inside the window are walked.

Not covered: a resumed session's tokens (Hermes keeps them per session, so they include its
days before the window), the models' live load (the adapter asks llama-swap and the manager itself), and anything
while Hermes is idle: no hook fires, the file ages, and the adapter reports the age.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import tempfile
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from _shared import _probes

_LOGGER = logging.getLogger(__name__)

SCHEMA = 1
#: The kind of a session a deploy's smoke or golden turn ran in (aura-hud's habits records it).
PROBE_KIND = "probe"
WINDOW_S = 86400
MIN_GAP_S = 10.0
#: Bounds on a growing database: the newest sessions in the window, and their messages.
SESSION_CAP = 5000
MESSAGE_CAP = 100_000
#: A turn asked this recently with no reply yet is still running, not unanswered.
RUNNING_S = 120
TOP_TOOLS = 8

# A session's last activity is Hermes's own expression, so idx_sessions_effective_activity covers
# it (0.3 ms on production): started_at alone missed 3 of the day's 473 sessions, resumed ones.
_ACTIVE = "coalesce(last_activity_at, started_at)"
# Every messages query goes through this subquery: it walks that index and then
# idx_messages_session (3 ms on production); a join over messages took 5.6 s cold.
_IN_WINDOW = f"session_id in (select id from sessions where {_ACTIVE} >= ? order by {_ACTIVE} desc limit ?)"


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def board_path() -> Path:
    return _hermes_home() / "board" / "board.json"


def _pct(values: list[float], q: float) -> int | None:
    """Nearest-rank percentile in ms."""
    if not values:
        return None
    s = sorted(values)
    return round(1000 * s[max(0, math.ceil(q * len(s)) - 1)])


def _hour(now: float, at: float) -> int | None:
    """The hourly bucket (0 oldest, 23 the hour ending now), None outside the window."""
    back = int((now - at) // 3600)
    return 23 - back if 0 <= back < 24 else None


def _usage(c: sqlite3.Connection, since: float) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    by_model: dict[str, dict[str, Any]] = {}
    usd = 0.0
    recorded = False
    for model, calls, tin, tout, cost, status in c.execute(
        "select model, api_call_count, input_tokens, output_tokens, estimated_cost_usd, cost_status "
        f"from session_model_usage where {_IN_WINDOW}",
        (since, SESSION_CAP),
    ):
        m = by_model.setdefault(str(model or "unknown"), {"turns": 0, "input_tokens": 0, "output_tokens": 0, "calls": 0})
        m["input_tokens"] += int(tin or 0)
        m["output_tokens"] += int(tout or 0)
        m["calls"] += int(calls or 0)
        if cost is not None and status not in (None, "", "unknown"):
            usd += float(cost)
            recorded = True
    cost_out = {"usd": round(usd, 4), "why": None} if recorded else {"usd": None, "why": "Hermes recorded no cost for these models"}
    return by_model, cost_out


def _turns(c: sqlite3.Connection, since: float, now: float, model_of: dict[str, str],
           source_of: dict[str, str]) -> dict[str, Any]:
    """One walk over the window's messages: turns per hour by model, outcomes by kind, latency."""
    hours: list[Counter[str]] = [Counter() for _ in range(24)]
    kinds: dict[str, Counter[str]] = defaultdict(Counter)
    flows: Counter[tuple[str, str]] = Counter()  # (kind, model) -> turns
    latencies: list[float] = []
    open_turn: tuple[str, float] | None = None  # (session, asked at)

    def close_unanswered() -> None:
        if open_turn:
            sid, asked = open_turn
            kinds[source_of.get(sid, "unknown")]["running" if now - asked < RUNNING_S else "no_reply"] += 1

    rows = c.execute(
        f"select session_id, role, finish_reason, timestamp from messages where {_IN_WINDOW} "
        # Newest ids first, so the cap drops the oldest sessions (Hermes's ids begin with the date).
        "and timestamp >= ? and role in ('user', 'assistant') order by session_id desc, timestamp limit ?",
        (since, SESSION_CAP, since, MESSAGE_CAP),
    )
    for sid, role, finish, at in rows:
        if open_turn and open_turn[0] != sid:
            close_unanswered()
            open_turn = None
        kind = source_of.get(sid, "unknown")
        if role == "user":
            close_unanswered()
            open_turn = (sid, float(at))
            kinds[kind]["turns"] += 1
            flows[(kind, model_of.get(sid, "unknown"))] += 1
            bucket = _hour(now, float(at))
            if bucket is not None:
                hours[bucket][model_of.get(sid, "unknown")] += 1
        elif open_turn and finish in ("stop", "length"):
            # The reply that ends the turn; "tool_calls" is a step inside it.
            kinds[kind]["answered" if finish == "stop" else "cut_off"] += 1
            latencies.append(float(at) - open_turn[1])
            open_turn = None
    close_unanswered()

    return {
        "hours": [{"at": now - (23 - i) * 3600, "turns": dict(h)} for i, h in enumerate(hours)],
        "kinds": [
            {"kind": k, **{f: int(v[f]) for f in ("turns", "answered", "cut_off", "no_reply", "running")}}
            for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]["turns"])
        ],
        "flows": [{"kind": k, "model": m, "turns": n}
                  for (k, m), n in sorted(flows.items(), key=lambda kv: (-kv[1], kv[0]))],
        "latency": {"turns": len(latencies), "p50_ms": _pct(latencies, 0.5), "p95_ms": _pct(latencies, 0.95)},
    }


def _tools(c: sqlite3.Connection, since: float, now: float) -> dict[str, Any]:
    per_minute = [0] * 60
    top: Counter[str] = Counter()
    for name, at in c.execute(
        f"select tool_name, timestamp from messages where {_IN_WINDOW} and timestamp >= ? and role = 'tool' "
        "order by timestamp desc limit ?",
        (since, SESSION_CAP, since, MESSAGE_CAP),
    ):
        top[str(name or "unknown")] += 1
        back = int((now - float(at)) // 60)
        if 0 <= back < 60:
            per_minute[59 - back] += 1
    return {"per_minute": per_minute,
            "top": [{"name": n, "calls": k} for n, k in sorted(top.items(), key=lambda t: (-t[1], t[0]))[:TOP_TOOLS]]}


def collect(db: Path, now: float | None = None) -> dict[str, Any]:
    """The board for the window ending ``now``. A section whose query fails (a column a Hermes
    bump renamed) is None, with the reason under ``errors``; the others still fill."""
    now = time.time() if now is None else now
    since = now - WINDOW_S
    out: dict[str, Any] = {"schema": SCHEMA, "at": now, "window_s": WINDOW_S, "models": None, "hours": None, "flows": None,
                           "kinds": None, "latency": None, "tools": None, "cost": None, "errors": {}}
    if not db.exists():
        out["errors"]["db"] = "no state.db"
        return out
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    try:
        sessions = c.execute(
            f"select id, source, model from sessions where {_ACTIVE} >= ? order by {_ACTIVE} desc limit ?",
            (since, SESSION_CAP),
        ).fetchall()
        model_of = {sid: str(model or "unknown") for sid, _source, model in sessions}
        probes = _probes.ids()  # a deploy's own check turns: reported as their own kind, still counted in TURNS
        source_of = {sid: PROBE_KIND if sid in probes else str(source or "unknown") for sid, source, _model in sessions}

        by_model: dict[str, dict[str, Any]] = {}
        try:
            by_model, out["cost"] = _usage(c, since)
        except sqlite3.Error as exc:
            out["errors"]["usage"] = str(exc)
        try:
            turns = _turns(c, since, now, model_of, source_of)
            out.update(turns)
            for h in turns["hours"]:
                for model, n in h["turns"].items():
                    by_model.setdefault(model, {"turns": 0, "input_tokens": 0, "output_tokens": 0, "calls": 0})
                    by_model[model]["turns"] += n
        except sqlite3.Error as exc:
            out["errors"]["turns"] = str(exc)
        try:
            out["tools"] = _tools(c, since, now)
        except sqlite3.Error as exc:
            out["errors"]["tools"] = str(exc)
        out["models"] = [{"model": m, **v} for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]["turns"])]
    except sqlite3.Error as exc:
        out["errors"]["db"] = str(exc)
    finally:
        c.close()
    return out


def write(path: Path, data: dict[str, Any]) -> None:
    """Atomically, 0640 in a 0750 directory: the adapter reads it as group hermes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o750)
    # A name of its own: the gateway and `hermes serve` each load the plugin, and two writers
    # sharing one tmp name could publish each other's half-written file.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".board-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, separators=(",", ":")))
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


_LOCK = threading.Lock()
_last = 0.0
_warned = False
_pending: threading.Timer | None = None


def refresh(now: float | None = None) -> bool:
    """Write the board unless one was written in the last :data:`MIN_GAP_S`; never raises."""
    global _last, _warned
    now = time.time() if now is None else now
    if now - _last < MIN_GAP_S or not _LOCK.acquire(blocking=False):
        return False
    try:
        _last = now
        write(board_path(), collect(_hermes_home() / "state.db", now))
        return True
    except Exception as exc:  # noqa: BLE001 - a board must never break a turn
        if not _warned:  # once per process, not once per turn
            _warned = True
            _LOGGER.warning("board: not written (%s); the adapter reports its age", exc)
        return False
    finally:
        _LOCK.release()


def after(**_kwargs: Any) -> None:
    """The post_llm_call / post_tool_call hook: a refresh on a thread of its own, when due, and
    one when the gap ends for a hook that fell inside it: the reply that ends a burst usually
    does, and dropped, its turn would read as running until the next turn."""
    global _pending
    wait = MIN_GAP_S - (time.time() - _last)
    if wait <= 0:
        threading.Thread(target=refresh, name="aura-board", daemon=True).start()
    elif _pending is None or not _pending.is_alive():
        _pending = threading.Timer(wait, lambda: refresh())
        _pending.daemon = True
        _pending.start()
