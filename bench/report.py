"""Results files and the regression gate (the v2 plan §2.6/§2.7, D15).

A run writes two files under ``bench/results``: ``<ISO>.json`` (per-task rows + a summary
block) and ``<ISO>.md`` (per-category pass rate, p50/p95 latency, mean tokens, model-swap
counts, judge-flagged count, and a diff against the previous run). The baseline run is
copied to ``bench/results/baseline.json`` (the lead records it) and is the reference for
the ``> 2 points`` regression gate that blocks a merge — ``bench report --gate 2``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bench import CATEGORIES
from bench.runner import TaskRecord, summarize

_LOGGER = logging.getLogger(__name__)

#: A category losing more than this many points versus the baseline blocks the merge
#: (brief Rule 1 / V2_PLAN §2.4). The overall gate uses the same threshold via ``--gate``.
DEFAULT_GATE_POINTS = 2.0


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (no numpy). ``values`` need not be sorted."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = pct / 100.0 * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 3)


def _latency_tokens_swaps(
    records: list[TaskRecord],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Per-category and overall latency (p50/p95), mean tokens and swap counts.

    ``swaps`` is the model-routing evidence D21 asks for: ``total`` is how many tasks in
    the category saw the loaded model change across the run (``TaskRecord.swap_events``),
    ``tasks`` how many ran, and ``per_task`` their ratio. A route that swaps in the hot
    path shows up here as a non-zero total against a slower p50 in the same row, which is
    the comparison "only route where the margin outweighs the swap" needs; a fully
    co-resident or single-model deployment reads 0 everywhere and costs nothing to keep.
    """
    ran = [r for r in records if r.status in {"pass", "fail", "error"}]
    latency: dict[str, Any] = {}
    tokens: dict[str, Any] = {}
    swaps: dict[str, Any] = {}
    by_cat: dict[str, list[TaskRecord]] = {c: [] for c in CATEGORIES}
    for rec in ran:
        by_cat.setdefault(rec.category, []).append(rec)
    for cat, rows in by_cat.items():
        walls = [r.wall_time_s for r in rows]
        toks = [r.total_tokens for r in rows]
        latency[cat] = {"p50": _percentile(walls, 50), "p95": _percentile(walls, 95)}
        tokens[cat] = {
            "mean_total": round(sum(toks) / len(toks), 1) if toks else 0.0,
            "sum_total": sum(toks),
        }
        swaps[cat] = _swap_block(rows)
    all_walls = [r.wall_time_s for r in ran]
    all_toks = [r.total_tokens for r in ran]
    latency["overall"] = {"p50": _percentile(all_walls, 50), "p95": _percentile(all_walls, 95)}
    tokens["overall"] = {
        "mean_total": round(sum(all_toks) / len(all_toks), 1) if all_toks else 0.0,
        "sum_total": sum(all_toks),
    }
    swaps["overall"] = _swap_block(ran)
    return latency, tokens, swaps


def _swap_block(rows: list[TaskRecord]) -> dict[str, Any]:
    total = sum(r.swap_events for r in rows)
    return {
        "total": total,
        "tasks": len(rows),
        "per_task": round(total / len(rows), 3) if rows else 0.0,
    }


def _public(url: str) -> str:
    """The endpoint as it may be committed: loopback as it is, any other host as ``host``.
    A run against the deployed stack reaches it on the host's LAN address (T-0731's runs);
    the scheme and port still say which endpoint it was. Does not touch any other field."""
    parts = urlsplit(url)
    if not parts.hostname or parts.hostname in {"127.0.0.1", "localhost", "::1"}:
        return url
    netloc = "host" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


def build_results_doc(records: list[TaskRecord], config: Any = None) -> dict[str, Any]:
    """The JSON document a run writes and ``report`` reads back."""
    latency, tokens, swaps = _latency_tokens_swaps(records)
    doc: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(records),
        "latency": latency,
        "tokens": tokens,
        "swaps": swaps,
        "records": [r.to_dict() for r in records],
    }
    if config is not None:
        # Never persist the bearer keys — only the endpoints and exec mode, so a results
        # file can live in the repo alongside baseline.json without leaking a secret.
        doc["config"] = {
            "api_base": _public(config.api_base),
            "ws_base": _public(config.ws_base),
            "model": config.model,
            "exec_prefix": config.exec_prefix,
            "work_root": config.work_root,
            "file_root": config.file_root,
        }
    return doc


def write_results(records: list[TaskRecord], out_dir: str | Path, config: Any = None) -> tuple[Path, Path]:
    """Write ``<ISO>.json`` and ``<ISO>.md`` under ``out_dir``; return both paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doc = build_results_doc(records, config)
    stamp = doc["timestamp"].replace(":", "-").replace("+00:00", "Z")
    json_path = out / f"{stamp}.json"
    json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = out / f"{stamp}.md"
    md_path.write_text(render_markdown(doc), encoding="utf-8")
    return json_path, md_path


#: A category with fewer shared tasks than this cannot trip the gate on its own: one task
#: flipping is a 100-point swing, and a fast subset carries one or two tasks per category.
MIN_CATEGORY_TASKS_FOR_GATE = 3


def _ran(records: list[dict[str, Any]], ids: set[str]) -> list[dict[str, Any]]:
    return [r for r in records if r["id"] in ids and r["status"] in ("pass", "fail", "error")]


def _pass_rate(records: list[dict[str, Any]], ids: set[str]) -> float | None:
    ran = _ran(records, ids)
    if not ran:
        return None
    return round(sum(1 for r in ran if r["status"] == "pass") / len(ran) * 100.0, 1)


def diff_summaries(current: dict[str, Any], previous: dict[str, Any] | None, threshold: float = DEFAULT_GATE_POINTS) -> dict[str, Any]:
    """Pass-rate deltas of ``current`` vs ``previous`` **over the tasks both runs ran**.

    A merge-request run is the 16-task fast subset while the reference is the 66-task
    baseline; comparing category percentages across the two put a one-task category at
    ±100 points (the first post-Phase-2 run "regressed" homelab-ops by 25 and voice by 60
    on one task each). Both rates are therefore recomputed on the shared task ids, and a
    category trips the gate only with at least MIN_CATEGORY_TASKS_FOR_GATE shared tasks;
    the overall rate over the shared tasks always counts. ``regressed_more_than_2_points``
    is the flag the CI gate keys on (V2_PLAN §2.4/§2.7, D30).
    """
    if not previous:
        return {"overall_delta": None, "categories": {}, "regressed_more_than_2_points": False}
    cur_recs = current.get("records") or []
    prev_recs = previous.get("records") or []
    shared = {r["id"] for r in cur_recs} & {r["id"] for r in prev_recs}
    cur_rate = _pass_rate(cur_recs, shared)
    prev_rate = _pass_rate(prev_recs, shared)
    overall_delta = round(cur_rate - prev_rate, 1) if cur_rate is not None and prev_rate is not None else None
    regressed = overall_delta is not None and overall_delta < -threshold
    cat_deltas: dict[str, Any] = {}
    for cat in CATEGORIES:
        ids = {r["id"] for r in cur_recs if r["category"] == cat} & shared
        if not ids:
            continue
        c = _pass_rate(cur_recs, ids)
        p = _pass_rate(prev_recs, ids)
        if c is None or p is None:
            continue
        delta = round(c - p, 1)
        counts = len(ids) >= MIN_CATEGORY_TASKS_FOR_GATE
        cat_regressed = counts and delta < -threshold
        cat_deltas[cat] = {
            "current": c,
            "previous": p,
            "delta": delta,
            "shared_tasks": len(ids),
            "gated": counts,
            "regressed": cat_regressed,
        }
        regressed = regressed or cat_regressed
    return {
        "overall_delta": overall_delta,
        "overall_current": cur_rate,
        "overall_previous": prev_rate,
        "shared_tasks": len(shared),
        "categories": cat_deltas,
        "regressed_more_than_2_points": regressed,
        "threshold": threshold,
    }


def gate_check(current: dict[str, Any], previous: dict[str, Any] | None, threshold: float = DEFAULT_GATE_POINTS) -> tuple[bool, str]:
    """(ok, message). ok is False only when a real regression beyond ``threshold`` exists.
    A missing previous baseline passes with a note — the first run has nothing to beat."""
    if not previous:
        return True, "no previous baseline to compare against; gate passes by default"
    diff = diff_summaries(current, previous, threshold)
    if not diff["regressed_more_than_2_points"]:
        return True, (
            f"overall {diff['overall_previous']}→{diff['overall_current']} "
            f"(Δ{diff['overall_delta']:+}), within {threshold} points"
        )
    regressions = [f"overall Δ{diff['overall_delta']:+}"] if diff["overall_delta"] < -threshold else []
    for cat, d in diff["categories"].items():
        if d["regressed"]:
            regressions.append(f"{cat} Δ{d['delta']:+}")
    return False, "regression beyond gate: " + "; ".join(regressions)


def _fmt_swaps(block: dict[str, Any] | None) -> str:
    """``total`` swaps, with the per-task rate when the category swapped at all."""
    if not block:
        return "—"
    total = block.get("total", 0)
    if not total:
        return "0"
    return f"{total} ({block.get('per_task', 0.0)}/task)"


def _fmt_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    if delta == 0:
        return "±0"
    return f"{delta:+}"


def render_markdown(doc: dict[str, Any], previous: dict[str, Any] | None = None, threshold: float = DEFAULT_GATE_POINTS) -> str:
    """Render the human report: overall, per-category table, latency/tokens, judge count,
    and (when a previous run is given) the diff and gate verdict."""
    summary = doc["summary"]
    latency = doc.get("latency", {})
    tokens = doc.get("tokens", {})
    # Results files written before the swap column exists carry no "swaps" block; the
    # report must still render them (bench/results/baseline.json is one), so a missing
    # block reads as an em dash rather than raising a KeyError mid-render.
    swaps = doc.get("swaps", {})
    diff = diff_summaries(doc, previous, threshold) if previous else None

    lines: list[str] = []
    lines.append(f"# JCB results — {doc['timestamp']}")
    lines.append("")
    lines.append(
        f"**Overall pass rate: {summary['overall_pass_rate']}%** "
        f"({summary['passed']}/{summary['ran']} ran, {summary['skipped']} skipped, "
        f"{summary['judge_flagged']} judge-flagged)."
    )
    if diff is not None:
        verdict = "REGRESSED" if diff["regressed_more_than_2_points"] else "within gate"
        lines.append(
            f"Versus previous: overall {diff['overall_previous']}% → {diff['overall_current']}% "
            f"({_fmt_delta(diff['overall_delta'])} points) — **{verdict}** at {threshold} points."
        )
    lines.append("")
    lines.append("## Per category")
    lines.append("")
    header = (
        "| Category | Pass | Ran | Pass rate | Δ vs prev | p50 s | p95 s | mean tokens | swaps |"
    )
    lines.append(header)
    lines.append("|" + "---|" * 9)
    for cat in CATEGORIES:
        c = summary["categories"].get(cat, {})
        lat = latency.get(cat, {})
        tok = tokens.get(cat, {})
        delta = diff["categories"].get(cat, {}).get("delta") if diff else None
        lines.append(
            f"| {cat} | {c.get('pass', 0)} | {c.get('ran', 0)} | {c.get('pass_rate', 0.0)}% "
            f"| {_fmt_delta(delta)} | {lat.get('p50', 0.0)} | {lat.get('p95', 0.0)} "
            f"| {tok.get('mean_total', 0.0)} | {_fmt_swaps(swaps.get(cat))} |"
        )
    ov_lat = latency.get("overall", {})
    ov_tok = tokens.get("overall", {})
    lines.append(
        f"| **overall** | {summary['passed']} | {summary['ran']} | {summary['overall_pass_rate']}% "
        f"| {_fmt_delta(diff['overall_delta']) if diff else '—'} | {ov_lat.get('p50', 0.0)} "
        f"| {ov_lat.get('p95', 0.0)} | {ov_tok.get('mean_total', 0.0)} "
        f"| {_fmt_swaps(swaps.get('overall'))} |"
    )
    lines.append("")

    failures = [r for r in doc["records"] if r.get("status") in {"fail", "error"}]
    if failures:
        lines.append("## Failures and errors")
        lines.append("")
        for r in failures:
            flag = " [judge]" if r.get("judge") else ""
            lines.append(f"- **{r['id']}** ({r['status']}){flag}: {r.get('detail', '')[:300]}")
        lines.append("")

    skipped = [r for r in doc["records"] if r.get("status") == "skip"]
    if skipped:
        lines.append("## Skipped")
        lines.append("")
        for r in skipped:
            lines.append(f"- {r['id']}: {r.get('detail', '')}")
        lines.append("")

    return "\n".join(lines) + "\n"


def load_results(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
