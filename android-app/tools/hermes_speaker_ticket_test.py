#!/usr/bin/env python3
"""Executable spec: the phone carries the speaker gate's word from the transcript to the prompt.

The adapter's `POST /api/audio/transcribe` answers with `speaker` (the house's
verdict on the voice) and, for a recognised voice, a `speaker_ticket`; a
transcript the gate refused comes back empty with `speaker.dropped` true
(`aura-adapter/aura_adapter/speaker.py`, 2026-09-10). The console hands
the ticket back on `prompt.submit` and says "I don't recognise that voice"
for a dropped turn (`aura-web/src/lib/hud/voice.svelte.ts`). This pins
that the phone does the same, so a spoken turn from the phone is credited to
the person and not silently to the key holder, and a refused voice is not
mistaken for silence.

Run: `python3 android-app/tools/hermes_speaker_ticket_test.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/aura/app"
CLIENT = KOTLIN / "assist/hermes/HermesClient.kt"
CONVERSATION = KOTLIN / "assist/hermes/HermesConversation.kt"
CONSOLE = ANDROID.parent / "aura-web/src/lib/hud/voice.svelte.ts"
CAR_VOICE = KOTLIN / "car/voice/CarVoiceStream.kt"

CHECKS = [
    (CLIENT, 'json.opt("speaker_ticket")', "the client does not read the ticket off the transcribe reply"),
    (CLIENT, 'optBoolean("dropped", false)', "the client does not read whether the gate dropped the words"),
    (CLIENT, '"speaker_ticket" to speakerTicket', "the client does not hand the ticket back on prompt.submit"),
    # A dropped voice, or words with no ticket in a turn "Aura" opened (AURA §4), told apart from silence.
    (CONVERSATION, "WakePersona.refused(heard.dropped, heard.ticket, heard.transcript, unverified)", "a dropped voice is not told apart from silence"),
    (CAR_VOICE, "WakePersona.refused(heard?.dropped == true, heard?.ticket, transcript, unverified)", "the car does not refuse an unrecognised voice"),
    (CONVERSATION, "submit(heard.transcript, heard.ticket, voice = true)", "the spoken turn is submitted without its ticket"),
    (CONVERSATION, 'NOT_RECOGNISED = "I don\'t recognise that voice."', "the refusal line differs from the console's"),
    # Build 1447: refused and empty turns went back to listening for ever.
    (CONVERSATION, "if (emptyTurns >= MAX_EMPTY_TURNS) end()", "empty or refused turns never end the conversation"),
    # The car's phone-microphone path once skipped the gate check and showed a refusal as "".
    (CAR_VOICE, 'return resolve(stt, transcript, " (phone microphone)")', "the car's phone-microphone path judges the transcript on its own"),
    (CAR_VOICE, "CarVoice.post(CarVoiceEvent.Ended)", "a car listening session can end with the reactor left on Listening"),
]


def main() -> int:
    failures = 0
    turned = CONVERSATION.read_text(encoding="utf-8").split("private fun onTranscribed", 1)[1].split("private fun submit", 1)[0]
    if "beginNextTurn() }, ERROR_LINGER_MS)" in turned:
        print("FAIL an empty turn goes back to listening without being counted (use listenAgainOrEnd)")
        failures += 1
    for path, needle, why in CHECKS:
        if needle not in path.read_text(encoding="utf-8"):
            print(f"FAIL {path.relative_to(ANDROID.parent)}: {why} (missing {needle!r})")
            failures += 1
    # The same words on both surfaces: the console's constant is the source.
    console = CONSOLE.read_text(encoding="utf-8") if CONSOLE.exists() else ""
    if console and "I don't recognise that voice." not in console:
        print("FAIL the console's NOT_RECOGNISED line moved; move the phone's with it")
        failures += 1
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(f"hermes speaker ticket: {len(CHECKS)} checks passed, the refusal line matches the console")
    return 0


if __name__ == "__main__":
    sys.exit(main())
