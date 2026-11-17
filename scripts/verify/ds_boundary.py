#!/usr/bin/env python3
"""DS boundary: console screens draw with `$lib/ds` and nothing else.

The v2 design system (aura-web/src/lib/ds) is the only place a console
component may get its look from. Outside it, a screen or component fails when
it

  imports a UI component that is not the design system's: a `.svelte`
      specifier that is neither `$lib/ds…` nor a relative path into
      src/lib/ds (a static `import … from` or a dynamic `import(…)`);
  types a font family: a `font-family:` or `font:` declaration that names a
      face instead of `var(--jv-font-…)` (CSS keywords and sizes are fine);
  types a colour: the same hex and rgb()/hsl() literals token_lint finds
      (its scanner, reused, so the two can never disagree about a colour;
      its documented exceptions, the QR encoder's fills, hold here too).

Scanned: aura-web/src, every .svelte, .ts and .css, minus src/lib/ds itself,
tests and generated files. An `@font-face` block is where a face's name is
declared, so it is not a hit. Not covered: a component reached through a barrel
(`import { Panel } from '$lib/glass'`): the specifier names no `.svelte`, and
telling a component from a helper there needs the barrel resolved. Nor a font
set from script (`ctx.font = …`).

The ratchet. `ds_boundary.baseline.json` holds, per file, the hits that
existed when the v2 package landed. A file not in it may have none; a file in
it may not have more. `--update-baseline` only lowers counts and drops clean
files; it never raises one or adds a file, so the line only moves one way.

    python3 scripts/verify/ds_boundary.py                   lint (runs the self-test first)
    python3 scripts/verify/ds_boundary.py --report          every hit, no exit status
    python3 scripts/verify/ds_boundary.py --update-baseline lock in what was cleaned
    python3 scripts/verify/ds_boundary.py --require-empty   fail on any hit, baseline or not

The baseline has been empty since the legacy UI was deleted (T-0611), so the
ratchet and `--require-empty` now say the same thing. A `.svelte` specifier that
is a runes module (`./feeds.svelte` for `feeds.svelte.ts`) is not a component.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from token_lint import MARK, ROOT, THEME_MARK, load_baseline, scan_web, strip_comments_css, walk  # noqa: E402

SRC = ROOT / "aura-web" / "src"
DS = SRC / "lib" / "ds"
BASELINE = Path(__file__).with_name("ds_boundary.baseline.json")

#: `import X from '….svelte'`, `import X from "….svelte"`, `import('….svelte')`.
IMPORT = re.compile(r"""(?:\bfrom|\bimport\s*\(?)\s*['"]([^'"\n]+\.svelte)['"]""")
#: `font-family:` or the `font:` shorthand, never `--jv-font:` or `style:font-family`.
FONT = re.compile(r"(?<![\w:-])font(?:-family)?\s*:\s*((?:\$\{[^}]*\}|[^;{}\n])*)")
FACE = re.compile(r"@font-face\s*\{[^}]*\}")
#: What a font declaration may say besides `var(…)`: keywords, weights, sizes.
FONT_WORDS = {
    "inherit", "initial", "unset", "revert", "revert-layer", "normal", "italic",
    "oblique", "bold", "bolder", "lighter", "small-caps", "important",
}


def code(text: str) -> str:
    """The text with comments blanked, newlines kept so line numbers hold.

    A component's `<!-- @component … -->` doc and a JSDoc block both show
    imports as examples; counted, the check would argue with its own docs.
    """
    text = strip_comments_css(text)
    blank = lambda m: "".join(c if c == "\n" else " " for c in m.group(0))  # noqa: E731
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    # `//` after a colon or a quote is a URL, not a comment.
    return re.sub(r"(?<![:'\"\w])//[^\n]*", blank, text)


def outside_ds(spec: str, path: Path) -> bool:
    """Whether a `.svelte` specifier names a component outside the design system."""
    if spec == "$lib/ds" or spec.startswith("$lib/ds/"):
        return False
    if spec.startswith("."):
        return not (path.parent / spec).resolve().is_relative_to(DS.resolve())
    return True


def rune_module(spec: str, path: Path) -> bool:
    """Whether a `.svelte` specifier is a runes module (`store.svelte` is `store.svelte.ts`).

    Svelte's runes live in `.svelte.ts` files, imported without the `.ts`; read as
    components they made the root layout's two links and the feeds' loader hits
    that no import could ever clear. Only `$lib/` and relative specifiers resolve.
    """
    if spec.startswith("$lib/"):
        base = SRC / "lib" / spec[len("$lib/"):]
    elif spec.startswith("."):
        base = path.parent / spec
    else:
        return False
    return not base.is_file() and any(base.with_name(base.name + ext).is_file() for ext in (".ts", ".js"))


def font_named(value: str) -> bool:
    """Whether a font declaration's value names a face rather than a token."""
    # A template (`var(${f.name})`) is a name chosen at run time, not typed.
    v = re.sub(r"var\((?:\$\{[^}]*\}|[^)])*\)", " ", value)
    v = re.sub(r"\d*\.?\d+[a-z%]*", " ", v)
    if re.search(r"['\"]", v):
        return True
    return any(w.lower() not in FONT_WORDS for w in re.findall(r"[A-Za-z][\w-]*", v))


def scan(path: Path, text: str, colours: bool = True) -> list[str]:
    hits = []
    body = code(text)
    line = lambda pos: body.count("\n", 0, pos) + 1  # noqa: E731
    for m in IMPORT.finditer(body):
        if outside_ds(m.group(1), path) and not rune_module(m.group(1), path):
            hits.append(f"{line(m.start())}: non-ds component: {m.group(1)}")
    faces = FACE.sub(lambda m: "".join(c if c == "\n" else " " for c in m.group(0)), body)
    for m in FONT.finditer(faces):
        if font_named(m.group(1)):
            hits.append(f"{line(m.start())}: font family typed: {m.group(0).strip()[:90]}")
    if colours:
        hits += [h for h in scan_web(path, text) if ": colour " in h]
    return hits


def scan_all() -> dict[str, list[str]]:
    out = {}
    exempt = load_baseline().get("exceptions", {})
    for path in walk(SRC, (".svelte", ".ts", ".css")):
        if path.is_relative_to(DS):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # The theme packs' stylesheet is generated too: its values are typed in themes/.
        if MARK in text or THEME_MARK in text:
            continue
        rel = path.relative_to(ROOT).as_posix()
        hits = scan(path, text, colours=rel not in exempt)
        if hits:
            out[rel] = hits
    return out


def judge(results: dict[str, list[str]], allowed: dict[str, int]) -> list[str]:
    failures = []
    for rel, hits in sorted(results.items()):
        cap = allowed.get(rel)
        if cap is None:
            failures.append(f"{rel}: {len(hits)} hit(s) in a file with no baseline (use $lib/ds and tokens)")
        elif len(hits) > cap:
            failures.append(f"{rel}: {len(hits)} hit(s), baseline allows {cap} (the ratchet only goes down)")
        else:
            continue
        failures += [f"   {h}" for h in hits[:20]]
    return failures


def shrink(allowed: dict[str, int], results: dict[str, list[str]]) -> dict[str, int]:
    """The baseline after cleaning: each count lowered to today's, never raised."""
    return {rel: min(cap, len(results.get(rel, ()))) for rel, cap in sorted(allowed.items())
            if results.get(rel)}


def self_test() -> None:
    """A planted import, colour and font are caught; ds imports and tokens are not."""
    here = SRC / "lib" / "hud" / "Planted.svelte"
    bad = (
        "<script>\n"
        "import Orb from '$lib/hud/Orb.svelte';\n"
        "const Lazy = import('../glass/Glass.svelte');\n"
        "</script>\n"
        "<style>\n.x { color: #ff0000; font-family: 'Inter', sans-serif; }\n</style>\n"
    )
    hits = scan(here, bad)
    assert any("non-ds component: $lib/hud/Orb.svelte" in h for h in hits), hits
    assert any("non-ds component: ../glass/Glass.svelte" in h for h in hits), hits
    assert any(h.startswith("6: colour") for h in hits), hits
    assert any(h.startswith("6: font family typed") for h in hits), hits
    good = (
        "<!-- import Old from '$lib/hud/Old.svelte' -->\n"
        "<script>\n"
        "import { Core } from '$lib/ds';\n"
        "import Label from '$lib/ds/Label.svelte';\n"
        "import Figure from '../ds/Figure.svelte';\n"
        "import { provideHermes } from '$lib/hermes/store.svelte';\n"
        "import { Feeds } from './feeds.svelte';\n"
        "// see https://example.com/x.svelte\n"
        "</script>\n"
        "<style>\n.x { color: rgb(from var(--jv-accent) r g b / 0.5);\n"
        "  font-family: var(--jv-font-chrome); font: inherit; --jv-font: x; }\n"
        "@font-face { font-family: 'Space Grotesk'; }\n</style>\n"
        "<div style={`font-family:var(${name})`}></div>\n"
    )
    assert scan(here, good) == [], scan(here, good)
    rel = "aura-web/src/lib/hud/Planted.svelte"
    assert judge({rel: hits}, {}), "a new file with hits must fail"
    assert judge({rel: hits}, {rel: len(hits) - 1}), "a file over its baseline must fail"
    assert not judge({rel: hits}, {rel: len(hits)}), "a file at its baseline passes"
    assert shrink({rel: 9, "gone.svelte": 2}, {rel: hits}) == {rel: len(hits)}, "shrink lowers and drops"
    assert shrink({rel: 1}, {rel: hits}) == {rel: 1}, "shrink never raises"


def main(argv: list[str]) -> int:
    self_test()
    results = scan_all()
    total = sum(len(v) for v in results.values())
    if "--require-empty" in argv:
        # No baseline: the legacy UI is gone (T-0611), so any hit at all is new.
        for rel, hits in sorted(results.items()):
            print(f"{rel} ({len(hits)})")
            print("\n".join(f"   {h}" for h in hits[:20]))
        print(f"ds boundary {'FAILED' if total else 'empty'}: {total} hit(s) in {len(results)} file(s)")
        return 1 if total else 0
    if "--report" in argv:
        for rel, hits in sorted(results.items()):
            print(f"{rel} ({len(hits)})")
            print("\n".join(f"   {h}" for h in hits[:40]))
        print(f"\n{total} hit(s) in {len(results)} file(s)")
        return 0
    saved = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else None
    if "--update-baseline" in argv:
        # The first write records today; after that the baseline can only shrink.
        files = {r: len(h) for r, h in sorted(results.items())} if saved is None else shrink(saved["files"], results)
        note = "Per-file hits when the v2 design system landed (T-0603). Only ever goes down."
        BASELINE.write_text(json.dumps({"$note": note, "files": files}, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written: {len(files)} file(s), {sum(files.values())} hit(s)")
        return 0
    allowed = saved["files"] if saved else {}
    failures = judge(results, allowed)
    if failures:
        print("\n".join(failures))
        print(f"\nds boundary FAILED: {total} hit(s) across {len(results)} file(s); --report lists them all")
        return 1
    slack = sum(allowed.values()) - total
    tail = f"; {slack} cleaned, run --update-baseline to lock it in" if slack > 0 else ""
    print(f"ds boundary ok: {total} legacy hit(s) in {len(results)} baselined file(s), none new{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
