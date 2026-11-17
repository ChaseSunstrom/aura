#!/usr/bin/env python3
"""Executable spec: the owner's phone calls on their other screens (T-0616).

`calls/CallEvents.kt` and `calls/CallActions.kt` are the rule; `tests/contracts/call-event.json` is
their table, which CallEventsTest and CallActionsTest run too. This mirror runs the relationship and
screening cases, and checks the Kotlin keeps the names, limits and fixed reply lines verbatim, never
logs a caller, never sends a number in the event, never uses CONFIRM-less dialling for a stranger's
text, and is wired where the contract says.

Run:  python3 android-app/tools/call_event_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KT = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app"
EVENTS = (KT / "calls/CallEvents.kt").read_text()
ACTIONS = (KT / "calls/CallActions.kt").read_text()
SCREEN = (KT / "calls/JarvisCallScreeningService.kt").read_text()
CONTRACT = json.loads((ROOT / "tests/contracts/call-event.json").read_text())


def relationship(contact: bool, starred: bool) -> str:
    return "starred" if starred else "contact" if contact else "unknown"


def screen(rule: str, rel: str) -> str:
    if rel != "unknown":
        return "allow"
    return rule if rule in ("silence", "reject") else "allow"


def check(name: str, ok: bool, failed: list[str]) -> None:
    print(("PASS" if ok else "FAIL") + f"  {name}")
    if not ok:
        failed.append(name)


def main() -> int:
    failed: list[str] = []
    for c in CONTRACT["relationship_cases"]:
        check(c["name"], relationship(c["contact"], c["starred"]) == c["relationship"], failed)
    for c in CONTRACT["policy"]:
        check(c["name"], screen(c["rule"], c["relationship"]) == c["response"], failed)

    check("the event name is the contract's", f'EVENT = "{CONTRACT["event"]}"' in EVENTS, failed)
    for key, const in (("answer", "ACTION_ANSWER"), ("decline", "ACTION_DECLINE"), ("back", "ACTION_BACK"), ("policy", "ACTION_POLICY")):
        check(f"{const} is the contract's", f'{const} = "{CONTRACT["actions"][key]}"' in EVENTS, failed)
    check("FROM_MAX is the contract's", f'FROM_MAX = {CONTRACT["limits"]["from_max"]}' in EVENTS, failed)
    check("ID_MAX is the contract's", f'ID_MAX = {CONTRACT["limits"]["id_max"]}' in EVENTS, failed)
    for state in CONTRACT["states"]:
        check(f"state {state} is emitted or listed", json.dumps(state) in EVENTS, failed)
    for key, line in CONTRACT["replies"].items():
        check(f"the {key} line is verbatim", json.dumps(line, ensure_ascii=False).replace("\\u2019", "'") in EVENTS
              or f'"{key}" to "{line}"' in EVENTS, failed)
    check("the frame carries no number", '.put("number"' not in EVENTS and '.put("phone"' not in EVENTS, failed)
    for name, text in (("CallEvents", EVENTS), ("CallActions", ACTIONS), ("the screening service", SCREEN)):
        check(f"{name} never logs", not re.search(r"\b(Log|SessionLog)\b[.\w]*\(", text), failed)
    check("no action is CONFIRM", "ActionTier.CONFIRM" not in ACTIONS.replace("none is CONFIRM", ""), failed)
    check("a reply is one of the fixed lines", "reply !in CallEvents.REPLIES" in ACTIONS, failed)
    check("the actions are registered", "CallActions.all.forEach" in
          (KT / "automation/actions/builtin/Builtins.kt").read_text(), failed)
    host = (KT / "channel/DeviceChannelHost.kt").read_text()
    check("the sink sends only on a registered socket, untrusted",
          "if (built.isRegistered) built.sendEvent(ai.jarvis.app.calls.CallEvents.EVENT, data, untrusted = true)" in host, failed)
    check("the sink is cleared with the channel", "CallEvents.sink = null" in host, failed)
    check("the call observer is removed with the channel", "CarPhone.unobserve(it)" in host, failed)
    manifest = (ROOT / "android-app/app/src/main/AndroidManifest.xml").read_text()
    check("the screening service needs the system's bind permission",
          "JarvisCallScreeningService" in manifest and "android.permission.BIND_SCREENING_SERVICE" in manifest, failed)
    check("the screening service never rejects a contact", 'relationshipOf(contact, false)' in SCREEN, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
