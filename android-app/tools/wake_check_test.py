#!/usr/bin/env python3
"""Executable spec for the wake word's second stage on the phone (T-1573).

`WakeCheck.decide` turns the adapter's `/api/audio/wake-check` answer into open or drop,
`WakeCheck.line` writes the one decision line per wake, and `WakeWindow` keeps the last two
seconds the head scored. A wake is dropped only on the route's `drop`: no answer in time, an
old adapter or any other body opens it, so a house that cannot say never costs the owner a wake.

The rows are `tests/contracts/wake_check.json`, which WakeCheckTest.kt reads too. This file
also checks that WakeWordService asks before it opens a turn and writes the line to the car's
session log, by reading the Kotlin source.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "tests" / "contracts" / "wake_check.json").read_text())
ASSIST = ROOT / "android-app" / "app" / "src" / "main" / "kotlin" / "ai" / "aura" / "app" / "assist"


def decide(status: int | None, body: object) -> tuple[bool, str, float | None]:
    if status is None:
        return True, "no answer", None
    if status != 200:
        return True, f"house answered {status}", None
    if not isinstance(body, dict):
        return True, "no verdict", None
    wake = body.get("wake")
    if wake not in ("open", "drop"):
        return True, "no verdict", None
    sim = body.get("similarity")
    return wake == "open", str(body.get("reason") or "no verdict"), float(sim) if isinstance(sim, (int, float)) else None


def line(head: str, score: float, bar: float, music: bool, open_: bool, verdict: str, sim: float | None) -> str:
    s = f" (similarity {sim:.2f})" if sim is not None else ""
    return (f"wake {head}: score {score:.2f} over {bar:.2f}, car music {'on' if music else 'off'}, "
            f"voice {verdict}{s}: {'opened' if open_ else 'dropped'}")


class Window:
    def __init__(self, sample_rate: int = 16000) -> None:
        self.size = sample_rate * 2 * CONTRACT["window_ms"] // 1000
        self.data = b""

    def push(self, chunk: bytes) -> None:
        self.data = (self.data + chunk)[-self.size:]


def check_phone_rows() -> int:
    bad = 0
    for row in CONTRACT["phone"]:
        got = decide(row["status"], row["body"])
        if (got[0], got[1]) != (row["open"], row["verdict"]):
            print(f"FAIL phone row {row['name']}: {got}")
            bad += 1
    return bad


def check_line_rows() -> int:
    bad = 0
    for row in CONTRACT["line"]:
        got = line(row["head"], row["score"], row["bar"], row["music"], row["open"], row["verdict"], row["similarity"])
        if got != row["text"]:
            print(f"FAIL line row: {got!r}")
            bad += 1
    return bad


def check_window() -> int:
    w = Window(sample_rate=4)
    w.push(bytes(range(10)))
    w.push(bytes(range(10, 18)))
    w.push(bytes(range(100, 140)))
    return 0 if w.data == bytes(range(124, 140)) else (print("FAIL window keeps the newest bytes") or 1)


def check_kotlin_agrees() -> int:
    """The constants, and the service asking before a turn and logging to the car's session log."""
    bad = 0
    check = (ASSIST / "WakeCheck.kt").read_text()
    for name, key in (("TIMEOUT_MS", "timeout_ms"), ("WINDOW_MS", "window_ms")):
        m = re.search(rf"const val {name}: \w+ = ([\d_]+)L?", check)
        if not m or int(m.group(1).replace("_", "")) != CONTRACT[key]:
            print(f"FAIL WakeCheck.{name} is not the contract's {key}")
            bad += 1
    service = (ASSIST / "WakeWordService.kt").read_text()
    for needle, why in (("WakeCheck.decide(", "the service decides from the route's answer"),
                        ("wakeWindow.push(", "the service keeps the window it scored"),
                        ("SessionLog.shared", "the decision line reaches the car's session log"),
                        ("config.wakeVoiceCheck", "the setting turns the check off")):
        if needle not in service:
            print(f"FAIL WakeWordService: {why} ({needle} missing)")
            bad += 1
    return bad


def main() -> int:
    failures = check_phone_rows() + check_line_rows() + check_window() + check_kotlin_agrees()
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(f"wake check: {len(CONTRACT['phone'])} answers, {len(CONTRACT['line'])} lines, the window, the service's wiring")
    return 0


if __name__ == "__main__":
    sys.exit(main())
