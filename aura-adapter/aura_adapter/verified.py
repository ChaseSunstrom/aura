"""The last-verified board: what was last proved, check by check and surface by surface (T-0580).

After a deploy, ``scripts/smoke-report.py`` sends which checks passed, the deployed tag and the
first failure if there was one. This keeps, per check, when it last passed and on which tag,
and the last run. A check that last passed on an older tag is shown as not proved on the
deploy that is running now. ``scripts/verify/golden-turn.mjs`` sends, per surface, whether its
turn passed, failed or was skipped and why; each surface keeps its last pass beside its last
result.

Not covered: any history beyond the last pass, a deploy whose smoke never ran or could not
reach the adapter (the board then still shows the run before it), and whether a surface's
"passed" was a whole turn: the reporter says what it drove, this only keeps it.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from . import artifacts

#: Beside the artifacts, the adapter's one writable volume. Not ``.json``: the artifact sweep
#: reads every ``*.json`` under the root as an artifact's metadata.
FILE = artifacts.ROOT / "verified.state"

#: The checks, in the smoke's order; ``tests/contracts/verified.json`` binds these to the
#: reporter's prefixes and the console's labels.
WORDS = {"health": "health", "matrix": "approval matrix", "chat": "streamed reply",
         "context": "context window", "tool": "tool call", "goal": "goal loop", "gate": "failing gate",
         "panel": "panel", "layout": "layout", "approval": "approval", "speech": "speech round trip",
         "voice": "persona voice", "erase": "erase doors"}
CHECKS = tuple(WORDS)
#: The surfaces the golden turn reports on, in the board's order.
SURFACES = {"console": "console", "car": "car screen", "phone": "phone app", "desktop": "desktop app"}
NOT_RUN = "not run yet: scripts/verify/golden-turn.mjs"
MAX_TAG = 80
MAX_FAILED = 2000
MAX_WHY = 200


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load() -> dict[str, Any]:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return _dict(data)


def _surfaces(value: Any) -> dict[str, dict[str, Any]] | None:
    if value is None:
        return {}
    if not isinstance(value, dict) or not value:
        return None
    for name, row in value.items():
        if name not in SURFACES or not isinstance(row, dict):
            return None
        why = row.get("why", "")
        ok = row.get("ok")
        # `in (True, False, None)` would take 1 and 0, which the console would draw as a pass.
        if not (ok is None or isinstance(ok, bool)) or not isinstance(why, str) or len(why) > MAX_WHY:
            return None
    return {name: {"ok": row.get("ok"), "why": row.get("why", "")} for name, row in value.items()}


def parse(body: Any) -> dict[str, Any] | None:
    """A report (a smoke run, surface results, or both), or None when it is neither.

    ``tag`` and ``passed`` make it a smoke run; ``surfaces`` alone is a golden turn.
    """
    if not isinstance(body, dict):
        return None
    at, surfaces = body.get("at"), _surfaces(body.get("surfaces"))
    # NaN and Infinity are valid to json.loads, and would be written back as a file no
    # strict reader (the console's JSON.parse) can read.
    if isinstance(at, bool) or not isinstance(at, (int, float)) or surfaces is None:
        return None
    try:
        at = float(at)
    except OverflowError:
        return None
    if not math.isfinite(at):
        return None
    if "tag" not in body:
        return {"at": at, "surfaces": surfaces} if surfaces else None
    tag, passed, failed, failed_check = body.get("tag"), body.get("passed"), body.get("failed"), body.get("failed_check")
    if not isinstance(tag, str) or not 0 < len(tag) <= MAX_TAG:
        return None
    if not isinstance(passed, list) or any(p not in CHECKS for p in passed):
        return None
    if failed is not None and (not isinstance(failed, str) or len(failed) > MAX_FAILED):
        return None
    if failed_check is not None and failed_check not in CHECKS:
        return None
    return {"at": at, "surfaces": surfaces, "tag": tag, "passed": sorted(set(passed), key=CHECKS.index),
            "failed": failed, "failed_check": failed_check}


def record(run: dict[str, Any]) -> None:
    """Stamp what passed and keep the run. Written whole, then renamed: a crash leaves the old file."""
    data = _load()
    checks, surfaces, last = _dict(data.get("checks")), _dict(data.get("surfaces")), data.get("run")
    if "tag" in run:
        for check in run["passed"]:
            checks[check] = {"passed_at": run["at"], "tag": run["tag"]}
        last = {"tag": run["tag"], "at": run["at"], "ok": run["failed"] is None, "failed": run["failed"],
                "failed_check": run["failed_check"]}
    for name, row in run["surfaces"].items():
        passed_at = run["at"] if row["ok"] is True else _dict(surfaces.get(name)).get("passed_at")
        surfaces[name] = {"ok": row["ok"], "why": row["why"], "at": run["at"], "passed_at": passed_at}
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"checks": checks, "surfaces": surfaces, "run": last}), encoding="utf-8")
    os.replace(tmp, FILE)


def board() -> dict[str, Any]:
    data = _load()
    run = _dict(data.get("run")) or None
    stamps, seen = _dict(data.get("checks")), _dict(data.get("surfaces"))
    rows = []
    for check in CHECKS:
        stamp = _dict(stamps.get(check))
        rows.append({"id": check, "words": WORDS[check], "passed_at": stamp.get("passed_at"), "tag": stamp.get("tag"),
                     "current": run is not None and stamp.get("tag") == run.get("tag")})
    surfaces = []
    for name, words in SURFACES.items():
        row = _dict(seen.get(name))
        surfaces.append({"id": name, "words": words, "ok": row.get("ok"), "at": row.get("at"),
                         "passed_at": row.get("passed_at"), "why": row.get("why", NOT_RUN) if row else NOT_RUN})
    return {"checks": rows, "run": run, "surfaces": surfaces}
