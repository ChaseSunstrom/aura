#!/usr/bin/env python3
"""Executable spec: the phone wears its persona's theme pack, AURA by default (T-0869).

`tests/contracts/persona.json` names the personas, the default one and the theme each
persona wears; `themes/<id>/theme.json` is a pack, generated into `JarvisThemes.kt`.
This reads the phone back against both: the theme reader (`ActiveTheme`) picks a
persona's own pack or JARVIS's, every default is `JarvisThemes.DEFAULT_ID`, the
launcher has one alias per persona with exactly the default's enabled, the default's
label is the pack's name, and AURA's adaptive icon is the pack's own art.
`PersonaAccentTest` (JVM) proves the live switch; this proves the shape, without an
SDK, in a second.

Run:  python3 android-app/tools/aura_theme_test.py
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET  # the repo's own resources, never fetched input
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "android-app/app/src/main"
APP = MAIN / "kotlin/ai/jarvis/app"
RES = MAIN / "res"
NS = "{http://schemas.android.com/apk/res/android}"

CONTRACT = ROOT / "tests/contracts/persona.json"
THEMES = APP / "ui/theme/JarvisThemes.kt"
ACTIVE = APP / "ui/theme/ActiveTheme.kt"
ACCENT = APP / "ui/theme/PersonaAccent.kt"
ICON = APP / "ui/theme/PersonaIcon.kt"
CONFIG = APP / "config/JarvisConfig.kt"
V2 = APP / "ui/v2/V2.kt"
CAR_SETUP = APP / "car/setup/CarSetupActivity.kt"
CAR_LINK = APP / "car/service/CarLinkService.kt"
MANIFEST = MAIN / "AndroidManifest.xml"
STRINGS = RES / "values/strings.xml"
ADAPTIVE = RES / "mipmap-anydpi-v26/ic_aura.xml"
GLYPH = RES / "drawable/ic_aura_monochrome.xml"
STATUS = RES / "drawable/ic_aura_status.xml"
WORDS = APP / "assist/V2Words.kt"
ISLAND = APP / "assist/island/IslandView.kt"
ASK_HOST = APP / "companion/ConversationAskHost.kt"
SPLASH = RES / "values-v31/theme_splash.xml"
MAIN_ACTIVITY = APP / "MainActivity.kt"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, bool(ok) and "" or detail))


def main() -> int:
    for path in (CONTRACT, THEMES, ACTIVE, ACCENT, ICON, CONFIG, V2, CAR_SETUP, CAR_LINK,
                 MANIFEST, STRINGS, ADAPTIVE, GLYPH, WORDS, ISLAND, ASK_HOST):
        if not path.is_file():
            print(f"  FAIL  {path.relative_to(ROOT)} is missing")
            return 1

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    themes_src = THEMES.read_text(encoding="utf-8")
    active_src = ACTIVE.read_text(encoding="utf-8")
    accent_src = ACCENT.read_text(encoding="utf-8")
    icon_src = ICON.read_text(encoding="utf-8")
    personas: list[str] = contract["personas"]
    default: str = contract["default"]
    themes: dict[str, str] = {k: v for k, v in contract["themes"].items() if not k.startswith("$")}

    # 1. The packs the phone has, and the default, are the contract's.
    packs = re.findall(r'^\s*val ([A-Z]+) = Theme\(\s*id = "([a-z]+)"', themes_src, re.M)
    pack_ids = [i for _, i in packs]
    check("JarvisThemes has packs", bool(pack_ids), "no `val X = Theme(id = ...)` found")
    m = re.search(r'const val DEFAULT_ID = "([a-z]+)"', themes_src)
    check("JarvisThemes.DEFAULT_ID is the contract's default", bool(m) and m.group(1) == default,
          m.group(1) if m else "missing")

    # 2. The reader: a persona wears its own pack (each has one since T-0936 removed the
    # packless three), and an unknown name the default's, as the console and the adapter do.
    check("ActiveTheme picks the persona's own pack", "JarvisThemes.ALL.firstOrNull { it.id == persona }" in active_src)
    check("every persona has a pack of its own", set(personas) <= set(pack_ids), str(sorted(set(personas) - set(pack_ids))))
    check("no persona wears another's pack", "PACKLESS" not in active_src)
    check("an unknown name wears the default", "it.id == JarvisThemes.DEFAULT_ID" in active_src)
    for name in personas:
        worn = name if name in pack_ids else default
        check(f"`{name}` wears `{themes.get(name)}`", worn == themes.get(name), f"phone {worn}")
    check("the theme follows PersonaAccent.name", "forPersona(PersonaAccent.name)" in active_src)

    # 3. Every default is the generated default, never a typed name.
    check("PersonaAccent starts on the default",
          "mutableStateOf(JarvisThemes.DEFAULT_ID)" in accent_src
          and "ifEmpty { JarvisThemes.DEFAULT_ID }" in accent_src)
    check("load() reconciles the launcher alias", "PersonaIcon.apply(context.applicationContext, name)" in accent_src)
    check("JarvisConfig.persona defaults to the default",
          "prefs.getString(KEY_PERSONA, null) ?: JarvisThemes.DEFAULT_ID" in CONFIG.read_text(encoding="utf-8"))
    check("PersonaIcon falls back to the default's alias", "ALIASES.getValue(JarvisThemes.DEFAULT_ID)" in icon_src)

    # 4. One launcher alias per persona, exactly the default's enabled, labelled with the pack's name.
    aliases = dict(re.findall(r'"([a-z]+)" to "(Launcher[A-Za-z]+)"', icon_src))
    check("PersonaIcon has an alias per persona", sorted(aliases) == sorted(personas), f"{sorted(aliases)}")
    manifest = ET.fromstring(MANIFEST.read_text(encoding="utf-8"))
    app = manifest.find("application")
    declared = {e.get(NS + "name", "").lstrip("."): e for e in app.iter("activity-alias")}
    for persona, alias in aliases.items():
        check(f"{alias} is declared", alias in declared)
    enabled = sorted(a for a in aliases.values() if a in declared and declared[a].get(NS + "enabled") != "false")
    check("exactly the default's alias is enabled", enabled == [aliases.get(default)], f"enabled {enabled}")
    strings = {e.get("name"): (e.text or "") for e in ET.fromstring(STRINGS.read_text(encoding="utf-8")).iter("string")}
    pack = json.loads((ROOT / f"themes/{default}/theme.json").read_text(encoding="utf-8"))
    check("app_name is the default pack's name", strings.get("app_name") == pack["name"],
          f"{strings.get('app_name')!r} vs {pack['name']!r}")
    chosen = declared.get(aliases.get(default, ""))
    check("the default's alias wears app_name and its icon",
          chosen is not None and chosen.get(NS + "label") == "@string/app_name"
          and chosen.get(NS + "icon") == f"@mipmap/ic_{default}")
    check("the application wears the default's icon", app.get(NS + "icon") == f"@mipmap/ic_{default}"
          and app.get(NS + "roundIcon") == f"@mipmap/ic_{default}")

    # 5. AURA's icon is the pack's art: adaptive layers byte-equal, the glyph its monochrome.
    adaptive = ADAPTIVE.read_text(encoding="utf-8")
    for layer in ("background", "foreground", "monochrome"):
        check(f"ic_aura has a {layer} layer", f"<{layer} " in adaptive)
    icons = pack["icons"]["androidAdaptive"]
    for layer in ("foreground", "background"):
        src = ROOT / f"themes/{default}" / icons[layer]
        dst = RES / f"drawable-xxxhdpi/ic_aura_{layer}.png"
        check(f"ic_aura_{layer}.png is the pack's", dst.is_file() and src.read_bytes() == dst.read_bytes(), str(src))
    svg = (ROOT / f"themes/{default}" / pack["icons"]["monochrome"]).read_text(encoding="utf-8")
    glyph = GLYPH.read_text(encoding="utf-8")
    for d in re.findall(r'<path d="([^"]+)"', svg):
        check(f"the glyph carries the pack's path {d[:16]}...", d in glyph)

    # 5b. The notification icon is the worn pack's (T-0908: the owner saw JARVIS's logo in the tray
    # with AURA worn). AURA's is the glyph at the status bar's 24 dp, alpha only, with no
    # safe-zone shrink; every notification reads ActiveTheme.statusIcon, none types JARVIS's.
    status = STATUS.read_text(encoding="utf-8") if STATUS.is_file() else ""
    check("ic_aura_status is 24 dp", 'android:width="24dp"' in status and 'android:height="24dp"' in status)
    check("ic_aura_status is not shrunk to the launcher's safe zone", 'android:scaleX="0.6"' not in status)
    for d in re.findall(r'<path d="([^"]+)"', svg):
        check(f"ic_aura_status carries the pack's path {d[:16]}...", d in status)
    typed = [f"{k.relative_to(MAIN)}:{n}" for k in (MAIN / "kotlin").rglob("*.kt")
             for n, line in enumerate(k.read_text(encoding="utf-8").splitlines(), 1)
             if "setSmallIcon(" in line and "ActiveTheme.statusIcon" not in line]
    check("every notification wears the worn pack's icon", not typed, ", ".join(typed[:5]))

    # 5c. The phone's own words name the app, AURA (T-0929: the owner, "why do a bunch of things
    # in the app still say jarvis"). Identifiers (jarvis-core) are lower case and not checked.
    named = [e.get("name") for e in ET.fromstring(STRINGS.read_text(encoding="utf-8")).iter()
             if e.tag in ("string", "item") and re.search(r"\bJarvis\b|\bJARVIS\b", "".join(e.itertext()))]
    check("no string of the phone's says Jarvis", not named, ", ".join(named[:6]))
    # aapt refuses a bare apostrophe; the rename above once wrote one and broke the build.
    bare = [m for m in re.findall(r"<string name=\"([^\"]+)\">((?:(?!</string>).)*)</string>", STRINGS.read_text(encoding="utf-8"), re.S)
            if re.search(r"(?<!\\)'", m[1]) and not m[1].startswith('"')]
    check("no string has a bare apostrophe", not bare, ", ".join(n for n, _ in bare[:6]))
    tile = (MAIN / "kotlin/ai/jarvis/app/assist/WakeTileService.kt").read_text(encoding="utf-8")
    check("the wake tile says the worn pack's phrase", "tile.label = ActiveTheme.wakePhrase" in tile)

    # 6. The surfaces that draw with Compose read the theme, not the fixed tokens.
    v2 = V2.read_text(encoding="utf-8")
    check("V2's stage wears AppTheme", "AppTheme {" in v2 and "JarvisTheme {" not in v2)
    check("V2 reads ActiveTheme", "ActiveTheme." in v2)
    check("the car setup wears AppTheme", "AppTheme {" in CAR_SETUP.read_text(encoding="utf-8"))
    check("the car's app button is the pack's name",
          "labelText = ActiveTheme.theme.name" in CAR_LINK.read_text(encoding="utf-8"))

    # 7. The views drawn without Compose (T-0885): the overlay's island, its turn labels and the
    # dock name and paint the worn pack, as the owner saw "JARVIS" talking with AURA worn.
    words = WORDS.read_text(encoding="utf-8")
    check("the turn label is the worn pack's name, not a fixed JARVIS",
          'const val PERSONA = "JARVIS"' not in words and "name: String = ActiveTheme.name" in words)
    check("the dock's hint names the worn pack", '"Ask JARVIS anything"' not in words and '"Message JARVIS"' not in words)
    check("the assistant's label is the pack's accent", 'ActiveTheme.argb("accent"' in words)
    island = ISLAND.read_text(encoding="utf-8")
    check("the island's fill and edge are the worn pack's",
          "color = JarvisTokens.Material.Floating.FILL" not in island and island.count("ActiveTheme.tint(") >= 2)
    check("the ask host says the worn name while it speaks",
          'onMode(Phase.SPEAKING, "Jarvis")' not in ASK_HOST.read_text(encoding="utf-8"))

    # 8. The launch (T-0885): the system splash is the worn pack's ground and mark, not JARVIS's,
    # as the owner saw JARVIS's reactor open the app with AURA worn.
    splash = ET.parse(SPLASH).getroot() if SPLASH.exists() else ET.Element("resources")
    styles = {s.get("name"): {i.get("name"): (i.text or "") for i in s} for s in splash.iter("style")}
    grounds = {c.get("name"): (c.text or "").upper() for c in splash.iter("color")}
    for pid in pack_ids:
        style = styles.get(f"Theme.Jarvis.Splash.{pid.capitalize()}", {})
        ground = style.get("android:windowSplashScreenBackground", "")
        want = json.loads((ROOT / f"themes/{pid}/theme.json").read_text(encoding="utf-8"))["color"]["bg.void"]
        check(f"the {pid} splash is its pack's ground",
              ground.startswith("@color/") and grounds.get(ground[7:], "").endswith(want.upper().lstrip("#")),
              f"{ground} -> {grounds.get(ground[7:])}, want {want}")
    check("MainActivity hands the worn pack's splash to the next launch",
          "setSplashScreenTheme(" in MAIN_ACTIVITY.read_text(encoding="utf-8"))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("  ok    " if ok else "  FAIL  ") + name + (f"\n        | {detail}" if not ok and detail else ""))
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
