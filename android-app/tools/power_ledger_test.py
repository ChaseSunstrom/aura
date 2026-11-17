#!/usr/bin/env python3
"""Executable spec: the phone's daily power ledger (T-0582), the Python mirror of `PowerLedger.kt`.

`tests/contracts/power-ledger.json` holds a made-up day of readings and the sums it must give;
`PowerLedgerTest.kt` feeds them to the Kotlin and this file feeds them to the spec below, so the
rules (an interval is credited to the features on at its start, a gap is capped, a drop is shared
between the features on and counted only while unplugged, midnight rolls the day) live in one table.
It also pins that the Kotlin still carries the contract's constants and that `power_report` answers
the ledger under the key the console's mapper (jarvis-web `hud/data/power.ts`) reads.

Run:  python3 android-app/tools/power_ledger_test.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "tests/contracts/power-ledger.json").read_text())
KOTLIN = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin"
FEATURES = CONTRACT["features"]


def ledger(readings: list[dict]) -> list[dict]:
    days: dict[str, dict] = {}
    last = None
    for r in readings:
        at = datetime.fromisoformat(r["at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        date = at.date().isoformat()
        day = days.setdefault(
            date,
            {"cov": 0, "cpu": 0, "drop": 0.0, "peak": None, "f": {n: {"s": 0, "d": 0.0, "p": None} for n in FEATURES}},
        )
        if "temp_c" in r:
            t = r["temp_c"]
            day["peak"] = t if day["peak"] is None else max(day["peak"], t)
            for n in (n for n in r["on"] if n in FEATURES):
                p = day["f"][n]["p"]
                day["f"][n]["p"] = t if p is None else max(p, t)
        prev, last = last, (at, r)
        if prev is None:
            continue
        dt = int((at - prev[0]).total_seconds())
        if dt <= 0 or prev[0].date() != at.date():
            continue
        credited = min(dt, CONTRACT["max_gap_s"])
        day["cov"] += credited
        on = [n for n in prev[1]["on"] if n in FEATURES]
        for n in on:
            day["f"][n]["s"] += credited
        if dt > CONTRACT["max_gap_s"]:
            continue
        cpu = r["cpu_ms"] - prev[1]["cpu_ms"]
        day["cpu"] += r["cpu_ms"] if cpu < 0 else cpu
        drop = prev[1]["percent"] - r["percent"]
        if not prev[1]["charging"] and not r["charging"] and drop > 0:
            day["drop"] += drop
            for n in on:
                day["f"][n]["d"] += drop / len(on)
    out = []
    for date in sorted(days, reverse=True)[: CONTRACT["keep_days"]]:
        d = days[date]
        row = {
            "date": date,
            "covered_min": round(d["cov"] / 60),
            "cpu_s": round(d["cpu"] / 1000),
            "drop_pct": round(d["drop"], 2),
            "features": {
                n: {"seconds": x["s"], "drop_pct": round(x["d"], 2), **({"peak_temp_c": x["p"]} if x["p"] is not None else {})}
                for n, x in d["f"].items()
            },
        }
        if d["peak"] is not None:
            row["peak_temp_c"] = d["peak"]
        out.append(row)
    return out


def test_the_contract_cases_add_up() -> None:
    for case in CONTRACT["cases"]:
        got = ledger(case["readings"])
        if "days" in case:
            assert got == case["days"], f"{case['name']}: {got}"
        if "dates" in case:
            assert [d["date"] for d in got] == case["dates"], case["name"]


def test_the_answer_rows_carry_the_contract_fields() -> None:
    for day in CONTRACT["answer"]["days"]:
        assert set(day) <= set(CONTRACT["day_fields"]) and set(CONTRACT["day_fields"]) - {"peak_temp_c"} <= set(day)
        for feature in FEATURES:
            assert set(day["features"][feature]) <= set(CONTRACT["feature_fields"])


def test_the_kotlin_carries_the_contract_constants_and_answers_it() -> None:
    src = (KOTLIN / "PowerLedger.kt").read_text()
    assert f'val FEATURES = listOf({", ".join(chr(34) + n + chr(34) for n in FEATURES)})' in src
    assert f"const val KEEP_DAYS = {CONTRACT['keep_days']}" in src
    assert f"const val MAX_GAP_S = {CONTRACT['max_gap_s']}L" in src
    report = (KOTLIN / "DeviceActions.kt").read_text()
    assert re.search(r'out\.put\("ledger", PowerLedger\.report\(ctx\)\)', report), "power_report no longer answers `ledger`"


def main() -> int:
    try:
        test_the_contract_cases_add_up()
        test_the_answer_rows_carry_the_contract_fields()
        test_the_kotlin_carries_the_contract_constants_and_answers_it()
    except AssertionError as exc:
        print(f"FAIL {exc}")
        return 1
    print("ok   the power ledger's contract cases add up and the Kotlin carries them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
