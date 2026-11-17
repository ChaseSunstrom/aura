#!/usr/bin/env python3
"""Fail when the files every Claude Code session loads outgrow their budget.

The root CLAUDE.md, the always-on rules and the skill descriptions go into every session's
context whatever the work is; they had grown to 14.4 KB plus 13 skills (seven of them for
Vercel and React, which this repo does not use) before the 2026-10-09 cleanup cut them back.
Nothing noticed the growth, so this check does. A nested CLAUDE.md (android-app/, jarvis-web/,
bench/) loads only when a session works in that tree and gets its own, smaller budget.

Not covered: the user's own memory and global rules outside the repo, and the size of a
doc a session chooses to read.

Run:  python3 scripts/verify/context_budget.py   (make docs-check runs it)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Bytes. Raise one only with a reason in the commit body; move detail into a nested
#: CLAUDE.md or a doc before that.
BUDGETS = {"CLAUDE.md": 9_000}
NESTED_BUDGET = 3_000
#: Rules without a `paths:` front matter load in every session.
ALWAYS_RULES_BUDGET = 2_000
SKILLS_MAX = 12


def check() -> list[str]:
    errors = []
    for name, budget in BUDGETS.items():
        size = (ROOT / name).stat().st_size
        if size > budget:
            errors.append(f"{name} is {size} bytes, over its {budget}-byte budget")
    for nested in ROOT.glob("*/CLAUDE.md"):
        size = nested.stat().st_size
        if size > NESTED_BUDGET:
            errors.append(f"{nested.relative_to(ROOT)} is {size} bytes, over {NESTED_BUDGET}")
    always = 0
    for rule in (ROOT / ".claude/rules").glob("*.md"):
        text = rule.read_text(encoding="utf-8")
        if not re.match(r"---\s*\n(?:.*\n)*?paths:", text):
            always += len(text.encode())
    if always > ALWAYS_RULES_BUDGET:
        errors.append(f"always-loaded .claude/rules are {always} bytes, over {ALWAYS_RULES_BUDGET}")
    skills = [p for p in (ROOT / ".claude/skills").iterdir() if (p / "SKILL.md").exists()]
    if len(skills) > SKILLS_MAX:
        errors.append(f"{len(skills)} skills in .claude/skills, over {SKILLS_MAX}: each one's "
                      "description is listed in every session")
    return errors


def main() -> int:
    errors = check()
    for e in errors:
        print(f"context-budget: {e}")
    if not errors:
        print("context-budget: OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
