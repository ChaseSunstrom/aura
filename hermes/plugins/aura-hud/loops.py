"""Open loops: what was promised or left hanging, kept on disk until it is closed.

``promises.py`` only nags the next turn of the same session, from memory; a restart loses it.
An unbacked promise from Jarvis ("I'll let you know when she replies") and an aside from Sir
("remind me to call the plumber", "I'll get back to you on that") are saved here instead, and
an evening gate (``loops-check.py``) wakes the model only when one is open, to list them in
two lines and offer to queue (later_add) or drop (loops_close) each.

A loop is closed by ``loops_close``, or when a later turn of the same session calls a backing
tool (``promises.BACKING``): that is the promise being kept.

Loops are only listed back to Sir. Their text came out of a reply or a message, so nothing
here ever runs, queues or replays it; queuing is a later_add the model makes under that
tool's own guards, in a turn where Sir said yes.
Not covered: a loop worded outside promises.py's patterns and the aside pattern below, and a
promise kept by a tool in a different session (it stays open until closed or it goes stale).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from _shared._jsonstore import ok as _ok, read_json, write_json

MAX_OPEN = 50
#: An open loop nobody closed in this long is stale, not pressing: the gate stops listing it.
STALE_AFTER = 14 * 86400
_LOCK = threading.Lock()
_ASIDE = re.compile(
    r"\b(?:remind me|get back to you|get back to (?:it|that|this)|circle back|come back to (?:it|that|this)|"
    r"don't let me forget|let me know when)\b", re.I)


def _path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-routines" / "loops.json"


def load() -> list[dict]:
    return list(read_json(_path()).get("items") or [])


def _save(items: list[dict]) -> None:
    write_json(_path(), {"items": items})


def aside_in(said: str) -> Optional[str]:
    """Sir's own sentence that leaves something hanging, or None."""
    for s in re.split(r"(?<=[.!?])\s+", " ".join(str(said or "").split())):
        if _ASIDE.search(s):
            return s[:240]
    return None


def add(text: str, kind: str, session_id: str = "", now: Optional[float] = None) -> None:
    text = " ".join(str(text or "").split())[:240]
    if not text:
        return
    with _LOCK:
        items = load()
        open_ = [i for i in items if i["state"] == "open"]
        if len(open_) >= MAX_OPEN or any(i["text"] == text for i in open_):
            return
        items.append({"id": uuid.uuid4().hex[:8], "text": text, "kind": kind, "session": session_id,
                      "state": "open", "added": time.time() if now is None else now})
        _save(items)


def close_session(session_id: str, now: Optional[float] = None) -> None:
    """A backing tool ran in [session_id]: its open loops were kept."""
    if not session_id:
        return
    with _LOCK:
        items = load()
        hit = [i for i in items if i["state"] == "open" and i.get("session") == session_id]
        for i in hit:
            i.update(state="closed", closed=time.time() if now is None else now, why="backed")
        if hit:
            _save(items)


def handle_loops_list(args: dict, now: Optional[float] = None, **_: Any) -> str:
    now = time.time() if now is None else now
    return _ok(items=[i for i in load() if i["state"] == "open" and now - i["added"] < STALE_AFTER])


def handle_loops_close(args: dict, now: Optional[float] = None, **_: Any) -> str:
    ident = str(args.get("id") or "").strip()
    with _LOCK:
        items = load()
        hit = next((i for i in items if i["id"] == ident and i["state"] == "open"), None)
        if hit is None:
            return json.dumps({"ok": False, "error": "no such open loop; loops_list shows them"})
        hit.update(state="closed", closed=time.time() if now is None else now, why="closed")
        _save(items)
    return _ok(said="Closed.")


def main(now: Optional[float] = None) -> int:
    """The cron gate: the open loops for the model, or the wake gate's nothing."""
    items = json.loads(handle_loops_list({}, now=now))["items"]
    if items:
        print("\n".join(f"- [{i['id']}] ({i['kind']}) {i['text']}" for i in items))
    else:
        print(json.dumps({"wakeAgent": False}))
    return 0
