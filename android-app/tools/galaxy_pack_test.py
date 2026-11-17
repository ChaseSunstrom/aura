#!/usr/bin/env python3
"""Executable spec: AURA's galaxy core and space drive read the pack, never a typed copy (T-0871).

`themes/aura/theme.json` is the pack. Its `core` (budgets, palette, tints, rings, tween and
the eight per-state targets) and its `map` are generated into `JarvisThemes.AURA`; this
reads the generated Kotlin back against the pack, then checks that `GalaxyModel` takes its
star budgets, palette, tints and state targets from that pack (no 1200/360/2100 or palette
hex typed in it), that the phone's core and the car's corner switch on `core.renderer`,
and that the drive map switches on `map.style` and types no colour of its own.
`GalaxyModelTest` (JVM) proves the model's behaviour; this proves where its numbers come
from, without an SDK, in a second.

Run:  python3 android-app/tools/galaxy_pack_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app"
PACK = ROOT / "themes/aura/theme.json"
THEMES = APP / "ui/theme/JarvisThemes.kt"
MODEL = APP / "reactor/core/GalaxyModel.kt"
CANVAS = APP / "reactor/core/GalaxyCanvas.kt"
V2CORE = APP / "reactor/core/V2Core.kt"
CAR = APP / "car/holo/CarDrive.kt"
REST = APP / "assist/island/RestView.kt"
SPACE = APP / "car/holo/SpacePainter.kt"
GROUND = APP / "car/holo/HoloGround.kt"

STATES = ("idle", "listen", "speak", "think", "embed", "music", "alert", "work")
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, bool(ok) and "" or detail))


def block(src: str, name: str) -> str:
    """The text of `val NAME = Theme(` up to the next pack (or the end)."""
    m = re.search(rf"val {name} = Theme\((.*?)(?=\n\s*val [A-Z]+ = Theme\(|\Z)", src, re.S)
    return m.group(1) if m else ""


def kt_map(text: str, field: str) -> dict[str, str]:
    """`field = mapOf("k" to v, ...)` on one line, as strings."""
    m = re.search(rf"{field} = mapOf\((.*)\),?\n", text)
    if not m:
        return {}
    return {k: v.strip('"').rstrip("f") for k, v in re.findall(r'"([^"]+)" to ("[^"]*"|[-0-9.]+f?)', m.group(1))}


def num(a: str, b: object) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def main() -> int:
    for path in (PACK, THEMES, MODEL, CANVAS, V2CORE, CAR, SPACE, GROUND):
        if not path.is_file():
            print(f"  FAIL  {path.relative_to(ROOT)} is missing")
            return 1
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    core, mp = pack["core"], pack["map"]
    aura = block(THEMES.read_text(encoding="utf-8"), "AURA")
    check("JarvisThemes has the AURA pack", bool(aura))

    # 1. The generated pack is the pack's own numbers.
    check("renderer is the pack's", f'renderer = "{core["renderer"]}"' in aura)
    check("mapStyle is the pack's", f'mapStyle = "{mp["style"]}"' in aura)
    params = kt_map(aura, "coreParams")
    for k, v in core["params"].items():
        if isinstance(v, (int, float)):
            check(f"coreParams.{k} = {v}", num(params.get(k, ""), v), params.get(k, "missing"))
    rings = re.search(r"coreRings = listOf\(([^)]*)\)", aura)
    got = [float(x.rstrip("f")) for x in rings.group(1).split(",") if x.strip()] if rings else []
    check("coreRings are the pack's auraRings", got == [float(x) for x in core["params"]["auraRings"]], str(got))
    tween = re.search(r"coreTween = ([0-9.]+)f", aura)
    check("coreTween is the pack's", bool(tween) and num(tween.group(1), core["tween"]))
    palette = kt_map(aura, "corePalette")
    check("corePalette is the pack's palette", palette == core["palette"], str(set(palette.items()) ^ set(core["palette"].items())))
    tints = kt_map(aura, "coreTints")
    want_tints = {s: row["tint"] for s, row in core["states"].items() if "tint" in row}
    check("coreTints are the states' tints", tints == want_tints, str(tints))
    for s in STATES:
        row = core["states"].get(s)
        check(f"the pack has state `{s}`", row is not None)
        m = re.search(rf'"{s}" to mapOf\(([^)]*)\)', aura)
        kt = dict((k, v.rstrip("f")) for k, v in re.findall(r'"([^"]+)" to ([-0-9.]+f)', m.group(1))) if m else {}
        for k, v in (row or {}).items():
            if isinstance(v, (int, float)):
                check(f"state {s}.{k} = {v}", num(kt.get(k, ""), v), kt.get(k, "missing"))
    kmap = kt_map(aura, "map")
    for key, value in (("sky.stars", mp["sky"]["stars"]), ("route.color", mp["route"]["color"]),
                       ("buildings.near", mp["buildings"]["near"]), ("sky.gradient", " ".join(mp["sky"]["gradient"]))):
        check(f"map.{key} is the pack's", kmap.get(key) == str(value), kmap.get(key, "missing"))

    # 2. The model reads the pack and types none of its numbers.
    model = MODEL.read_text(encoding="utf-8")
    for field in ("coreParams", "corePalette", "coreTints", "coreStates", "coreTween", "coreRings"):
        check(f"GalaxyModel reads {field}", field in model)
    for budget in ("starsPhone", "starsMini", "starsDesktop"):
        check(f"GalaxyQuality names {budget}", f'("{budget}")' in model)
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", model, flags=re.S)
    for n in ("1200", "360", "2100"):
        check(f"GalaxyModel types no budget {n}", not re.search(rf"\b{n}\b", code))
    # The small-size counts are the pack's too (the review of T-0871 found 6 and 30 typed).
    for key, was in (("nebulaeMini", 6), ("jetsMini", 30), ("jets", 90)):
        check(f"the pack's galaxy holds {key}", core["params"].get(key) == was, str(core["params"].get(key)))
        check(f"GalaxyModel reads {key}", f'"{key}"' in code)
    check("GalaxyModel types no MINI_ count", not re.search(r"MINI_(NEBULAE|JETS)|JETS_N", code))
    canvas = re.sub(r"/\*.*?\*/|//[^\n]*", "", CANVAS.read_text(encoding="utf-8"), flags=re.S)
    for name, src in (("GalaxyModel", code), ("GalaxyCanvas", canvas)):
        check(f"{name} types no colour", not HEX.search(src), str(HEX.findall(src)[:3]))

    # 3. The renderer and the map style choose; the drive types no colour of its own.
    check("V2Core switches on core.renderer", "ActiveTheme.theme.renderer == GALAXY" in V2CORE.read_text(encoding="utf-8"))
    car = CAR.read_text(encoding="utf-8")
    check("CarDrive switches on core.renderer", "ActiveTheme.theme.renderer == GALAXY" in car)
    check("the car's galaxy is the dock budget", "GalaxyQuality.MINI" in car)
    # A galaxy a screen remembers is the worn pack's: a change of persona makes a new one.
    v2 = V2CORE.read_text(encoding="utf-8")
    check("V2Core keys its galaxy on the worn pack", "remember(reduced, pack)" in v2 and "remember(target, it, pack)" in v2)
    check("CarDrive rebuilds its galaxy when the pack changes", "it.pack === pack" in car)
    rest = REST.read_text(encoding="utf-8")
    check("RestView builds its still from the worn pack", "GalaxyModel.packOf(theme)" in rest)
    space = SPACE.read_text(encoding="utf-8")
    check("MapLook switches on map.style", "theme.mapStyle != SPACE_DRIVE" in space)
    check("SPACE_DRIVE is the pack's style", f'SPACE_DRIVE = "{mp["style"]}"' in space)
    for key in ("sky.gradient", "ground.gradient", "sky.stars", "sky.band", "route.color", "road.lanes",
                "buildings.near", "buildings.far", "buildings.beacons"):
        check(f"the space drive reads map `{key}`", f'"{key}"' in space)
    space_code = re.sub(r"/\*.*?\*/|//[^\n]*", "", space, flags=re.S)
    check("SpacePainter types no colour", not HEX.search(space_code), str(HEX.findall(space_code)[:3]))
    check("the drive follows the persona's map each frame", "MapLook.of()" in GROUND.read_text(encoding="utf-8"))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        if not ok:
            print(f"  FAIL  {name}" + (f": {detail}" if detail else ""))
    print(f"galaxy_pack: {len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
