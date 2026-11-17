"""The later queue: "do this later", worked in the background, answered when it is done.

Asks Sir cannot take now -- a long question while driving, "when I'm home", "tonight" --
are saved with a due time. A cron gate (``later-check.py``) wakes the model every ten
minutes only when one is due. The worker turn does the ask under the normal approval matrix
(nothing gated runs unasked), tells him through house_tell_user and records the answer with
``later_done``; the morning brief reads the results.

``when`` is resolved to a clock time at queue time: parked +30 min and home +60 min are
fallback guesses: the drive-end event calls ``release("parked")`` the moment the car
session really ends, which makes those items due then. Nothing releases "home" yet, and
an item queued before ``when`` was stored keeps its guess.
Not covered: a worker turn that dies mid-ask (the item sits ``running`` and is retried
after two hours), and an ask that needs him to answer a question first.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from _shared._jsonstore import err as _err, ok as _ok, read_json, write_json

MAX_ITEMS = 100
RETRY_AFTER = 2 * 3600
_LOCK = threading.Lock()
_DELAYS = {"parked": 30 * 60, "home": 60 * 60}


def _path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-routines" / "later.json"


def load() -> list[dict]:
    return list(read_json(_path()).get("items") or [])


def _save(items: list[dict]) -> None:
    write_json(_path(), {"items": items})


def due_at(when: Any, now: float) -> Optional[tuple[float, str]]:
    """(epoch, how) for parked / home / tonight / morning / a local datetime, else None.

    "morning" is not worked by the gate: how == "brief" leaves it for the 07:00 brief.
    """
    word = str(when or "").strip().lower()
    if word in _DELAYS:
        return now + _DELAYS[word], "worker"
    local = datetime.datetime.fromtimestamp(now)
    if word in ("tonight", "morning"):
        t = local.replace(hour=21 if word == "tonight" else 7, minute=0, second=0, microsecond=0)
        if t <= local:
            t += datetime.timedelta(days=1)
        return t.timestamp(), "worker" if word == "tonight" else "brief"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", word):
        try:
            return datetime.datetime.fromisoformat(word).timestamp(), "worker"
        except ValueError:
            return None
    return None


#: Tools that bring no one else's words into the turn. Any other tool may (a page, a mail, a
#: text, a file, a command's output, a sub-agent's answer), so it marks the turn: an allowlist,
#: because a list of the tools that read outside text is never complete, and stored people,
#: places and routines can hold words someone else wrote (commit reviews, 2026-10-09).
_QUIET = {"later_add", "later_list", "later_drop", "later_done", "later_release"}
_EVENT = re.compile(r"^\s*\[")
#: Per session, this turn: Sir's own words, and whether anything else has been read since.
_TURN: dict[str, dict] = {}


def note_tool(tool_name: str = "", session_id: str = "", **_: Any) -> None:
    """``post_tool_call``: a tool that may bring outside words marks the turn."""
    if session_id and str(tool_name or "") not in _QUIET:
        with _LOCK:
            if session_id in _TURN:
                _TURN[session_id]["outside"] = True


def turn_start(session_id: str = "", user_message: Any = "", platform: str = "", **_: Any) -> None:
    """``pre_llm_call`` (turn-scoped in Hermes): the words that opened this turn, and where."""
    if not session_id:
        return
    said = re.sub(r"\s+", " ", str(user_message or "")).strip()[:600]
    with _LOCK:
        _TURN[session_id] = {"said": said, "outside": False, "platform": str(platform or "")}
        while len(_TURN) > 500:
            _TURN.pop(next(iter(_TURN)))


def _guest(session_id: str) -> bool:
    try:
        from _shared import _speaker  # the plugin's own sys.path entry
        return _speaker.speaker_of(session_id).get("level") == "guest"
    except Exception:  # noqa: BLE001 - no adapter record: the key holder's turn, as the gate reads it
        return False


def handle_later_add(args: dict, now: Optional[float] = None, session_id: str = "", **_: Any) -> str:
    now = time.time() if now is None else now
    # A queued ask is replayed later in a turn that no longer knows where it came from, so it
    # carries his own words with it and is refused when it cannot (commit review, 2026-10-09):
    # a turn opened by an event, a routine or a job is not Sir speaking, and a turn that has
    # read anything outside since he spoke may be repeating a planted instruction. The worker
    # does only what his words ask; text from earlier turns is never his words here.
    with _LOCK:
        turn = dict(_TURN.get(session_id) or {})
    said = turn.get("said") or ""
    # Only the owner's own surfaces (the console, the phone, the car: Hermes' "tui"), never
    # the API that n8n and scripts call, and never a voice the speaker gate calls a guest.
    if turn.get("platform") != "tui":
        return _err("not queued: only Sir, on the console, phone or car, can queue an ask")
    if _guest(session_id):
        return _err("not queued: a guest cannot queue an ask for Sir -- say so and suggest they ask him")
    if not said or _EVENT.match(said):
        return _err("not queued: only Sir's own words in this turn can queue an ask -- ask him")
    if turn.get("outside"):
        return _err("not queued: this turn read outside text (a page, a message, a file), so the "
                    "queue only takes it in Sir's own next words -- ask him to say it again")
    ask = re.sub(r"\s+", " ", str(args.get("ask") or "")).strip()[:600]
    if not ask:
        return _err("ask is required: the whole request, so a fresh session can do it")
    due = due_at(args.get("when"), now)
    if due is None:
        return _err("when is parked, home, tonight, morning or a local time, YYYY-MM-DD HH:MM")
    item = {"id": uuid.uuid4().hex[:8], "ask": ask, "said": said, "due": due[0], "how": due[1],
            "state": "queued", "result": "", "added": now,
            "when": str(args.get("when") or "").strip().lower()}
    where = re.sub(r"\s+", " ", str(args.get("where") or "")).strip()[:80]
    if where:
        item["where"] = where  # learned.py briefs it when a drive starts, recalls it on arrival
    with _LOCK:
        items = load()
        if len([i for i in items if i["state"] != "done"]) >= MAX_ITEMS:
            return _err("the later queue is full; drop some first")
        items.append(item)
        _save(items)
    return _ok(item=item, said="Queued for later; I'll tell you what I find.")


def handle_later_list(args: dict, **_: Any) -> str:
    """Open items soonest first; with done=true the finished ones and their results."""
    want_done = bool(args.get("done"))
    items = [i for i in load() if (i["state"] == "done") == want_done]
    return _ok(items=sorted(items, key=lambda i: i["due"]))


def handle_later_drop(args: dict, **_: Any) -> str:
    ident = str(args.get("id") or "").strip()
    with _LOCK:
        items = load()
        kept = [i for i in items if i["id"] != ident]
        if len(kept) == len(items):
            return _err("no such item; later_list shows them")
        _save(kept)
    return _ok(said="Dropped.")


def handle_later_done(args: dict, now: Optional[float] = None, **_: Any) -> str:
    ident = str(args.get("id") or "").strip()
    with _LOCK:
        items = load()
        hit = next((i for i in items if i["id"] == ident), None)
        if hit is None:
            return _err("no such item; later_list shows them")
        hit.update(state="done", result=str(args.get("result") or "").strip()[:1200],
                   finished=time.time() if now is None else now)
        _save(items)
    return _ok(item=hit)


def handle_later_release(args: dict, now: Optional[float] = None, **_: Any) -> str:
    """The drive-end event itself makes the asks queued for "parked" due now; only re-dates."""
    word = str(args.get("when") or "").strip().lower()
    if word not in _DELAYS:
        return _err("when is parked or home")
    now = time.time() if now is None else now
    with _LOCK:
        items = load()
        hit = [i for i in items if i["state"] == "queued" and i["how"] == "worker"
               and i.get("when") == word and i["due"] > now]
        for i in hit:
            i["due"] = now
        if hit:
            _save(items)
    return _ok(released=len(hit), said="Released." if hit else "Nothing was waiting.")


def take_due(now: float) -> list[dict]:
    """Worker items now due (or stuck running past RETRY_AFTER); they become ``running``."""
    with _LOCK:
        items = load()
        due = [i for i in items if i["how"] == "worker" and i["due"] <= now and (
            i["state"] == "queued" or (i["state"] == "running" and now - i.get("taken", 0) > RETRY_AFTER))]
        for i in due:
            i.update(state="running", taken=now)
        if due:
            _save(items)
    return due


def main() -> int:
    """The cron gate: one line per due ask for the model, or the wake gate's nothing."""
    due = take_due(time.time())
    if due:
        print("\n".join(f"- [{i['id']}] {i['ask']} (Sir's words: \u00ab{i.get('said', '')}\u00bb)" for i in due))
    else:
        print(json.dumps({"wakeAgent": False}))
    return 0
