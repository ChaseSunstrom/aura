"""Report building, percentiles, the diff, the > 2-point gate, and file writing."""

from __future__ import annotations

from bench.report import (
    build_results_doc,
    diff_summaries,
    gate_check,
    load_results,
    render_markdown,
    write_results,
)
from bench.runner import TaskRecord


def _rec(id_, cat, status, wall=1.0, tokens=100, judge=False, swaps=0):
    return TaskRecord(
        id=id_, category=cat, status=status, wall_time_s=wall, total_tokens=tokens, judge=judge,
        swap_events=swaps,
    )


def _doc(records):
    return build_results_doc(records)


def test_summary_and_latency_tokens():
    records = [
        _rec("a", "web-research", "pass", wall=1.0, tokens=100),
        _rec("b", "web-research", "fail", wall=3.0, tokens=300),
        _rec("c", "safety", "pass", wall=2.0, tokens=200, judge=True),
        _rec("d", "voice", "skip"),
    ]
    doc = _doc(records)
    summary = doc["summary"]
    assert summary["total"] == 4
    assert summary["ran"] == 3
    assert summary["passed"] == 2
    assert summary["skipped"] == 1
    assert summary["judge_flagged"] == 1
    assert summary["overall_pass_rate"] == round(2 / 3 * 100, 1)
    assert summary["categories"]["web-research"]["pass_rate"] == 50.0
    # skips are excluded from latency/token stats
    assert doc["latency"]["overall"]["p50"] == 2.0
    assert doc["tokens"]["web-research"]["mean_total"] == 200.0


def test_swaps_counted_per_category_and_overall():
    """D21's evidence: a category that swapped models shows its count, one that did not
    shows zero — a routing table is only worth its swaps if this column stays small."""
    records = [
        _rec("a", "intelligence", "pass", swaps=1),
        _rec("b", "intelligence", "fail", swaps=1),
        _rec("c", "web-research", "pass", swaps=0),
        _rec("d", "voice", "skip", swaps=1),  # a skipped task never ran, so never swapped
    ]
    doc = _doc(records)
    assert doc["swaps"]["intelligence"] == {"total": 2, "tasks": 2, "per_task": 1.0}
    assert doc["swaps"]["web-research"] == {"total": 0, "tasks": 1, "per_task": 0.0}
    assert doc["swaps"]["overall"] == {"total": 2, "tasks": 3, "per_task": 0.667}


def test_render_markdown_shows_the_swap_column():
    md = render_markdown(_doc([
        _rec("a", "intelligence", "pass", swaps=1),
        _rec("b", "web-research", "pass"),
    ]))
    assert "| swaps |" in md
    assert "1 (1.0/task)" in md
    header = next(line for line in md.splitlines() if line.startswith("| Category |"))
    rows = [line for line in md.splitlines() if line.startswith("| intelligence |")]
    assert header.count("|") == rows[0].count("|")


def test_render_markdown_tolerates_a_results_file_without_swaps():
    """bench/results/baseline.json predates the column; rendering it must not raise."""
    doc = _doc([_rec("a", "intelligence", "pass")])
    del doc["swaps"]
    md = render_markdown(doc)
    assert "| intelligence | 1 | 1 | 100.0% | — | 1.0 | 1.0 | 100.0 | — |" in md


def test_diff_and_gate_pass_within_threshold():
    prev = _doc([_rec("a", "web-research", "pass"), _rec("b", "web-research", "pass")])
    # one of two now fails: 100% -> 50%, a 50-point drop
    cur = _doc([_rec("a", "web-research", "pass"), _rec("b", "web-research", "fail")])
    diff = diff_summaries(cur, prev)
    assert diff["overall_delta"] == -50.0
    assert diff["regressed_more_than_2_points"] is True
    ok, msg = gate_check(cur, prev, 2)
    assert not ok
    assert "regression" in msg


def test_gate_small_change_passes():
    prev = _doc([_rec(f"p{i}", "intelligence", "pass") for i in range(100)])
    cur_records = [_rec(f"p{i}", "intelligence", "pass") for i in range(99)] + [
        _rec("p99", "intelligence", "fail")
    ]
    cur = _doc(cur_records)
    # 100% -> 99%, a 1-point drop, within the 2-point gate
    ok, msg = gate_check(cur, prev, 2)
    assert ok
    assert "within" in msg


def test_gate_no_previous_passes():
    cur = _doc([_rec("a", "voice", "fail")])
    ok, msg = gate_check(cur, None, 2)
    assert ok
    assert "no previous baseline" in msg


def test_render_markdown_contains_key_rows():
    records = [_rec("a", "web-research", "pass"), _rec("b", "safety", "fail")]
    doc = _doc(records)
    md = render_markdown(doc)
    assert "Overall pass rate" in md
    assert "web-research" in md
    assert "Failures and errors" in md
    assert "**b**" in md


def test_write_results_creates_json_and_md(tmp_path):
    records = [_rec("a", "web-research", "pass")]
    json_path, md_path = write_results(records, tmp_path)
    assert json_path.is_file() and md_path.is_file()
    doc = load_results(json_path)
    assert doc["summary"]["overall_pass_rate"] == 100.0
    # secrets never persisted even when a config is passed
    assert "config" not in doc


def test_write_results_redacts_keys(tmp_path):
    class Cfg:
        api_base = "http://api"
        ws_base = "http://ws"
        api_key = "SECRET"
        ws_key = "SECRET2"
        model = "jarvis"
        exec_prefix = ""
        work_root = "/opt/data/work"
        file_root = "/opt/data"

    json_path, _ = write_results([_rec("a", "voice", "pass")], tmp_path, Cfg())
    text = json_path.read_text()
    assert "SECRET" not in text
    assert "http://host" in text  # the endpoint stays, its host masked


def test_write_results_keeps_the_hosts_lan_address_out(tmp_path):
    # A run against the deployed stack reaches it on the host's LAN address, and the
    # results are committed: the address would be in the repo (T-0731's runs).
    class Cfg:
        api_base = "http://192.0.2.10:8642"
        ws_base = "http://192.0.2.10:8643/"
        model = "jarvis"
        exec_prefix = ""
        work_root = "/opt/data/work"
        file_root = "/opt/data"

    cfg = build_results_doc([_rec("a", "voice", "pass")], Cfg())["config"]
    assert cfg["api_base"] == "http://host:8642"
    assert cfg["ws_base"] == "http://host:8643/"
    loop = type("Loop", (Cfg,), {"api_base": "http://127.0.0.1:8642", "ws_base": "http://localhost:8643"})
    assert build_results_doc([], loop())["config"]["api_base"] == "http://127.0.0.1:8642"
    assert build_results_doc([], loop())["config"]["ws_base"] == "http://localhost:8643"


def test_gate_compares_on_the_tasks_both_runs_ran():
    """A fast subset against the full baseline: rates over the shared ids only, and a
    category with one shared task cannot trip the gate by itself."""
    from bench.report import diff_summaries

    def doc(records):
        return {"summary": {"overall_pass_rate": 0.0, "categories": {}}, "records": records}

    prev = doc([
        {"id": "h1", "category": "homelab-ops", "status": "pass"},
        {"id": "h2", "category": "homelab-ops", "status": "pass"},
        {"id": "h3", "category": "homelab-ops", "status": "fail"},
        {"id": "v1", "category": "voice", "status": "pass"},
        {"id": "w1", "category": "web-research", "status": "fail"},
    ])
    cur = doc([
        {"id": "h1", "category": "homelab-ops", "status": "fail"},   # the one-task flip
        {"id": "v1", "category": "voice", "status": "pass"},
        {"id": "x9", "category": "safety", "status": "pass"},        # not in the baseline: ignored
    ])
    d = diff_summaries(cur, prev, 2.0)
    assert d["shared_tasks"] == 2
    assert d["categories"]["homelab-ops"]["shared_tasks"] == 1 and not d["categories"]["homelab-ops"]["gated"]
    assert d["overall_previous"] == 100.0 and d["overall_current"] == 50.0
    assert d["regressed_more_than_2_points"]  # the overall over shared tasks still counts
    # three shared tasks in a category gate it
    cur3 = doc([{"id": i, "category": "homelab-ops", "status": "fail"} for i in ("h1", "h2", "h3")])
    d3 = diff_summaries(cur3, prev, 2.0)
    assert d3["categories"]["homelab-ops"]["gated"] and d3["categories"]["homelab-ops"]["regressed"]
