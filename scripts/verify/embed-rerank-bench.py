#!/usr/bin/env python3
"""The two retrieval pairs on production, side by side: TEI (bge-small-en-v1.5 + ms-marco-MiniLM)
against llama-swap (qwen3-embedding-0.6b + qwen3-reranker-0.6b), both on the CPU (T-0513).

    python3 scripts/verify/embed-rerank-bench.py [--runs 50] [--warmups 5] [--out result.json]
    python3 scripts/verify/embed-rerank-bench.py --scales [--out bench/results/embed-rerank-scales.json]

Latency: embed one question, embed 16 notes, rerank one question over 20 notes; p50/p95 ms after
the warmups, with the 1-minute load average first (a busy CPU makes the CPU pair look slow).
Quality, on the made-up notes in embed-rerank-fixture.json: the embed path ranks every note by
cosine, as memory recall does (no floor: rank is what is compared); the rerank path orders a fixed
20-note candidate set per question, the right note and 19 drawn with a seed, as research does with
pages. recall@1 and MRR with a seeded bootstrap 95% interval over every question, and the held-out
half (odd pairs) on its own. Prints one JSON line and a verdict by T-0513's rule. Takes about
25 minutes and most of the host's CPU (the llama-swap reranker: ~5 s per 20 notes), so run it
with no CI job going; bench/results/embed-rerank.md is the last run.
--scales only embeds (about a minute): each embedder's cosine scale, the right note against the
wrong ones per question, and the 0.05 step at or below the worst right answer, which is the floor
vectors.py gives that model (T-0684; a test reads the file, so a floor and its measure cannot drift).
Does not cover research's real pages, Hermes' memory (holographic, FTS5, no embedder) or a model
server under load from another caller. The llama-swap key is LLM_API_KEY from the environment,
else from AURA_ENV_FILE (default /opt/jarvis-host/.env); it is never printed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "embed-rerank-fixture.json"
# vectors.py is stdlib-only; loaded by path so the bench gets each model's query prefix from the code
# that uses it in production, without jarvis-core's import tree.
_spec = importlib.util.spec_from_file_location(
    "vectors", HERE.parents[1] / "aura-core/aura/integrations/memory/vectors.py")
vectors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vectors)

CANDIDATES = 20
BOOT = 2000
SEED = 513


def pair_specs(key: str) -> dict[str, dict]:
    bge, qwen = "BAAI/bge-small-en-v1.5", "qwen3-embedding-0.6b"
    return {
        "tei": {"embed": "http://127.0.0.1:7997/v1/embeddings", "embed_model": bge,
                "query_prefix": vectors.prefixes_for(bge)[1],
                "rerank": "http://127.0.0.1:7998/rerank", "rerank_model": None, "key": ""},
        "llama-swap": {"embed": "http://127.0.0.1:9000/v1/embeddings", "embed_model": "qwen3-embedding-0.6b",
                       "query_prefix": vectors.prefixes_for(qwen)[1],
                       "rerank": "http://127.0.0.1:9000/v1/rerank", "rerank_model": "qwen3-reranker-0.6b",
                       "key": key},
    }


def _key_from(env_file: str) -> str:
    try:
        for line in open(env_file, encoding="utf-8"):
            if line.startswith("LLM_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _post(url: str, body: dict, key: str) -> object:
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=120) as f:
        return json.load(f)


def embed(p: dict, texts: list[str]) -> list[list[float]]:
    out = _post(p["embed"], {"model": p["embed_model"], "input": texts}, p["key"])
    return [d["embedding"] for d in sorted(out["data"], key=lambda d: d["index"])]


def rerank(p: dict, query: str, texts: list[str]) -> list[float]:
    """A score per text, in the order given. TEI answers a list with "score"; llama-server
    {"results": [...]} with "relevance_score" (the two shapes jarvis-core's rerank.py reads)."""
    if p["rerank_model"]:
        out = _post(p["rerank"], {"model": p["rerank_model"], "query": query, "documents": texts}, p["key"])
        rows, field = out["results"], "relevance_score"
    else:
        rows, field = _post(p["rerank"], {"query": query, "texts": texts}, p["key"]), "score"
    scores = [0.0] * len(texts)
    for r in rows:
        scores[r["index"]] = r[field]
    return scores


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def rank_of(scores: list[float], right: int) -> int:
    """1-based rank of the right item; a tie counts against it."""
    return 1 + sum(1 for i, s in enumerate(scores) if i != right and s >= scores[right])


def quality(ranks: list[int], rng: random.Random) -> dict:
    def stats(rs: list[int]) -> tuple[float, float]:
        return sum(r == 1 for r in rs) / len(rs), sum(1 / r for r in rs) / len(rs)

    r1, mrr = stats(ranks)
    boots = [stats([rng.choice(ranks) for _ in ranks]) for _ in range(BOOT)]
    ci = lambda xs: [round(sorted(xs)[int(0.025 * BOOT)], 3), round(sorted(xs)[int(0.975 * BOOT) - 1], 3)]  # noqa: E731
    return {"recall_at_1": round(r1, 3), "recall_at_1_ci": ci([b[0] for b in boots]),
            "mrr": round(mrr, 3), "mrr_ci": ci([b[1] for b in boots]), "n": len(ranks)}


def timed(fn, warmups: int, runs: int) -> dict:
    for _ in range(warmups):
        fn()
    ms = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        ms.append((time.perf_counter() - t0) * 1000)
    ms.sort()
    return {"p50": round(statistics.median(ms), 1), "p95": round(ms[math.ceil(0.95 * len(ms)) - 1], 1)}


def candidates(n_notes: int, right: int, rng: random.Random) -> list[int]:
    picks = rng.sample([i for i in range(n_notes) if i != right], CANDIDATES - 1) + [right]
    rng.shuffle(picks)
    return picks


def bench(p: dict, notes: list[str], questions: list[str], warmups: int, runs: int) -> dict:
    q0 = p["query_prefix"] + questions[0]
    latency = {
        "embed_1": timed(lambda: embed(p, [q0]), warmups, runs),
        "embed_16": timed(lambda: embed(p, notes[:16]), warmups, runs),
        "rerank_20": timed(lambda: rerank(p, questions[0], notes[:CANDIDATES]), warmups, runs),
    }
    note_vecs = [v for i in range(0, len(notes), 16) for v in embed(p, notes[i:i + 16])]
    q_vecs = [v for i in range(0, len(questions), 16)
              for v in embed(p, [p["query_prefix"] + q for q in questions[i:i + 16]])]
    embed_ranks = [rank_of([cosine(q, n) for n in note_vecs], i) for i, q in enumerate(q_vecs)]
    pick = random.Random(SEED)  # the same candidate sets for both pairs
    rerank_ranks = []
    for i, q in enumerate(questions):
        ids = candidates(len(notes), i, pick)
        rerank_ranks.append(rank_of(rerank(p, q, [notes[j] for j in ids]), ids.index(i)))
    out = {"latency_ms": latency}
    for name, ranks in (("embed", embed_ranks), ("rerank", rerank_ranks)):
        out[name] = quality(ranks, random.Random(SEED))
        out[name + "_held"] = quality(ranks[1::2], random.Random(SEED))
    return out


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3)


def scales(p: dict, notes: list[str], questions: list[str]) -> dict:
    """The right note's cosine against the best wrong one and against every wrong one; the floor is
    the 0.05 step at or below the worst right answer, and what that floor keeps."""
    note_vecs = [v for i in range(0, len(notes), 16) for v in embed(p, notes[i:i + 16])]
    q_vecs = [v for i in range(0, len(questions), 16)
              for v in embed(p, [p["query_prefix"] + q for q in questions[i:i + 16]])]
    right, best_wrong, every = [], [], []
    for i, q in enumerate(q_vecs):
        s = [cosine(q, n) for n in note_vecs]
        right.append(s[i])
        best_wrong.append(max(x for j, x in enumerate(s) if j != i))
        every.extend(x for j, x in enumerate(s) if j != i)
    floor = math.floor(min(right) * 20) / 20
    return {"model": p["embed_model"],
            "right": {"min": _pct(right, 0), "p5": _pct(right, 0.05), "p50": _pct(right, 0.5),
                      "kept_held": round(sum(r >= floor for r in right[1::2]) / len(right[1::2]), 3)},
            "best_wrong": {"p50": _pct(best_wrong, 0.5), "p95": _pct(best_wrong, 0.95), "max": _pct(best_wrong, 1.0)},
            "any_wrong": {"p50": _pct(every, 0.5), "p95": _pct(every, 0.95),
                          "kept": round(sum(w >= floor for w in every) / len(every), 3)},
            "floor": floor}


def verdict(tei: dict, llama: dict) -> str:
    """T-0513's rule: switch only if the llama-swap pair's recall@1 interval sits at or above
    TEI's (its lower bound is not below TEI's) on both paths, its rerank-20 p95 is under 300 ms
    and its embed-1 p95 under 150 ms."""
    why = []
    for path in ("embed", "rerank"):
        if llama[path]["recall_at_1_ci"][0] < tei[path]["recall_at_1_ci"][0]:
            why.append(f"{path} recall@1 interval below TEI's")
    if llama["latency_ms"]["rerank_20"]["p95"] >= 300:
        why.append("rerank-20 p95 >= 300 ms")
    if llama["latency_ms"]["embed_1"]["p95"] >= 150:
        why.append("embed-1 p95 >= 150 ms")
    return "switch" if not why else "keep TEI: " + "; ".join(why)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--warmups", type=int, default=5)
    ap.add_argument("--out", help="also write the JSON here")
    ap.add_argument("--scales", action="store_true", help="only each embedder's cosine scale and floor")
    a = ap.parse_args()
    pairs = json.loads(FIXTURE.read_text())["pairs"]
    notes, questions = [n for n, _ in pairs], [q for _, q in pairs]
    if len(pairs) < 100 or len(set(notes)) != len(notes):
        sys.exit("the fixture needs 100+ pairs with distinct notes")
    key = os.environ.get("LLM_API_KEY") or _key_from(os.environ.get("AURA_ENV_FILE", "/opt/jarvis-host/.env"))
    if a.scales:
        result = {name: scales(p, notes, questions) for name, p in pair_specs(key).items()}
        line = json.dumps(result, indent=1)
    else:
        result = {"load_1m": round(os.getloadavg()[0], 2), "cpus": os.cpu_count()}
        for name, p in pair_specs(key).items():
            result[name] = bench(p, notes, questions, a.warmups, a.runs)
        result["verdict"] = verdict(result["tei"], result["llama-swap"])
        line = json.dumps(result)
    print(line)
    if a.out:
        Path(a.out).write_text(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
