#!/usr/bin/env python3
"""Token lint — no hard-coded colour, spacing, type or motion value in app code.

The design system's rule, enforced: a colour, size, radius, shadow or duration
is typed once, in ``design/tokens.json``, and reaches app code through the
generated files (``tokens.css``/``tokens.ts``, ``tokens.py``, ``JarvisTokens.kt``).
This scans every surface for a value typed anywhere else.

What counts as a hit:

  web      (aura-web/src, aura-desktop-app/ui — .svelte .css .ts)
           #hex · rgb()/rgba()/hsl() that is not the relative-colour form
           `rgb(from var(--jv-…) …)` · a numeric px/rem/em/ms/s in a spacing,
           sizing, type, radius, shadow or motion property (CSS and <style>).
           `0` and `1px` (a hairline) are the only literals allowed.
           · `var(--jv-…)` naming a token nothing defines. The name has to be
           in the generated table, declared by some stylesheet, or written from
           a component's script — otherwise what renders is the fallback beside
           it, which is a value typed by hand in the one place the design system
           says a value may not be typed, and which every other check here is
           blind to because `var(…)` is deleted before they look.
  android  (android-app/app/src/main/kotlin — .kt)
           0xAARRGGBB colour ints · Color.parseColor/rgb/argb · dp(<any receiver>, N)
           · textSize = N · COMPLEX_UNIT_SP, N · letterSpacing = N (0 is allowed:
           it is the absence of tracking, not a value).
  desktop  (aura-desktop/aura_desktop — .py)  #hex.

Exempt: generated files (they carry the `@generated from design/tokens.json`
marker), tests, and the files named under "exceptions" in the baseline with a
sentence saying why — today the orb's GLSL shader and renderer (a shader cannot
read a custom property; their palette is drift-checked by design/build.py) and
the QR encoder (SVG fills for a code that must survive being photographed).

The ratchet. ``design/token-lint.baseline.json`` records, per file, how many
hits existed when the rule was introduced. The lint FAILS when a file not in the
baseline has any hit, or a baselined file has more than its baseline — so new
code is always clean and old code can only get cleaner. A milestone that finishes
a surface runs ``--require-clean <path>`` to insist its baseline is zero.

    python3 scripts/verify/token_lint.py                 lint against the baseline
    python3 scripts/verify/token_lint.py --report        every hit, no exit status
    python3 scripts/verify/token_lint.py --require-clean aura-web/src
    python3 scripts/verify/token_lint.py --update-baseline   (only when a milestone
                                                          legitimately moves the line)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "design" / "token-lint.baseline.json"
MARK = "@generated from design/tokens.json"
#: The theme packs' half of the pipeline (design/themes.py, T-0861): themes/ is the other place a value lives.
THEME_MARK = "@generated from themes/<id>/theme.json"

#: deploy/setup is the installer's setup page (T-0864) and site the Pages site (T-0866): not the
#: console, but the same tokens and themes.
WEB_ROOTS = ("aura-web/src", "aura-desktop-app/ui", "deploy/setup", "site")
ANDROID_ROOT = "android-app/app/src/main/kotlin"
#: The car modules that DRAW (T-1336). The rest of `android-app/car/*` (protocol, transport,
#: audio, video, input, voice, the emulator) hold ports, buffer sizes and timeouts, which the
#: Kotlin rules would report as design values.
CAR_UI_MODULES = ("core-dash",)
DESKTOP_ROOT = "aura-desktop/aura_desktop"
SKIP_DIRS = {"node_modules", "build", ".svelte-kit", "__pycache__", "dist"}

#: `var(--jv-…)`, whose NAME is checked against the generated set.
#:
#: This is the hole the rule had. `PROPS`/`RAW_UNIT` below delete every
#: `var(…)` from a declaration before looking for a raw unit, so
#: `var(--does-not-exist, 2px)` passed the one gate whose whole purpose is to
#: catch a hard-coded 2px — and because the name is undefined, the 2px is what
#: renders. The window manager shipped ten of them (`--jv-color-focus`,
#: `--jv-ease-glass`, `--jv-motion-reorder` and friends), every one drawing off
#: its fallback, and nothing anywhere went red.
VAR_USE = re.compile(r"var\(\s*(--jv-[a-z0-9-]+)")
#: A custom property DECLARED, in CSS.
VAR_DECL = re.compile(r"(--jv-[a-z0-9-]+)\s*:")
#: One WRITTEN from script: `style:--jv-x={…}`, `setProperty('--jv-x', …)`, or a
#: name in a map of them. Quoted or after `style:`, never a bare occurrence — a
#: prose mention must not count as a definition, which is exactly how the first
#: version of this check passed on the tree it was written to catch: the
#: paragraph above names five properties that do not exist.
SCRIPT_DECL = re.compile(r"""(?:style:|['"`])(--jv-[a-z0-9-]+)""")

HEX = re.compile(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b")
COLOUR_FN = re.compile(r"\b(rgba?|hsla?)\(\s*([^)]*)")
PROPS = re.compile(
    r"^\s*(?:margin|padding|gap|row-gap|column-gap|inset|top|right|bottom|left|width|height|"
    r"min-width|max-width|min-height|max-height|font-size|letter-spacing|line-height|"
    r"border-radius|box-shadow|text-shadow|transition|transition-duration|transition-delay|"
    r"animation|animation-duration|animation-delay|translate|outline-offset)\s*:\s*([^;{}]*)"
)
RAW_UNIT = re.compile(r"(?<![\w.-])\d*\.?\d+(px|rem|em|ms|s)\b")
#: A colour nobody can find from `design/tokens.json`.
#:
#: `Color.argb(a, r, g, b)` counts only when the components are NUMBERS.
#: `Color.argb(alpha, Color.red(c), Color.green(c), Color.blue(c))` is an
#: existing colour at a new opacity — there is no literal in it, and flagging
#: it taught people that this check cries wolf, which is how a real one gets
#: waved through.
KT_COLOUR = re.compile(
    # Not `0x00……`: an RGB mask (`color and 0x00FFFFFF`) has an alpha of zero,
    # which is to say it is not a colour — nobody can see it, and it is the
    # idiom for "keep the hue, change the opacity".
    r"0x(?!00)[0-9A-Fa-f]{8}\b"
    r"|Color\.parseColor\("
    r"|Color\.(?:rgb|argb)\(\s*[0-9]"
)
#: A dp literal, whatever the receiver is written as.
#:
#: The receiver used to be `\w+`, which is `ctx` and `context` and `this` — and
#: not `this@MainActivity`, which is how every Activity that builds a view
#: inside an `apply { }` block has to name itself. Forty-five literals in
#: eleven files slipped past on the `@` alone (`dp(this@MainActivity, 200)`,
#: `dp(this@SettingsActivity, 14)`), and a lint that misses the common spelling
#: of the thing it exists to catch is a lint that reports clean by accident.
#: `view.context` is allowed as a receiver for the same reason.
KT_SPACE = re.compile(r"\bdp\(\s*[\w@.]+\s*,\s*\d+(?:\.\d+)?f?\s*\)")
KT_TYPE = re.compile(r"\btextSize\s*=\s*\d+(?:\.\d+)?f?\b|COMPLEX_UNIT_SP\s*,\s*\d+(?:\.\d+)?f?\b")
#: Tracking typed by hand. Nine screens had `letterSpacing = 0.14f`, `0.16f`,
#: `0.2f`, `0.24f` and `0.3f` for the same idea — an uppercase label — where
#: the console has two steps (`--jv-track-chrome`, `--jv-track-wide`) and the
#: phone names them `JarvisUi.TRACK_*`. `0f` is not a value, it is no tracking.
KT_TRACK = re.compile(r"\bletterSpacing\s*=\s*(?!(?:0|0\.0+)f?(?![\d.]))\d*\.?\d+f?\b")


def walk(root: Path, suffixes: tuple[str, ...]):
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        name = path.name
        if name.endswith((".test.ts", ".d.ts", "_test.py")) or "/tests/" in path.as_posix():
            continue
        yield path


def strip_comments_css(text: str) -> str:
    """Blank out `/* … */`, keeping every newline so line numbers survive.

    Applied to the WHOLE text rather than line by line: a JSDoc block that
    explains why a colour is written the way it is spans lines, and a per-line
    version could not see that it was inside one. Reported as a hit, that comment
    was a linter arguing with its own documentation.
    """
    return re.sub(
        r"/\*.*?\*/",
        lambda m: "".join(c if c == "\n" else " " for c in m.group(0)),
        text,
        flags=re.S,
    )


def generated_names() -> set[str]:
    """Every `--jv-*` `design/build.py` emits, read from the file it emits.

    `tokens.css` rather than `tokens.json`, because the JSON is the source and
    the CSS is the contract: the name a stylesheet may write is the name the
    generator produced, and the transform between them lives in build.py.
    """
    css = ROOT / "aura-web/src/lib/styles/tokens.css"
    if not css.is_file():
        raise SystemExit(f"token lint: the generated token table is missing: {css}")
    names = set(VAR_DECL.findall(css.read_text(encoding="utf-8")))
    if len(names) < 50:
        # A parse that quietly matched nothing is a check that passes for ever.
        raise SystemExit(f"token lint: only {len(names)} token(s) parsed from {css}")
    return names


def declared_names(paths: list[Path]) -> set[str]:
    """Names something in the tree defines, on top of the generated table.

    Two kinds, and both are legitimate. A stylesheet may declare its own
    plumbing — `--jv-g-*` composes the glass recipe, `--jv-lit-*` carries the
    reactor's light onto a surface. And a component may WRITE one from script
    (`setProperty`, or Svelte's `style:--jv-…`): the halo's position, the
    parallax offset and the stagger delay are values, not tokens, and they never
    appear in the generated table. So the script side of every component counts
    as a declaration — but not a test's, since naming a property in an assertion
    does not define it anywhere.
    """
    out: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        out.update(VAR_DECL.findall(strip_comments_css(text)))
        if path.suffix in (".ts", ".svelte"):
            script = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S)
            out.update(SCRIPT_DECL.findall(script))
    return out


def scan_web(path: Path, text: str, known: set[str] | None = None) -> list[str]:
    hits = []
    # Block comments first, across the whole file: see `strip_comments_css`.
    lines = strip_comments_css(text).split("\n")
    raw_lines = text.split("\n")
    # Colour anywhere; raw units only where CSS is (a .css file, or <style> blocks).
    in_style = path.suffix == ".css"
    for n, blanked in enumerate(lines, 1):
        raw = raw_lines[n - 1] if n - 1 < len(raw_lines) else blanked
        line = (
            re.sub(r"//.*$", "", blanked)
            if path.suffix in (".ts", ".svelte") and not in_style
            else blanked
        )
        if path.suffix == ".svelte":
            if "<style" in raw:
                in_style = True
            if "</style>" in raw:
                in_style = False
        stripped = line
        if HEX.search(stripped):
            hits.append(f"{n}: colour literal: {raw.strip()[:110]}")
        m = COLOUR_FN.search(stripped)
        if m and not m.group(2).strip().startswith("from"):
            hits.append(f"{n}: colour function: {raw.strip()[:110]}")
        if known is not None:
            for use in VAR_USE.finditer(stripped):
                if use.group(1) in known:
                    continue
                hits.append(f"{n}: undefined token {use.group(1)}: {raw.strip()[:110]}")
        if in_style or path.suffix == ".css":
            prop = PROPS.match(stripped)
            if prop:
                value = re.sub(r"var\([^)]*\)", "", prop.group(1))
                value = re.sub(r"\b(0|1px)\b", "", value)
                if RAW_UNIT.search(value):
                    hits.append(f"{n}: raw value: {raw.strip()[:110]}")
    return hits


def scan_kotlin(text: str) -> list[str]:
    hits = []
    for n, raw in enumerate(text.split("\n"), 1):
        line = re.sub(r"//.*$", "", raw)
        if KT_COLOUR.search(line):
            hits.append(f"{n}: colour literal: {raw.strip()[:110]}")
        if KT_SPACE.search(line):
            hits.append(f"{n}: raw dp: {raw.strip()[:110]}")
        if KT_TYPE.search(line):
            hits.append(f"{n}: raw sp: {raw.strip()[:110]}")
        if KT_TRACK.search(line):
            hits.append(f"{n}: raw tracking: {raw.strip()[:110]}")
    return hits


def scan_python(text: str) -> list[str]:
    hits = []
    for n, raw in enumerate(text.split("\n"), 1):
        line = re.sub(r"#(?![0-9a-fA-F]{3,8}\b).*$", "", raw)  # drop comments, keep '#hex'
        if re.search(r"[\"']#[0-9a-fA-F]{6}[\"']", line):
            hits.append(f"{n}: colour literal: {raw.strip()[:110]}")
    return hits


def scan_all(exceptions: dict[str, str]) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}

    def record(path: Path, hits: list[str]) -> None:
        rel = path.relative_to(ROOT).as_posix()
        if rel in exceptions or not hits:
            return
        results[rel] = hits

    web_paths = [p for root in WEB_ROOTS for p in walk(ROOT / root, (".svelte", ".css", ".ts"))]
    known = generated_names() | declared_names(web_paths)
    for path in web_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if MARK in text or THEME_MARK in text:
            continue
        record(path, scan_web(path, text, known))
    android_roots = [ROOT / ANDROID_ROOT] + [
        ROOT / "android-app/car" / m / "src/main" for m in CAR_UI_MODULES
    ]
    for path in (p for root in android_roots for p in walk(root, (".kt",))):
        text = path.read_text(encoding="utf-8", errors="replace")
        if MARK in text or THEME_MARK in text:
            continue
        record(path, scan_kotlin(text))
    for path in walk(ROOT / DESKTOP_ROOT, (".py",)):
        text = path.read_text(encoding="utf-8", errors="replace")
        if MARK in text or THEME_MARK in text:
            continue
        record(path, scan_python(text))
    return results


def load_baseline() -> dict:
    if BASELINE.is_file():
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    return {"exceptions": {}, "files": {}}


def main(argv: list[str]) -> int:
    baseline = load_baseline()
    exceptions = baseline.get("exceptions", {})
    results = scan_all(exceptions)
    total = sum(len(v) for v in results.values())

    if "--update-baseline" in argv:
        baseline["files"] = {path: len(hits) for path, hits in sorted(results.items())}
        baseline.setdefault("$note", "Per-file hit counts when the rule was introduced. Only ever goes down.")
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written: {len(results)} files, {total} hits")
        return 0

    if "--report" in argv:
        for path, hits in sorted(results.items()):
            print(f"{path} ({len(hits)})")
            for h in hits[:40]:
                print(f"   {h}")
        print(f"\n{total} hit(s) in {len(results)} file(s)")
        return 0

    failures = []
    allowed = baseline.get("files", {})
    for path, hits in sorted(results.items()):
        cap = allowed.get(path)
        if cap is None:
            failures.append(f"{path}: {len(hits)} hit(s) in a file with no baseline (new code must use tokens)")
            failures.extend(f"   {h}" for h in hits[:20])
        elif len(hits) > cap:
            failures.append(f"{path}: {len(hits)} hit(s), baseline allows {cap} (the ratchet only goes down)")
            failures.extend(f"   {h}" for h in hits[:20])

    if "--require-clean" in argv:
        prefix = argv[argv.index("--require-clean") + 1]
        dirty = {p: len(h) for p, h in results.items() if p.startswith(prefix)}
        if dirty:
            failures.append(f"--require-clean {prefix}: {sum(dirty.values())} hit(s) remain in {len(dirty)} file(s):")
            failures.extend(f"   {p}: {n}" for p, n in sorted(dirty.items()))

    if failures:
        print("\n".join(failures))
        print(f"\ntoken lint FAILED — {total} hit(s) across {len(results)} file(s); run with --report for all")
        return 1
    print(f"token lint ok — {total} legacy hit(s) in {len(results)} baselined file(s), none new; "
          f"{len(exceptions)} documented exception(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
