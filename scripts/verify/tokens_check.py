#!/usr/bin/env python3
"""design/tokens.json is the one place a design value is typed by a human.

Checks that it exists, parses, uses DTCG leaves (`{"$value": …, "$type": …}`),
has the nine groups the target names with a minimum count each — the six of
Reactor II plus material, light and spring — and that every colour value is a
6/8-digit hex, an rgba()
(alpha tokens: wash, glow), an alias (`{color.focus}`) or an alias at an alpha
(`{color.text@0.06}`: a hairline edge that follows Text when Text moves).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "design" / "tokens.json"
NEED = {
    "color": 24, "type": 8, "space": 5, "radius": 3, "elevation": 3, "motion": 6,
    # ADR-0031 deleted the glass: material is three grades × {solid, edge, radius}
    # (+ edge-hover), depth generates nothing at all, and light is the reactor's own
    # halo. The floors are what the flat material actually needs.
    "material": 10, "light": 12, "spring": 12,
}

if not SRC.is_file():
    print(f"missing {SRC.relative_to(ROOT)}")
    sys.exit(1)
try:
    tokens = json.loads(SRC.read_text())
except ValueError as exc:
    print(f"{SRC.relative_to(ROOT)}: invalid JSON: {exc}")
    sys.exit(1)


def leaves(node):
    if isinstance(node, dict):
        if "$value" in node:
            return [node]
        return [leaf for key, value in node.items() if not key.startswith("$") for leaf in leaves(value)]
    return []


problems: list[str] = []
counts = {group: len(leaves(tokens.get(group, {}))) for group in NEED}
for group, minimum in NEED.items():
    if counts[group] < minimum:
        problems.append(f"group {group!r}: {counts[group]} tokens, need at least {minimum}")
for leaf in leaves(tokens.get("color", {})) + [
    leaf for group in ("material", "light") for leaf in leaves(tokens.get(group, {})) if leaf.get("$type") == "color"
]:
    value = str(leaf["$value"])
    if (
        not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value)
        and not re.fullmatch(r"rgba?\(\s*\d+,\s*\d+,\s*\d+(?:,\s*[\d.]+)?\s*\)", value)
        and not re.fullmatch(r"\{[\w.-]+(?:@[\d.]+)?\}", value)
    ):
        problems.append(f"colour value is neither hex, rgba() nor alias: {value}")

print(f"{sum(counts.values())} tokens: " + ", ".join(f"{g}={n}" for g, n in counts.items()))
if problems:
    print("\n".join(problems))
    sys.exit(1)
