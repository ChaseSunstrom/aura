#!/usr/bin/env python3
"""Executable spec: the phone speaks `hud.say` the way the console does.

`hud_say` (hermes/plugins/aura-hud) emits one `hud.say` event, `{text,
voice}`, for a line the person asked to hear (the owner, 2026-09-10: a typed
"can you say it" was answered with a file path). The console speaks it in
whatever mode it is in (`voice.say`, Hud.svelte); the phone must too, in the
named voice, and never over the person's own sentence. The event's name is one
string in three places -- the plugin's `SAY_EVENT`, the phone's, the
console's -- and this pins them together.

Run: `python3 android-app/tools/hermes_say_test.py`
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
ROOT = ANDROID.parent
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"
CLIENT = KOTLIN / "assist/hermes/HermesClient.kt"
CONVERSATION = KOTLIN / "assist/hermes/HermesConversation.kt"
PLUGIN = ROOT / "hermes/plugins/aura-hud/hud.py"
CONSOLE = ROOT / "aura-web/src/lib/ds/shell/Shell.svelte"

CHECKS = [
    (CONVERSATION, 'const val SAY_EVENT = "hud.say"', "the phone does not name the say event"),
    (CONVERSATION, "if (type == SAY_EVENT)", "the phone does not answer the say event"),
    (CONVERSATION, 'sayLine(line, payload.optString("voice"))', "the phone drops the named voice"),
    (CONVERSATION, "!(turnActive && sawSpeech)", "the phone would talk over the person's own sentence"),
    (CLIENT, 'fun speak(text: String, voice: String = "")', "the client cannot ask for a persona's voice"),
    (CLIENT, 'if (voice.isNotEmpty()) body.put("voice", voice)', "the client sends no voice, or sends an empty one"),
]


def main() -> int:
    failures = 0
    for path, needle, why in CHECKS:
        if needle not in path.read_text(encoding="utf-8"):
            print(f"FAIL {path.relative_to(ROOT)}: {why} (missing {needle!r})")
            failures += 1
    # One event name, three readers.
    plugin = re.search(r'SAY_EVENT: str = "([^"]+)"', PLUGIN.read_text(encoding="utf-8"))
    name = plugin.group(1) if plugin else None
    if name != "hud.say":
        print(f"FAIL the plugin's SAY_EVENT is {name!r}, not 'hud.say'")
        failures += 1
    if "event.type !== 'hud.say'" not in CONSOLE.read_text(encoding="utf-8"):
        print("FAIL the console no longer listens for 'hud.say'")
        failures += 1
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(f"hermes say: {len(CHECKS)} checks passed, one event name across plugin, console and phone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
