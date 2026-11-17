"""``python -m bench`` — run, validate, and report the JCB (the v2 plan §2).

Subcommands:

* ``run`` — run tasks against a live stack, write ``bench/results/<ISO>.{json,md}``, and
  (with ``--gate``) fail non-zero on a regression beyond the point threshold.
* ``validate`` — load every task and report counts per category (no stack needed).
* ``report`` — re-render the Markdown for an existing results file, diff it against a
  previous one, and apply the gate.

Endpoints and keys come from the ``BENCH_*`` environment (see :class:`bench.runner.BenchConfig`);
``--base``/``--key`` override the API server, ``--ws-base``/``--ws-key`` the adapter.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from bench import CATEGORIES
from bench.report import (
    DEFAULT_GATE_POINTS,
    build_results_doc,
    gate_check,
    load_results,
    render_markdown,
    write_results,
)
from bench.runner import BenchConfig, TaskRecord, filter_tasks, probe_unconfigured_plugins, run_tasks
from bench.schema import TaskError, counts_by_category, load_all

_DEFAULT_TASKS_ROOT = Path(__file__).parent / "tasks"
_DEFAULT_RESULTS_DIR = Path(__file__).parent / "results"
_DEFAULT_BASELINE = _DEFAULT_RESULTS_DIR / "baseline.json"


def _config_from_args(args: argparse.Namespace) -> BenchConfig:
    config = BenchConfig.from_env()
    if getattr(args, "base", None):
        config.api_base = args.base
    if getattr(args, "key", None):
        config.api_key = args.key
    if getattr(args, "ws_base", None):
        config.ws_base = args.ws_base
    if getattr(args, "ws_key", None):
        config.ws_key = args.ws_key
    if getattr(args, "model", None):
        config.model = args.model
    if getattr(args, "fixture_base", None):
        config.fixture_base = args.fixture_base
    return config


def _cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.tasks_root)
    try:
        tasks = load_all(root)
    except TaskError as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    counts = counts_by_category(tasks)
    print(f"Loaded {len(tasks)} tasks from {root}")
    for cat in CATEGORIES:
        marker = "" if counts[cat] else "  <-- empty"
        print(f"  {cat:22s} {counts[cat]:3d}{marker}")
    fast = sum(1 for t in tasks if t.fast)
    print(f"fast subset: {fast} tasks")
    if len(tasks) < 60:
        print(f"NOTE: {len(tasks)} tasks (< 60 required by V2_PLAN §2.4)", file=sys.stderr)
    return 0


def _progress(record: TaskRecord) -> None:
    mark = {"pass": "PASS", "fail": "FAIL", "error": "ERR ", "skip": "SKIP"}.get(record.status, "?")
    judge = " [judge]" if record.judge else ""
    print(
        f"  [{mark}] {record.id:28s} {record.wall_time_s:6.1f}s "
        f"{record.tool_calls:2d} tools{judge} — {record.detail[:80]}",
        flush=True,
    )


def _start(task) -> None:
    print(f"  [RUN ] {task.id:28s} budget {task.timeout_s}s", flush=True)


def _cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.tasks_root)
    try:
        tasks = load_all(root)
    except TaskError as exc:
        print(f"could not load tasks: {exc}", file=sys.stderr)
        return 1
    tasks = filter_tasks(
        tasks,
        categories=args.category,
        subset_fast=(args.subset == "fast"),
        tags=args.tag,
        ids=args.id,
    )
    if not tasks:
        print("no tasks matched the selectors", file=sys.stderr)
        return 1

    config = _config_from_args(args)
    # A stack that is not on loopback lives in a container, and with no exec prefix every
    # fixture and plugin_state checker silently runs on this box instead — three runs were
    # lost that way (2026-09-03, 2026-09-06) with failures that looked like the agent's.
    if not config.exec_prefix and "127.0.0.1" not in config.api_base and "localhost" not in config.api_base:
        print(f"BENCH_EXEC is empty but the stack is at {config.api_base}: fixtures and checkers "
              "would run on this host. Set BENCH_EXEC='docker exec jarvis' (or BENCH_EXEC=host "
              "to mean it).", file=sys.stderr)
        return 2
    if config.exec_prefix == "host":
        config.exec_prefix = ""
    config.unconfigured_plugins = probe_unconfigured_plugins(config.exec_prefix)
    if config.unconfigured_plugins:
        print("not configured on the stack (their tasks are skipped): " + ", ".join(
            f"{name} ({var})" for name, var in config.unconfigured_plugins.items()))
    # Two dozen tasks send the agent to the fixture server, a separate process nothing
    # starts for you. With it down the browser category went 0 of 6 on 2026-09-06 with
    # verdicts that read as the agent's ("Chrome exited early", a 300 s hunt for the port).
    if any(config.agent_fixture_base in json.dumps(t.raw) or "fixture" in json.dumps(t.raw)
           for t in tasks):
        probe = (config.fixture_base or "http://127.0.0.1:8899").rstrip("/") + "/health"
        try:
            urllib.request.urlopen(probe, timeout=3).read()
        except OSError as exc:
            print(f"the JCB fixture server does not answer at {probe} ({exc}); start it with "
                  "`python3 bench/fixtures/server.py --port 8899 --state <dir>` and set "
                  "BENCH_FIXTURE_BASE to where the harness reaches it.", file=sys.stderr)
            return 2
    journal = Path(args.out) / "journal.jsonl"
    print(f"Running {len(tasks)} tasks against {config.api_base} (ws {config.ws_base})"
          f"{' — resuming from ' + str(journal) if args.resume else ''}")
    records = run_tasks(
        tasks, config, progress=_progress, on_start=_start,
        journal=journal, resume=args.resume,
    )

    json_path, md_path = write_results(records, args.out, config)
    print(f"\nwrote {json_path}\nwrote {md_path}\n")

    doc = load_results(json_path)
    print(render_markdown(doc))

    if args.gate is not None:
        previous_path = Path(args.previous) if args.previous else _DEFAULT_BASELINE
        previous = load_results(previous_path) if previous_path.is_file() else None
        ok, message = gate_check(doc, previous, args.gate)
        print(f"GATE ({args.gate} pts vs {previous_path.name}): {'OK' if ok else 'FAIL'} — {message}")
        return 0 if ok else 2
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    doc = load_results(args.results)
    # Accept either a full results doc or a bare list of records (re-render from rows).
    if "summary" not in doc and isinstance(doc, dict) and "records" in doc:
        records = [TaskRecord(**r) for r in doc["records"]]
        doc = build_results_doc(records)
    previous = load_results(args.previous) if args.previous and Path(args.previous).is_file() else None
    print(render_markdown(doc, previous, args.gate if args.gate is not None else DEFAULT_GATE_POINTS))
    if args.gate is not None:
        ok, message = gate_check(doc, previous, args.gate)
        print(f"GATE ({args.gate} pts): {'OK' if ok else 'FAIL'} — {message}")
        return 0 if ok else 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench", description="JARVIS Capability Benchmark",
        epilog="also: --replay-sessions N --models a,b --store state.db (bench/replay.py)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run tasks against a live stack")
    run.add_argument("--tasks-root", default=str(_DEFAULT_TASKS_ROOT))
    run.add_argument("--category", action="append", choices=list(CATEGORIES))
    run.add_argument("--subset", choices=["fast"], default=None)
    run.add_argument("--tag", action="append")
    run.add_argument("--id", action="append", help="run only these task ids")
    run.add_argument("--out", default=str(_DEFAULT_RESULTS_DIR))
    run.add_argument("--resume", action="store_true",
                     help="skip tasks already in <out>/journal.jsonl from an interrupted run")
    run.add_argument("--base", help="API server base URL (overrides BENCH_API_BASE)")
    run.add_argument("--key", help="API server key (overrides BENCH_API_KEY)")
    run.add_argument("--ws-base", dest="ws_base", help="adapter base URL (overrides BENCH_WS_BASE)")
    run.add_argument("--ws-key", dest="ws_key", help="adapter key (overrides BENCH_WS_KEY)")
    run.add_argument("--model", help="model name (overrides BENCH_MODEL)")
    run.add_argument("--fixture-base", dest="fixture_base",
                     help="fixture server base URL for setup resets (overrides BENCH_FIXTURE_BASE)")
    run.add_argument("--gate", type=float, nargs="?", const=DEFAULT_GATE_POINTS, default=None,
                     help="fail if overall/any category regresses more than N points vs baseline")
    run.add_argument("--previous", help="results file to gate against (default results/baseline.json)")
    run.set_defaults(func=_cmd_run)

    validate = sub.add_parser("validate", help="load every task and report counts")
    validate.add_argument("--tasks-root", default=str(_DEFAULT_TASKS_ROOT))
    validate.set_defaults(func=_cmd_validate)

    report = sub.add_parser("report", help="render a results file and diff against a previous one")
    report.add_argument("results")
    report.add_argument("--previous")
    report.add_argument("--gate", type=float, nargs="?", const=DEFAULT_GATE_POINTS, default=None)
    report.set_defaults(func=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # `python -m bench --replay-sessions N --models a,b`: a flag, not a subcommand (T-0588).
    if any(a == "--replay-sessions" or a.startswith("--replay-sessions=") for a in argv):
        from bench import replay

        return replay.main(argv)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
