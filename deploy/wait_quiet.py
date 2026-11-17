#!/usr/bin/env python3
"""Wait for a quiet moment before a deploy recreates jarvis and the adapter (T-0577).

A recreate cuts whatever reply is being spoken, in the house or in the car. This asks the
adapter's /api/status for its "busy" (open voice turns; a turn from the car in the last ten
minutes, which is what a drive looks like from the house) and waits while either holds,
polling, up to a cap; then it returns and the deploy goes on either way. It always exits 0: a deploy must not fail because nobody could say
whether the house was talking.

Does not wait for: a drive nobody has spoken on for ten minutes, a turn Hermes started itself
(cron, a goal), or anything when the adapter is down or slow, answers without "busy" (an older
image) or the key is missing: it says so and goes on. During a drive every deploy waits the
whole cap: the car is "active" for ten minutes after each turn.

    AURA_API_KEY=... python3 wait_quiet.py http://HOST:8643 [--cap 180] [--every 5]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request


def log(message: str) -> None:
    print(f"[wait-quiet] {message}", file=sys.stderr, flush=True)


def busy(base: str, key: str) -> dict | None:
    """The adapter's busy report, or None when it cannot be had."""
    request = urllib.request.Request(f"{base.rstrip('/')}/api/status", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:  # noqa: S310 - the deploy's own adapter
            report = json.load(answer).get("busy")
    except Exception as error:  # noqa: BLE001 - down, refused, not JSON: all "cannot tell"
        log(f"cannot ask the adapter ({type(error).__name__}); not waiting")
        return None
    return report if isinstance(report, dict) else None


def wait(base: str, key: str, cap: float, every: float, sleep=time.sleep, clock=time.monotonic) -> float:
    """Seconds waited. Returns once no turn is open and no drive is on, or the adapter cannot
    say, or at the cap."""
    start = clock()
    while True:
        report = busy(base, key)
        waited = clock() - start
        if report is None or not (report.get("turns") or report.get("car")):
            if waited:
                log(f"quiet after {waited:.0f} s")
            return waited
        if waited >= cap:
            log(f"still busy after {cap:.0f} s; deploying anyway")
            return waited
        why = [f"{report['turns']} voice turn(s) open"] if report.get("turns") else []
        if report.get("car"):
            why.append("the car spoke in the last ten minutes")
        log(f"waiting: {', '.join(why)} ({waited:.0f} of {cap:.0f} s)")
        sleep(every)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base")
    parser.add_argument("--cap", type=float, default=180.0)
    parser.add_argument("--every", type=float, default=5.0)
    args = parser.parse_args()
    key = os.environ.get("AURA_API_KEY") or os.environ.get("JARVIS_API_KEY") or ""
    if not key:
        log("no adapter key in the environment; not waiting")
        return 0
    wait(args.base, key, args.cap, args.every)
    return 0


if __name__ == "__main__":
    sys.exit(main())
