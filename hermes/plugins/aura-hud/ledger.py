"""The life-admin ledger: what is due, and when (2026-10-08 wants, Phase 7).

Renewals (car registration, passport, domain), warranties (from a receipt photo),
deadlines, medication refills and bills: each a title, a kind and a due date, kept in
the same place as the people and places (``plugin-data/jarvis-routines``), never in the
fact store -- memory is searched on every turn, and a list of dates is not knowledge.
The morning brief lists what is due within a week; ``ledger_list`` answers "what's due".

A recurring item (``every_days``) moves to its next date when marked done rather than
disappearing: a yearly registration, a 30-day prescription. Not covered: anything that
needs a bank, carrier or pharmacy account -- the ledger only knows what it was told.
"""
from __future__ import annotations

import datetime
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from _shared._jsonstore import err as _err, ok as _ok, read_json, write_json

KINDS = ("renewal", "warranty", "deadline", "refill", "bill", "other")
MAX_ITEMS = 300
_LOCK = threading.Lock()


def _path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-routines" / "ledger.json"


def load() -> list[dict]:
    return list(read_json(_path()).get("items") or [])


def _save(items: list[dict]) -> None:
    write_json(_path(), {"items": items})


def due_of(raw: Any) -> Optional[datetime.date]:
    """YYYY-MM-DD only: a relative date ("next Friday") is the model's to resolve first."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw or "").strip()):
        return None
    try:
        return datetime.date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def due_within(items: list[dict], days: int, today: datetime.date) -> list[dict]:
    """Open items due in the next [days] days, overdue ones included, soonest first."""
    out = []
    for it in items:
        due = due_of(it.get("due"))
        if it.get("done") or due is None:
            continue
        until = (due - today).days
        if until <= days:
            out.append({**it, "days_until": until})
    return sorted(out, key=lambda i: i["days_until"])


def handle_ledger_add(args: dict, **_: Any) -> str:
    title = re.sub(r"\s+", " ", str(args.get("title") or "")).strip()[:120]
    if not title:
        return _err("title is required: what is due (\"car registration\", \"Lipitor refill\")")
    due = due_of(args.get("due"))
    if due is None:
        return _err("due is a date, YYYY-MM-DD")
    kind = str(args.get("kind") or "other").strip().lower()
    if kind not in KINDS:
        return _err(f"kind is one of {', '.join(KINDS)}")
    every = args.get("every_days")
    try:
        every_days = int(every) if every not in (None, "") else 0
    except (TypeError, ValueError):
        return _err("every_days is a whole number of days")
    item = {"id": uuid.uuid4().hex[:8], "title": title, "kind": kind, "due": due.isoformat(),
            "every_days": max(0, every_days), "notes": str(args.get("notes") or "").strip()[:300], "done": False}
    with _LOCK:
        items = load()
        if len([i for i in items if not i.get("done")]) >= MAX_ITEMS:
            return _err("the ledger is full; mark some done first")
        items.append(item)
        _save(items)
    return _ok(item=item, said=f"Noted: {title}, due {due.strftime('%B %-d, %Y')}.")


def handle_ledger_list(args: dict, today: Optional[datetime.date] = None, **_: Any) -> str:
    today = today or datetime.date.today()
    try:
        days = int(args.get("within_days", 30))
    except (TypeError, ValueError):
        return _err("within_days is a number of days")
    items = load()
    kind = str(args.get("kind") or "").strip().lower()
    if kind:
        items = [i for i in items if i.get("kind") == kind]
    return _ok(due=due_within(items, max(0, min(3650, days)), today))


def handle_ledger_done(args: dict, **_: Any) -> str:
    ident = str(args.get("id") or "").strip()
    title = str(args.get("title") or "").strip().casefold()
    with _LOCK:
        items = load()
        hit = next((i for i in items if not i.get("done") and (i["id"] == ident or (title and title in i["title"].casefold()))), None)
        if hit is None:
            return _err("no open item matches; ledger_list shows them")
        due = due_of(hit["due"])
        if hit.get("every_days") and due is not None:
            hit["due"] = (due + datetime.timedelta(days=int(hit["every_days"]))).isoformat()
            said = f"Done. Next {hit['title']} is due {hit['due']}."
        else:
            hit["done"] = True
            said = f"Done: {hit['title']}."
        _save(items)
    return _ok(item=hit, said=said)
