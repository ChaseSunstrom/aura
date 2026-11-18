#!/usr/bin/env python3
"""Generate every surface's design tokens from ``design/tokens.json``.

``design/tokens.json`` is the only file where a colour, size, radius, shadow or
duration is typed by a human. This script turns it into what each surface
actually consumes, and ``--check`` refuses to let any of those outputs drift:

    aura-web/src/lib/styles/tokens.css          CSS custom properties on :root
    aura-web/src/lib/tokens.ts                  the same table for TypeScript
    aura-desktop/aura_desktop/tokens.py       the same table for the agent
    android-app/.../ui/theme/JarvisTokens.kt      const vals for the Views
    android-app/.../ui/theme/JarvisTheme.kt       a Compose MaterialTheme
    android-app/app/src/main/res/values/tokens.xml colour + dimen resources
    android-app/app/src/main/res/values/colors.xml aliases the themes read
    design/generated/console-theme.json           the terminal's reading of it
    hermes/plugins/_shared/_glyphs.py             the reactor's glyphs for the chat templates
    aura-desktop/aura_desktop/console/console_theme.json  the same, shipped with the agent

Two things in the source are not typed. ``"{color.surface-2@0.55}"`` is an
alias at an alpha, resolved to ``rgba()`` here so a glass tint moves with the
colour it is made of; and ``spring.*.ratio`` — Compose's damping ratio — is
derived from stiffness, damping and mass rather than written beside them, so
the two forms of one spring cannot disagree.

The reactor is not checked here any more. Reactor II's SVG geometry contract
(``tests/contracts/reactor_geometry.json``: ticks, blades, radii) described the
component the Glass HUD retired with the old console (bf8f6940, D30); the glass
reactor is a WebGL instrument whose contract is ``tests/contracts/reactor.json``
(the eleven states), bound by the web, the phone and the console through their
own tests. A geometry check kept here would only ever fail on the file's absence.

    python3 design/build.py            write every output
    python3 design/build.py --check    exit 1 if any output or pinned file differs

Stdlib only, so it runs wherever python3 does — including CI's ``tokens-sync`` job.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import themes  # noqa: E402 — design/themes.py: the theme packs' half of this pipeline (T-0861)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "design" / "tokens.json"
MARK = "@generated from design/tokens.json"

WEB_CSS = ROOT / "aura-web/src/lib/styles/tokens.css"
#: The desktop app's own stylesheet. Its main window loads the console, which
#: brings its own tokens — this is for the pages the desktop app draws itself
#: (ADR-0051): the consent prompt, which must be answerable when the console is
#: closed, the pairing, permissions and updates pages, and the "cannot reach the
#: console" page, which by definition cannot load them from the console.
SHELL_CSS = ROOT / "aura-desktop-app/ui/tokens.css"
WEB_TS = ROOT / "aura-web/src/lib/tokens.ts"
#: The app's own pages' scripts read the glyph table and a colour or two; they
#: cannot import from `aura-web`.
SHELL_TS = ROOT / "aura-desktop-app/ui/tokens.ts"
#: The Rust side's tray line and notification titles: the state words and glyphs.
SHELL_RS = ROOT / "aura-desktop-app/src-tauri/logic/src/states.rs"
DESKTOP_PY = ROOT / "aura-desktop/aura_desktop/tokens.py"
KT_DIR = ROOT / "android-app/app/src/main/kotlin/ai/aura/app/ui/theme"
KT_TOKENS = KT_DIR / "JarvisTokens.kt"
KT_THEME = KT_DIR / "JarvisTheme.kt"
RES = ROOT / "android-app/app/src/main/res/values"
XML_TOKENS = RES / "tokens.xml"
INTERPOLATORS = RES.parent / "interpolator"
XML_COLORS = RES / "colors.xml"
#: The console's theme: the palette with its 256- and 16-colour fallbacks, the
#: type roles as SGR attributes, the border sets and the reactor's glyphs
#: (GLASS_HUD §9.7, §10). JSON, because the console is not one program: the
#: Hermes TUI, a tmux status line and a curses panel host all read it.
CONSOLE_THEME = ROOT / "design/generated/console-theme.json"
#: The same theme, inside the device agent's package. The agent is installed on
#: machines that have no ``design/`` directory, and its console (``python -m
#: aura_desktop console``) reads the theme at runtime rather than typing a
#: colour — so the file ships with the package. Same text, second path; a
#: ``--check`` that finds the two apart names the stale one.
CONSOLE_THEME_PKG = ROOT / "aura-desktop/aura_desktop/console/console_theme.json"
#: The chat-platform templates' reading of the reactor table: glyph, word and
#: colour per state, so a plugin never types a glyph or a hex. Words come from
#: the reactor contract, which is the second input this one output has —
#: the same pairing `scripts/gen-icons.py` reads.
PLUGIN_GLYPHS = ROOT / "hermes/plugins/_shared/_glyphs.py"
REACTOR_CONTRACT = ROOT / "tests/contracts/reactor.json"

#: Desktop constant -> token. ``theme.py`` imports these names from ``tokens.py``;
#: ``tests/test_theme.py`` reads the same lines back and checks AA contrast.
DESKTOP_COLOURS = {
    "BG": "bg", "PANEL": "panel-solid", "ACCENT": "accent", "ACCENT_DEEP": "accent-deep",
    "ACCENT_INK": "accent-ink", "TEXT": "text", "TEXT_BRIGHT": "text-bright",
    "TEXT_DIM": "text-dim", "TEXT_FAINT": "text-faint", "OK": "ok", "DANGER": "danger",
    "WARN": "warn",
}
#: Android constants derived from a token at an alpha.
#:
#: Every one of these was a literal in the Kotlin — `0x553FD8FF`, `0xF0000308`,
#: `0x22FF9E2C` — which is a colour nobody can find from `design/tokens.json`
#: and nobody notices when the palette moves. Named here, they move with it.
#: The alphas are the ones the app actually used; the RGB is the token's, so a
#: few of these shift by a shade or two from the hand-mixed originals. The
#: screenshot goldens are what that shift is reviewed in.
ANDROID_DERIVED = {
    "TEXT_DIM_80": ("text-dim", 0xCC),
    #: The overlay behind a floating sheet. These were called SCRIM until
    #: `color.scrim` — the text scrim of GLASS_HUD §3.2, Room at half —
    #: took the name; an overlay at 90 % is a different thing from a scrim
    #: at 50 %, and two constants called SCRIM would have collided here.
    "OVERLAY": ("bg", 0xE6),
    #: Heavier, behind an approval — what is under it must not compete with a
    #: decision.
    "OVERLAY_HEAVY": ("bg", 0xF0),
    "OVERLAY_APPROVAL": ("bg", 0xF2),
    #: The accent, at the four weights the chrome uses it: a hairline stroke, a
    #: resting border, a lit border, a filled chip.
    "ACCENT_13": ("accent", 0x22),
    "ACCENT_20": ("accent", 0x33),
    "ACCENT_27": ("accent", 0x44),
    "ACCENT_33": ("accent", 0x55),
    #: Warn, likewise: the banner's fill, its border, and the text on it.
    "WARN_13": ("warn", 0x22),
    "WARN_40": ("warn", 0x66),
    "WARN_53": ("warn", 0x88),
    #: A progress track, which is the one thing here that is grey rather than
    #: coloured: it is the ABSENCE of progress.
    "TRACK": ("text-faint", 0x33),
    #: The panel a floating sheet is made of, at the two opacities in use.
    "PANEL_94": ("panel", 0xF0),
}
#: ``colors.xml`` names the platform themes read -> token resource. Only the
#: names ``themes.xml`` / ``values-v31`` actually read live here: an alias
#: nothing reads is an ``UnusedResources`` lint error (V2_PLAN D29), so adding
#: one means adding its reader in the same change.
XML_ALIASES = {
    "jarvis_bg": "jv_bg", "jarvis_accent": "jv_accent", "jarvis_dim": "jv_text_dim_80",
}
#: The faces Android bundles under ``res/font`` (GLASS_HUD §3): the same subsets
#: the console serves, as TTF because a font resource cannot be woff2. Weight ->
#: resource name; ``design/fonts.py`` is what puts the files there.
ANDROID_FONTS = {
    "jetbrains_mono": {400: "jetbrains_mono_400", 500: "jetbrains_mono_500", 600: "jetbrains_mono_600"},
    "space_grotesk": {300: "space_grotesk_300", 400: "space_grotesk_400"},
}


# --- reading the source -------------------------------------------------------


def load() -> dict:
    tokens = json.loads(SOURCE.read_text(encoding="utf-8"))
    derive_spring_ratios(tokens)
    return tokens


def derive_spring_ratios(tokens: dict) -> None:
    """Add ``spring.<name>.ratio`` — Compose's damping ratio — beside each spring.

    ζ = c / (2·√(k·m)). Typed beside the stiffness it comes from, the ratio is
    the number somebody updates last: the web would settle on one spring and
    the phone on another, which is the drift a person notices first.
    """
    for name, spring in tokens.get("spring", {}).items():
        if name.startswith("$") or "stiffness" not in spring:
            continue
        k = float(spring["stiffness"]["$value"])
        c = float(spring["damping"]["$value"])
        m = float(spring["mass"]["$value"])
        spring["ratio"] = {
            "$type": "number",
            "$value": round(c / (2 * math.sqrt(k * m)), 2),
            "$description": "Derived: damping / (2·√(stiffness·mass)), for Compose's spring(dampingRatio).",
        }


def leaves(node: dict, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str, object]]:
    """Every ``{"$type", "$value"}`` leaf as ``(path, type, value)``, in file order."""
    out = []
    if "$value" in node:
        return [(path, node.get("$type", "other"), node["$value"])]
    for key, value in node.items():
        if key.startswith("$") or not isinstance(value, dict):
            continue
        out.extend(leaves(value, path + (key,)))
    return out


ALIAS = re.compile(r"\{([a-z0-9.-]+)(?:@([0-9.]+))?\}")


def resolve(tokens: dict, value: object) -> object:
    """``"2px solid {color.focus}"`` -> ``"2px solid #4fe3ff"``. Aliases may nest.

    ``{color.text@0.06}`` is that colour at an alpha — ``rgba(211, 230, 236, 0.06)``
    — so a glass edge is Text at 6 % and follows Text when it moves.
    """
    if not isinstance(value, str) or "{" not in value:
        return value

    def lookup(match: re.Match) -> str:
        node = tokens
        for part in match.group(1).split("."):
            node = node[part]
        resolved = str(resolve(tokens, node["$value"]))
        if match.group(2) is None:
            return resolved
        r, g, b = rgb_of(resolved)
        return f"rgba({r}, {g}, {b}, {match.group(2)})"

    return ALIAS.sub(lookup, value)


def rgb_of(value: str) -> tuple[int, int, int]:
    """``#rrggbb`` or ``rgba(r, g, b[, a])`` -> the three channels."""
    if value.startswith("#") and len(value) >= 7:
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    m = re.fullmatch(r"rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\s*\)", value)
    if not m:
        raise ValueError(f"not a colour: {value}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def css_name(path: tuple[str, ...]) -> str | None:
    """The console's ``--jv-*`` name for a token path, or None if the token is
    phone-only. The shorthands are the names the console already used before
    the source of truth moved, so nothing downstream had to be renamed."""
    group, rest = path[0], path[1:]
    if "android" in rest:
        return None
    if group == "color":
        return "--jv-" + "-".join(rest)
    if group == "type":
        kind, name = rest[0], "-".join(rest[1:])
        prefix = {"family": "font", "size": "fs", "weight": "weight", "tracking": "track",
                  "relative": "rel"}[kind]
        return f"--jv-{prefix}-{name}"
    if group == "space":
        return "--jv-space-" + "-".join(rest)
    if group == "radius":
        return "--jv-radius-" + "-".join(rest)
    if group == "elevation":
        name = "-".join(rest)
        return f"--jv-{name}" if name.startswith("glow-") else f"--jv-elev-{name}"
    if group == "motion":
        kind = rest[0]
        if kind == "drift":
            return "--jv-drift"
        # `budget` (what a choreography may cost) is measured by tests, not read
        # by CSS, but it is a motion token like the rest and gets a name.
        prefix = {"dur": "dur", "ease": "ease", "stagger": "stagger", "reactor": "rx", "ambient": "amb", "budget": "budget",
                  "boot": "boot", "hover": "hover"}[kind]
        return f"--jv-{prefix}-" + "-".join(rest[1:])
    if group == "chrome":
        return "--jv-" + "-".join(rest)
    # These groups keep their group name in the CSS variable: `--jv-material-panel-edge`,
    # `--jv-spring-glass-stiffness`, `--jv-light-halo-idle`.
    if group in ("material", "spring", "light", "emission", "band", "camera", "sound"):
        return f"--jv-{group}-" + "-".join(rest)
    raise ValueError(f"unknown token group {group!r}")


def css_value(kind: str, value: object) -> str:
    if kind == "fontFamily":
        names = []
        for name in value:
            quoted = " " in name or (any(c.isupper() for c in name) and "-" in name)
            names.append(f"'{name}'" if quoted else name)
        return ", ".join(names)
    return str(value)


def web_tokens(tokens: dict) -> list[tuple[str, str, tuple[str, ...]]]:
    out = []
    for path, kind, value in leaves(tokens):
        name = css_name(path)
        if name is None:
            continue
        out.append((name, css_value(kind, resolve(tokens, value)), path))
    return out


# --- colour helpers ------------------------------------------------------------


def argb(value: str, alpha: int | None = None) -> str:
    """``#rrggbb`` or ``rgba(r, g, b, a)`` -> ``AARRGGBB`` (upper-case hex)."""
    if value.startswith("#"):
        rgb = value[1:]
        a = 0xFF if alpha is None else alpha
        return f"{a:02X}{rgb.upper()}"
    m = re.fullmatch(r"rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\s*\)", value)
    if not m:
        raise ValueError(f"not a colour: {value}")
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    a = round(float(m.group(4) or 1) * 255) if alpha is None else alpha
    return f"{a:02X}{r:02X}{g:02X}{b:02X}"


def const_name(path: tuple[str, ...]) -> str:
    name = "_".join(path).upper().replace("-", "_")
    # `depth.2` would be `2_BLUR`, which is not an identifier; a level is `L2`.
    return f"L{name}" if name[0].isdigit() else name


COLOUR_IN_TEXT = re.compile(r"rgba?\([^)]*\)|#[0-9a-fA-F]{6}\b")
SHADOW = re.compile(
    r"^\s*(-?[\d.]+)(?:px)?\s+(-?[\d.]+)(?:px)?\s+(-?[\d.]+)(?:px)?(?:\s+(-?[\d.]+)(?:px)?)?\s+(rgba?\([^)]*\))\s*$"
)


def kt_number(value: object, force_float: bool = False) -> str:
    """A JSON integer stays an Int (a blur budget is a count); a decimal is a
    Float; a spring's physics are Floats whatever was typed, because that is
    what Compose's spring() takes."""
    if isinstance(value, int) and not force_float:
        return str(value)
    return f"{float(value)}f"


def kt_dp(value: str) -> str | None:
    """``"24px"`` -> ``24``; anything not in px (``60vmin``) has no dp reading."""
    m = re.fullmatch(r"(-?[\d.]+)px", str(value))
    if not m:
        return None
    n = m.group(1)
    return n if "." not in n else f"{float(n)}f"


# --- outputs ------------------------------------------------------------------


def gen_css(tokens: dict) -> str:
    lines = [
        "/*",
        f" * {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        " *",
        " * Jarvis design tokens (Glass HUD). Every colour, size, duration, glass grade,",
        " * spring and light step the HUD and the console use is declared",
        " * here once; `src/lib/tokens.ts` is the same table for JavaScript and",
        " * `tokens.test.ts` fails if they drift. Nothing else in the app may write a",
        " * raw value — `scripts/verify/token_lint.py` enforces that. Format: one",
        " * declaration per line, `\\t--name: value;`.",
        " */",
        ":root {",
    ]
    last_group = None
    for name, value, path in web_tokens(tokens):
        group = path[0] if path[0] != "color" or len(path) < 3 else "color/" + path[1]
        if group != last_group:
            lines.append(f"\t/* {group} */")
            last_group = group
        lines.append(f"\t{name}: {value};")
    lines.append("}")
    return "\n".join(lines) + "\n"


TS_TAIL = '''
export type TokenName = keyof typeof TOKENS;

/** Look a token up by name. Throws on a typo rather than emitting `undefined`. */
export function token(name: TokenName): string {
	const value = TOKENS[name];
	if (value === undefined) throw new Error(`unknown design token ${name}`);
	return value;
}

/** `var(--jv-…)`, for inline styles that want the live (overridable) value. */
export function cssVar(name: TokenName): string {
	return `var(${name})`;
}

/**
 * The reactor's state colour C, one per state (GLASS_HUD §10). It lives in JS
 * as well as CSS because the layout derives every panel's rim, shadow and halo
 * from it at runtime, and CSS cannot pick it from `data-state` without
 * repeating every rule eleven times.
 */
export const STATE_ACCENT = {
	idle: TOKENS['--jv-reactor-idle'],
	listening: TOKENS['--jv-reactor-listening'],
	thinking: TOKENS['--jv-reactor-thinking'],
	speaking: TOKENS['--jv-reactor-speaking'],
	tool: TOKENS['--jv-reactor-tool'],
	goal: TOKENS['--jv-reactor-goal'],
	attention: TOKENS['--jv-reactor-attention'],
	warming: TOKENS['--jv-reactor-warming'],
	alert: TOKENS['--jv-reactor-alert'],
	kill: TOKENS['--jv-reactor-kill'],
	offline: TOKENS['--jv-reactor-offline']
} as const;

export type AccentState = keyof typeof STATE_ACCENT;

/**
 * The reactor's static glyph per state (GLASS_HUD §10, the reduced-motion
 * column): the character a notice, a toast or a chat message carries where a
 * drawn reactor cannot go. The same table the console theme and the plugins'
 * templates are generated from.
 */
export const STATE_GLYPH: Readonly<Record<AccentState, string>> = {
%%GLYPHS%%
};

/** C for a state; an error is the alert state whatever the pipeline says it is in. */
export function accentFor(state: string, isError = false): string {
	if (isError) return STATE_ACCENT.alert;
	return STATE_ACCENT[state as AccentState] ?? STATE_ACCENT.idle;
}

/** A duration token as a number of milliseconds (`'260ms'` → 260, `'3.4s'` → 3400). */
export function tokenMs(name: TokenName): number {
	const value = TOKENS[name];
	const m = /^([\\d.]+)(ms|s)$/.exec(value);
	if (!m) throw new Error(`${name} is not a duration: ${value}`);
	return m[2] === 's' ? Number(m[1]) * 1000 : Number(m[1]);
}

/**
 * A dimension token as a number of CSS pixels (`'150px'` → 150).
 *
 * For the places a measurement has to reach code that is not CSS: a virtual
 * list's row height, a canvas's line width, an iframe's default height. Reading
 * the number from the same token the stylesheet uses is what stops the two
 * drifting.
 *
 * Deliberately strict about `rem` and `clamp()`: those depend on the root font
 * size or the viewport, so a number taken from them here would be a guess at
 * what the browser is going to do. A value that has to cross this boundary is
 * typed in px.
 */
export function tokenPx(name: TokenName): number {
	const value = TOKENS[name];
	const m = /^([\\d.]+)px$/.exec(value);
	if (!m) throw new Error(`${name} is not a pixel dimension: ${value}`);
	return Number(m[1]);
}
'''


def ts_glyphs(tokens: dict) -> str:
    rows = []
    for state in tokens["color"]["reactor"]:
        if state.startswith("$"):
            continue
        rows.append(f"\t{state}: '{REACTOR_GLYPHS[state][0]}'")
    return ",\n".join(rows)


def gen_ts(tokens: dict) -> str:
    lines = [
        f"// {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        "//",
        "// The design tokens, as data. `src/lib/styles/tokens.css` declares the same",
        "// names on `:root`; this module is what TypeScript reads when a value has to",
        "// reach JavaScript. `tokens.test.ts` diffs the two.",
        "",
        "/** Every `--jv-*` custom property, in the order tokens.css declares them. */",
        "export const TOKENS = {",
    ]
    entries = web_tokens(tokens)
    for i, (name, value, _path) in enumerate(entries):
        quoted = json.dumps(value) if "'" in value else f"'{value}'"
        lines.append(f"\t'{name}': {quoted}{',' if i < len(entries) - 1 else ''}")
    lines.append("} as const;")
    return "\n".join(lines) + "\n" + TS_TAIL.replace("%%GLYPHS%%", ts_glyphs(tokens))


def gen_plugin_glyphs(tokens: dict) -> str:
    """The reactor table for the chat-platform templates (``_shared/_messages.py``).

    Glyph and ASCII stand-in from ``REACTOR_GLYPHS``, the state word from the
    reactor contract, the colour from the tokens — one dict per state, in the
    contract's precedence order with ``goal`` (the overlay) last. A plugin that
    reads this never types a glyph or a hex, which is what lets a Telegram alert
    and the console's reactor change together when the table does.
    """
    contract = json.loads(REACTOR_CONTRACT.read_text(encoding="utf-8"))
    order = list(contract["precedence"]) + ["goal"]
    lines = [
        f"# {MARK} and tests/contracts/reactor.json — DO NOT EDIT. Run `python3 design/build.py`.",
        '"""The reactor\'s eleven states as a chat platform can carry them (GLASS_HUD §10).',
        "",
        "``STATES[name]`` is the state's static glyph (the reduced-motion column of the",
        "table), its ASCII stand-in, its word, what the glyph depicts and its colour C as",
        "hex. ``PRECEDENCE`` is the contract's order, highest first; ``goal`` is not in it",
        "because it is the overlay, worn on top of whichever state won.",
        "",
        "``_messages.py`` is the only intended reader. Nothing else in a plugin should",
        "need a glyph: if it does, it wants a template.",
        '"""',
        "from __future__ import annotations",
        "",
        "#: state -> glyph, ascii, word, meaning, color.",
        "STATES: dict[str, dict[str, str]] = {",
    ]
    for state in order:
        glyph, ascii_glyph, meaning = REACTOR_GLYPHS[state]
        word = contract["states"][state]["word"]
        colour = value_of(tokens, ("color", "reactor", state))
        lines.append(
            f"    {json.dumps(state)}: {{\"glyph\": {json.dumps(glyph, ensure_ascii=False)}, "
            f"\"ascii\": {json.dumps(ascii_glyph)}, \"word\": {json.dumps(word)}, "
            f"\"meaning\": {json.dumps(meaning)}, \"color\": {json.dumps(colour)}}},"
        )
    lines.append("}")
    lines.append("")
    lines.append("#: Highest first; the overlay is absent because it cannot win.")
    lines.append("PRECEDENCE: tuple[str, ...] = (" + ", ".join(json.dumps(s) for s in contract["precedence"]) + ")")
    lines.append("")
    lines.append('__all__ = ["STATES", "PRECEDENCE"]')
    return "\n".join(lines) + "\n"


def gen_rust_states(tokens: dict) -> str:
    """The reactor's states for the desktop app's Rust side (ADR-0051).

    The glyph from ``REACTOR_GLYPHS`` and the word from the reactor contract, in
    the contract's precedence order with ``goal`` last -- the tray's status line
    and a notification's title are drawn from this, so the app never types a
    glyph or a state word. ``json.dumps`` of these strings is a valid Rust string
    literal: no escapes beyond what both languages share.
    """
    contract = json.loads(REACTOR_CONTRACT.read_text(encoding="utf-8"))
    order = list(contract["precedence"]) + ["goal"]
    lines = [
        f"// {MARK} and tests/contracts/reactor.json — DO NOT EDIT. Run `python3 design/build.py`.",
        "//! The reactor's eleven states as the desktop app carries them: the static glyph",
        "//! (the reduced-motion column, GLASS_HUD §10) and the state's word.",
        "",
        "/// `(state, glyph, word)`, highest precedence first, `goal` (the overlay) last.",
        "pub const STATES: &[(&str, &str, &str)] = &[",
    ]
    for state in order:
        glyph = REACTOR_GLYPHS[state][0]
        word = contract["states"][state]["word"]
        lines.append(f"    ({json.dumps(state)}, {json.dumps(glyph, ensure_ascii=False)}, {json.dumps(word)}),")
    lines += ["];", ""]
    # The popup window's own motion (the page animates the card; the app waits for
    # the exit before hiding, and glides the window), so Rust types no duration.
    dur = tokens["motion"]["dur"]
    lines += [
        "/// `motion.dur.float-out`: how long the popup's page plays its exit before the window hides.",
        f"pub const FLOAT_OUT_MS: u64 = {ms_of(dur['float-out']['$value'])};",
        "/// `motion.dur.glide`: a floating window moving to a new place on the screen.",
        f"pub const GLIDE_MS: u64 = {ms_of(dur['glide']['$value'])};",
        "",
    ]
    # The island (T-0695): the window shrinks round the pill once its morph has settled,
    # so the clear rest of a card-sized window does not take clicks at the top of the screen.
    floating = tokens["material"]["floating"]

    def px(leaf: str) -> float:
        return float(str(resolve(tokens, floating[leaf]["$value"])).removesuffix("px"))

    halo = px("halo")
    lines += [
        "/// The popup window round the island's pill: `material.floating.pill-*` and the",
        "/// `material.floating.halo` margin on every side.",
        f"pub const ISLAND_PILL_WINDOW: (f64, f64) = ({px('pill-width') + 2 * halo:.1f}, {px('pill-height') + 2 * halo:.1f});",
        "/// `spring.glass.settle`: the island's morph, waited out before the window shrinks.",
        f"pub const ISLAND_SETTLE_MS: u64 = {ms_of(tokens['spring']['glass']['settle']['$value'])};",
        "",
    ]
    return "\n".join(lines)


def gen_py(tokens: dict) -> str:
    lines = [
        f"# {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        '"""The design tokens the desktop agent draws with.',
        "",
        "``theme.py`` imports the palette from here; ``tests/test_theme.py`` reads the",
        "``NAME = \"#rrggbb\"  # --jv-token`` lines back and checks every text colour for",
        "WCAG AA on the ground it is drawn on.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "#: Every ``--jv-*`` token the console declares, by name.",
        "TOKENS: dict[str, str] = {",
    ]
    for name, value, _path in web_tokens(tokens):
        lines.append(f"    {json.dumps(name)}: {json.dumps(value)},")
    lines.append("}")
    lines.append("")
    lines.append("# --- the palette, named for the desktop's jobs ---------------------------")
    for const, token_name in DESKTOP_COLOURS.items():
        lines.append(f'{const} = "{value_of(tokens, ("color", token_name))}"  # --jv-{token_name}')
    lines.append("")
    lines.append("__all__ = [\"TOKENS\", " + ", ".join(f'"{c}"' for c in DESKTOP_COLOURS) + "]")
    return "\n".join(lines) + "\n"


def value_of(tokens: dict, path: tuple[str, ...]) -> str:
    node = tokens
    for part in path:
        node = node[part]
    return str(resolve(tokens, node["$value"]))


def ms_of(value: object) -> int:
    """`"260ms"` / `"1.4s"` -> 260 / 1400. Android animates in whole ms."""
    text = str(value).strip()
    if text.endswith("ms"):
        return int(round(float(text[:-2])))
    if text.endswith("s"):
        return int(round(float(text[:-1]) * 1000))
    return int(round(float(text)))


def bezier_points(value: str) -> list[str]:
    """`cubic-bezier(0.22, 0.61, 0.36, 1)` -> the four numbers, as written."""
    found = re.search(r"cubic-bezier\(([^)]*)\)", value)
    if not found:
        return []
    return [part.strip() for part in found.group(1).split(",") if part.strip()]


def kt_leaf(tokens: dict, name: str, kind: str, value: object, css: str | None,
            force_float: bool = False) -> list[str]:
    """One token as Kotlin constants — a colour as ARGB, a px as dp, a number as a
    float, a duration as ms, a shadow as its offsets plus its colour. A value the
    phone cannot use (a CSS gradient, `none`, a vmin) becomes a comment naming
    the token, so a reader knows it exists rather than wondering where it went."""
    resolved = str(resolve(tokens, value))
    tag = f" // {css}" if css else ""
    if kind == "color":
        return [f"const val {name} = 0x{argb(resolved)}.toInt(){tag}"]
    if kind == "number":
        return [f"const val {name} = {kt_number(value, force_float)}{tag}"]
    if kind == "duration":
        return [f"const val {name} = {ms_of(resolved)}{tag}"]
    if kind == "fontWeight":
        return [f"const val {name} = {int(value)}{tag}"]
    if kind == "dimension":
        dp = kt_dp(resolved)
        if dp is not None:
            return [f"const val {name}_DP = {dp}{tag}"]
        deg = re.fullmatch(r"(-?[\d.]+)deg", resolved)
        if deg:
            return [f"const val {name}_DEG = {kt_number(float(deg.group(1)))}{tag}"]
        return [f"// {name}: {resolved} has no dp reading{tag}"]
    m = SHADOW.match(resolved)
    if m:
        x, y, blur, spread, colour = m.groups()
        return [
            f"const val {name}_X_DP = {x}{tag}",
            f"const val {name}_Y_DP = {y}",
            f"const val {name}_BLUR_DP = {blur}",
            f"const val {name}_SPREAD_DP = {spread or 0}",
            f"const val {name}_COLOR = 0x{argb(colour)}.toInt()",
        ]
    colour = COLOUR_IN_TEXT.search(resolved)
    if colour:
        # A gradient: the phone draws its own Brush from the one colour in it.
        return [f"const val {name}_COLOR = 0x{argb(colour.group(0))}.toInt(){tag} — {resolved}"]
    return [f"// {name}: {resolved}{tag}"]


def kt_object(tokens: dict, group: str, node: dict, indent: str, doc: str) -> list[str]:
    """A token group as a nested Kotlin object, in file order."""
    lines = [f"{indent}/** {doc} */", f"{indent}object {const_name((group,)).title().replace('_', '')} {{"]
    for key, child in node.items():
        if key.startswith("$") or not isinstance(child, dict):
            continue
        if "$value" in child:
            for line in kt_leaf(tokens, const_name((key,)), child.get("$type", "other"), child["$value"],
                                css_name((group, key)) if group in ("material", "light", "spring") else None):
                lines.append(f"{indent}    {line}")
        else:
            inner = child.get("$description", key).split(". ")[0].rstrip(".")
            lines.append("")
            lines.append(f"{indent}    /** {inner}. */")
            lines.append(f"{indent}    object {const_name((key,)).title().replace('_', '')} {{")
            for sub, leaf in child.items():
                if sub.startswith("$") or not isinstance(leaf, dict) or "$value" not in leaf:
                    continue
                for line in kt_leaf(tokens, const_name((sub,)), leaf.get("$type", "other"), leaf["$value"],
                                    css_name((group, key, sub)), force_float=group == "spring"):
                    lines.append(f"{indent}        {line}")
            lines.append(f"{indent}    }}")
    lines.append(f"{indent}}}")
    return lines


def gen_kotlin_tokens(tokens: dict) -> str:
    lines = [
        "package ai.aura.app.ui.theme",
        "",
        "/**",
        f" * {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        " *",
        " * The design tokens for the phone's Views. `JarvisUi` aliases these under the",
        " * names its builders use; `JarvisTheme` turns them into a Compose theme;",
        " * `android-app/tools/design_token_test.py` reads them back against the source.",
        " */",
        "object JarvisTokens {",
        "    object Color {",
    ]
    for path, kind, value in leaves(tokens["color"]):
        full = ("color",) + path
        css = css_name(full)
        lines.append(f"        const val {const_name(path)} = 0x{argb(str(resolve(tokens, value)))}.toInt() // {css}")
    for const, (token_name, alpha) in ANDROID_DERIVED.items():
        hexv = argb(value_of(tokens, ("color", token_name)), alpha)
        lines.append(f"        const val {const} = 0x{hexv}.toInt() // --jv-{token_name} at {round(alpha / 255 * 100)}%")
    lines.append("    }")
    lines.append("")
    lines.append("    /** The type scale in sp, named for the job. */")
    lines.append("    object Type {")
    for path, _kind, value in leaves(tokens["type"]["android"]):
        lines.append(f"        const val {const_name(path)} = {value}f")
    lines.append("    }")
    lines.append("")
    # Weights are a token too (GLASS_HUD §3): the Compose theme reads them here
    # rather than naming FontWeight.Light, so the 300 display line and the 600
    # primary control are the console's 300 and 600.
    lines.append("    /** Font weights, as the CSS numbers. */")
    lines.append("    object Weight {")
    for path, _kind, value in leaves(tokens["type"]["weight"]):
        lines.append(f"        const val {const_name(path)} = {int(value)} // --jv-weight-{path[-1]}")
    lines.append("    }")
    lines.append("")
    # Tracking, as a View's `letterSpacing` (em): the wordmark's, the uppercase
    # label's and control's (BUILD_BRIEF §1), and the hair on mono readouts.
    lines.append("    /** Letter spacing in em, as `TextView.letterSpacing` takes it. */")
    lines.append("    object Tracking {")
    for path, _kind, value in leaves(tokens["type"]["tracking"]):
        lines.append(f"        const val {const_name(path)} = {str(value).removesuffix('em')}f // --jv-track-{path[-1]}")
    lines.append("    }")
    lines.append("")
    lines.append("    /** The spacing scale in dp, named for the job. */")
    lines.append("    object Space {")
    for path, _kind, value in leaves(tokens["space"]["android"]):
        lines.append(f"        const val {const_name(path)} = {value}")
    lines.append("    }")
    lines.append("")
    # Sizes, not spacing. A gap and a thing are different kinds of number, and
    # snapping the second to the first is how a 34 dp button quietly becomes a
    # 32 dp one — see the `size` block's description in tokens.json.
    lines.append("    /** Control and ornament sizes in dp, named for the job. */")
    lines.append("    object Size {")
    for path, _kind, value in leaves(tokens["size"]["android"]):
        lines.append(f"        const val {const_name(path)} = {value}")
    lines.append("    }")
    lines.append("")
    lines.append("    /** Corner radii in dp. */")
    lines.append("    object Radius {")
    for path, _kind, value in leaves(tokens["radius"]):
        px = str(value)
        if px.endswith("px") and px[:-2].isdigit():
            lines.append(f"        const val {const_name(path)} = {px[:-2]}")
    lines.append("    }")
    lines.append("")
    # Motion (M44). The phone had colour, type, spacing and radii generated and
    # animated with numbers somebody typed — so the one property that is FELT
    # rather than seen was the one not coming from the design system.
    lines.append("    /** Durations in milliseconds, and the curves, for animators. */")
    lines.append("    object Motion {")
    lines.append("        object Dur {")
    for path, _kind, value in leaves(tokens["motion"]["dur"]):
        lines.append(f"            const val {const_name(path)} = {ms_of(value)}")
    lines.append("        }")
    lines.append("")
    lines.append("        /** Cubic-bezier control points, as (x1, y1, x2, y2). */")
    lines.append("        object Ease {")
    for path, _kind, value in leaves(tokens["motion"]["ease"]):
        points = bezier_points(str(value))
        if points:
            joined = ", ".join(f"{p}f" for p in points)
            lines.append(f"            val {const_name(path)} = floatArrayOf({joined})")
    lines.append("        }")
    lines.append("")
    lines.append("        /** Stagger between one row and the next, in milliseconds. */")
    lines.append("        object Stagger {")
    for path, _kind, value in leaves(tokens["motion"]["stagger"]):
        lines.append(f"            const val {const_name(path)} = {ms_of(value)}")
    lines.append("        }")
    lines.append("")
    # The reactor's clock (M51). The web reads `--jv-rx-*`; the phone draws
    # the same instrument on Canvas and used to turn its rings at rates
    # somebody typed — two reactors turning at two speeds is the one drift a
    # person notices first, so the periods are generated like everything else.
    lines.append("        /** The reactor's own clock — rotation periods and the breath — in milliseconds. */")
    lines.append("        object Reactor {")
    for path, _kind, value in leaves(tokens["motion"]["reactor"]):
        lines.append(f"            const val {const_name(path)} = {ms_of(value)}")
    lines.append("        }")
    lines.append("")
    lines.append("        /** Ambient drift: rotations slow enough to read as alive rather than as motion. */")
    lines.append("        object Ambient {")
    for path, _kind, value in leaves(tokens["motion"]["ambient"]):
        lines.append(f"            const val {const_name(path)} = {ms_of(value)}")
    lines.append("        }")
    lines.append("")
    # The boot (GLASS_HUD §8.2): what a condensing panel starts FROM. The
    # ignition is Dur.ENTER and the stagger is Stagger.*; these two are the
    # numbers the phone would otherwise type beside them.
    lines.append("        /** The boot: a panel condenses from SCALE and BLUR_DP to its own grade and scale 1. */")
    lines.append("        object Boot {")
    for path, kind, value in leaves(tokens["motion"]["boot"]):
        for line in kt_leaf(tokens, const_name(path), kind, value, css_name(("motion", "boot") + path)):
            lines.append(f"            {line}")
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    # The material and the springs. A spring is stiffness plus the damping ratio
    # Compose's spring() takes, derived from the same numbers the web integrates.
    # The blur, tint, specular and depth tables went with the glass (ADR-0031).
    lines += kt_object(tokens, "material", tokens["material"], "    ",
                       "The three surfaces: the solid each is filled with, its hairline edge and its radius")
    lines.append("")
    lines += kt_object(tokens, "light", tokens["light"], "    ",
                       "The reactor's own core brightness per state")
    lines.append("")
    lines += kt_object(tokens, "spring", tokens["spring"], "    ",
                       "The three springs, as spring(stiffness = STIFFNESS, dampingRatio = RATIO); SETTLE is the longest they may take, in ms")
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_kotlin_theme(tokens: dict) -> str:
    def family(name: str) -> str:
        fonts = ",\n".join(
            f"    Font(R.font.{res}, FontWeight({weight}))" for weight, res in ANDROID_FONTS[name].items()
        )
        return f"val {name.title().replace('_', '')}Family = FontFamily(\n{fonts},\n)"

    return f"""package ai.aura.app.ui.theme

import ai.aura.app.R
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp

/**
 * {MARK} — DO NOT EDIT. Run `python3 design/build.py`.
 *
 * The Compose theme, built from [JarvisTokens]. The Glass HUD is one dark room,
 * so there is one colour scheme and no light variant: a Composable that reads
 * `MaterialTheme.colorScheme` gets the same palette the console and the desktop
 * draw with, and `MaterialTheme.typography` the same two faces — JetBrains Mono
 * for what a person reads, Space Grotesk Light for big numbers — bundled under
 * res/font so the phone, like the console, fetches nothing (BUILD_BRIEF §1).
 */
object JarvisColors {{
    val Bg = Color(JarvisTokens.Color.BG)
    val Panel = Color(JarvisTokens.Color.PANEL)
    val Surface2 = Color(JarvisTokens.Color.SURFACE_2)
    val Accent = Color(JarvisTokens.Color.ACCENT)
    val AccentDeep = Color(JarvisTokens.Color.ACCENT_DEEP)
    val AccentInk = Color(JarvisTokens.Color.ACCENT_INK)
    val Warn = Color(JarvisTokens.Color.WARN)
    val Danger = Color(JarvisTokens.Color.DANGER)
    val Ok = Color(JarvisTokens.Color.OK)
    val Text = Color(JarvisTokens.Color.TEXT)
    val TextBright = Color(JarvisTokens.Color.TEXT_BRIGHT)
    val TextDim = Color(JarvisTokens.Color.TEXT_DIM)
    val TextFaint = Color(JarvisTokens.Color.TEXT_FAINT)
    val Line = Color(JarvisTokens.Color.LINE)
    val LineSoft = Color(JarvisTokens.Color.LINE_SOFT)
}}

val JarvisColorScheme = darkColorScheme(
    primary = JarvisColors.Accent,
    onPrimary = JarvisColors.AccentInk,
    secondary = JarvisColors.AccentDeep,
    onSecondary = JarvisColors.AccentInk,
    tertiary = JarvisColors.Warn,
    onTertiary = JarvisColors.AccentInk,
    background = JarvisColors.Bg,
    onBackground = JarvisColors.Text,
    surface = JarvisColors.Panel,
    onSurface = JarvisColors.Text,
    surfaceVariant = JarvisColors.Surface2,
    onSurfaceVariant = JarvisColors.TextDim,
    outline = JarvisColors.Line,
    outlineVariant = JarvisColors.LineSoft,
    error = JarvisColors.Danger,
    onError = JarvisColors.AccentInk,
)

/** The UI face (type.family.body / chrome): labels, running text, data. */
{family("jetbrains_mono")}

/** The figure face (type.family.display): big numbers, the wordmark. */
{family("space_grotesk")}

val JarvisTypography = Typography(
    displayLarge = TextStyle(fontFamily = SpaceGroteskFamily, fontWeight = FontWeight(JarvisTokens.Weight.DISPLAY), fontSize = 40.sp),
    titleLarge = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.LABEL), fontSize = JarvisTokens.Type.TITLE.sp),
    bodyLarge = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.BODY), fontSize = JarvisTokens.Type.RESPONSE.sp),
    bodyMedium = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.BODY), fontSize = JarvisTokens.Type.BODY.sp),
    bodySmall = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.BODY), fontSize = JarvisTokens.Type.HINT.sp),
    labelLarge = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.STRONG), fontSize = JarvisTokens.Type.FIELD.sp),
    labelMedium = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.BODY), fontSize = JarvisTokens.Type.MONO.sp),
    labelSmall = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.LABEL), fontSize = JarvisTokens.Type.LABEL.sp, letterSpacing = JarvisTokens.Tracking.LABEL.em),
)

val JarvisShapes = Shapes(
    small = RoundedCornerShape(JarvisTokens.Radius.SM.dp),
    medium = RoundedCornerShape(JarvisTokens.Radius.MD.dp),
    large = RoundedCornerShape(JarvisTokens.Radius.LG.dp),
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {{
    MaterialTheme(
        colorScheme = JarvisColorScheme,
        typography = JarvisTypography,
        shapes = JarvisShapes,
        content = content,
    )
}}
"""


def gen_xml_tokens(tokens: dict) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<!-- {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        "     The whole scale is emitted on purpose: this file is the design",
        "     system's Android surface, not a list of what a layout happens to",
        "     use today, and a layout reaching for a step that is not here would",
        "     type the number instead. tools:keep is what stops the promoted",
        "     UnusedResources lint (V2_PLAN D29) from asking for the steps that",
        "     are not referenced yet; hand-written resources get no such pass. -->",
        '<resources xmlns:tools="http://schemas.android.com/tools"',
        '    tools:keep="@color/jv_*,@dimen/jv_*,@integer/jv_*,@interpolator/jv_ease_*">',
    ]
    for path, _kind, value in leaves(tokens["color"]):
        name = "jv_" + "_".join(path).replace("-", "_")
        lines.append(f'    <color name="{name}">#{argb(str(resolve(tokens, value)))}</color>')
    for const, (token_name, alpha) in ANDROID_DERIVED.items():
        lines.append(f'    <color name="jv_{const.lower()}">#{argb(value_of(tokens, ("color", token_name)), alpha)}</color>')
    for path, _kind, value in leaves(tokens["space"]["android"]):
        lines.append(f'    <dimen name="jv_space_{path[-1]}">{value}dp</dimen>')
    for path, _kind, value in leaves(tokens["type"]["android"]):
        lines.append(f'    <dimen name="jv_type_{path[-1]}">{value}sp</dimen>')
    for path, _kind, value in leaves(tokens["radius"]):
        px = str(value)
        if px.endswith("px") and px[:-2].isdigit():
            lines.append(f'    <dimen name="jv_radius_{path[-1]}">{px[:-2]}dp</dimen>')
    # Durations for what XML animates (the activities' slide, T-0725): an anim
    # resource cannot read JarvisTokens.Motion, so it reads these.
    for path, _kind, value in leaves(tokens["motion"]["dur"]):
        lines.append(f'    <integer name="jv_dur_{"_".join(path).replace("-", "_")}">{ms_of(value)}</integer>')
    lines.append("</resources>")
    return "\n".join(lines) + "\n"


def gen_xml_interpolators(tokens: dict) -> dict[Path, str]:
    """One `res/interpolator/jv_ease_<name>.xml` per cubic-bezier curve, for anim resources."""
    out: dict[Path, str] = {}
    for path, _kind, value in leaves(tokens["motion"]["ease"]):
        points = bezier_points(str(value))
        if not points:
            continue
        name = "jv_ease_" + "_".join(path).replace("-", "_")
        x1, y1, x2, y2 = points
        out[INTERPOLATORS / f"{name}.xml"] = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f"<!-- {MARK} — DO NOT EDIT. Run `python3 design/build.py`. -->\n"
            '<pathInterpolator xmlns:android="http://schemas.android.com/apk/res/android"\n'
            f'    android:controlX1="{x1}"\n    android:controlY1="{y1}"\n'
            f'    android:controlX2="{x2}"\n    android:controlY2="{y2}" />\n'
        )
    return out


def gen_xml_colors(tokens: dict) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<!-- {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        "     The names the platform themes (themes.xml, values-v31) read, each an",
        "     alias of a token in tokens.xml. No hex lives here: a value typed in",
        "     this file is exactly the stale second palette this replaced. -->",
        "<resources>",
    ]
    for alias, target in XML_ALIASES.items():
        lines.append(f'    <color name="{alias}">@color/{target}</color>')
    lines.append("</resources>")
    return "\n".join(lines) + "\n"


# --- the console theme ------------------------------------------------------------

#: xterm's 16 system colours, as the RGB most terminals ship them. The
#: 16-colour fallback is chosen by nearest distance to these; a terminal that
#: re-themes them will be a little off, which is the most any 16-colour
#: fallback can promise.
ANSI16 = [
    (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205), (0, 205, 205),
    (229, 229, 229), (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0), (92, 92, 255),
    (255, 0, 255), (0, 255, 255), (255, 255, 255),
]
CUBE = (0, 95, 135, 175, 215, 255)


def xterm256() -> list[tuple[int, int, int]]:
    """Indices 16–255 of the xterm palette: the 6×6×6 cube, then the 24 greys.
    The first 16 are left out of the search — they are whatever the terminal's
    theme says they are, which is the one thing a fixed table cannot know."""
    out = [(r, g, b) for r in CUBE for g in CUBE for b in CUBE]
    out += [(8 + 10 * i,) * 3 for i in range(24)]
    return out


def nearest(rgb: tuple[int, int, int], table: list[tuple[int, int, int]], offset: int = 0) -> int:
    def dist(c: tuple[int, int, int]) -> int:
        return sum((a - b) ** 2 for a, b in zip(rgb, c))

    return offset + min(range(len(table)), key=lambda i: dist(table[i]))


def bezier_sample(points: list[str], steps: int) -> list[float]:
    """y at `steps` evenly spaced x on a CSS cubic-bezier — the curve the console
    plays frame by frame, since it has no animator to hand a bezier to."""
    x1, y1, x2, y2 = (float(p) for p in points)

    def at(t: float, a: float, b: float) -> float:
        return 3 * (1 - t) ** 2 * t * a + 3 * (1 - t) * t**2 * b + t**3

    out = []
    for i in range(steps + 1):
        x = i / steps
        lo, hi = 0.0, 1.0
        for _ in range(40):  # bisection: x(t) is monotonic on a CSS bezier
            mid = (lo + hi) / 2
            if at(mid, x1, x2) < x:
                lo = mid
            else:
                hi = mid
        out.append(round(at((lo + hi) / 2, y1, y2), 3))
    return out


#: The reactor's static glyphs (GLASS_HUD §10, the reduced-motion column), one per
#: state, with what a terminal without Unicode shows instead. The one place a
#: glyph is typed: the console theme, the web's `STATE_GLYPH` and the plugins'
#: `_shared/_glyphs.py` are all emitted from here, so a notice on the phone, a
#: toast on the console and a Telegram alert cannot disagree about what the
#: attention state looks like as a character.
REACTOR_GLYPHS = {
    "idle": ("○", "o", "steady ring"),
    "listening": ("●", "*", "filled core"),
    "thinking": ("◐", "d", "half ring"),
    "speaking": ("◎", "@", "three rings"),
    "tool": ("◔", "%", "segmented ring"),
    "goal": ("⊙", "o.", "dot on ring"),
    "attention": ("◡", "n", "ring with a gap at the top"),
    "warming": ("○", "o", "dim ring"),
    "alert": ("⊖", "!", "ring with a bar"),
    "kill": ("◯", "X", "single red ring"),
    "offline": ("◌", ".", "hollow ring"),
}


def gen_console_theme(tokens: dict) -> str:
    cube = xterm256()
    palette = {}
    for path, _kind, value in leaves(tokens["color"]):
        resolved = str(resolve(tokens, value))
        if not resolved.startswith("#"):
            continue  # a tint at an alpha has no terminal reading; the tint is the border
        rgb = rgb_of(resolved)
        palette["-".join(path)] = {
            "hex": resolved,
            "rgb": list(rgb),
            "xterm256": nearest(rgb, cube, 16),
            "ansi16": nearest(rgb, ANSI16),
        }

    def weight_of(name: str) -> int:
        return int(tokens["type"]["weight"][name]["$value"])

    def role(colour: str, weight: str, *sgr: str) -> dict:
        w = weight_of(weight)
        attrs = list(sgr) + (["bold"] if w >= 500 else [])
        return {"color": colour, "weight": w, "sgr": attrs}

    light = tokens["light"]
    states = {}
    for state in tokens["color"]["reactor"]:
        if state.startswith("$"):
            continue
        glyph, ascii_glyph, meaning = REACTOR_GLYPHS[state]
        entry = {
            "color": f"reactor-{state}",
            "glyph": glyph,
            "ascii": ascii_glyph,
            "meaning": meaning,
            "sgr": ["dim"] if state in ("warming", "offline") else [],
        }
        # Only the reactor's own halo survives: the rim and shadow that lit panels
        # from it went with the light layer (ADR-0031).
        if state in light["halo"]:
            entry["halo"] = light["halo"][state]["$value"]
        states[state] = entry

    ease = bezier_points(str(tokens["motion"]["ease"]["standard"]["$value"]))
    fps = 30
    theme = {
        "$generated": f"{MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        "$about": (
            "The console's reading of the design tokens (GLASS_HUD §9.7, ADR-0031): the "
            "palette with its 256- and 16-colour fallbacks, the type roles as SGR attributes, "
            "the border sets and the reactor's glyphs. A terminal is the one surface with no "
            "glass, so the material is the border: a single hairline for every grade, the "
            "reactor-facing side in C and the far side in Mark (tick) to read as its shadow."
        ),
        "degrade": {
            "truecolor": "palette.<name>.hex",
            "256": "palette.<name>.xterm256 — the nearest cube or grey; the 16 system colours are skipped because a theme redefines them",
            "16": "palette.<name>.ansi16 — nearest of xterm's default sixteen",
            "no-unicode": "borders.ascii, reactor.spinner, reactor.states.<state>.ascii",
        },
        "palette": palette,
        "type": {
            "$rule": "One size, so hierarchy is colour and weight: 500 and up is bold, 300 is the plain face, Muted is the second line.",
            "display": role("text-bright", "display"),
            # A panel's title (ADR-0031, "small capitals, dim"): Muted and the
            # plain weight, like the web's `.title` — a panel is named, not
            # headlined. `render.py` upper-cases the letters it draws in this
            # role, which is the terminal's small-caps.
            "title": role("text-dim", "body"),
            "body": role("text", "body"),
            "secondary": role("text-dim", "body"),
            "label": role("text-dim", "label"),
            "figure": role("text-bright", "label"),
            "mono": role("text", "body"),
            "mono-faint": role("text-faint", "body"),
            "control": role("accent-ink", "strong"),
            "link": role("accent-deep", "body"),
        },
        "borders": {
            # ADR-0031: a single hairline for every grade — the double-line
            # panel border was this surface's own glass, and it went the way
            # the web's blur and specular did. The two keys stay (the grade
            # still decides other things, and callers key on the name) but
            # they now draw the same set.
            "panel": {"h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘"},
            "pane": {"h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘"},
            "ascii": {"h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+"},
            "divider": "─",
            "live": "▌",
            "facing": "reactor-<state>",
            "far": "tick",
            "focused": {"facing": "reactor-<state>", "sgr": ["bold"]},
        },
        "material": {
            "pane": {"border": "pane", "text": ["text", "text-dim"]},
            "panel": {"border": "panel"},
            "control": {"border": "none", "sgr": ["reverse"]},
            "scrim": "none",
        },
        "reactor": {
            "ring": ["◜", "◠", "◝", "◞", "◡", "◟"],
            "spinner": ["-", "\\", "|", "/"],
            "housing": "orb-housing",
            "states": states,
            "precedence": ["kill", "offline", "alert", "attention", "warming", "speaking",
                           "listening", "tool", "thinking", "goal", "idle"],
        },
        "motion": {
            "fps": fps,
            "durations_ms": {k: ms_of(v) for (k,), _t, v in leaves(tokens["motion"]["dur"])},
            "stagger_ms": {k: ms_of(v) for (k,), _t, v in leaves(tokens["motion"]["stagger"])},
            "springs_ms": {name: ms_of(s["settle"]["$value"]) for name, s in tokens["spring"].items()
                           if not name.startswith("$")},
            "reactor_ms": {k: ms_of(v) for (k,), _t, v in leaves(tokens["motion"]["reactor"])},
            "ease_standard": bezier_sample(ease, fps // 2),
            "reduced_ms": ms_of(tokens["motion"]["dur"]["panel-reduced"]["$value"]),
        },
    }
    return json.dumps(theme, indent=2, ensure_ascii=False) + "\n"


# --- pinned files: checked, never rewritten -------------------------------------


# --- main ----------------------------------------------------------------------


def outputs(tokens: dict) -> dict[Path, str]:
    return {
        WEB_CSS: gen_css(tokens),
        SHELL_CSS: gen_css(tokens),
        SHELL_TS: gen_ts(tokens),
        SHELL_RS: gen_rust_states(tokens),
        WEB_TS: gen_ts(tokens),
        DESKTOP_PY: gen_py(tokens),
        KT_TOKENS: gen_kotlin_tokens(tokens),
        KT_THEME: gen_kotlin_theme(tokens),
        XML_TOKENS: gen_xml_tokens(tokens),
        XML_COLORS: gen_xml_colors(tokens),
        **gen_xml_interpolators(tokens),
        CONSOLE_THEME: gen_console_theme(tokens),
        PLUGIN_GLYPHS: gen_plugin_glyphs(tokens),
        CONSOLE_THEME_PKG: gen_console_theme(tokens),
        # The theme packs re-point these same tokens per theme (design/themes.py, T-0861).
        **themes.outputs(sys.modules[__name__], tokens),
    }


def main(argv: list[str]) -> int:
    tokens = load()
    generated = outputs(tokens)
    if "--check" in argv:
        problems = []
        for path, text in generated.items():
            if not path.is_file():
                problems.append(f"missing: {path.relative_to(ROOT)}")
            elif path.read_text(encoding="utf-8") != text:
                problems.append(f"stale: {path.relative_to(ROOT)} (run python3 design/build.py)")
        if problems:
            print("\n".join(problems))
            return 1
        print(f"{len(generated)} generated files current")
        return 0
    for path, text in generated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
