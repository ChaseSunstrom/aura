#!/usr/bin/env python3
"""The arc reactor's state table, on the phone, held to the contract.

GLASS_HUD §10 puts one reactor on every surface with one precedence rule
between them. The report behind that section was that there were already two
orbs, then three, and nothing anywhere said they were supposed to agree — so
they did not: the notification's orb and the wake-word orb wore different
colours for the same thing.

The Kotlin is checked twice, and on purpose. `ReactorStateTest.kt` runs the
contract's truth table against ``derive`` on the JVM, which is the real proof;
this reads the same files as *text* and holds their shape to the contract, which
catches what a passing test cannot — a state added to the enum and not to the
table.`make test-android` runs this without an SDK, so a change to the
Kotlin fails here in a second rather than after a three-minute Gradle run.

Run:  python3 android-app/tools/reactor_state_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
REPO = ANDROID.parent

CONTRACT = REPO / "tests/contracts/reactor.json"
KOTLIN = ANDROID / "app/src/main/kotlin/ai/aura/app/reactor"
STATE = KOTLIN / "ReactorState.kt"
#: The phone's live core, which the car draws too. The pre-v2 rings (the AGSL shader, the
#: animator, the Canvas fallback, the frame maths and the geometry in `ReactorSpec`) are gone
#: (T-0779, T-1299); `tests/contracts/reactor.json`'s `geometry` stays for the console.
CORE = KOTLIN / "core" / "V2Core.kt"
HAPTICS = KOTLIN / "ReactorHaptics.kt"

def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_every_state_of_the_contract_is_in_the_enum():
    spec = contract()
    text = STATE.read_text()
    declared = re.findall(r"^\s{4}([A-Z_]+)\(\"([a-z]+)\",", text, re.M)
    keys = [key for _, key in declared]
    assert sorted(keys) == sorted(spec["states"]), (
        f"the enum and the contract disagree: {sorted(keys)} vs {sorted(spec['states'])}"
    )


def test_every_state_carries_the_contracts_word():
    spec = contract()
    text = STATE.read_text()
    for key, word in re.findall(r"^\s{4}[A-Z_]+\(\"([a-z]+)\", \"([^\"]+)\"", text, re.M):
        expected = spec["states"][key]["word"]
        assert word == expected, f"{key}: the phone says {word!r}, the contract says {expected!r}"


def test_every_state_takes_its_colour_from_a_generated_token():
    # C is an alias of a base colour (§10). A hex here would be a twelfth
    # palette and the token lint would be right to fail it.
    text = STATE.read_text()
    for key, colour in re.findall(r"^\s{4}[A-Z_]+\(\"([a-z]+)\", \"[^\"]+\", ([^)]+)\)", text, re.M):
        assert colour.startswith("JarvisTokens.Color.REACTOR_"), f"{key} does not read a reactor token: {colour}"
        assert colour.upper().endswith(key.upper()), f"{key} reads the wrong token: {colour}"


def test_the_precedence_list_is_the_contracts():
    spec = contract()
    text = STATE.read_text()
    m = re.search(r"val PRECEDENCE: List<ReactorState> = listOf\(\s*(.*?)\s*,?\s*\)", text, re.S)
    assert m, "PRECEDENCE is not declared as a list"
    names = [n.strip() for n in m.group(1).replace("\n", " ").split(",") if n.strip()]
    keys = [n.lower() for n in names]
    assert keys == spec["precedence"], f"precedence drifted: {keys} vs {spec['precedence']}"
    # The overlay is not in the list: it cannot win, it can only be worn.
    assert "goal" not in keys, "GOAL is in the precedence list; it is an overlay"


def test_the_derivation_has_one_branch_per_rule_in_the_contracts_order():
    spec = contract()
    text = STATE.read_text()
    body = text[text.index("fun derive("):]
    states = re.findall(r"->\s*ReactorState\.([A-Z_]+)", body)
    expected = [rule["state"].upper() for rule in spec["derivation"]["rules"]]
    assert states == expected, f"the branches are {states}, the contract's rules are {expected}"


def test_every_input_the_contract_names_is_a_field():
    spec = contract()
    text = STATE.read_text()
    block = text[text.index("data class ReactorInputs("):]
    block = block[: block.index("\n)")]
    fields = set(re.findall(r"^\s*val (\w+):", block, re.M))
    named = set(spec["derivation"]["inputs"])
    assert fields == named, f"inputs differ: only in Kotlin {fields - named}, only in the contract {named - fields}"


def test_the_reactor_pauses_when_nothing_can_see_it():
    # §8.3 rule 2. A reactor animating behind a locked screen is a battery bug
    # that no test would otherwise notice.
    text = CORE.read_text()
    assert "Lifecycle.State.RESUMED" in text, "the clock does not stop when the screen does"
    assert "reduced" in text, "reduced motion is not honoured"


def test_only_two_states_are_felt():
    text = HAPTICS.read_text()
    felt = set(re.findall(r"ReactorState\.([A-Z_]+) ->", text))
    assert felt == {"ATTENTION", "ALERT", "KILL"}, f"felt states are {felt}"


def test_the_contract_reaches_every_state_it_declares():
    spec = contract()
    reached = {case["state"] for case in spec["derivation"]["cases"]}
    missing = set(spec["precedence"]) - reached
    assert not missing, f"no truth-table case produces {sorted(missing)}"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
