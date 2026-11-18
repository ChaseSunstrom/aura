#!/usr/bin/env python3
"""Executable spec: the drive-end event the phone sends is the one the skill describes.

Same shape as drive_start_test.py: `CarTurn.DRIVE_END` carries the whole procedure and
`hermes/skills/drive-routine/SKILL.md` quotes it word for word. Also pins that the tools the
event names exist, so a rename in the plugin cannot leave the phone calling nothing.

Run:  python3 android-app/tools/drive_end_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KOTLIN = ROOT / "android-app/app/src/main/kotlin/ai/aura/app/car/voice/CarTurn.kt"
SKILL = ROOT / "hermes/skills/drive-routine/SKILL.md"
PLUGIN = ROOT / "hermes/plugins/aura-hud/__init__.py"


def drive_end() -> str:
    m = re.search(r"const val DRIVE_END =\n((?:\s*\".*?\"(?: \+)?\n)+)", KOTLIN.read_text())
    assert m, "CarTurn.DRIVE_END is gone"
    return "".join(re.findall(r'"(.*?)"', m.group(1)))


def test_the_skill_quotes_the_event_word_for_word() -> None:
    assert f"```\n{drive_end()}\n```" in SKILL.read_text(), "drive-routine/SKILL.md no longer quotes CarTurn.DRIVE_END"


def test_the_tools_the_event_names_exist() -> None:
    for tool in ("later_release", "loops_list"):
        assert tool in drive_end() and f'"{tool}": _schema(' in PLUGIN.read_text(), f"{tool} is not a aura-hud tool"


def main() -> int:
    try:
        test_the_skill_quotes_the_event_word_for_word()
        test_the_tools_the_event_names_exist()
    except AssertionError as exc:
        print(f"FAIL {exc}")
        return 1
    print("ok   the drive-routine skill quotes the drive-end event word for word")
    return 0


if __name__ == "__main__":
    sys.exit(main())
