"""What should have happened and did not: the gate of the "Health check (daily)" job.

Reads four stores without a model -- Hermes' cron state (``cron/jobs.json``), the routines
store, the watches store and the backup container's status files -- and names only what is broken *now*, each breakage once. A
breakage is remembered by key in ``plugin-data/jarvis-health/seen.json`` and forgotten the
moment it stops being true, so the same job failing again next month is news again.

The stores are read as files, not imported: the cron script loads this module by path, so
there is no package for a sibling import to resolve against.

What it does NOT see: the phone runs recurring routines and reports nothing back except that
a one-off is spent (``routine_done``), so a recurring routine that never fires is invisible
here; only a one-off still stored long after its time is caught. Nor does the phone record
when it last synced its routines. Of the backups it sees only the verdict of the last nightly and
the last drill, not the backups: not that the mirror is current, not how big or old the newest
file is, and a status file written by something other than jarvis-backup would be believed. A
nightly that stopped running altogether is not caught (only a drill that did), and with no
status directory mounted, a fresh install, or a drill that never ran, it says nothing.
"""
from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path
from typing import Any

#: A job whose next run is this far in the past means the scheduler is not running it.
CRON_LATE_S = 30 * 60
#: A one-shot job retires as completed when it runs; one finished this recently whose run or
#: delivery failed is still news (a missed reminder). Two days covers a daily check that ran late.
ONESHOT_FRESH_S = 2 * 86400
#: A one-off routine still stored this long after its time never ran on the phone.
ROUTINE_LATE_S = 2 * 3600
#: Consecutive failed looks (ten minutes apart) before a watch counts as unable to check;
#: watches.check_all keeps the count in each watch's ``fails``.
WATCH_FAILS = 3
#: Where the jarvis-backup container leaves its one-line status files: a small volume the compose
#: file mounts here read-only, so the assistant sees the verdict and never the backups. A fixed
#: path, not a setting: it is a mount point, and a plugin reads no environment of its own.
BACKUP_STATUS_DIR = Path("/opt/backup-status")
#: The drill runs on Sundays; a week and a day without a passing one means it is not running.
DRILL_STALE_S = 8 * 86400


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ts(raw: Any) -> float | None:
    try:
        return datetime.datetime.fromisoformat(str(raw)).timestamp()
    except ValueError:
        return None


def _clean(text: Any, n: int = 120) -> str:
    # Stored text is data: one bounded line, so it cannot carry a paragraph at the model.
    return " ".join(str(text or "").split())[:n]


def breakages(now: float) -> dict[str, str]:
    """{key: one line}, for everything broken at [now]."""
    out: dict[str, str] = {}
    jobs = _json(_home() / "cron" / "jobs.json").get("jobs") or []
    for j in jobs:
        if j.get("state") == "completed":
            ran = _ts(j.get("last_run_at"))
            if ran is None or now - ran > ONESHOT_FRESH_S:
                continue
        elif not j.get("enabled", True):
            continue
        name, jid = _clean(j.get("name"), 60), j.get("id")
        err = j.get("last_error") or j.get("last_delivery_error")
        if j.get("last_status") not in (None, "ok") or err:
            why = _clean(err) or j.get("last_status")
            out[f"cron:{jid}"] = f"The scheduled job “{name}” failed its last run: {why}."
        due = _ts(j.get("next_run_at"))
        if due is not None and now - due > CRON_LATE_S:
            out[f"cron-late:{jid}"] = f"The scheduled job “{name}” is overdue and has not run."
    store = _json(_home() / "plugin-data" / "jarvis-routines" / "memory.json")
    for r in store.get("routines") or []:
        w = r.get("when") or {}
        if r.get("enabled", True) and w.get("event") == "time" and w.get("date"):
            at = _ts(f"{w['date']}T{w.get('at')}:00")
            if at is not None and now - at > ROUTINE_LATE_S:
                out[f"routine:{r.get('id')}"] = (
                    f"The routine “{_clean(r.get('name'), 60)}” was due on {w['date']} "
                    f"at {w['at']} and never ran.")
    for w in _json(_home() / "plugin-data" / "jarvis-watches" / "watches.json").get("watches") or []:
        if int(w.get("fails") or 0) >= WATCH_FAILS:
            out[f"watch:{w.get('id')}"] = f"The watch on {_clean(w.get('target'), 80)} cannot be checked."
    out.update(_backups(now))
    return out


def _status(kind: str) -> tuple[int, str] | None:
    """(epoch, "ok" | "failed") from the backup container's one-line status, else None.

    Anything but exactly ``<kind> <digits> ok|failed`` is ignored: a half-written, empty or
    foreign file is not a failed drill, and what is in the file is never passed on as text.
    """
    try:
        raw = (BACKUP_STATUS_DIR / f"{kind}.status").read_text(encoding="utf-8")[:200]
    except (OSError, ValueError):
        return None
    parts = raw.split()
    if len(parts) != 3 or parts[0] != kind or not parts[1].isdecimal() or parts[2] not in ("ok", "failed"):
        return None
    return int(parts[1]), parts[2]


def _backups(now: float) -> dict[str, str]:
    out: dict[str, str] = {}
    nightly, drill = _status("nightly"), _status("drill")
    if nightly and nightly[1] == "failed":
        out["backup-nightly"] = ("The nightly backup of the assistant's home failed its last run, "
                                 "so there may be no new backup: see the jarvis-backup log.")
    if drill and drill[1] == "failed":
        out["backup-drill"] = ("The weekly backup drill failed: the newest backup could not be restored "
                               "and read back, so it may not be usable. See the jarvis-backup log.")
    elif drill and now - drill[0] > DRILL_STALE_S:
        # Only once a drill has left a status: a fresh install, whose first Sunday is ahead of it,
        # is not broken. A failed drill is already the key above.
        out["backup-drill-stale"] = ("No backup drill has passed for more than eight days, so no "
                                     "backup is proven restorable: check that jarvis-backup is running.")
    return out


def main() -> int:
    """Lines for the model for breakages not yet said, else the wake gate's nothing."""
    now = time.time()
    found = breakages(now)
    path = _home() / "plugin-data" / "jarvis-health" / "seen.json"
    seen = _json(path)
    seen = seen if isinstance(seen, dict) else {}
    new = [line for key, line in sorted(found.items()) if key not in seen]
    kept = {k: seen.get(k, now) for k in found}  # a recovered breakage drops out here
    if kept != seen:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept), encoding="utf-8")
    if new:
        print("\n".join(f"- {line}" for line in new))
    else:
        print(json.dumps({"wakeAgent": False}))
    return 0
