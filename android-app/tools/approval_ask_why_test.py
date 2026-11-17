#!/usr/bin/env python3
"""Executable spec: "Ask why" on a Hermes approval says what the contract says (T-1054).

`tests/contracts/approval-event.json` `ask_why` is the one table: the sentence the model is
asked to answer, the template of the line, and the cap on the headline. The console's
`approval.test.ts` holds `approval.ts` to it; this holds the phone's `assist/hermes/AskWhy.kt`, and that
the approval screen's button, the bridge's deny-then-ask and the conversation's sender are
wired the way the design says. Read the Kotlin as text: the JVM is not here.

Run:  python3 android-app/tools/approval_ask_why_test.py
      python3 -m pytest android-app/tools/approval_ask_why_test.py -q
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KT = ROOT / "app/src/main/kotlin/ai/jarvis/app"
CONTRACT = ROOT.parent / "tests/contracts/approval-event.json"


def spec() -> dict:
    return json.loads(CONTRACT.read_text())["ask_why"]


def test_approval_ask_why_sentence_and_cap_are_the_contracts() -> None:
    kt = (KT / "assist/hermes/AskWhy.kt").read_text()
    s = spec()
    assert f'const val SENTENCE = "{s["sentence"]}"' in kt
    assert f"const val WHAT_CAP = {s['what_cap']}" in kt


def test_approval_ask_why_template_is_the_contracts() -> None:
    kt = (KT / "assist/hermes/AskWhy.kt").read_text()
    # The Kotlin writes the template with ${...} holes; turn them back into the contract's.
    line = re.search(r'fun line\(what: String\): String =\s*"(.+)"', kt)
    assert line, "AskWhy.line not found"
    shape = line.group(1).replace("${what.trim().take(WHAT_CAP)}", "{what}").replace("$SENTENCE", "{why}")
    assert shape == spec()["template"]


def test_approval_ask_why_console_constants_are_the_contracts() -> None:
    ts = (ROOT.parent / "jarvis-web/src/lib/ds/shell/approval.ts").read_text()
    s = spec()
    assert f"export const ASK_WHY = '{s['sentence']}'" in ts
    assert f"ASK_WHY_WHAT_CAP = {s['what_cap']}" in ts


def test_approval_ask_why_is_offered_only_with_a_hermes_way_to_ask() -> None:
    bridge = (KT / "ui/ApprovalBridge.kt").read_text()
    # Only raiseHermesRequest takes a `why`; the device and phone-confirm paths pass none.
    assert "why: (() -> Unit)? = null" in bridge
    assert "askWhy = why != null" in bridge
    act = (KT / "ApprovalActivity.kt").read_text()
    assert "askWhyOffered = choices != null &&" in act
    assert 'ActionButton("Ask why"' in act
    # The countdown's and Back's denies go through answer(), never askWhy().
    body = act.split("private fun answer(approved", 1)[1].split("/** Deny, then ask", 1)[0]
    assert "deliverTimeout" in body and "deliverAskWhy" not in body


def test_approval_ask_why_sender_is_dropped_with_the_link() -> None:
    conv = (KT / "assist/hermes/HermesConversation.kt").read_text()
    assert conv.count("askWhy.drop()") >= 3  # stop, error, disconnect
    assert "askWhy.turnEnded(client?.sessionId, ::sendAskWhy)" in conv
    assert "AskWhy.line(" in conv
    q = (KT / "assist/hermes/AskWhyQueue.kt").read_text()
    assert "const val HOLD_MS = 60_000L" in q


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_approval") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
