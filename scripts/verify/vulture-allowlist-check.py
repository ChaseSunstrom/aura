#!/usr/bin/env python3
"""Fail when a name in the vulture allow-list occurs nowhere in the scanned trees.

usage: vulture-allowlist-check.py ALLOWLIST PATH...

A stale entry keeps silencing vulture for a symbol that no longer exists, so the list only grows.
This checks presence as a whole word, not that the occurrence is a definition: a name that is
still mentioned (a comment, a string) passes. The allow-list itself is excluded from the search.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


DEFAULT = (
    "vulture-allowlist.py aura-core/aura aura-adapter/aura_adapter "
    "aura-desktop/aura_desktop bench hermes/plugins scripts deploy "
    "aura-voice/aura_voice"
).split()  # mirrors Makefile PY_DEAD_PATHS; the Makefile and CI pass them explicitly


def main(argv: list[str]) -> int:
    argv = argv if len(argv) > 2 else ["", *DEFAULT]
    allow = Path(argv[1]).resolve()
    names = {
        line.strip()
        for line in allow.read_text().splitlines()
        if re.fullmatch(r"\s*[A-Za-z_]\w*\s*", line)
    }
    text = ""
    for root in map(Path, argv[2:]):
        files = [root] if root.is_file() else root.rglob("*.py")
        text += "\n".join(f.read_text(errors="ignore") for f in files
                         if f.resolve() != allow and f.name != "test_vulture_allowlist.py")  # its fixtures name allow-list entries
    words = set(re.findall(r"\w+", text))
    stale = sorted(names - words)
    for n in stale:
        print(f"{allow.name}: '{n}' matches no identifier in the scanned trees", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
