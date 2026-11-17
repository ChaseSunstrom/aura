#!/usr/bin/env python3
"""Vendor the two faces the design system names, subset, from their upstream repos.

``design/tokens.json`` names JetBrains Mono (the UI face: labels, running text,
data) and Space Grotesk (the big numbers), both SIL Open Font License 1.1
(BUILD_BRIEF §1). Nothing may
fetch a font at run time — the console's CSP forbids a font CDN and the phone
is offline-first — so the files ship with each surface:

    aura-web/static/fonts/<face>-<weight>.woff2      the console (and the desktop
                                                       shell, which loads it)
    android-app/app/src/main/res/font/<face>_<weight>.ttf
                                                       the phone: a font resource
                                                       cannot be woff2
    OFL-<Face>.txt beside each set                     the licence, which the OFL
                                                       requires to travel with the
                                                       files

This is not part of ``build.py`` because it needs the network and fontTools;
it is run when a face or a weight is added and the result is committed.
Sources are pinned to a commit, so running it twice yields the same bytes.
Subset: the Latin range the console has always shipped (ASCII, Latin-1, the
typographic punctuation, the euro), listed once below.

    . .venv/bin/activate && python3 -m pip install fonttools brotli
    python3 design/fonts.py
"""

from __future__ import annotations

import base64
import io
import json
import sys
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "aura-web/static/fonts"
ANDROID = ROOT / "android-app/app/src/main/res/font"
ANDROID_LICENCES = ROOT / "android-app/app/src/main/assets/fonts"

#: Upstream, pinned. The tag is the commit the files were taken from; move it
#: on purpose, with the goldens that show the change.
GROTESK = "https://raw.githubusercontent.com/floriankarsten/space-grotesk/03507d024a01282884232081fc6011c09ff4e849"
JETBRAINS = "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/19371302b95d218af43299bce79ddbddd0bc364d"
#: The AURA theme's faces (themes/aura/theme.json, T-0861), from Google Fonts' own repository.
GOOGLE = "https://cdn.jsdelivr.net/gh/google/fonts@5bb28733cec313b63fbd415b574b5ae35c8aa8b8"  # jsDelivr: the raw host rate-limits this box (429)
SORA = f"{GOOGLE}/ofl/sora/Sora%5Bwght%5D.ttf"  # variable: each weight is instanced below

#: (web basename, android resource, upstream file). The weights are the ones
#: ``type.weight.*`` names: the mono at 400 body, 500 label and 600 strong;
#: Space Grotesk at 300 (display, the brief's "Light") and 400.
FACES = [
    ("jetbrains-mono-400", "jetbrains_mono_400", f"{JETBRAINS}/fonts/ttf/JetBrainsMono-Regular.ttf"),
    ("jetbrains-mono-500", "jetbrains_mono_500", f"{JETBRAINS}/fonts/ttf/JetBrainsMono-Medium.ttf"),
    ("jetbrains-mono-600", "jetbrains_mono_600", f"{JETBRAINS}/fonts/ttf/JetBrainsMono-SemiBold.ttf"),
    ("space-grotesk-300", "space_grotesk_300", f"{GROTESK}/fonts/ttf/static/SpaceGrotesk-Light.ttf"),
    ("space-grotesk-400", "space_grotesk_400", f"{GROTESK}/fonts/ttf/static/SpaceGrotesk-Regular.ttf"),
    ("sora-200", "sora_200", SORA),
    ("sora-300", "sora_300", SORA),
    ("sora-400", "sora_400", SORA),
    ("sora-500", "sora_500", SORA),
    ("dm-mono-300", "dm_mono_300", f"{GOOGLE}/ofl/dmmono/DMMono-Light.ttf"),
    ("dm-mono-400", "dm_mono_400", f"{GOOGLE}/ofl/dmmono/DMMono-Regular.ttf"),
    ("dm-mono-500", "dm_mono_500", f"{GOOGLE}/ofl/dmmono/DMMono-Medium.ttf"),
]
LICENCES = {
    "OFL-SpaceGrotesk.txt": f"{GROTESK}/OFL.txt",
    "OFL-JetBrainsMono.txt": f"{JETBRAINS}/OFL.txt",
    "OFL-Sora.txt": f"{GOOGLE}/ofl/sora/OFL.txt",
    # jsDelivr answers 403 for this one file; the contents API serves the same pinned bytes.
    "OFL-DMMono.txt": "https://api.github.com/repos/google/fonts/contents/ofl/dmmono/OFL.txt"
    "?ref=5bb28733cec313b63fbd415b574b5ae35c8aa8b8",
}

#: The console's Latin subset: what the faces have carried since they were first
#: vendored (read back from the old Barlow cmap), so every weight matches.
UNICODES = (
    "U+0000,U+000D,U+0020-007E,U+00A0-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,"
    "U+02DA,U+02DC,U+0300-0301,U+0303-0304,U+0308-0309,U+0323,U+2013-2014,"
    "U+2018-201A,U+201C-201E,U+2022,U+2026,U+2032-2033,U+2039-203A,U+2044,"
    "U+20AC,U+2122,U+2212,U+2215"
)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "jarvis-design-fonts/1"})  # jsDelivr refuses urllib's default
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 — pinned GitHub raw URLs
        body = response.read()
    if url.startswith("https://api.github.com/"):  # the contents API wraps the file in base64 JSON
        return base64.b64decode(json.loads(body)["content"])
    return body


def subset_font(data: bytes, flavor: str | None) -> bytes:
    """The Latin subset, as woff2 (``flavor="woff2"``) or as TTF (``None``)."""
    font = TTFont(io.BytesIO(data))
    options = subset.Options()
    options.layout_features = ["*"]  # kerning and the rest: a subset that loses kern reads wrong
    options.name_IDs = ["*"]  # the licence and the designer stay in the file
    options.notdef_outline = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=subset.parse_unicodes(UNICODES))
    subsetter.subset(font)
    font.flavor = flavor  # on the font, not the options: `save` reads it from here
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def instance(data: bytes, weight: int) -> bytes:
    """One static weight of a variable font, so each surface ships the plain faces it names."""
    from fontTools.varLib import instancer

    font = instancer.instantiateVariableFont(TTFont(io.BytesIO(data)), {"wght": weight})
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def main(argv: list[str]) -> int:
    """Vendor every face, or with --missing only those not yet on disk (adding a theme's faces
    without re-writing the ones the goldens were recorded with)."""
    WEB.mkdir(parents=True, exist_ok=True)
    ANDROID.mkdir(parents=True, exist_ok=True)
    ANDROID_LICENCES.mkdir(parents=True, exist_ok=True)
    fetched: dict[str, bytes] = {}
    for web_name, android_name, url in FACES:
        if "--missing" in argv and (WEB / f"{web_name}.woff2").is_file() and (ANDROID / f"{android_name}.ttf").is_file():
            continue
        print(f"{web_name} ...", end=" ", flush=True)
        if url not in fetched:
            fetched[url] = fetch(url)
        original = fetched[url]
        if "%5Bwght%5D" in url:
            original = instance(original, int(web_name.rsplit("-", 1)[1]))
        woff2 = subset_font(original, "woff2")
        ttf = subset_font(original, None)
        (WEB / f"{web_name}.woff2").write_bytes(woff2)
        (ANDROID / f"{android_name}.ttf").write_bytes(ttf)
        print(f"{len(original) // 1024} KB -> {len(woff2) // 1024} KB woff2, {len(ttf) // 1024} KB ttf")
    for name, url in LICENCES.items():
        if "--missing" in argv and (WEB / name).is_file():
            continue
        text = fetch(url)
        (WEB / name).write_bytes(text)
        (ANDROID_LICENCES / name).write_bytes(text)
        print(f"{name}: {len(text)} bytes, beside both sets")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
