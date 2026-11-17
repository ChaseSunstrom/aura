#!/usr/bin/env python3
"""What AURA got stuck on lately, read from the Hermes session store (T-0910).

    aura_recent.py STATE_DB [--hours 24] [--text]

Opens the store read-only and prints one line per finding in the last HOURS:

    loop    the same tool call, or the same reply, three or more times in a row in one turn
    errors  a turn in which a tool answered with an error
    silent  a turn that ended on a tool result, with no reply after it

Each line carries the time, where the session came from, the tool's name and a count. The
words of a message are printed only with --text, and then at most 80 characters of them.
Exit status 1 when anything was found, so a cron row can gate on it.

Not covered: it reads what was stored, so a turn the gateway never wrote is not seen; a
reply that is wrong but different each time is not a loop; the newest turn of a session is
never called silent, because it may still be running.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone

REPEATS = 3
SNIPPET = 80
DENIED = "BLOCKED: Action denied by user"


def _call_key(tool_calls: str | None) -> str | None:
    """A tool-call message's calls as one comparable string: each name with its arguments."""
    if not tool_calls:
        return None
    try:
        calls = json.loads(tool_calls)
    except ValueError:
        return tool_calls
    if not isinstance(calls, list):
        return tool_calls
    parts = []
    for call in calls:
        fn = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn, dict):
            parts.append(f"{fn.get('name')}({fn.get('arguments')})")
    return "|".join(parts) or None


def _tool_names(tool_calls: str | None) -> str:
    key = _call_key(tool_calls) or ""
    return ",".join(part.split("(", 1)[0] for part in key.split("|") if part) or "?"


def _is_error(content: str | None) -> bool:
    """A tool result that says it failed: a JSON object with a truthy `error`, or text that starts so."""
    text = (content or "").lstrip()
    if text[:6].lower() == "error:" or text.startswith("Traceback"):
        return True
    if text.startswith("{"):
        try:
            body = json.loads(text)
        except ValueError:
            return False
        # A denied approval is somebody's answer (the deploy smoke denies one on purpose), not a failure.
        if isinstance(body, dict) and str(body.get("error") or "").startswith(DENIED):
            return False
        return isinstance(body, dict) and bool(body.get("error")) or (
            isinstance(body, dict) and body.get("success") is False
        )
    return False


def findings(db: sqlite3.Connection, since: float) -> list[dict]:
    """Every finding newer than `since` (epoch seconds), oldest first."""
    sources = dict(db.execute("SELECT id, source FROM sessions"))
    rows = db.execute(
        "SELECT session_id, role, content, tool_calls, tool_name, timestamp FROM messages "
        "WHERE timestamp >= ? ORDER BY session_id, id",
        (since,),
    ).fetchall()
    turns: list[list[tuple]] = []
    last: dict[str, int] = {}
    for row in rows:
        if row[1] == "user" or not turns or turns[-1][0][0] != row[0]:
            turns.append([])
            last[row[0]] = len(turns) - 1
        turns[-1].append(row)
    out: list[dict] = []
    for index, turn in enumerate(turns):
        session, at = turn[0][0], turn[0][5]
        base = {"at": at, "source": sources.get(session, "?"), "session": session}
        ask = turn[0][2] if turn[0][1] == "user" else ""
        said = [m for m in turn if m[1] == "assistant"]
        run_key, run = None, 0
        worst: tuple[int, str, str] | None = None
        for m in said:
            key = _call_key(m[3]) or ("say:" + (m[2] or "").strip())
            run = run + 1 if key == run_key and key != "say:" else 1
            run_key = key
            if run >= REPEATS and (worst is None or run > worst[0]):
                worst = (run, "reply" if key.startswith("say:") else _tool_names(m[3]), m[2] or "")
        if worst:
            out.append({**base, "kind": "loop", "what": worst[1], "count": worst[0], "text": ask})
        failed = [m for m in turn if m[1] == "tool" and _is_error(m[2])]
        if failed:
            names = sorted({m[4] or "?" for m in failed})
            out.append({**base, "kind": "errors", "what": ",".join(names), "count": len(failed), "text": ask})
        if turn[-1][1] == "tool" and last[session] != index:
            out.append({**base, "kind": "silent", "what": turn[-1][4] or "?", "count": 1, "text": ask})
    return sorted(out, key=lambda f: f["at"])


def render(found: list[dict], text: bool) -> list[str]:
    lines = []
    for f in found:
        when = datetime.fromtimestamp(f["at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")
        line = f"{when}  {f['source']:<10}  {f['kind']:<6}  {f['what']}  x{f['count']}"
        if text and f["text"]:
            line += "  | " + " ".join(f["text"].split())[:SNIPPET]
        lines.append(line)
    return lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("state_db")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--text", action="store_true", help="add up to 80 characters of what was asked")
    args = ap.parse_args(argv)
    db = sqlite3.connect(f"file:{args.state_db}?mode=ro", uri=True)
    found = findings(db, time.time() - args.hours * 3600)
    for line in render(found, args.text):
        print(line)
    kinds = {k: sum(1 for f in found if f["kind"] == k) for k in ("loop", "errors", "silent")}
    print(f"last {args.hours:g} h: {kinds['loop']} loop(s), {kinds['errors']} turn(s) with tool errors, {kinds['silent']} silent turn(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
