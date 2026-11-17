#!/usr/bin/env python3
"""The phone and car with the server out (OfflineVoice.kt, OnDeviceStt.kt), mirrored.

Cases and wording are tests/contracts/offline-voice.json; OfflineVoiceTest.kt,
OnDeviceSttTest.kt and AudioFocusTest.kt run the same table. The last test pins the
Kotlin source to the table's constants, so the strings cannot drift apart.

Run:  python3 android-app/tools/offline_voice_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
C = json.loads((ROOT / "tests" / "contracts" / "offline-voice.json").read_text(encoding="utf-8"))
ASSIST = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/assist"


def speaker(has_clip: bool, bundled_ready: bool, fallback: str = "bundled-voice", own_realtime: bool = False) -> str:
    if has_clip:
        return "clip"
    if fallback == "prerendered-clips":
        return "on_device" if own_realtime else "notice_only"
    return "bundled" if bundled_ready else "system_announced"


def listener(audio_in_hand: bool, server: bool, bundled: bool, platform: bool, gate: bool) -> str:
    if server:
        return "server"
    if gate:
        return "gate"
    if audio_in_hand:
        return "bundled" if bundled else "none"
    return "platform" if platform else "none"


def test_speech_table():
    assert C["speech"]["orders"]["bundled-voice"] == ["clip", "bundled", "system_announced"]
    for c in C["speech"]["cases"]:
        assert speaker(c["hasClip"], c["bundledReady"], c["fallback"], c.get("ownVoiceRealtime", False)) == c["expect"], c["name"]
        assert c["expect"] in C["speech"]["orders"][c["fallback"]], c["name"]


def test_a_clips_only_pack_is_never_said_by_another_voice():
    for clip in (False, True):
        for ready in (False, True):
            for own in (False, True):
                assert speaker(clip, ready, "prerendered-clips", own) in ("clip", "on_device", "notice_only")


def test_every_pack_names_an_offline_order():
    for pack in sorted((ROOT / "themes").glob("*/theme.json")):
        fallback = json.loads(pack.read_text(encoding="utf-8"))["voice"]["offline"]["fallback"]
        assert fallback in C["speech"]["orders"], pack


def test_listen_table():
    for c in C["listen"]["cases"]:
        got = listener(c["audioInHand"], c["serverReachable"], c["bundledModel"],
                       c["platformRecognizer"], c["gateEnforcing"])
        assert got == c["expect"], c["name"]


def test_offline_never_picks_the_system_engine_over_the_bundled_voice():
    for clip in (False, True):
        assert speaker(clip, True) != "system_announced"


def test_kotlin_carries_the_table_strings():
    stt = (ASSIST / "OnDeviceStt.kt").read_text(encoding="utf-8")
    assert C["listen"]["none_says"].replace("'", "'") in stt
    assert C["listen"]["gate_says"] in stt
    focus = (ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/audio/AudioAttention.kt").read_text(
        encoding="utf-8")
    assert "AudioManager.AUDIOFOCUS_" + C["focus"]["gain"].replace("GAIN", "GAIN", 1) in focus or (
        "AUDIOFOCUS_" + C["focus"]["gain"] in focus)
    assert C["focus"]["usage"] in focus
    for name in C["focus"]["endsOn"] + C["focus"]["continuesOn"]:
        assert name in focus, name


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)
