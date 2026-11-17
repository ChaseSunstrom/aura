"""Habits: the same ask from Sir at about the same time on several days, offered as a routine.

``note_turn`` (``pre_llm_call``) keeps Sir's own opening words of each turn for two weeks:
only his surfaces (Hermes' "tui": the console, the phone, the car), never a voice the
speaker gate calls a guest, never the API that n8n and scripts call, never a turn opened by
an event, a routine or a job, never a live-rig probe (its session id carries the rig's
``test:`` namespace) or a deploy's smoke or golden turn (it reports the surface ``probe``) or a
cron session, and never anything a tool read. ``find`` groups them by the
words and finds an ask said on MIN_DAYS separate days within one hour of the clock.

The "Habits and trials" cron gate (``habit-check.py`` -> ``main``) wakes the model only when
there is something to say: a habit not offered before (the model offers it as a routine on
a seven-day trial, ``routine_add`` with ``trial_days``), or a trial routine whose week is up
(asked once: keep it with ``routine_update keep``, drop it with ``routine_remove``).

Not covered: asks worded differently each time (only case, punctuation and spacing are
ignored), a window that spans midnight, habits tied to a place or the car (the weekly
"Habit finder" job reads those from history), and a trial nobody answers -- it keeps
running, no longer on trial in name only.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from _shared import _probes
from _shared._jsonstore import read_json, write_json

WINDOW_DAYS = 14
MIN_DAYS = 3
WINDOW_MIN = 60
#: "yes please", "thanks", "stop": the replies said every day are not asks.
MIN_WORDS = 3
MAX_TURNS = 2000
#: The namespace the live rig names its conversations with; tests/contracts/rig-session.json
#: pins it to testing/live/ground.py. A turn through the real console has Hermes' own session id,
#: so it carries PROBE_SURFACE instead (below).
RIG_SESSION = "test:"
#: Hermes names a scheduled job's session ``cron_<job>_<time>``; its platform is not "tui"
#: either, so this only backs up that check.
CRON_SESSION = "cron_"
#: What a deploy's own probe turns (scripts/smoke.sh, scripts/verify/golden-turn.mjs) say they are
#: on prompt.submit as ``surface.name``: Hermes chose their session id, so the id cannot tell them
#: from Sir's. tests/contracts/rig-session.json pins it to both scripts.
PROBE_SURFACE = "probe"
_EVENT = re.compile(r"^\s*\[")
_LOCK = threading.Lock()


def _path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-routines" / "habits.json"


def load() -> dict:
    data = read_json(_path())
    return {"turns": list(data.get("turns") or []), "offered": list(data.get("offered") or [])}


def _key(text: Any) -> str:
    return " ".join(re.findall(r"\w+", str(text or "").casefold()))


def _inert(text: Any, cap: int = 200) -> str:
    """His words quoted as data: a turn can hold words he read aloud from someone else."""
    return "«" + re.sub(r"[\s«»]+", " ", str(text or "")).strip()[:cap] + "»"


def _guest(session_id: str) -> bool:
    from . import later  # the same speaker-gate read the later queue trusts

    return later._guest(session_id)


def _probe(session_id: str) -> bool:
    """Whether the session's latest prompt said it is a deploy probe (T-1201). Reads the adapter's
    surface record, as the phone check does; an adapter that cannot answer counts the turn, so
    Sir's own ask is never lost to an outage."""
    from . import phone

    surface = (phone.surface_record(session_id) or {}).get("surface")
    return isinstance(surface, dict) and surface.get("name") == PROBE_SURFACE


def note_turn(session_id: str = "", user_message: Any = "", platform: str = "",
              now: Optional[float] = None, **_: Any) -> None:
    said = re.sub(r"\s+", " ", str(user_message or "")).strip()[:200]
    if platform != "tui" or not said:
        return
    # A probe from the live rig, or a scheduled job's prompt, is not Sir asking (T-0523).
    if RIG_SESSION in session_id or session_id.startswith(CRON_SESSION):
        return
    # Before the wording filters: the Board reads the recorded ids to name a deploy's turns (T-1142),
    # and a probe prompt that is short or opens with "[" must still be recognised.
    if _probe(session_id):
        _probes.record(session_id)
        return
    if _EVENT.match(said) or len(_key(said).split()) < MIN_WORDS or _guest(session_id):
        return
    now = time.time() if now is None else now
    with _LOCK:
        data = load()
        data["turns"] = [t for t in data["turns"] if now - t["t"] <= WINDOW_DAYS * 86400][-MAX_TURNS:]
        data["turns"].append({"t": now, "ask": said})
        write_json(_path(), data)


def forget_since(since: float) -> int:
    """Drop every noted turn stamped at or after [since]; how many went. Does NOT touch
    `offered` (keys with no stamp): a habit already offered stays offered."""
    with _LOCK:
        data = load()
        keep = [t for t in data["turns"] if t["t"] < since]
        gone = len(data["turns"]) - len(keep)
        if gone:
            data["turns"] = keep
            write_json(_path(), data)
    return gone


def find(turns: list[dict], now: float) -> list[dict]:
    """Each ask said on MIN_DAYS separate days within WINDOW_MIN of the clock, in the last
    WINDOW_DAYS: its words, the earliest time in the window ("07:05") and how many days."""
    groups: dict[str, list[tuple[int, datetime.date, str]]] = defaultdict(list)
    for t in turns:
        if 0 <= now - t["t"] <= WINDOW_DAYS * 86400:
            d = datetime.datetime.fromtimestamp(t["t"])
            groups[_key(t["ask"])].append((d.hour * 60 + d.minute, d.date(), t["ask"]))
    out = []
    for key, said in groups.items():
        said.sort()
        for start, _, ask in said:
            days = {day for m, day, _ in said if start <= m < start + WINDOW_MIN}
            if len(days) >= MIN_DAYS:
                out.append({"key": key, "ask": ask, "at": f"{start // 60:02d}:{start % 60:02d}",
                            "days": len(days)})
                break
    return out


def main(routines: Any, now: Optional[float] = None) -> int:
    """The cron gate: one line per new habit and per ended trial, or the wake gate's nothing.
    [routines] is the plugin's routines module (the gate loads both by path)."""
    now = time.time() if now is None else now
    have = {_key(r.get("do")) for r in routines.load()["routines"]}
    with _LOCK:
        data = load()
        new = [h for h in find(data["turns"], now) if h["key"] not in data["offered"] and h["key"] not in have]
        if new:
            data["offered"] = (data["offered"] + [h["key"] for h in new])[-500:]
            write_json(_path(), data)
    lines = [f"- habit: Sir asked {_inert(h['ask'])} on {h['days']} days around {h['at']}" for h in new]
    lines += [f"- trial over: [{r['id']}] {_inert(routines.describe(r))} -- ask Sir once: keep it?"
              for r in routines.take_ended_trials(now)]
    print("\n".join(lines) if lines else json.dumps({"wakeAgent": False}))
    return 0
