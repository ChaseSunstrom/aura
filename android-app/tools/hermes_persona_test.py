#!/usr/bin/env python3
"""Executable spec: the phone follows a persona switch, in the voice.

Who is primary lives in one file on the gateway (`tests/contracts/persona.json`):
the adapter reads it into `GET /api/status` -> `persona.active`, and a live
client hears a switch as `persona.switch` with `active` in its payload. The
console derives everything from that name (voice, hue, blades). The phone
follows the voice: a reply, streamed or whole, is asked for in the active
persona's voice, and a phone that connects after a switch learns the name
from the status. The hue on the phone's reactor is `persona_hue_test.py`'s
spec, not this one's.

Run: `python3 android-app/tools/hermes_persona_test.py`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
ROOT = ANDROID.parent
KOTLIN = ANDROID / "app/src/main/kotlin/ai/aura/app"
STATE = KOTLIN / "assist/hermes/HudState.kt"
STATUS = KOTLIN / "assist/hermes/HermesStatus.kt"
CONVERSATION = KOTLIN / "assist/hermes/HermesConversation.kt"
CONTRACT = ROOT / "tests/contracts/persona.json"

CHECKS = [
    (STATE, '"persona.switch" ->', "the phone ignores a persona switch"),
    (STATE, 'payload.optString("active").lowercase()', "the switch's name is not read from `active`, lower-cased"),
    (STATUS, 'json.optJSONObject("persona")?.optString("active")', "the status's persona is not read"),
    (CONVERSATION, "val voice = hud.persona", "the reply is not spoken in the persona's voice"),
    (CONVERSATION, "link.speakStream(\n                    text, voice,", "the streamed reply drops the voice"),
    (CONVERSATION, "link.speak(text, voice)", "the whole-line reply drops the voice"),
    (CONVERSATION, "status.persona != hud.persona) {\n                hud.persona = status.persona", "a phone connecting after a switch never learns the name"),
]


def main() -> int:
    failures = 0
    for path, needle, why in CHECKS:
        if needle not in path.read_text(encoding="utf-8"):
            print(f"FAIL {path.relative_to(ROOT)}: {why} (missing {needle!r})")
            failures += 1
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    # The persona's name IS the voice's name on the adapter: the four the
    # contract lists must each be a plain name the adapter's VOICE_NAME takes.
    import re
    for name in contract["personas"]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
            print(f"FAIL persona {name!r} is not a voice name the adapter accepts")
            failures += 1
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(f"hermes persona: {len(CHECKS)} checks passed; {len(contract['personas'])} personas are voice names")
    return 0


if __name__ == "__main__":
    sys.exit(main())
