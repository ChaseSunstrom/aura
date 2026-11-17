"""The nightly canary's diff: shared tasks only, a drop of two passes is a regression."""

from __future__ import annotations

import json

from bench.canary import compare, main
from bench.report import build_results_doc
from bench.runner import TaskRecord


def _doc(**statuses):
    return build_results_doc([TaskRecord(id=i, category="safety", status=s) for i, s in statuses.items()])


def test_two_lost_passes_is_a_regression_and_names_them():
    before = _doc(a="pass", b="pass", c="pass", d="fail")
    after = _doc(a="pass", b="fail", c="error", d="pass")
    out = compare(after, before)
    assert out["regressed"] is False  # 3 -> 2: one net loss
    after = _doc(a="fail", b="fail", c="error", d="fail")
    out = compare(after, before)
    assert out["regressed"] is True
    assert out["lost"] == ["a", "b", "c"]
    assert "3 -> 0" in out["line"] and "a, b, c" in out["line"]


def test_only_shared_tasks_count():
    # A task added to the subset (or skipped tonight) must not read as a regression.
    before = _doc(a="pass", b="pass", c="pass")
    after = _doc(a="pass", b="skip", d="fail", e="fail")
    out = compare(after, before)
    assert out["shared"] == 1
    assert out["regressed"] is False


def test_first_night_has_nothing_to_compare():
    out = compare(_doc(a="pass"), None)
    assert out["regressed"] is False and "first canary" in out["line"]


def test_main_picks_the_two_newest_runs_and_writes_latest(tmp_path):
    for day, doc in (("2026-10-04", _doc(a="pass", b="pass")),
                     ("2026-10-05", _doc(a="pass", b="pass")),
                     ("2026-10-06", _doc(a="fail", b="fail"))):
        (tmp_path / day).mkdir()
        (tmp_path / day / "2026-10-06T01-00-00.json").write_text(json.dumps(doc))
    assert main([str(tmp_path)]) == 1
    latest = (tmp_path / "latest.md").read_text()
    assert "2 -> 0" in latest
    # A clean night exits 0 so the cron wrapper sends nothing.
    (tmp_path / "2026-10-07").mkdir()
    (tmp_path / "2026-10-07" / "x.json").write_text(json.dumps(_doc(a="fail", b="fail")))
    assert main([str(tmp_path)]) == 0


def test_an_unreadable_night_is_not_a_regression(tmp_path):
    # Exit 1 is the script's "regressed, tell Sir"; a half-written file must not be,
    # or last night's latest.md is sent again under tonight's date.
    for night in ("2026-10-05", "2026-10-06"):
        (tmp_path / night).mkdir()
        (tmp_path / night / "a.json").write_text("{", encoding="utf-8")
    assert main([str(tmp_path)]) == 2
    assert not (tmp_path / "latest.md").exists()
