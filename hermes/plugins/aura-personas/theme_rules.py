"""What makes a theme pack valid, shared by the validator CLI and the personas plugin (T-0861).

scripts/verify/theme_check.py runs these over themes/ in CI; the plugin runs them over a pack
AURA drafts by voice before it is previewed. Stdlib only, so both run them as they are.
Not covered: assets, fonts and the wake head, which only the CLI can see on disk.
"""
from __future__ import annotations

import re

#: Kokoro-82M v1.0's stock English voices (hexgrad/Kokoro-82M VOICES.md). Synthetic, not clones.
KOKORO_VOICES = frozenset(
    "af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river af_sarah af_sky "
    "am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa "
    "bf_alice bf_emma bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis".split()
)
AA = 4.5
#: Text roles and the surfaces each is drawn on; the "on.*" inks only sit on their fill.
TEXT_ON = {
    "text": ("bg.void", "bg.deep", "bg.nebula"),
    "text.secondary": ("bg.void", "bg.deep"),
    "text.label": ("bg.void", "bg.deep"),
    "accent": ("bg.void", "bg.deep"),
    "attention": ("bg.void", "bg.deep"),
    "danger": ("bg.void", "bg.deep"),
    "ok": ("bg.void", "bg.deep"),
    "on.accent": ("accent",),
    "on.attention": ("attention",),
}


# --- schema: the subset theme.schema.json uses --------------------------------------------

def schema_problems(value: object, schema: dict, where: str = "$") -> list[str]:
    out: list[str] = []
    if "oneOf" in schema:
        if not any(not schema_problems(value, s, where) for s in schema["oneOf"]):
            out.append(f"{where}: matches none of its allowed shapes")
        return out
    kind = schema.get("type")
    types: dict[str, type | tuple[type, ...]] = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if kind and not (isinstance(value, types[kind]) and not (kind in ("integer", "number") and isinstance(value, bool))):
        return [f"{where}: expected {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        out.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{where}: needs at least {schema['minItems']} item(s)")
        for i, item in enumerate(value):
            out += schema_problems(item, schema.get("items", {}), f"{where}[{i}]")
    if isinstance(value, dict):
        out += [f"{where}: missing {key!r}" for key in schema.get("required", []) if key not in value]
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for key, item in value.items():
            if key in props:
                out += schema_problems(item, props[key], f"{where}.{key}")
            elif isinstance(extra, dict):
                out += schema_problems(item, extra, f"{where}.{key}")
    return out


# --- contrast ------------------------------------------------------------------------------

def _lum(hex_colour: str) -> float:
    v = hex_colour.lstrip("#")
    chans = [int(v[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def contrast_problems(pack: dict) -> list[str]:
    colors = pack["color"]
    pairs = dict(TEXT_ON)
    fill = pack["typography"]["roles"].get("label", {}).get("fill", "")
    if fill.startswith("gradient:"):  # a gradient label must read at both ends
        for role in (s.strip() for s in fill[len("gradient:"):].split("→")):
            pairs.setdefault(role, ("bg.void", "bg.deep"))
    out = []
    for fg, surfaces in pairs.items():
        for bg in surfaces:
            if fg in colors and bg in colors:
                r = ratio(colors[fg], colors[bg])
                if r < AA:
                    out.append(f"contrast: {fg} {colors[fg]} on {bg} {colors[bg]} is {r:.2f}:1, under {AA}:1")
    return out
