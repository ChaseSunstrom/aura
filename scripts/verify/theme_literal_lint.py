#!/usr/bin/env python3
"""Theme literal lint — no assistant name, voice id or wake phrase typed in app code (T-0861).

design/AURA/PROMPT.md §2: a theme says who the assistant is (its name, its Kokoro voice, its
wake phrases), and app code reads them from the generated theme files. A name typed into a
string, a ``"af_heart"`` or a ``"Hey Jarvis"`` is a screen that keeps saying the old theme
after a switch, which token_lint.py cannot see because none of them is a design value.

What counts as a hit, in the files token_lint.py scans (web .svelte/.ts, Android .kt,
desktop .py; tests and ``@generated`` files exempt):

  name   a string literal containing AURA/Aura/JARVIS/Jarvis as a word
  voice  a quoted Kokoro voice id (``"af_heart"``, ``'bm_george'``)
  wake   "hey aura" / "hey jarvis" in any case, with or without a comma

The ratchet is token_lint's: ``design/theme-literal.baseline.json`` holds each file's count
when the rule came in (empty since T-0881: every hit reads the worn pack or is a ``SKIP``
below, one exact literal with its reason); a new file with a hit, or a file over its count,
fails, and so does a ``SKIP`` that no longer matches. Not covered:
names in identifiers (``JarvisTokens``, ``ai.jarvis``) and package or service names, which
are the product's code, not what a person is told; and literals in jarvis-core and the
Hermes plugins, whose words reach a person through the persona's own prompt.

    python3 scripts/verify/theme_literal_lint.py                 lint against the baseline
    python3 scripts/verify/theme_literal_lint.py --report        every hit
    python3 scripts/verify/theme_literal_lint.py --update-baseline   (only when hits went down)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from token_lint import ANDROID_ROOT, DESKTOP_ROOT, MARK, ROOT, THEME_MARK, WEB_ROOTS, walk  # noqa: E402

BASELINE = ROOT / "design" / "theme-literal.baseline.json"
STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`[^`]*`")
NAME = re.compile(r"\b(?:AURA|Aura|JARVIS|Jarvis)\b")
VOICE = re.compile(r"^[\"'`][abefhijpz][fm]_[a-z]+[\"'`]$")
COMMENT = ("//", "/*", "*", "#", "<!--")
WAKE = re.compile(r"\bhey,?\s+(?:aura|jarvis)\b", re.I)


WEB = "jarvis-web/src/"
APP = "android-app/app/src/main/kotlin/ai/jarvis/app/"

# Product identifiers, not words a person is told: a literal that names a package, a wire
# value, a stored key or a persona pack's own definition. Each is one exact literal in one
# file (or "*" for a file that IS the pack definition) with the reason it stays; changing
# one breaks a stored value or a contract, and a skip that matches nothing fails the lint.
SKIP: dict[str, dict[str, str]] = {
    APP + "assist/ModelStore.kt": {'"X-Jarvis-SHA256"': "the HTTP header the adapter's model route sends (wire)"},
    APP + "car/dash/CarDisplay.kt": {'"AURA dashboard"': "the virtual display's name in `dumpsys display` (a developer's handle)"},
    APP + "car/dash/CarSurface.kt": {'"AURA car screen"': "the virtual display's name in `dumpsys display` (a developer's handle)"},
    APP + "car/dash/DashLayoutStore.kt": {'"Jarvis"': "the stored name of the shipped tab, shown through ActiveTheme.name (`shown`)"},
    APP + "config/JarvisConfig.kt": {'"Jarvis"': "the house's Assist pipeline name, a stored and wire value (AURA_PIPELINE's default)"},
    APP + "update/ReleaseFeed.kt": {r'"^Jarvis v?(\d+\.\d+\.(\d+))\b"': "the title the build gave APKs already in the adapter's store"},
    WEB + "routes/api/config/+server.ts": {"'Jarvis'": "the house's Assist pipeline name, the phone's DEFAULT_PIPELINE (wire)"},
    WEB + "lib/orb/personas.ts": {"*": "the persona pack's own definition: the names are its ids"},
    WEB + "lib/orb/companions.ts": {"*": "persona ids: a ring is the pool of the two less the primary"},
}


def scan(text: str, skip: dict[str, str] | None = None, used: set[str] | None = None) -> list[str]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        # A comment's apostrophes ("AURA's ... the pack's") read as a quoted string.
        if line.lstrip().startswith(COMMENT):
            continue
        for m in STRING.finditer(line):
            lit = m.group(0)
            kind = ("wake" if WAKE.search(lit) else "voice" if VOICE.match(lit)
                    else "name" if NAME.search(lit) else "")
            if kind and lit in (skip or {}):
                if used is not None:
                    used.add(lit)
            elif kind:
                hits.append(f"{lineno}: {kind} {lit[:60]}")
    return hits


def scan_all(stale: list[str] | None = None) -> dict[str, list[str]]:
    paths = [p for r in WEB_ROOTS for p in walk(ROOT / r, (".svelte", ".ts"))]
    paths += list(walk(ROOT / ANDROID_ROOT, (".kt",))) + list(walk(ROOT / DESKTOP_ROOT, (".py",)))
    results = {}
    seen: set[str] = set()
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        skip = SKIP.get(rel, {})
        text = path.read_text(encoding="utf-8", errors="replace")
        if MARK in text or THEME_MARK in text or "*" in skip:
            seen.add(rel)
            continue
        used: set[str] = set()
        hits = scan(text, skip, used)
        if skip:
            seen.add(rel)
            if stale is not None:
                stale += [f"{rel}: skip {lit} matches nothing" for lit in skip if lit not in used]
        if hits:
            results[rel] = hits
    if stale is not None:
        stale += [f"{rel}: skipped file is gone" for rel in SKIP if rel not in seen]
    return results


def main(argv: list[str]) -> int:
    stale: list[str] = []
    results = scan_all(stale)
    total = sum(map(len, results.values()))
    if "--update-baseline" in argv:
        BASELINE.write_text(json.dumps({
            "$note": "Per-file counts of name/voice/wake literals when the rule came in. Only ever goes down.",
            "files": {p: len(h) for p, h in sorted(results.items())}}, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written: {len(results)} files, {total} hits")
        return 0
    if "--report" in argv:
        for path, hits in sorted(results.items()):
            print(f"{path} ({len(hits)})", *(f"   {h}" for h in hits), sep="\n")
        print(f"\n{total} hit(s) in {len(results)} file(s)")
        return 0
    allowed = json.loads(BASELINE.read_text(encoding="utf-8"))["files"] if BASELINE.is_file() else {}
    failures = list(stale)
    for path, hits in sorted(results.items()):
        cap = allowed.get(path, 0)
        if len(hits) > cap:
            failures.append(f"{path}: {len(hits)} literal(s), baseline allows {cap} (read the theme instead)")
            failures += [f"   {h}" for h in hits[:20]]
    if failures:
        print("\n".join(failures))
        print(f"\ntheme literal lint FAILED — {total} hit(s) in {len(results)} file(s)")
        return 1
    print(f"theme literal lint ok — {total} legacy hit(s) in {len(results)} baselined file(s), none new")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
