#!/usr/bin/env python3
"""Executable spec for the personal wake verifier (WakeVerifier.kt, T-0714).

The owner, 2026-11-08: "I have to ask it in a specific way, I can't use different
intonations". The stock hey_jarvis head was trained on synthetic voices; the owner's
other ways of saying it score under the 0.5 a stranger's voice must also clear. Rather
than lower the bar for everyone, the phone learns the owner's own takes: openWakeWord's
custom verifier, a logistic regression over the 16x96 embedding window the head reads.

  * **fit** — standardise each of the 1536 features by the training set's mean and
    standard deviation (floored, so a constant feature is not a division by zero), then
    plain batch gradient descent on the class-balanced log loss with an L2 penalty, from
    zero. Class-balanced because eight takes face hundreds of room windows: unweighted,
    "never" is the cheapest answer.
  * **lift** — a frame the head scores at or over the gate is lifted to the verifier's
    probability when that is higher; under the gate it is left alone. WakeScore's
    threshold, frames and refractory then apply unchanged, so a frame the head is sure
    of fires as before and the verifier only ever adds the owner's half-heard ones.

Does not cover: how the takes are recorded or which windows are positives (the
enrolment screen picks each take's top-scoring windows), nor the live behaviour on real
audio (`scripts/verify/wake_verifier_live_check.py`, synthetic voices only).

The numbers are `tests/contracts/wake_verifier.json`; WakeVerifierTest.kt carries the
example verbatim, and this file fails when either drifts from the contract.

Run:  python3 android-app/tools/wake_verifier_test.py
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "tests" / "contracts" / "wake_verifier.json").read_text())
KOTLIN = ROOT / "android-app" / "app" / "src" / "main" / "kotlin" / "ai" / "jarvis" / "app" / "assist" / "WakeVerifier.kt"
DETECTOR = ROOT / "android-app" / "app" / "src" / "main" / "kotlin" / "ai" / "jarvis" / "app" / "assist" / "OnDeviceWakeWord.kt"
KOTLIN_TEST = ROOT / "android-app" / "app" / "src" / "test" / "kotlin" / "ai" / "jarvis" / "app" / "assist" / "WakeVerifierTest.kt"

FIT = CONTRACT["fit"]
GATE = CONTRACT["lift"]["gate"]
LEVELS = CONTRACT["lift"]["levels"]
SENSITIVITY = ROOT / "android-app" / "app" / "src" / "main" / "kotlin" / "ai" / "jarvis" / "app" / "assist" / "WakeSensitivity.kt"


def _sigmoid(x: float) -> float:
    # Split so a large |x| never overflows exp().
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


class Verifier:
    """Mirrors WakeVerifier.kt: the standardisation and the weights, nothing else."""

    def __init__(self, mean, std, weights, bias):
        self.mean, self.std, self.weights, self.bias = mean, std, weights, bias

    def probability(self, window) -> float:
        z = sum(w * (x - m) / s for w, x, m, s in zip(self.weights, window, self.mean, self.std))
        return _sigmoid(z + self.bias)

    def lift(self, score: float, window, gate: float = GATE) -> float:
        if score < gate:
            return score
        p = self.probability(window)
        return max(score, p) if math.isfinite(p) else score


def fit(positives, negatives, l2=FIT["l2"], epochs=FIT["epochs"], rate=FIT["rate"], floor=FIT["std_floor"]) -> Verifier:
    rows = positives + negatives
    d, n = len(rows[0]), len(rows)
    mean = [sum(r[j] for r in rows) / n for j in range(d)]
    std = [max(math.sqrt(sum((r[j] - mean[j]) ** 2 for r in rows) / n), floor) for j in range(d)]
    z = [[(r[j] - mean[j]) / std[j] for j in range(d)] for r in rows]
    labels = [1.0] * len(positives) + [0.0] * len(negatives)
    # Each class carries half the loss, whatever its count.
    weight = [0.5 / len(positives)] * len(positives) + [0.5 / len(negatives)] * len(negatives)
    w, b = [0.0] * d, 0.0
    for _ in range(epochs):
        gw = [l2 * x for x in w]
        gb = 0.0
        for row, y, c in zip(z, labels, weight):
            e = (_sigmoid(sum(a * x for a, x in zip(w, row)) + b) - y) * c
            for j in range(d):
                gw[j] += e * row[j]
            gb += e
        w = [a - rate * g for a, g in zip(w, gw)]
        b -= rate * gb
    return Verifier(mean, std, w, b)


def test_the_contract_example_fits_to_its_expected_probabilities():
    ex = CONTRACT["example"]
    v = fit(ex["positives"], ex["negatives"])
    got = [round(v.probability(p), 4) for p in ex["probes"]]
    assert all(abs(a - b) < 1e-3 for a, b in zip(got, ex["expect"])), (got, ex["expect"])
    # The probes are a new take and a room window: one each side of a half.
    assert got[0] > 0.5 > got[1], got


def test_lift_only_raises_a_half_heard_frame():
    ex = CONTRACT["example"]
    v = fit(ex["positives"], ex["negatives"])
    take, room = ex["probes"]
    assert v.lift(GATE, take) > 0.5  # half-heard and the owner's: lifted
    assert v.lift(GATE - 0.01, take) == GATE - 0.01  # under the gate: the head's own word
    assert v.lift(0.9, room) == 0.9  # never lowered: a frame the head is sure of still fires
    assert v.lift(GATE, room) == GATE  # a room window is not lifted
    # A level's own gate: a stricter level does not lift what an eager one would.
    assert v.lift(0.25, take, LEVELS["STRICT"]) == 0.25
    assert v.lift(0.25, take, LEVELS["NORMAL"]) > 0.5


def test_a_constant_feature_is_not_a_division_by_zero():
    v = fit([[1.0, 2.0], [1.0, 2.2]], [[1.0, -2.0], [1.0, -2.1]])
    assert v.std[0] == FIT["std_floor"]
    assert v.probability([1.0, 2.1]) > 0.5 > v.probability([1.0, -2.05])


def test_the_kotlin_carries_the_contract():
    src, test = KOTLIN.read_text(), KOTLIN_TEST.read_text()
    for name, key in (("L2", "l2"), ("EPOCHS", "epochs"), ("RATE", "rate"), ("STD_FLOOR", "std_floor")):
        m = re.search(rf"const val {name} = ([0-9.e-]+)f?", src)
        assert m and float(m.group(1)) == FIT[key], (name, m and m.group(1), FIT[key])
    m = re.search(r"const val GATE = ([0-9.]+)f", src)
    assert m and float(m.group(1)) == GATE, ("GATE", m and m.group(1))
    levels = dict(re.findall(r"^\s*(STRICT|NORMAL|EAGER)\(.*?,\s*([0-9.]+f|WakeVerifier\.GATE)\),$", SENSITIVITY.read_text(), re.M))
    got = {k: GATE if v == "WakeVerifier.GATE" else float(v.rstrip("f")) for k, v in levels.items()}
    assert got == LEVELS, ("WakeSensitivity.verifierGate", got, LEVELS)
    # The gates rise with strictness, and none is under the gate a take must clear.
    assert LEVELS["EAGER"] <= LEVELS["NORMAL"] <= LEVELS["STRICT"] and min(LEVELS.values()) >= GATE
    for value in CONTRACT["example"]["expect"]:
        assert f"{value}" in test, f"WakeVerifierTest.kt lacks the contract's expected {value}"


def test_the_phone_feeds_the_melspectrogram_openwakewords_overlap():
    # 1280 new samples and the 480 before them: 8 mel frames per 80 ms step, as the
    # heads were trained on. Without the 480 a step makes 5 and the name arrives gapped.
    src = DETECTOR.read_text()
    assert re.search(r"const val MEL_OVERLAP = 480\b", src), "OnDeviceWakeWord.MEL_OVERLAP is not openWakeWord's 480"
    assert "runMels(tail + chunk)" in src or ("val input = tail + chunk" in src and "runMels(input)" in src)


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
