"""scripts/verify/embed-rerank-bench.py (T-0513): the scoring and the verdict, offline. The run
itself needs both pairs on production, so it is evidence, not a test."""
from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("embed_rerank_bench", ROOT / "scripts/verify/embed-rerank-bench.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


def test_a_tie_counts_against_the_right_note_and_the_scores_are_exact():
    assert bench.rank_of([0.9, 0.5, 0.1], 0) == 1
    assert bench.rank_of([0.5, 0.5, 0.1], 1) == 2  # a tie is not a win
    q = bench.quality([1, 1, 2, 4], random.Random(1))
    assert q["recall_at_1"] == 0.5 and q["mrr"] == round((1 + 1 + 0.5 + 0.25) / 4, 3)
    assert q["recall_at_1_ci"][0] <= 0.5 <= q["recall_at_1_ci"][1]


def test_the_verdict_switches_only_when_quality_holds_and_both_latencies_are_under_the_limits():
    def pair(lo, rerank_p95, embed_p95):
        path = {"recall_at_1_ci": [lo, 1.0]}
        return {"embed": path, "rerank": path, "latency_ms": {
            "rerank_20": {"p95": rerank_p95}, "embed_1": {"p95": embed_p95}}}

    tei = pair(0.8, 10, 5)
    assert bench.verdict(tei, pair(0.8, 299, 149)) == "switch"
    assert "below" in bench.verdict(tei, pair(0.79, 100, 50))
    assert "rerank-20" in bench.verdict(tei, pair(0.9, 300, 50))
    assert "embed-1" in bench.verdict(tei, pair(0.9, 100, 150))


def test_the_fixture_has_enough_distinct_notes_and_the_same_candidates_for_both_pairs():
    pairs = json.loads((ROOT / "scripts/verify/embed-rerank-fixture.json").read_text())["pairs"]
    notes = [n for n, _ in pairs]
    assert len(pairs) >= 100 and len(set(notes)) == len(notes)
    a, b = random.Random(bench.SEED), random.Random(bench.SEED)
    for i in range(len(notes)):
        ids = bench.candidates(len(notes), i, a)
        assert ids == bench.candidates(len(notes), i, b) and i in ids and len(set(ids)) == bench.CANDIDATES


def test_the_recorded_run_covers_both_pairs_on_100_questions_and_says_its_verdict():
    """bench/results/embed-rerank.json is the production run the decision rests on."""
    run = json.loads((ROOT / "bench/results/embed-rerank.json").read_text())
    for pair in ("tei", "llama-swap"):
        assert set(run[pair]["latency_ms"]) == {"embed_1", "embed_16", "rerank_20"}
        assert all(run[pair][path]["n"] >= 100 for path in ("embed", "rerank"))
    assert run["verdict"] == bench.verdict(run["tei"], run["llama-swap"])
