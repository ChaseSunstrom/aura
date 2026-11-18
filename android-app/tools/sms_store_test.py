#!/usr/bin/env python3
"""Executable spec: a sent text reports where it landed in the phone's SMS store.

Reported as: *"messages Jarvis sends are still not showing up in my sms app"* (2026-10-08).
The send's answer said `stored_in_sms_app: true` -- the row is in `Telephony.Sms.Sent` -- yet
GrapheneOS Messaging did not show it. A yes/no cannot say why, so the answer now carries the
row's metadata (thread, type, status, read/seen, creator) beside the last sent row some other
app wrote itself, and the default SMS app's package: the next send shows what differs.

Structural checks over `CommsActions.kt`, as the other tools/ specs do. The privacy check is
the one that matters long-term: the answer reaches the model, so no message BODY is read.

Run:  python3 android-app/tools/sms_store_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

KOTLIN = Path(__file__).resolve().parents[1] / (
    "app/src/main/kotlin/ai/aura/app/automation/actions/builtin/CommsActions.kt"
)
COLUMNS = ("THREAD_ID", "TYPE", "STATUS", "READ", "SEEN", "CREATOR")


def _store_fn() -> str:
    src = KOTLIN.read_text()
    m = re.search(r"private suspend fun smsStore\(.*?\n}\n", src, re.S)
    assert m, "smsStore() is gone from CommsActions.kt"
    return m.group(0)


def test_the_answer_carries_the_row_and_its_neighbour() -> None:
    src = KOTLIN.read_text()
    for key in ("stored_in_sms_app", "sms_row", "messaging_row", "default_sms_app"):
        assert f'"{key}"' in src, f"send_sms no longer answers {key}"


def test_the_row_metadata_is_read() -> None:
    fn = _store_fn()
    for col in COLUMNS:
        assert f"Telephony.Sms.{col}" in fn, f"the row's {col} is not reported"


def test_no_message_body_is_read() -> None:
    assert "Sms.BODY" not in _store_fn(), "the SMS-store look reads message bodies"


def main() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
