#!/usr/bin/env python3
"""Executable spec: the phone hears the first word before the line is finished.

The adapter's `POST /api/audio/speak-pcm` answers 16-bit mono PCM in pieces
with `x-sample-rate` (jarvis-adapter, 2026-09-10); the console plays each piece
as it lands (`Player.playPcm`) and falls back to the whole-line `speak` when the
route answers 404 or 503. The phone must do the same: stream first, whole clip
when the voice does not stream, and a barge-in ends the line mid-word.

Run: `python3 android-app/tools/hermes_speech_stream_test.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
ROOT = ANDROID.parent
KOTLIN = ANDROID / "app/src/main/kotlin/ai/aura/app"
CLIENT = KOTLIN / "assist/hermes/HermesClient.kt"
CONVERSATION = KOTLIN / "assist/hermes/HermesConversation.kt"
PLAYER = KOTLIN / "assist/TtsPlayer.kt"
ADAPTER = ROOT / "aura-adapter/aura_adapter/app.py"

CHECKS = [
    (CLIENT, '"/api/audio/speak-pcm"', "the client does not ask for the streamed line"),
    # tests/contracts/first-clause.json `requestFlag`: the adapter cuts the first clause only on this.
    (CLIENT, '.put("first_clause", true)', "the start of a line does not ask for its first clause first"),
    (CLIENT, 'response.header("x-sample-rate")', "the client does not read the stream's sample rate"),
    (CLIENT, "if (!response.isSuccessful) return false", "a 404 or 503 must send the caller to the whole-line route"),
    (CONVERSATION, "link.speakStream(", "the reply is not streamed"),
    (CONVERSATION, "val audio = link.speak(text, voice)", "the whole-line route is no longer the fallback"),
    (CONVERSATION, "lineOut?.close()", "a stream that gave nothing leaves its track open"),
    (PLAYER, "AudioTrack.MODE_STREAM", "the player does not stream PCM"),
    (PLAYER, "line?.close()", "a barge-in does not end a streamed line"),
    (PLAYER, "AudioFormat.ENCODING_PCM_16BIT", "the track is not 16-bit, which is what the adapter sends"),
]


def main() -> int:
    failures = 0
    for path, needle, why in CHECKS:
        if needle not in path.read_text(encoding="utf-8"):
            print(f"FAIL {path.relative_to(ROOT)}: {why} (missing {needle!r})")
            failures += 1
    # The route and the header are the adapter's, not the phone's to invent.
    adapter = ADAPTER.read_text(encoding="utf-8")
    for needle in ('"/api/audio/speak-pcm"', '"x-sample-rate"'):
        if needle not in adapter:
            print(f"FAIL the adapter no longer carries {needle}; the phone reads it")
            failures += 1
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(f"hermes speech stream: {len(CHECKS)} checks passed, the route and header match the adapter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
