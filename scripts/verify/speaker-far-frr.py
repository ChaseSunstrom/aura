#!/usr/bin/env python3
"""The speaker gate's false-accept and false-reject rates, measured (S7).

The brief asks for the operating point to be tuned by measurement and the
two rates reported. This is the first measurement, and it is honest about
what it is: **synthetic voices**. The test suite's `SynthSpeaker`s (a seeded
glottal source through three formants) are the only voices this box holds
that are not somebody's biometric data, and every number here comes from
them. It measures the *gate*, not the world: a real household is the
console's `POST /api/voice/speaker/verify` with a friend in the room, and
the numbers here say what the embedder and the z-score threshold do to
voices that differ the way these do -- pitch, formants, breathiness -- and
nothing about a recording of the owner, which the brief already says the
gate does not stop.

What it does: enrol the owner from five prompted utterances the way the
console does, take the profile's own suggested threshold (what enrolment
stores), then score N fresh owner utterances (with pitch and gain moved the
way a day moves them) and N utterances from each impostor. FAR is impostor
utterances accepted over impostor utterances; FRR is owner utterances
refused over owner utterances. Both are printed at the stored threshold and
along a sweep, so the operating point the config can set
(`voice: speaker: threshold:`) has a curve to be chosen from.

    python3 scripts/verify/speaker-far-frr.py [--per-voice N] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aura-core"))
sys.path.insert(0, str(ROOT / "aura-core" / "tests"))

from aura.voice.speaker import VoiceProfile, embed  # noqa: E402
from synth_voice import IMPOSTORS, OWNER  # noqa: E402

#: The five enrolment takes the console walks through, moved the way a real
#: enrolment moves: length, pitch, gain. The same as tests/test_speaker_gate.py.
ENROL = (
    {"seconds": 2.5, "seed": 0},
    {"seconds": 2.0, "seed": 1, "f0_scale": 1.10},
    {"seconds": 3.0, "seed": 2, "f0_scale": 0.93},
    {"seconds": 2.2, "seed": 3, "gain": 0.18},
    {"seconds": 2.6, "seed": 4, "f0_scale": 1.05},
)


def owner_day(i: int) -> dict:
    """An owner utterance on another day: a little higher or lower, louder or softer."""
    return {"seconds": 1.8 + (i % 5) * 0.3, "seed": 100 + i, "f0_scale": 0.92 + (i % 7) * 0.025, "gain": 0.2 + (i % 4) * 0.06}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--per-voice", type=int, default=40, help="utterances per voice (default 40)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()
    n = max(5, args.per_voice)

    profile = VoiceProfile.enrol([embed(OWNER.utterance(**kw)) for kw in ENROL])
    threshold = profile.suggested_threshold()
    profile.threshold = threshold

    owner_scores = [profile.verify(embed(OWNER.utterance(**owner_day(i)))).score for i in range(n)]
    impostor_scores: dict[str, list[float]] = {}
    for who in IMPOSTORS:
        impostor_scores[who.name] = [profile.verify(embed(who.utterance(**owner_day(i)))).score for i in range(n)]

    def rates(at: float) -> tuple[float, float, dict[str, float]]:
        frr = sum(1 for s in owner_scores if s > at) / len(owner_scores)
        per = {name: sum(1 for s in scores if s <= at) / len(scores) for name, scores in impostor_scores.items()}
        far = sum(per.values()) / len(per) if per else 0.0
        return far, frr, per

    far, frr, per = rates(threshold)
    # Log-spaced: the scores run from under one to several hundred (a soprano
    # against a baritone's profile), and a linear sweep spends every step where
    # nothing changes. Bounded so a score of exactly zero does not break the log.
    import math

    sweep = []
    everything = owner_scores + [s for v in impostor_scores.values() for s in v]
    lo, hi = max(0.05, min(everything)), max(everything)
    steps = 40
    for k in range(steps + 1):
        at = math.exp(math.log(lo) + (math.log(hi) - math.log(lo)) * k / steps)
        f, r, _ = rates(at)
        sweep.append({"threshold": round(at, 3), "far": round(f, 4), "frr": round(r, 4)})
    # The equal-error point, the number a gate is usually quoted by.
    eer_point = min(sweep, key=lambda p: abs(p["far"] - p["frr"]))

    report = {
        "voices": "synthetic (aura-core/tests/synth_voice.py); not a measurement of real people",
        "per_voice": n,
        "enrolment_samples": len(ENROL),
        "stored_threshold": round(threshold, 3),
        "at_stored_threshold": {"far": round(far, 4), "frr": round(frr, 4), "far_per_impostor": {k: round(v, 4) for k, v in per.items()}},
        "owner_scores": {"min": round(min(owner_scores), 3), "median": round(sorted(owner_scores)[len(owner_scores) // 2], 3), "max": round(max(owner_scores), 3)},
        "impostor_scores": {k: {"min": round(min(v), 3), "median": round(sorted(v)[len(v) // 2], 3)} for k, v in impostor_scores.items()},
        "equal_error": eer_point,
        "sweep": sweep,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"speaker gate, synthetic voices, {n} utterances per voice, {len(ENROL)} enrolment samples")
        print(f"  stored threshold {threshold:.3f}: FAR {far:.1%}  FRR {frr:.1%}")
        for name, rate in per.items():
            print(f"    impostor {name:<10} accepted {rate:.1%}   (scores min {min(impostor_scores[name]):.2f} median {sorted(impostor_scores[name])[n // 2]:.2f})")
        print(f"  owner scores: min {min(owner_scores):.2f} median {sorted(owner_scores)[n // 2]:.2f} max {max(owner_scores):.2f}")
        print(f"  equal-error point: threshold {eer_point['threshold']}  FAR {eer_point['far']:.1%}  FRR {eer_point['frr']:.1%}")
        print("  sweep (threshold: FAR / FRR):")
        for p in sweep[::4]:
            print(f"    {p['threshold']:>8.3f}: {p['far']:.1%} / {p['frr']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
