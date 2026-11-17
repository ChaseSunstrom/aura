#!/usr/bin/env python3
"""Executable spec: the drive-start event the phone sends is the one the skill describes.

The phone carries the whole procedure in `CarTurn.DRIVE_START` so the turn does not stop to
read a skill (2-20 s a drive start); `hermes/skills/drive-routine/SKILL.md` quotes it so the
model and whoever reads the skill see the same words. Two copies drift: this pins them.

Run:  python3 android-app/tools/drive_start_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KOTLIN = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/CarTurn.kt"
SKILL = ROOT / "hermes/skills/drive-routine/SKILL.md"


def drive_start() -> str:
    m = re.search(r"const val DRIVE_START =\n((?:\s*\".*?\"(?: \+)?\n)+)", KOTLIN.read_text())
    assert m, "CarTurn.DRIVE_START is gone"
    return "".join(re.findall(r'"(.*?)"', m.group(1)))


def test_the_skill_quotes_the_event_word_for_word() -> None:
    assert f"```\n{drive_start()}\n```" in SKILL.read_text(), "drive-routine/SKILL.md no longer quotes CarTurn.DRIVE_START"


def main() -> int:
    try:
        test_the_skill_quotes_the_event_word_for_word()
    except AssertionError as exc:
        print(f"FAIL {exc}")
        return 1
    print("ok   the drive-routine skill quotes the drive-start event word for word")
    return 0


if __name__ == "__main__":
    sys.exit(main())
