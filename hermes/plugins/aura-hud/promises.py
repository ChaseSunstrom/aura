"""A promise of later is saved, or it is raised on the next turn.

Jarvis has no memory that wakes it up: a turn ends and nothing happens until the next one.
It still said "I can keep an eye out for her reply and add it the moment it lands" (2026-10-08,
after texting Grandma Lori), "I'll wait for it to fire and then verify the file" (09-18) and
"I'll let you know when it has fired" (09-18) -- each with nothing saved that would ever run,
so each was a promise that could not be kept.

``after_turn`` (``post_llm_call``) looks at the finished reply: a sentence that commits to
later ("keep an eye out", "let you know when", "remind you", "I'll wait", "as soon as she
replies") in a turn that called none of the tools that make later happen is remembered for
the session. ``before_turn`` (``pre_llm_call``) hands it back on that session's next turn as
a note to save it now or say plainly that it cannot be done -- in the Lori case the very next
words were "Yeah, can you do that?".

Not covered: a session with no next turn (the note waits in memory and is lost on a
restart), and a promise worded outside the patterns below; the SOUL rule is the other half.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Any, Iterable, Optional

from . import loops

#: The tools whose call means "later" was actually arranged in that turn.
BACKING = {"routine_add", "routine_update", "watch_add", "later_add", "ledger_add", "goal_set", "cronjob"}
MAX_SESSIONS = 200

_MODAL = r"(?:I'll|I will|I can|I'd|I could|let me|shall I|happy to)"
_LATER = (
    r"keep an eye|keep watch|watch for|monitor|let you know|tell you when|ping you|notify you|"
    r"remind you|check back|check again|follow up|get back to you|circle back"
)
_SAID = [
    re.compile(rf"\b{_MODAL}\s+(?:\w+\s+){{0,3}}?(?:{_LATER})\b", re.I),
    # "I'll wait for your OK" is an approval being asked for, not a promise of later.
    re.compile(r"\b(?:I'll|I will)\s+(?:just\s+)?wait\b(?!\s+for\s+(?:your|you|the word|the nod|a yes))", re.I),
    re.compile(
        rf"\b{_MODAL}\b.*\b(?:once|when|as soon as|the moment)\s+(?:she|he|they|it|that)\s+"
        r"(?:replies|reply|gets back|texts|sends|lands|comes back|responds|answers|fires|arrives)\b",
        re.I,
    ),
]
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

_PENDING: "OrderedDict[str, str]" = OrderedDict()
_LOCK = threading.Lock()


def promise_in(reply: str) -> Optional[str]:
    """The sentences of [reply] (at most two) that commit to doing something later, or None."""
    found = [s[:240] for s in _SENTENCE.split(" ".join(str(reply or "").split()))
             if any(p.search(s) for p in _SAID)]
    return " ".join(found[:2]) or None


def _calls_this_turn(history: Iterable[Any]) -> list[tuple[str, str]]:
    """(tool name, raw arguments) of every call since the last user message."""
    out: list[tuple[str, str]] = []
    for msg in reversed(list(history or [])):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user":
            break
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            fn = fn if isinstance(fn, dict) else (call if isinstance(call, dict) else {})
            out.append((str(fn.get("name") or ""), str(fn.get("arguments") or "")))
    return out


def backed(calls: list[tuple[str, str]]) -> bool:
    """Whether the turn arranged something later: a backing tool, or a phone reminder."""
    return any(name in BACKING or (name == "phone_action" and "set_reminder" in args) for name, args in calls)


def after_turn(session_id: str = "", assistant_response: Any = "", conversation_history: Any = None,
               user_message: Any = "", platform: str = "", **_: Any) -> None:
    # A backing tool ran: whatever this session left open was kept (loops.py).
    if backed(_calls_this_turn(conversation_history)):
        loops.close_session(session_id)
        return
    # Only Sir's own turns on his surfaces leave durable loops (commit review, 2026-10-09): a
    # cron turn -- the evening sweep reading the loops back, above all -- would otherwise save
    # its own reply as a new loop every night, and an event or job turn's text is not his.
    own = platform == "tui" and not str(user_message or "").lstrip().startswith("[")
    if own:
        # Sir's aside is an open loop even when the reply promised nothing; durable, unlike _PENDING.
        loops.add(loops.aside_in(str(user_message or "")) or "", "aside", session_id)
    said = promise_in(str(assistant_response or ""))
    if not session_id or not said:
        return
    if own:
        loops.add(said, "promise", session_id)
    with _LOCK:
        _PENDING[session_id] = said
        _PENDING.move_to_end(session_id)
        while len(_PENDING) > MAX_SESSIONS:
            _PENDING.popitem(last=False)


def before_turn(session_id: str = "", **_: Any) -> Optional[dict]:
    with _LOCK:
        said = _PENDING.pop(session_id, None) if session_id else None
    if not said:
        return None
    return {"context": (
        f'[Jarvis\'s own check] Last turn you told Sir: "{said}" -- and nothing was saved that '
        "will make it happen; you have no memory that wakes you. If he wants it, or has just said "
        "yes, save it in this turn: a reply from someone or a later time is routine_add (event "
        "message, or time with a date), watching a service, a page or a house device is "
        "watch_add, a due date is ledger_add. If none of those can do it, tell him plainly that "
        "you cannot. Do not mention this note."
    )}
