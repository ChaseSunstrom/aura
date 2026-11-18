#!/usr/bin/env python3
"""Executable spec: the phone takes the owner's emergency contacts from the house (T-1262).

The house sends one device command, `emergency_contacts_sync`, when the owner saves the list and
when a phone connects; the phone only STORES it for the fall and crash watch (T-1209). This
mirrors the handler's rules (`EmergencyContactsSync` in EmergencyActions.kt) over the shared table
`tests/contracts/emergency-contacts-sync.json`, and pins that the Kotlin names the same action,
registers it, keeps its audit entry to a count and starts no call or message.

Run: `python3 android-app/tools/emergency_contacts_sync_test.py`
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
ROOT = ANDROID.parent
CONTRACT = json.loads((ROOT / "tests/contracts/emergency-contacts-sync.json").read_text(encoding="utf-8"))
KOTLIN = ANDROID / "app/src/main/kotlin/ai/aura/app/automation"
ACTIONS = KOTLIN / "actions/builtin/EmergencyActions.kt"
BUILTINS = KOTLIN / "actions/builtin/Builtins.kt"
AUDIT = KOTLIN / "audit/AuditLog.kt"
LIMITS = CONTRACT["limits"]

# The phone's own cleaning of a name: control, format and line/paragraph separators become a
# space, runs of whitespace one space (EmergencyAlert.clean).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤﻿  ]")


def _clean(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    return re.sub(r"\s+", " ", _CONTROL.sub(" ", text)).strip() or None


def take(params: dict) -> list[dict] | None:
    """What the phone stores for these params, or None when it refuses the whole list."""
    contacts = params.get("contacts")
    if not isinstance(contacts, list) or len(contacts) > LIMITS["contacts_max"]:
        return None
    out = []
    for c in contacts:
        if not isinstance(c, dict):
            return None
        name, number = _clean(c.get("name")), c.get("number")
        if name is None or len(name) > LIMITS["name_max"]:
            return None
        if not isinstance(number, str) or not re.fullmatch(r"\+?[0-9]{%d,15}" % LIMITS["min_digits"], number):
            return None
        out.append({"name": name, "number": number})
    return out


def main() -> int:
    failures: list[str] = []

    for case in CONTRACT["accepted"] + CONTRACT["phone_cleans"]:
        got = take(case["params"])
        if got != case["stored"]:
            failures.append(f"takes {case['params']!r}: stored {got!r}, want {case['stored']!r}")
    for case in CONTRACT["refused"] + CONTRACT["refused_by_phone_only"]:
        if take(case["params"]) is not None:
            failures.append(f"takes what the contract refuses: {case['why']}")

    kotlin = ACTIONS.read_text(encoding="utf-8")
    action = CONTRACT["action"]
    for needle, why in [
        (f'override val id = "{action}"', "the phone does not name the action the contract names"),
        ("object EmergencyContactsSync : JarvisAction", "no handler object"),
        ("JarvisConfig(ctx).emergencyContacts =", "the handler does not store the list for the watch"),
    ]:
        if needle not in kotlin:
            failures.append(f"{ACTIONS.name}: {why} (missing {needle!r})")
    handler = kotlin.split("object EmergencyContactsSync", 1)[-1]
    if "ActionTier.AUTO" not in handler:
        failures.append(f"the handler is not tier {CONTRACT['tier']} (ActionTier.AUTO)")
    # The handler's section runs to the end of the file or the next top-level object: it must
    # not reach for a dial or a text, nor log the list.
    for banned in ("SendSms", "PlaceCall", "SmsManager", "Intent(", "Log.", "println"):
        if banned in handler.split("\nobject ", 1)[0].split("\ninternal object ", 1)[0]:
            failures.append(f"the handler touches {banned!r}: it must only store the list")
    registry = (KOTLIN / "actions/ActionRegistry.kt").read_text(encoding="utf-8")
    ids = ", ".join(f'"{i}"' for i in CONTRACT["house_only_actions"])
    if f"val HOUSE_ONLY: Set<String> = setOf({ids})" not in registry:
        failures.append(f"ActionRegistry.HOUSE_ONLY is not the contract's house_only_actions ({ids})")
    if "add(EmergencyContactsSync)" not in BUILTINS.read_text(encoding="utf-8"):
        failures.append("Builtins.kt does not register EmergencyContactsSync")
    audit = AUDIT.read_text(encoding="utf-8")
    if f'"{action}"' not in audit or "contacts_count" not in audit:
        failures.append("AuditLog.kt does not keep this action's params to a count")

    if failures:
        print("\n".join(f"FAIL {f}" for f in failures))
        print(f"\n{len(failures)} failure(s)")
        return 1
    n = len(CONTRACT["accepted"]) + len(CONTRACT["phone_cleans"]) + len(CONTRACT["refused"]) + len(CONTRACT["refused_by_phone_only"])
    print(f"emergency contacts sync: {n} contract cases, handler, registration and audit redaction pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
