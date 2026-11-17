"""``python -m bench.canary <dir>`` — the nightly canary's verdict on its two newest runs.

``scripts/bench-canary.sh`` runs the fast subset into ``<dir>/<date>/`` each night; this
compares tonight's results file with the previous night's, writes the one-line verdict to
``<dir>/latest.md`` and exits 1 when it is a regression, so the shell knows to tell Sir.

Why a count of tasks and not :func:`bench.report.gate_check`'s points: the subset carries
one or two tasks per category, so a single flip moves a category 50–100 points and the
whole run ~6. Nightly model variance flips one task routinely (two 2026-09-10 rollbacks were
exactly that); two lost passes on the same tasks is what is worth a message.

It does NOT say why a task failed (read the run's ``.md``), and it compares only adjacent
nights: a slow slide of one task a night never trips it — ``bench:nightly`` against
``baseline.json`` is the long view.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from bench.report import load_results

#: Net passes lost on the shared tasks that count as a regression.
DROP = 2


def compare(current: dict[str, Any], previous: dict[str, Any] | None, drop: int = DROP) -> dict[str, Any]:
    """Passes over the tasks both runs ran (pass/fail/error); a skip is not a run."""
    if previous is None:
        n = sum(1 for r in current["records"] if r["status"] == "pass")
        return {"regressed": False, "shared": 0, "lost": [], "gained": [],
                "line": f"JCB canary: first canary run, {n} passed; nothing to compare yet."}
    ran = ("pass", "fail", "error")
    now = {r["id"]: r["status"] for r in current["records"] if r["status"] in ran}
    then = {r["id"]: r["status"] for r in previous["records"] if r["status"] in ran}
    shared = sorted(now.keys() & then.keys())
    lost = [i for i in shared if then[i] == "pass" and now[i] != "pass"]
    gained = [i for i in shared if now[i] == "pass" and then[i] != "pass"]
    before = sum(1 for i in shared if then[i] == "pass")
    after = sum(1 for i in shared if now[i] == "pass")
    regressed = before - after >= drop
    line = f"JCB canary: {before} -> {after} of {len(shared)} shared tasks passed"
    if lost:
        line += f"; now failing: {', '.join(lost)}"
    if gained:
        line += f"; now passing: {', '.join(gained)}"
    return {"regressed": regressed, "shared": len(shared), "lost": lost, "gained": gained,
            "line": ("REGRESSION " if regressed else "") + line + "."}


def _runs(root: Path) -> list[Path]:
    # Dated directories sort by name; within one night the newest results file wins (a
    # re-run the same day replaces, it does not count as the previous night).
    nights = sorted(d for d in root.iterdir() if d.is_dir())
    return [max(js) for d in nights if (js := sorted(d.glob("*.json")))]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "bench/results/canary")
    runs = _runs(root)
    if not runs:
        print(f"no canary results under {root}", file=sys.stderr)
        return 2
    # A crash here would exit 1 -- the script's "regressed" -- and send last night's
    # latest.md again; an unreadable night is "did not run cleanly", 2.
    try:
        previous = load_results(runs[-2]) if len(runs) > 1 else None
        out = compare(load_results(runs[-1]), previous)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"the canary results could not be read: {exc}", file=sys.stderr)
        return 2
    (root / "latest.md").write_text(out["line"] + "\n", encoding="utf-8")
    print(out["line"])
    return 1 if out["regressed"] else 0


if __name__ == "__main__":
    sys.exit(main())
