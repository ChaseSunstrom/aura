"""Theme packs (``themes/<id>/theme.json``) -> every platform's theme (T-0861, the AURA brief §1).

``design/tokens.json`` is the JARVIS look and the whole token vocabulary; a theme pack
re-points that vocabulary. Each theme's roles are written onto the token paths in
``ROLES``, the tree is resolved again (so an alias such as ``color.reactor.listening`` =
``{color.accent}`` follows the theme's accent), and only what differs from the base is
emitted. Every screen already reads ``--jv-*``/``JarvisTokens`` instead of a literal (the
token lint's baseline is empty), so a theme moves every surface without editing one.

What the vocabulary has no name for (the persona, the voice, the wake phrases, the core
renderer and its eight states, the map style, the marker and toggle style, the label
fill, the backdrop) travels as data: ``THEMES`` in the console's ``themes.ts``, the
phone's ``JarvisThemes.kt``, the desktop's ``themes.py`` and the personas plugin's
``themes.json``.

Not covered: a theme cannot add a token the vocabulary lacks; that is a ``tokens.json``
change, on purpose.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEMES_DIR = ROOT / "themes"
# Not "themes/*/...": its "*/" closed the CSS comment this heads, and PostCSS failed the console build.
MARK = "@generated from themes/<id>/theme.json by design/build.py"

#: The pack every surface shows before it knows the persona (and a missing state file).
DEFAULT = "aura"

WEB_THEME_CSS = ROOT / "aura-web/src/lib/styles/themes.css"
SHELL_THEME_CSS = ROOT / "aura-desktop-app/ui/themes.css"
WEB_THEME_TS = ROOT / "aura-web/src/lib/themes.ts"
KT_THEMES = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme/JarvisThemes.kt"
DESKTOP_THEMES = ROOT / "aura-desktop/aura_desktop/themes.py"
PLUGIN_THEMES = ROOT / "hermes/plugins/aura-personas/themes.json"
SPLASH_XML = ROOT / "android-app/app/src/main/res/values-v31/theme_splash.xml"
DRAWABLES = ROOT / "android-app/app/src/main/res"

# A role is a theme colour role; ("alpha", role, a) is it at an alpha; ("mix", a, b, t) is
# a blended t of the way from a to b (what a deeper or a hairline shade of a role is);
# ("opt", role, spec) is that role when the pack names it, else spec (JARVIS names them all,
# because it is the base look and must move nothing).
ROLES: dict[tuple[str, ...], object] = {
    ("color", "bg"): "bg.void",
    ("color", "bg-raised"): ("opt", "bg.raised", "bg.deep"),
    ("color", "panel"): ("opt", "bg.panel", "bg.deep"),
    ("color", "panel-solid"): ("opt", "bg.panel", "bg.deep"),
    ("color", "field"): ("opt", "bg.raised", "bg.deep"),
    ("color", "surface-2"): ("opt", "surface.2", ("mix", "bg.deep", "accent", 0.12)),
    ("color", "surface-sunken"): ("opt", "surface.sunken", ("mix", "bg.void", "bg.deep", 0.4)),
    ("color", "core-glow"): "bg.nebula",
    ("color", "accent"): "accent",
    ("color", "accent-deep"): ("opt", "accent.deep", ("mix", "accent", "bg.void", 0.35)),
    ("color", "accent-lift"): "accent.soft",
    ("color", "accent-ink"): "on.accent",
    ("color", "focus"): "accent",
    ("color", "warn"): "attention",
    ("color", "ok"): "ok",
    ("color", "danger"): "danger",
    ("color", "violet"): "memory",
    ("color", "text"): "text",
    ("color", "text-bright"): "text",
    ("color", "text-dim"): "text.secondary",
    ("color", "text-faint"): "text.label",
    ("color", "tick"): "off",
    ("color", "line"): ("opt", "line", ("mix", "bg.void", "accent", 0.16)),
    ("color", "line-soft"): ("opt", "line.soft", ("mix", "bg.void", "accent", 0.11)),
    ("color", "line-hair"): ("opt", "line.hair", ("mix", "bg.void", "accent", 0.07)),
    ("color", "wash"): ("alpha", "accent", 0.08),
    ("color", "wash-strong"): ("alpha", "accent", 0.18),
    ("color", "glow"): ("alpha", "accent", 0.35),
}
FONTS = {("type", "family", "display"): "display", ("type", "family", "body"): "mono",
         ("type", "family", "chrome"): "mono"}
RADII = {("radius", "sm"): "button", ("radius", "md"): "panel", ("radius", "lg"): "card",
         ("radius", "pill"): "chip"}
# The pack's two curves are the vocabulary's quick and exit; out and appear keep their own.
MOTION = {("motion", "ease", "quick"): "easeOut", ("motion", "ease", "exit"): "easeIn"}
MOTION_MS = {("motion", "dur", "appear"): "appear", ("motion", "dur", "exit"): "leave",
             ("motion", "stagger", "project"): "stagger", ("motion", "dur", "bar"): "bars",
             ("motion", "dur", "connector"): "line", ("motion", "dur", "beat"): "beat"}


def load_themes() -> dict[str, dict]:
    """Every pack, by id, in id order; the folder name must be the pack's id."""
    themes = {}
    for path in sorted(THEMES_DIR.glob("*/theme.json")):
        theme = json.loads(path.read_text(encoding="utf-8"))
        if theme.get("id") != path.parent.name:
            raise ValueError(f"{path}: id {theme.get('id')!r} is not its folder's name")
        themes[theme["id"]] = theme
    if DEFAULT not in themes:
        raise ValueError(f"the default theme {DEFAULT!r} has no pack")
    return themes


def hex_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def argb_hex(value: str) -> str:
    """`#RRGGBB` or `rgba(r,g,b,a)` as `#AARRGGBB`, the one shape Kotlin parses (a nebula's colour)."""
    m = re.fullmatch(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", value.strip())
    if m:
        r, g, b, a = int(m[1]), int(m[2]), int(m[3]), float(m[4])
    else:
        (r, g, b), a = hex_rgb(value), 1.0
    return f"#{round(a * 255):02X}{r:02X}{g:02X}{b:02X}"


def role_value(theme: dict, spec: object) -> str:
    colors = theme["color"]
    if isinstance(spec, str):
        return colors[spec].lower()
    kind = spec[0]
    if kind == "opt":
        return role_value(theme, spec[1] if spec[1] in colors else spec[2])
    if kind == "alpha":
        r, g, b = hex_rgb(colors[spec[1]])
        return f"rgba({r}, {g}, {b}, {spec[2]})"
    a, b, t = hex_rgb(colors[spec[1]]), hex_rgb(colors[spec[2]]), spec[3]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def themed(tokens: dict, theme: dict) -> dict:
    """The token tree with this theme's roles written in (aliases resolve against it)."""
    out = copy.deepcopy(tokens)

    def put(path: tuple[str, ...], value: object) -> None:
        node = out
        for part in path:
            node = node[part]
        node["$value"] = value

    for path, spec in ROLES.items():
        put(path, role_value(theme, spec))
    typo = theme["typography"]
    for path, role in FONTS.items():
        put(path, [typo[role]["family"], *typo[role]["fallback"]])
    for path, role in RADII.items():
        put(path, f"{theme['shape']['radius'][role]}px")
    motion = theme["motion"]
    for path, key in MOTION.items():
        put(path, motion[key])
    for path, key in MOTION_MS.items():
        put(path, f"{motion['ms'][key]}ms")
    return out


def label_fill(theme: dict) -> str:
    """The label role's fill as CSS: a gradient between two colour roles, or one role."""
    fill = theme["typography"]["roles"]["label"].get("fill", "color:text.label")
    kind, _, spec = fill.partition(":")
    if kind == "gradient":
        a, b = (s.strip() for s in spec.split("→"))
        return f"linear-gradient(90deg, {role_value(theme, a)}, {role_value(theme, b)})"
    return role_value(theme, spec)


def backdrop(theme: dict) -> str:
    """The background's nebulae as layered radial gradients over the void (the stars are drawn)."""
    layers = [f"radial-gradient({w}px {h}px at {x * 100:g}% {y * 100:g}%, {n['color']}, transparent 70%)"
              for n in theme["background"].get("nebulae", []) for (w, h), (x, y) in [(n["size"], n["at"])]]
    return ", ".join([*layers, role_value(theme, "bg.void")])


def extras(theme: dict) -> list[tuple[str, str]]:
    """The theme's own custom properties: what the token vocabulary has no name for."""
    colors = theme["color"]
    fill = colors.get("panel.fill")
    fill = f"linear-gradient(180deg, {fill[0]}, {fill[1]})" if isinstance(fill, list) else fill
    grad = colors.get("accent.gradient") or [colors["accent"]] * 2
    return [
        ("--jv-theme-label-fill", label_fill(theme)),
        ("--jv-theme-accent-gradient", f"linear-gradient(90deg, {', '.join(c.lower() for c in grad)})"),
        ("--jv-theme-panel-fill", fill or "none"),
        ("--jv-theme-panel-edge", colors.get("panel.edge", role_value(theme, ("alpha", "accent", 0.14)))),
        ("--jv-theme-glow-text", colors.get("glow.text", role_value(theme, ("alpha", "accent", 0.45)))),
        ("--jv-theme-nebula", role_value(theme, "bg.nebula")),
        ("--jv-theme-memory", role_value(theme, "memory")),
        ("--jv-theme-backdrop", backdrop(theme)),
        ("--jv-theme-state-cross", f"{theme['motion']['ms']['stateCross']}ms"),
    ]


def canon(value: str) -> str:
    """One spelling per value, so `1.2s` is `1200ms` and `cubic-bezier(0.6, 0, …)` is `(.6,0,…)`."""
    v = re.sub(r"\s+", "", value.lower())
    v = re.sub(r"(?<![\d.])0\.", ".", v)
    m = re.fullmatch(r"([\d.]+)s", v)
    return f"{round(float(m.group(1)) * 1000)}ms" if m else v


def css_diff(build, tokens: dict, theme: dict) -> list[tuple[str, str]]:
    base = {name: canon(value) for name, value, _ in build.web_tokens(tokens)}
    mine = build.web_tokens(themed(tokens, theme))
    return [(name, value) for name, value, _ in mine if base.get(name) != canon(value)] + extras(theme)


def gen_theme_css(build, tokens: dict, themes: dict[str, dict]) -> str:
    lines = [f"/* {MARK} — DO NOT EDIT. One block per pack; the default also applies before",
             " * the page knows the persona (no data-theme yet). THEMES.md says how to add one. */", ""]
    for tid, theme in themes.items():
        for role in ("display", "mono"):
            face = theme["typography"][role]
            for name in face["faces"]:
                weight = name.rsplit("-", 1)[1]
                lines.append(f"@font-face {{ font-family: '{face['family']}'; font-weight: {weight}; "
                             f"font-display: swap; src: url('/fonts/{name}.woff2') format('woff2'); }}")
    lines.append("")
    for tid, theme in themes.items():
        sel = f":root[data-theme='{tid}']"
        if tid == DEFAULT:
            sel = f":root:not([data-theme]),\n{sel}"
        lines.append(f"{sel} {{")
        lines.extend(f"\t{name}: {value};" for name, value in css_diff(build, tokens, theme))
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def public(theme: dict) -> dict:
    """What a surface needs at run time (the pack minus its $schema and asset paths it serves itself)."""
    keep = ("id", "name", "version", "description", "persona", "wake", "voice", "shape", "motion",
            "background", "core", "map", "haptics", "typography")
    out = {k: theme[k] for k in keep if k in theme}
    out["color"] = theme["color"]
    out["icons"] = theme["icons"]
    return out


def gen_theme_ts(themes: dict[str, dict]) -> str:
    data = json.dumps({tid: public(t) for tid, t in themes.items()}, indent="\t", ensure_ascii=False)
    ids = " | ".join(f"'{tid}'" for tid in themes)
    return f"""// {MARK} — DO NOT EDIT.
// Every theme pack as data: what the token vocabulary has no name for (persona, voice, wake
// phrases, core renderer and states, map style, marker, toggle). The colours, fonts, radii and
// motion are already in themes.css under :root[data-theme=…].

export type ThemeId = {ids};
export type CoreState = 'idle' | 'listen' | 'speak' | 'think' | 'embed' | 'music' | 'alert' | 'work';
export const THEME_IDS: readonly ThemeId[] = {json.dumps(list(themes))} as const;
export const DEFAULT_THEME: ThemeId = '{DEFAULT}';

export const THEMES = {data} as const;
"""


def kt_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("$", "\\$")


def flat_leaves(tree: dict, prefix: str = "") -> dict[str, str]:
    """A nested section as dotted key -> text (lists space-joined, booleans lower-case)."""
    out: dict[str, str] = {}
    for k, v in tree.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flat_leaves(v, key + "."))
        elif isinstance(v, list):
            out[key] = " ".join(str(x) for x in v)
        elif isinstance(v, bool):
            out[key] = "true" if v else "false"
        else:
            out[key] = str(v)
    return out


def kt_str_map(pairs: dict[str, str]) -> str:
    return f"mapOf({', '.join(f'{kt_str(k)} to {kt_str(v)}' for k, v in pairs.items())})"


def gen_theme_kotlin(themes: dict[str, dict]) -> str:
    blocks = []
    for tid, t in themes.items():
        core = t["core"]
        params = core.get("params", {})
        # The galaxy's numbers reach the phone from the pack, never typed in Kotlin (T-0871):
        # scalars by name, the aura rings as a list, the palette and each state's tint.
        scalars = {k: v for k, v in params.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        rings = [float(x) for x in params.get("auraRings", []) if isinstance(x, (int, float))]
        tints = {s: p["tint"] for s, p in core["states"].items() if isinstance(p.get("tint"), str)}
        bg = t.get("background", {})
        nebulae = ", ".join(
            f"Nebula({kt_str(argb_hex(n['color']))}, {float(n['at'][0])}f, {float(n['at'][1])}f, "
            f"{float(n['size'][0])}f, {float(n['size'][1])}f)" for n in bg.get("nebulae", []))
        colors = ",\n".join(f"            {kt_str(k)} to {kt_str(v if isinstance(v, str) else ' '.join(v))}"
                            for k, v in t["color"].items())
        strings = ",\n".join(f"            {kt_str(k)} to {kt_str(v)}" for k, v in t["persona"]["strings"].items())
        states = ",\n".join(
            f"            {kt_str(s)} to mapOf({', '.join(f'{kt_str(k)} to {float(v)}f' for k, v in p.items() if isinstance(v, (int, float)))})"
            for s, p in t["core"]["states"].items())
        blocks.append(f"""    val {tid.upper().replace('-', '_')} = Theme(
        id = {kt_str(tid)},
        name = {kt_str(t['name'])},
        spokenName = {kt_str(t['persona']['spokenName'])},
        voiceId = {kt_str(t['voice']['voiceId'])},
        offlineFallback = {kt_str(t['voice']['offline']['fallback'])},
        wakePhrases = listOf({', '.join(kt_str(p) for p in t['wake']['phrases'])}),
        wakeModel = {kt_str(t['wake']['model'])},
        wakeThreshold = {float(t['wake']['threshold'])}f,
        wakeVerify = {str(bool(t['wake'].get('useSpeakerVerification'))).lower()},
        renderer = {kt_str(t['core']['renderer'])},
        mapStyle = {kt_str(t['map']['style'])},
        marker = {kt_str(t['shape']['marker'])},
        toggle = {kt_str(t['shape']['toggle'])},
        displayFamily = {kt_str(t['typography']['display']['family'])},
        monoFamily = {kt_str(t['typography']['mono']['family'])},
        colors = mapOf(
{colors},
        ),
        strings = mapOf(
{strings},
        ),
        coreStates = mapOf(
{states},
        ),
        coreTween = {float(t['core'].get('tween', 0.035))}f,
        coreParams = mapOf({', '.join(f'{kt_str(k)} to {float(v)}f' for k, v in scalars.items())}),
        coreRings = listOf({', '.join(f'{x}f' for x in rings)}),
        corePalette = {kt_str_map(core.get('palette', {}))},
        coreTints = {kt_str_map(tints)},
        map = {kt_str_map(flat_leaves(t['map']))},
        ground = {kt_str('starfield' if bg.get('type') == 'starfield' else 'radial')},
        nebulae = listOf({nebulae}),
    )""")
    body = "\n\n".join(blocks)
    return f"""// {MARK} — DO NOT EDIT.
package ai.jarvis.app.ui.theme

/**
 * Every theme pack as data (the AURA brief §1): the persona, voice, wake phrases, core
 * renderer and its eight states, the map style. Colours are role -> "#RRGGBB" (or a gradient's
 * stops, space-separated) exactly as the pack writes them.
 */
object JarvisThemes {{
    data class Theme(
        val id: String,
        val name: String,
        val spokenName: String,
        val voiceId: String,
        /** voice.offline.fallback: who speaks a line with no clip offline (OfflineVoice). */
        val offlineFallback: String,
        val wakePhrases: List<String>,
        val wakeModel: String,
        /** wake.threshold: a score at or over it, two frames running, fires a head trained for the pack. */
        val wakeThreshold: Float,
        /** wake.useSpeakerVerification: a turn its head opens needs a recognised voice (PROMPT §4). */
        val wakeVerify: Boolean,
        val renderer: String,
        val mapStyle: String,
        val marker: String,
        val toggle: String,
        val displayFamily: String,
        val monoFamily: String,
        val colors: Map<String, String>,
        val strings: Map<String, String>,
        val coreStates: Map<String, Map<String, Float>>,
        val coreTween: Float,
        /** The core's scalar params by name (budgets, wind, flareMs…); lists and objects aside. */
        val coreParams: Map<String, Float> = emptyMap(),
        /** The galaxy's aura ring radii, as fractions of its spread. */
        val coreRings: List<Float> = emptyList(),
        /** The core's palette, role -> "#RRGGBB". */
        val corePalette: Map<String, String> = emptyMap(),
        /** Each state's tint "#RRGGBB", for the states that have one. */
        val coreTints: Map<String, String> = emptyMap(),
        /** The map section flattened: dotted key -> text, lists space-joined ("sky.gradient"). */
        val map: Map<String, String> = emptyMap(),
        /** background.type: "starfield" (her space behind every panel) or "radial". */
        val ground: String = "radial",
        /** background.nebulae: the starfield's coloured washes. */
        val nebulae: List<Nebula> = emptyList(),
    )

    /** A nebula: [color] "#AARRGGBB", centred at ([x], [y]) as fractions of the ground, [w] x [h] across in px. */
    data class Nebula(val color: String, val x: Float, val y: Float, val w: Float, val h: Float)

{body}

    val ALL: List<Theme> = listOf({', '.join(t.upper().replace('-', '_') for t in themes)})
    const val DEFAULT_ID = {kt_str(DEFAULT)}

    /** The pack for an id, or the default for anything unknown (a persona without a pack). */
    fun of(id: String?): Theme = ALL.firstOrNull {{ it.id == id }} ?: ALL.first {{ it.id == DEFAULT_ID }}
}}
"""


def gen_theme_py(themes: dict[str, dict]) -> str:
    data = json.dumps({tid: public(t) for tid, t in themes.items()}, indent=4, ensure_ascii=False)
    data = data.replace(": true", ": True").replace(": false", ": False").replace(": null", ": None")
    return f'''"""{MARK} — DO NOT EDIT.

Every theme pack as data, for the desktop agent and its console.
"""

from __future__ import annotations

DEFAULT = "{DEFAULT}"

THEMES: dict = {data}


def theme_for(theme_id: str | None) -> dict:
    """The pack for an id, or the default for anything unknown."""
    return THEMES.get(theme_id or "", THEMES[DEFAULT])
'''


def gen_plugin_themes(themes: dict[str, dict]) -> str:
    """What the personas plugin needs: who each theme is, how she sounds and wakes."""
    data = {"$generated": MARK, "default": DEFAULT, "themes": {
        tid: {"name": t["name"], "spokenName": t["persona"]["spokenName"], "style": t["persona"].get("style", ""),
              "voiceDescription": t["persona"].get("voiceDescription", ""), "voice": t["voice"]["voiceId"],
              "wakePhrases": t["wake"]["phrases"], "wakeModel": t["wake"]["model"], "strings": t["persona"]["strings"],
              "accent": t["color"]["accent"]}
        for tid, t in themes.items()}}
    # The whole packs and the schema too: AURA drafts a new theme from one by voice, and the
    # plugin holds the draft to the same rules as the validator (theme_rules.py, T-0861).
    data["packs"] = themes
    data["schema"] = json.loads((THEMES_DIR / "theme.schema.json").read_text(encoding="utf-8"))
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def gen_splash_xml(themes: dict[str, dict]) -> str:
    """The phone's launch per pack (T-0885): MainActivity hands the worn pack's style to
    setSplashScreenTheme, so the next cold start opens on that pack's ground and mark. The
    mark is the pack's ic_<id>_splash, else its launcher foreground (the same 2/3 safe zone
    the splash crops to). Does not cover the very first launch after an install, which can
    only be the manifest's Theme.Jarvis."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<!-- {MARK} — DO NOT EDIT. Run `python3 design/build.py`. -->",
        "<resources>",
    ]
    for tid, theme in themes.items():
        ground = theme["color"]["bg.void"].lstrip("#").upper()
        mark = f"ic_{tid}_splash" if any(DRAWABLES.glob(f"drawable*/ic_{tid}_splash.*")) else f"ic_{tid}_foreground"
        lines += [
            f'    <color name="jv_theme_{tid}_ground">#FF{ground}</color>',
            f'    <style name="Theme.Jarvis.Splash.{tid.capitalize()}" parent="@style/Theme.Jarvis">',
            f'        <item name="android:windowSplashScreenBackground">@color/jv_theme_{tid}_ground</item>',
            f'        <item name="android:windowSplashScreenAnimatedIcon">@drawable/{mark}</item>',
            "    </style>",
        ]
    lines.append("</resources>")
    return "\n".join(lines) + "\n"


def outputs(build, tokens: dict) -> dict[Path, str]:
    themes = load_themes()
    css = gen_theme_css(build, tokens, themes)
    return {
        WEB_THEME_CSS: css,
        SHELL_THEME_CSS: css,
        WEB_THEME_TS: gen_theme_ts(themes),
        KT_THEMES: gen_theme_kotlin(themes),
        DESKTOP_THEMES: gen_theme_py(themes),
        PLUGIN_THEMES: gen_plugin_themes(themes),
        SPLASH_XML: gen_splash_xml(themes),
    }
