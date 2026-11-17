"""A pytest plugin for the pipeline: a skip reason nobody recorded fails the run.

A skipped test is green. On 2026-11-15 forty tests the pipeline could have run were skipped
in every pipeline, some for months, because the job's image lacked a tool, a package, a user
or an install (docs/research/ci-skips-2026-11-15.md). With this plugin loaded and
``SKIP_GUARD_LIST`` naming the recorded list (``scripts/ci/allowed-skips.json``), every skip
of the session is looked up by the job's name (``CI_JOB_NAME``) and its reason. One that is
not recorded turns a passing run into exit 1 and is printed with the tests it hid.

The pipeline turns it on through the ``.skip-guard`` template's variables
(``PYTEST_ADDOPTS=-p skip_guard`` with this folder on ``PYTHONPATH``), so it is off on a desk
and under ``make test``.

Not covered: a test that is deselected or never collected, an expected failure, the Node and
Kotlin suites, a job that does not carry the variables, and whether a recorded reason is
still met by any test (an entry nobody uses is not reported).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

LIST_ENV = "SKIP_GUARD_LIST"
OPTION = "-p skip_guard"
KINDS = ("design", "image")


def recorded(entries: list[dict], job: str, reason: str) -> bool:
    """Whether ``reason`` is recorded for ``job``: the whole reason, or its start where the
    entry says ``"prefix": true`` (a reason that carries text which changes per run)."""
    for entry in entries:
        if job not in entry.get("jobs", ()):
            continue
        want = entry.get("reason", "")
        if reason == want or (entry.get("prefix") is True and want and reason.startswith(want)):
            return True
    return False


class _Guard:
    def __init__(self, entries: list[dict], job: str, source: str) -> None:
        self.entries, self.job, self.source = entries, job, source
        self.unrecorded: dict[str, list[str]] = {}
        self.recorded_skips = 0

    def _see(self, report) -> None:
        # An expected failure is reported as skipped too; it ran, so it is not this guard's.
        if not report.skipped or hasattr(report, "wasxfail"):
            return
        longrepr = report.longrepr
        reason = str(longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else longrepr)
        reason = reason.removeprefix("Skipped: ").strip()
        if recorded(self.entries, self.job, reason):
            self.recorded_skips += 1
        else:
            self.unrecorded.setdefault(reason, []).append(report.nodeid)

    def pytest_runtest_logreport(self, report) -> None:
        self._see(report)

    def pytest_collectreport(self, report) -> None:
        # A module-level importorskip or skip: the whole file, reported once at collection.
        self._see(report)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session) -> None:
        # Only a run that would have been green is changed: a failure keeps pytest's own code.
        if self.unrecorded and int(session.exitstatus) == 0:
            session.exitstatus = 1

    def pytest_terminal_summary(self, terminalreporter) -> None:
        job = self.job or "(no CI_JOB_NAME)"
        if not self.unrecorded:
            # One line in every guarded log, so a green job shows the guard was on, not absent.
            terminalreporter.write_line(
                f"skip guard: on for {job}; {self.recorded_skips} skip(s), each for a recorded reason")
            return
        terminalreporter.section(f"skip guard: {len(self.unrecorded)} skip reason(s) not recorded for {job}",
                                 red=True, bold=True)
        for reason, tests in sorted(self.unrecorded.items()):
            terminalreporter.write_line(f"UNRECORDED SKIP [{len(tests)}] {reason}")
            for nodeid in tests[:5]:
                terminalreporter.write_line(f"    {nodeid}")
            if len(tests) > 5:
                terminalreporter.write_line(f"    and {len(tests) - 5} more")
        terminalreporter.write_line(
            "Each is a test this job did not run. Give the job what the test needs, or run it in "
            f"a job that has it; only then record the reason for this job, with why, in {self.source} "
            "(docs/ci.md, \"A skip nobody recorded\").")


def pytest_configure(config) -> None:
    # Read once and removed: a pytest run that a test starts is that test's business, and
    # must neither be judged by this job's list nor fail to import this module.
    source = os.environ.pop(LIST_ENV, "")
    addopts = os.environ.get("PYTEST_ADDOPTS", "")
    if OPTION in addopts:
        rest = " ".join(addopts.replace(OPTION, " ").split())
        if rest:
            os.environ["PYTEST_ADDOPTS"] = rest
        else:
            del os.environ["PYTEST_ADDOPTS"]
    if not source:
        return
    try:
        entries = json.loads(Path(source).read_text(encoding="utf-8"))["skips"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A list that cannot be read must not read as "nothing is recorded" or "all is well".
        raise pytest.UsageError(f"skip guard: cannot read the recorded list {source}: {exc}") from exc
    config.pluginmanager.register(_Guard(entries, os.environ.get("CI_JOB_NAME", ""), source), "skip-guard-session")
