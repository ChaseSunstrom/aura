#!/usr/bin/env python3
"""Fail on a string a person sees or hears that names Jarvis outside the JARVIS pack (T-0942).

The owner, 2026-11-10: rename everything from JARVIS to AURA unless it is part of the JARVIS
theme. A visible name is the worn pack's (ActiveTheme on the phone, identity.name in the console
and the desktop), so a JARVIS-pack user still sees and hears Jarvis and everyone else AURA.

What is scanned: string literals in the shipping sources of the phone and car (Kotlin and
strings.xml), the console (Svelte/TS), the desktop app's shell (Rust) and the desktop agent
(Python). A literal fails when it holds the word Jarvis or JARVIS. Identifiers are not words here
(JarvisConfig, ai.jarvis.app, AURA_TTS_ENGINE, jarvis-core), so packages, env names and hosts
pass here; T-0953 renames those.

Does not cover: log lines and exception text nobody is shown (skipped by their call), comments,
tests, or a name built at run time from pieces. ALLOWED lists the files that are the JARVIS pack
itself or name it on purpose, each with its reason; there is no baseline to grow.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: platform -> (globs, string literal pattern)
PLATFORMS: dict[str, tuple[tuple[str, ...], re.Pattern[str]]] = {
    "kotlin": (
        ("android-app/app/src/main/kotlin/**/*.kt", "android-app/car-*/src/main/**/*.kt",
         "android-app/app/src/main/res/values/strings.xml"),
        re.compile(r'"((?:[^"\\\n]|\\.)*)"|>([^<\n]+)</string>'),
    ),
    "web": (
        ("aura-web/src/**/*.svelte", "aura-web/src/**/*.ts"),
        re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`((?:[^`\\]|\\.)*)`|>([^<>{}\n]+)<"),
    ),
    "rust": (
        ("aura-desktop-app/src-tauri/src/**/*.rs",),
        re.compile(r'"((?:[^"\\\n]|\\.)*)"'),
    ),
    "python": (
        ("aura-desktop/aura_desktop/**/*.py",),
        re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\""),
    ),
}

NAME = re.compile(r"\b(?:Jarvis|JARVIS)\b")

#: Calls whose text nobody is shown: logs, tags and the owner's own diagnostics.
QUIET = re.compile(
    r"\bLog\.[vdiwe]\(|\bTAG\b|_LOGGER\.|\blogger\.|\blog::|\btracing::|eprintln!|console\.(?:log|warn|error|debug)\("
)
COMMENT = re.compile(r"^\s*(?://|#|\*|/\*|<!--)")

#: Files that are the JARVIS pack, or name it on purpose. Path -> why.
ALLOWED: dict[str, str] = {
    "android-app/app/src/main/kotlin/ai/aura/app/ui/theme/JarvisThemes.kt": "the JARVIS pack's own name",
    "aura-web/src/lib/themes.ts": "the packs, generated from themes/*/theme.json",
    "aura-desktop/aura_desktop/themes.py": "the packs, generated from themes/*/theme.json",
    "aura-web/src/lib/orb/personas.ts": "the two personas' own table, keyed by name",
    "aura-web/src/lib/orb/companions.ts": "which persona is the worker, keyed by the personas' names",
}

#: Literals that are stored or wire values, not words anyone is shown: (path, literal) -> why.
#: Each goes when T-0953 renames both of its ends.
KEPT: dict[tuple[str, str], str] = {
    ("android-app/app/src/main/kotlin/ai/aura/app/config/JarvisConfig.kt", "Jarvis"):
        "the house's assist pipeline is named this; a saved setting matches it",
    ("android-app/app/src/main/kotlin/ai/aura/app/car/dash/DashLayoutStore.kt", "Jarvis"):
        "the default tab's stored id; DashLayout.label shows the pack's name",
    ("aura-web/src/routes/api/config/+server.ts", "Jarvis"):
        "the house's assist pipeline name, as the phone's own default",
    ("android-app/app/src/main/kotlin/ai/aura/app/update/ReleaseFeed.kt", "^Jarvis v?(\\d+\\.\\d+\\.(\\d+))\\b"):
        "the stored APKs' titles say Jarvis; the console and android-app/tools/release_feed_test.py read the same",
}

#: A name joined to its neighbours (X-Jarvis-SHA256, Jarvis.app) is an identifier, not a word.
#: A sentence's full stop or colon after the name is not a join: the separator needs a word beyond it.
JOINED = re.compile(r"(?<=\w)[-_./:](?:Jarvis|JARVIS)\b|\b(?:Jarvis|JARVIS)[-_./:](?=\w)")


def tests_start(path: Path, lines: list[str]) -> int:
    """Rust keeps its tests in the file: stop at the first #[cfg(test)]."""
    if path.suffix == ".rs":
        for i, line in enumerate(lines):
            if line.strip() == "#[cfg(test)]":
                return i
    return len(lines)


def hits(platform: str) -> list[str]:
    globs, literal = PLATFORMS[platform]
    found: list[str] = []
    for pattern in globs:
        for path in sorted(ROOT.glob(pattern)):
            rel = path.relative_to(ROOT).as_posix()
            if rel in ALLOWED or "/test" in rel or rel.endswith((".test.ts", ".spec.ts")):
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            in_comment = False  # a Svelte file's <!-- --> block, as its header often is
            for n, line in enumerate(lines[: tests_start(path, lines)], 1):
                was, in_comment = in_comment or "<!--" in line, (in_comment or "<!--" in line) and "-->" not in line
                if was or COMMENT.match(line) or QUIET.search(line):
                    continue
                for m in literal.finditer(line):
                    text = next((g for g in m.groups() if g is not None), "")
                    if (rel, text) in KEPT or not NAME.search(JOINED.sub("", text)):
                        continue
                    if NAME.search(text):
                        found.append(f"{rel}:{n}: {text.strip()[:100]}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default=",".join(PLATFORMS), help="comma-separated: " + ", ".join(PLATFORMS))
    args = parser.parse_args(argv)
    found = [h for p in args.only.split(",") for h in hits(p.strip())]
    for h in found:
        print(h)
    if found:
        print(f"persona-name lint: {len(found)} visible string(s) name Jarvis outside the JARVIS pack; "
              "say the worn pack's name (ActiveTheme / identity.name)", file=sys.stderr)
        return 1
    print("persona-name lint ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
