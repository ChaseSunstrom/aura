#!/usr/bin/env python3
"""Validate every theme pack in themes/ (T-0861, design/AURA/PROMPT.md §1 "Validator").

Checks, per pack: the schema (themes/theme.schema.json); WCAG AA (4.5:1) for every text role
over every surface it is drawn on; that every asset it names exists, every icon is the size
its key says, and every font face is vendored for the console and the phone; that the wake
model loads and its measured rates meet the pack's targets; that the voice is a stock voice
the voice server can speak. ``--live URL`` also asks that voice server whether it has it.

Stdlib only, so CI's static job runs it as it is. Not covered: what a surface does with the
pack (the web and phone suites), and a voice's offline clips (aura_voice_check.py).

    python3 scripts/verify/theme_check.py [--skip wake] [--live http://127.0.0.1:8890]
"""
from __future__ import annotations

import json
import struct
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THEMES = ROOT / "themes"
WEB_FONTS = ROOT / "jarvis-web/static/fonts"
ANDROID_FONTS = ROOT / "android-app/app/src/main/res/font"

sys.path.insert(0, str(ROOT / "hermes/plugins/jarvis-personas"))
# The rules a pack is held to live beside the plugin that holds a drafted pack to them too.
from theme_rules import KOKORO_VOICES, contrast_problems, ratio, schema_problems  # noqa: E402,F401


# --- assets --------------------------------------------------------------------------------

def png_size(path: Path) -> tuple[int, int] | None:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def asset_problems(pack: dict, folder: Path) -> list[str]:
    out = []
    icons = pack["icons"]

    def need(rel: str, what: str) -> Path | None:
        path = folder / rel
        if not path.is_file():
            out.append(f"asset: {what} {rel} is missing")
            return None
        return path

    for key in ("app", "monochrome", "favicon"):
        need(icons[key], f"icon {key}")
    for size, rel in icons.get("sizes", {}).items():
        path = need(rel, f"icon {size}px")
        if path and png_size(path) != (int(size), int(size)):
            out.append(f"asset: {rel} is {png_size(path)}, not {size}x{size}")
    for group in ("maskable", "monochromePng", "androidAdaptive"):
        for key, rel in (icons.get(group) or {}).items():
            need(rel, f"{group} {key}")
    for key, rel in (pack["voice"].get("earcons") or {}).items():
        need(rel, f"earcon {key}")
    if pack["core"].get("reference"):
        need(pack["core"]["reference"], "core reference")
    for role in ("display", "mono"):
        for face in pack["typography"][role]["faces"]:
            if not (WEB_FONTS / f"{face}.woff2").is_file():
                out.append(f"font: {face}.woff2 is not vendored for the console (design/fonts.py)")
            if not (ANDROID_FONTS / f"{face.replace('-', '_')}.ttf").is_file():
                out.append(f"font: {face.replace('-', '_')}.ttf is not vendored for the phone (design/fonts.py)")
    return out


def wake_problems(pack: dict, folder: Path) -> list[str]:
    wake = pack["wake"]
    model = wake["model"]
    if model.startswith("builtin:"):
        return []
    path = folder / model
    if not path.is_file():
        return [f"wake: {model} is missing (train it: THEMES.md)"]
    out = []
    try:
        import onnxruntime  # noqa: PLC0415 — optional here; the phone and the house load it for real
    except ImportError:
        onnxruntime = None
    if onnxruntime is not None:
        try:
            onnxruntime.InferenceSession(str(path))
        except Exception as exc:  # a corrupt or wrong-format head
            out.append(f"wake: {model} does not load ({exc})")
    metrics = path.with_suffix(".metrics.json")
    targets = wake.get("targets") or {}
    if targets:
        if not metrics.is_file():
            return out + [f"wake: no measured rates beside {model} ({metrics.name})"]
        got = json.loads(metrics.read_text(encoding="utf-8"))
        fa, fr = got.get("falseAcceptsPer24hSpeech"), got.get("falseRejectRate")
        need_fa, need_fr = targets.get("falseAcceptsPer24hSpeech"), targets.get("falseRejectRate")
        # PROMPT §4: missing the false-accept target keeps the word on, behind speaker verification.
        if fa is not None and need_fa is not None and fa > need_fa and not wake.get("useSpeakerVerification"):
            out.append(f"wake: {fa} false accepts/24 h over the target {need_fa}, and no speaker verification")
        if fr is not None and need_fr is not None and fr > need_fr:
            out.append(f"wake: false-reject rate {fr:.3f} over the target {need_fr}")
    return out


def voice_problems(pack: dict, live: str | None) -> list[str]:
    voice = pack["voice"]
    out = []
    for where, spec in (("server", voice), ("offline", voice.get("offline") or {})):
        if str(spec.get("engine", "")).startswith("kokoro") and spec.get("voiceId") not in KOKORO_VOICES:
            out.append(f"voice: {where} voice {spec.get('voiceId')!r} is not a stock Kokoro voice")
    if live:
        try:
            with urllib.request.urlopen(f"{live.rstrip('/')}/health", timeout=5) as resp:  # noqa: S310 — the house's own voice server
                voices = json.loads(resp.read().decode("utf-8")).get("voices") or []
        except (OSError, ValueError) as exc:
            return out + [f"voice: {live} did not answer ({exc})"]
        if voice["voiceId"] not in voices:
            out.append(f"voice: the voice server at {live} has no {voice['voiceId']}")
    return out


def check_pack(folder: Path, schema: dict, skip: set[str], live: str | None) -> list[str]:
    pack = json.loads((folder / "theme.json").read_text(encoding="utf-8"))
    out = schema_problems(pack, schema)
    if out:
        return out
    if pack["id"] != folder.name:
        out.append(f"id {pack['id']!r} is not the folder's name")
    out += contrast_problems(pack)
    out += asset_problems(pack, folder)
    if "wake" not in skip:
        out += wake_problems(pack, folder)
    out += voice_problems(pack, live)
    return out


def main(argv: list[str]) -> int:
    skip = {argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--skip"}
    live = next((argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--live"), None)
    schema = json.loads((THEMES / "theme.schema.json").read_text(encoding="utf-8"))
    failed = 0
    for folder in sorted(p.parent for p in THEMES.glob("*/theme.json")):
        problems = check_pack(folder, schema, skip, live)
        print(f"{'ok  ' if not problems else 'FAIL'} {folder.name}")
        for p in problems:
            print(f"     {p}")
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
