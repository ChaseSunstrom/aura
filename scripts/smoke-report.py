#!/usr/bin/env python3
"""Send what a smoke run proved to the adapter's last-verified board (T-0580).

    python3 scripts/smoke-report.py SMOKE_LOG

scripts/smoke.sh runs this on its own output when the file sits beside it. Reads the run's
``PASS: ...`` and ``FAIL: ...`` lines, maps each pass to a check by its prefix
(tests/contracts/verified.json binds the table), and PUTs the run to ``$AURA_BASE/api/verified``
with ``$AURA_API_KEY``. The tag is ``$SMOKE_TAG``, else the running jarvis container's image tag.

Never decides a deploy: any trouble is one line on stderr and exit 0.
Not covered: a smoke that could not reach the adapter at all (nothing can be sent).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

#: A PASS line's first words -> the board's check id. Longest prefix first where two share a start.
PREFIXES = (
    ("health", "health"),
    ("approval matrix", "matrix"),
    ("SSE chunks", "chat"),
    ("context window", "context"),
    ("terminal tool ran", "tool"),
    ("goal loop achieved", "goal"),
    ("failing gate blocked", "gate"),
    ("the agent pulled up panel", "panel"),
    ("hud_layout get answered", "layout"),
    ("approval for", "approval"),
    ("TTS", "speech"),
    ("voice", "voice"),
    ("erase doors", "erase"),
)


#: A check -> the "== N." section of smoke.sh that runs it: a failure is laid at the first
#: check of the section it happened in that had not passed.
SECTIONS = {"health": "1.", "matrix": "1b.", "chat": "2.", "context": "2b.", "tool": "3.", "goal": "4.",
            "gate": "4.", "panel": "4.", "layout": "4.", "approval": "4.", "speech": "6.", "voice": "6b.", "erase": "7."}


def read(log: str) -> tuple[list[str], str | None, str | None]:
    """(the checks that passed, the first failure or None, the check it failed at or None)."""
    passed: list[str] = []
    failed: str | None = None
    section = at = ""
    for line in log.splitlines():
        if line.startswith("== ") and failed is None:
            section = (line.split() + [""])[1]
        elif line.startswith("PASS: "):
            said = line[6:]
            check = next((c for prefix, c in PREFIXES if said.startswith(prefix)), None)
            if check and check not in passed:
                passed.append(check)
        elif line.startswith("FAIL: ") and failed is None:
            failed, at = line[6:][:2000], section
    if failed is None and "ALL SMOKE CHECKS PASSED" not in log:
        failed, at = "the smoke ended before its last check", section
    where = next((c for c, s in SECTIONS.items() if s == at and c not in passed), None) if failed else None
    return passed, failed, where


def tag() -> str:
    if os.environ.get("SMOKE_TAG"):
        return os.environ["SMOKE_TAG"][:80]
    try:
        image = subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}", "jarvis"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        image = ""
    return (image.rsplit(":", 1)[-1] if ":" in image else "")[:80] or "unknown"


def main(argv: list[str]) -> int:
    try:
        with open(argv[1], encoding="utf-8", errors="replace") as fh:
            passed, failed, where = read(fh.read())
        body = json.dumps({"tag": tag(), "at": time.time(), "passed": passed, "failed": failed,
                           "failed_check": where}).encode()
        req = urllib.request.Request(
            os.environ.get("AURA_BASE", "http://127.0.0.1:8643") + "/api/verified", data=body, method="PUT",
            headers={"Authorization": "Bearer " + os.environ["AURA_API_KEY"], "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        print(f"smoke-report: {len(passed)} passed" + (f", failed: {failed[:80]}" if failed else ""), file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - reporting must never fail a deploy
        print(f"smoke-report: not sent ({type(exc).__name__}: {exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
