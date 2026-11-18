#!/usr/bin/env python3
"""Executable spec: the line the car says on link-up about what will not work.

`car/service/CarConnectCheck.kt` is the rule; `tests/contracts/car_connect_check.json`
is its table, which the Kotlin unit test runs too. This mirror runs every case and
checks the Kotlin carries each spoken line verbatim, so a reworded line in one place
fails here rather than drifting.

Run:  python3 android-app/tools/car_connect_check_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KOTLIN = ROOT / "android-app/app/src/main/kotlin/ai/aura/app/car/service/CarConnectCheck.kt"
CONTRACT = json.loads((ROOT / "tests/contracts/car_connect_check.json").read_text())
LINES = CONTRACT["lines"]


def line(home: bool | None, console: bool | None, grant_missing: bool) -> str | None:
    parts = []
    if home is False:
        parts.append(LINES["home"])
    elif console is False:
        parts.append(LINES["console"])
    if grant_missing:
        parts.append(LINES["music"])
    return " ".join(parts) or None


REMEDY = CONTRACT["remedy"]


def remedy(home: bool | None, own_tunnel: bool, vpn_apps: list[str]) -> str | None:
    if home is not False or own_tunnel:
        return None
    return REMEDY["text"].replace("{name}", REMEDY["worn"]).replace("{app}", REMEDY["worn"]).replace("{vpn}", " or ".join(vpn_apps) or REMEDY["vpn_fallback"])


def main() -> int:
    failed = 0
    for case in CONTRACT["cases"]:
        got = line(case["home"], case["console"], case["grant_missing"])
        ok = got == case["say"]
        failed += not ok
        print(("PASS" if ok else "FAIL") + f"  {case['name']}" + ("" if ok else f": got {got!r}"))
    for case in REMEDY["cases"]:
        got = remedy(case["home"], case["own_tunnel"], case["vpn_apps"])
        ok = got == case["remedy"]
        failed += not ok
        print(("PASS" if ok else "FAIL") + f"  remedy: {case['name']}" + ("" if ok else f": got {got!r}"))
    source = KOTLIN.read_text() if KOTLIN.exists() else ""
    for key in ("title", "text", "vpn_fallback"):
        ok = json.dumps(REMEDY[key]) in source
        failed += not ok
        print(("PASS" if ok else "FAIL") + f"  Kotlin carries the remedy's {key} verbatim")
    for key, text in LINES.items():
        ok = json.dumps(text) in source
        failed += not ok
        print(("PASS" if ok else "FAIL") + f"  Kotlin carries the {key} line verbatim")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
