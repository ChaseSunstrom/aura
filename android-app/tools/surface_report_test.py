#!/usr/bin/env python3
"""Executable spec: the phone reports what it can show, and the table is the contract's.

`tests/contracts/surface-capabilities.json` is what the artifacts plugin's registry,
the console, the TUI and the phone all hold to; this reads the phone's Kotlin back and
checks its `SURFACE` table is the contract's `android` row field for field, and that
it rides every `prompt.submit` — the typed turn and the spoken one with its ticket. A
phone that stopped sending it would be told it can show "no media at all" (the
unknown profile) and have its videos handed to the desktop.

Run:  python3 android-app/tools/surface_report_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "tests/contracts/surface-capabilities.json"
CLIENT = ROOT / "android-app/app/src/main/kotlin/ai/aura/app/assist/hermes/HermesClient.kt"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, "" if ok else detail))


def kotlin_map(src: str, decl: str) -> dict[str, object]:
    """`val <decl>: ... = mapOf("k" to v, ...)` -> {'k': v} with Kotlin literals as Python values."""
    m = re.search(re.escape(decl) + r"[^=]*=\s*mapOf\((.*?)\n\s*\)", src, re.S)
    if not m:
        return {}
    out: dict[str, object] = {}
    for key, value in re.findall(r'"(\w+)"\s+to\s+("[^"]*"|true|false|\d+)', m.group(1)):
        if value in ("true", "false"):
            out[key] = value == "true"
        elif value.startswith('"'):
            out[key] = value.strip('"')
        else:
            out[key] = int(value)
    return out


def main() -> int:
    for path in (CONTRACT, CLIENT):
        if not path.is_file():
            print(f"  FAIL  {path.relative_to(ROOT)} is missing")
            return 1
    contract = json.loads(CONTRACT.read_text())
    src = CLIENT.read_text()

    # 1. The table is the contract's android row, field for field and in its order.
    table = kotlin_map(src, "val SURFACE")
    want = contract["surfaces"]["android"]
    check("HermesClient.SURFACE is the contract's android row", table == want, f"phone {table} vs contract {want}")
    check("the fields are the contract's, in order", list(table) == contract["fields"], str(list(table)))

    # 2. It rides every prompt, under the contract's param name, typed or spoken.
    param = contract["param"]
    # One prompt.submit builds text and the report always; the ticket and voice join only when set.
    base = f'"text" to text,\\s*"{param}" to JSONObject\\(SURFACE\\),'
    check("every turn carries the report", re.search(base, src) is not None, base)
    check("a spoken turn carries the report beside its ticket",
          '"speaker_ticket" to speakerTicket' in src, "speaker_ticket")

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("  ok    " if ok else "  FAIL  ") + name + (f"\n        | {detail}" if not ok and detail else ""))
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
