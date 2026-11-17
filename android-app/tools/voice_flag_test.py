#!/usr/bin/env python3
"""Executable spec: the phone's and the car's spoken turns send `voice: true` (T-0644).

`tests/contracts/voice-flag.json` lists every sender and the text it must contain; this reads
the Kotlin back for the senders under android-app/ (the console's are held by
jarvis-web/src/lib/hud/voice.flag.test.ts). A sender that stopped sending it would run its
spoken turns at the typed effort whenever the speaker gate is off or refuses the voice, and
the first word would wait on full reasoning again.

Run:  python3 android-app/tools/voice_flag_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "tests/contracts/voice-flag.json"


def main() -> int:
    contract = json.loads(CONTRACT.read_text())
    failures = 0
    ours = 0
    for name, sender in contract["senders"].items():
        if not (sender["file"] or "").startswith("android-app/"):
            continue
        ours += 1
        src = (ROOT / sender["file"]).read_text(encoding="utf-8")
        if sender["needle"] not in src:
            print(f"FAIL {name}: {sender['file']} no longer contains {sender['needle']!r}")
            failures += 1
    # A typed turn never sends it: the conversation's typed submit takes the default.
    conv = (ROOT / contract["senders"]["phone_conversation"]["file"]).read_text(encoding="utf-8")
    if "submit(typed)" not in conv:
        print("FAIL the typed turn no longer calls submit(typed) with the voice default (false)")
        failures += 1
    if ours < 4:
        print(f"FAIL the contract names only {ours} android senders")
        failures += 1
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(f"voice flag: {ours} android senders hold to the contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
