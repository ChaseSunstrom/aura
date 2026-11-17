#!/usr/bin/env python3
"""Executable spec for the goal panel's glyph table.

Hermes announces the goal loop as one line of `status.update kind=goal` text
whose leading glyph is the state (`docs/hermes-hud-protocol.md` § events):

    ⊙  set          "⊙ Goal set (N-turn budget): <goal>"
    ↻  continuing   "↻ Continuing toward goal (n/N): <judge's reason>"
    ⏳  parked       "⏳ Goal (parked …)"
    ⏸  paused       "⏸ Goal paused — n/N turns used …"
    ✓  achieved     "✓ Goal achieved: <reason>"
       cleared      "Goal cleared"

`assist/hermes/GoalStatus.kt` turns that line into a state, a turn/budget and
the reason — which is always shown verbatim, because the judge's sentence is
the whole point of the panel. This is the same rule in Python, run against a
hand-written table, plus a read of the Kotlin to prove the two carry the same
glyphs in the same order and the same regexes for the numbers.

Run:  python3 android-app/tools/hermes_goal_glyph_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

KOTLIN = Path(__file__).resolve().parents[1] / "app/src/main/kotlin/ai/jarvis/app/assist/hermes/GoalStatus.kt"
DOC = Path(__file__).resolve().parents[2] / "docs/hermes-hud-protocol.md"

#: Glyph → state, in the order the Kotlin tries them.
GLYPHS: list[tuple[str, str]] = [
    ("⊙", "SET"),
    ("↻", "CONTINUING"),
    ("⏳", "PARKED"),
    ("⏸", "PAUSED"),
    ("✓", "ACHIEVED"),
]
CLEARED_TEXT = "Goal cleared"

TURN_OF_BUDGET = re.compile(r"\((\d+)/(\d+)\)")
TURNS_USED = re.compile(r"(\d+)/(\d+)\s+turns?\s+used")
BUDGET = re.compile(r"\((\d+)-turn budget\)")


def parse(text: str | None):
    """Mirror of GoalStatus.parse: (state, turn, budget, reason) or None."""
    text = (text or "").strip()
    if not text:
        return None
    if text.lower() == CLEARED_TEXT.lower() or text.lower().startswith(CLEARED_TEXT.lower() + "."):
        return ("CLEARED", None, None, "")
    state = next((s for g, s in GLYPHS if text.startswith(g)), None)
    if state is None:
        return ("UNKNOWN", None, None, text)
    glyph = next(g for g, s in GLYPHS if s == state)
    body = text[len(glyph):].strip()
    turn = budget = None
    m = TURN_OF_BUDGET.search(body) or TURNS_USED.search(body)
    if m:
        turn, budget = int(m.group(1)), int(m.group(2))
    else:
        m = BUDGET.search(body)
        if m:
            budget = int(m.group(1))
    colon = body.find(": ")
    reason = body[colon + 2:].strip() if colon >= 0 else body
    return (state, turn, budget, reason or body)


CASES = [
    ("⊙ Goal set (20-turn budget): tidy the notes folder", ("SET", None, 20, "tidy the notes folder")),
    ("↻ Continuing toward goal (3/20): two folders remain untagged", ("CONTINUING", 3, 20, "two folders remain untagged")),
    ("⏳ Goal (parked until pid 4242 exits) — waiting on the build", ("PARKED", None, None, "Goal (parked until pid 4242 exits) — waiting on the build")),
    ("⏸ Goal paused — 5/20 turns used. /goal resume to continue", ("PAUSED", 5, 20, "Goal paused — 5/20 turns used. /goal resume to continue")),
    ("✓ Goal achieved: every note carries a tag", ("ACHIEVED", None, None, "every note carries a tag")),
    ("Goal cleared", ("CLEARED", None, None, "")),
    ("↻ Continuing toward goal (1/5): judge says: not yet: two tests red", ("CONTINUING", 1, 5, "judge says: not yet: two tests red")),
    ("Something new the backend says", ("UNKNOWN", None, None, "Something new the backend says")),
    ("", None),
    ("   ", None),
]


def check_table() -> int:
    failures = 0
    for text, expected in CASES:
        got = parse(text)
        if got != expected:
            print(f"FAIL  {text!r}: expected {expected}, got {got}")
            failures += 1
    return failures


def check_every_glyph_has_a_case() -> int:
    """A glyph nobody wrote a case for is a glyph nobody checked."""
    failures = 0
    for glyph, state in GLYPHS:
        if not any(text.startswith(glyph) for text, _ in CASES):
            print(f"FAIL  no case starts with {glyph} ({state})")
            failures += 1
    return failures


def check_kotlin_agrees() -> int:
    if not KOTLIN.is_file():
        print(f"FAIL  {KOTLIN} is missing")
        return 1
    src = KOTLIN.read_text(encoding="utf-8")
    failures = 0
    # The glyph table, in order.
    kotlin_glyphs = re.findall(r'"([^"]+)" to State\.([A-Z]+)', src)
    if kotlin_glyphs != GLYPHS:
        print(f"FAIL  GoalStatus.kt's glyph table is {kotlin_glyphs}, this file has {GLYPHS}")
        failures += 1
    # Each state carries its glyph on the enum as well, so labels cannot drift.
    for glyph, state in GLYPHS:
        if f'{state}("{glyph}"' not in src:
            print(f"FAIL  State.{state} does not carry {glyph} on the enum")
            failures += 1
    if f'const val CLEARED_TEXT = "{CLEARED_TEXT}"' not in src:
        print("FAIL  GoalStatus.kt no longer names 'Goal cleared'")
        failures += 1
    for name, pattern in (("TURN_OF_BUDGET", TURN_OF_BUDGET), ("TURNS_USED", TURNS_USED), ("BUDGET", BUDGET)):
        m = re.search(rf'val {name} = Regex\("""(.*?)"""\)', src)
        if not m:
            print(f"FAIL  GoalStatus.kt has no {name} regex")
            failures += 1
        elif m.group(1) != pattern.pattern:
            print(f"FAIL  {name} differs: Kotlin {m.group(1)!r}, here {pattern.pattern!r}")
            failures += 1
    # The reason is what follows the first ": " — the rule that keeps a
    # judge's own colon intact.
    if 'indexOf(": ")' not in src:
        print("FAIL  GoalStatus.kt no longer splits the reason at the first ': '")
        failures += 1
    return failures


def check_doc_agrees() -> int:
    """The protocol doc names the same glyphs."""
    if not DOC.is_file():
        print(f"FAIL  {DOC} is missing")
        return 1
    text = DOC.read_text(encoding="utf-8")
    failures = 0
    for glyph, state in GLYPHS:
        if glyph not in text:
            print(f"FAIL  docs/hermes-hud-protocol.md does not mention {glyph} ({state})")
            failures += 1
    return failures


def main() -> int:
    failures = check_table() + check_every_glyph_has_a_case() + check_kotlin_agrees() + check_doc_agrees()
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(f"goal glyphs: {len(CASES)} lines, {len(GLYPHS)} glyphs, the Kotlin and the doc agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
