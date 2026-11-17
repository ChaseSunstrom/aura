#!/usr/bin/env python3
"""Executable spec: the owner's texts mirrored to their desktops (T-0615).

`messages/MessageMirror.kt` is the rule; `tests/contracts/message-mirror.json` is its table, which
MessageMirrorTest and ReplyActionTest run too. This mirror runs the conversation-id and emergency
cases, and checks the Kotlin keeps the words and limits verbatim, never logs a body, never replies
without the CONFIRM tier, and is wired where the contract says.

Run:  python3 android-app/tools/message_mirror_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KT = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app"
MIRROR = KT / "messages/MessageMirror.kt"
REPLY = KT / "messages/ReplyAction.kt"
CONTRACT = json.loads((ROOT / "tests/contracts/message-mirror.json").read_text())
LIMITS = CONTRACT["limits"]


def conversation(case: dict) -> str:
    if case["source"] == "app":
        out = f"app:{case['package'].strip().lower()}:{case['from'].strip().lower()}"
    else:
        a = case["address"].strip()
        numberish = bool(a) and all(c.isdigit() or c in "+ ()-." for c in a)
        out = "sms:" + ("".join(c for c in a if c.isdigit() or c == "+") if numberish else a.lower())
    return out[: LIMITS["conversation_max"]]


def emergency(urgent: bool, text: str) -> bool:
    return urgent and any(w in text.lower() for w in CONTRACT["emergency_words"])


def check(name: str, ok: bool, failed: list[str]) -> None:
    print(("PASS" if ok else "FAIL") + f"  {name}")
    if not ok:
        failed.append(name)


def main() -> int:
    failed: list[str] = []
    for c in CONTRACT["conversation"]:
        check(c["name"], conversation(c) == c["conversation"], failed)
    for c in CONTRACT["emergency_cases"]:
        check(c["name"], emergency(c["urgent"], c["text"]) == c["emergency"], failed)

    src = MIRROR.read_text()
    reply = REPLY.read_text()
    check("the event name is the contract's", f'EVENT = "{CONTRACT["event"]}"' in src, failed)
    check("the emergency words are the contract's",
          all(json.dumps(w) in src for w in CONTRACT["emergency_words"]), failed)
    for const, key in (("TEXT_MAX", "text_max"), ("FROM_MAX", "from_max"), ("CONVERSATION_MAX", "conversation_max")):
        check(f"{const} is the contract's", f"{const} = {LIMITS[key]}" in src, failed)
    check("the reply cap is the contract's", f"MAX_CHARS = {LIMITS['reply_card_max']}" in reply, failed)
    check("the reply action is the contract's", f'id = "{CONTRACT["reply_action"]}"' in reply, failed)
    check("a reply is always CONFIRM", "ActionTier.CONFIRM" in reply, failed)
    check("a reply is registered", "add(ai.jarvis.app.messages.ReplyAction)" in
          (KT / "automation/actions/builtin/Builtins.kt").read_text(), failed)
    for name, text in (("MessageMirror", src), ("ReplyAction", reply)):
        check(f"{name} never logs", not re.search(r"\b(Log|SessionLog)\b[.\w]*\(", text), failed)
    host = (KT / "channel/DeviceChannelHost.kt").read_text()
    check("the sink sends only on a registered socket, untrusted",
          "if (built.isRegistered) built.sendEvent(ai.jarvis.app.messages.MessageMirror.EVENT, data, untrusted = true)" in host,
          failed)
    check("the sink is cleared with the channel", "MessageMirror.sink = null" in host, failed)
    check("the owner is asked once, before anything leaves", '"messages.mirror"' in src, failed)
    check("a new SMS is offered to the mirror",
          "MessageMirror.onSms(" in (KT / "routines/RoutineWatcher.kt").read_text(), failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
