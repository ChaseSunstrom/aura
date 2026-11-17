"""The pipeline's skip guard (scripts/ci/skip_guard.py), driven through a real pytest run.

Each test writes a throwaway test file and a throwaway list, then starts pytest on it with
the variables the pipeline's template sets. Not covered here: that every job carries those
variables (deploy/tests/test_pipeline.py pins that) and what each job skips in its image
(only a run in the image shows it; `scripts/ci-job.py` makes one).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / "scripts" / "ci"
NEW = "the frobnicator is not on this machine"


def _pytest(tmp_path: Path, body: str, entries: list[dict] | None, job: str = "test:x") -> subprocess.CompletedProcess[str]:
    inner = tmp_path / "test_inner.py"
    inner.write_text("import os\nimport pytest\n" + body, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "SKIP_GUARD_LIST", "CI_JOB_NAME")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(PLUGIN_DIR), os.environ.get("PYTHONPATH", "")) if p)
    env["PYTEST_ADDOPTS"] = "-p skip_guard"
    env["CI_JOB_NAME"] = job
    if entries is not None:
        allow = tmp_path / "allow.json"
        allow.write_text(json.dumps({"skips": entries}), encoding="utf-8")
        env["SKIP_GUARD_LIST"] = str(allow)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(inner)],
                          capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)


def _entry(reason: str, job: str = "test:x", **more) -> dict:
    return {"jobs": [job], "reason": reason, "kind": "design", "why": "a test of the guard", **more}


SKIPS = f'''
def test_runs():
    assert True

@pytest.mark.skipif(True, reason="{NEW}")
def test_hidden():
    assert False
'''


def test_an_unrecorded_skip_reason_fails_a_green_run_and_names_the_reason_and_the_test(tmp_path):
    r = _pytest(tmp_path, SKIPS, [])
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"UNRECORDED SKIP [1] {NEW}" in r.stdout
    assert "test_inner.py::test_hidden" in r.stdout and "not recorded for test:x" in r.stdout
    assert "allow.json" in r.stdout, "the red says where a reason is recorded"


def test_a_recorded_reason_passes(tmp_path):
    r = _pytest(tmp_path, SKIPS, [_entry(NEW)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "UNRECORDED" not in r.stdout
    # A green log still shows the guard was on: its absence would read the same otherwise.
    assert "skip guard: on for test:x; 1 skip(s), each for a recorded reason" in r.stdout


def test_a_reason_recorded_for_another_job_does_not_cover_this_one(tmp_path):
    r = _pytest(tmp_path, SKIPS, [_entry(NEW, job="test:other")])
    assert r.returncode == 1 and f"UNRECORDED SKIP [1] {NEW}" in r.stdout


def test_a_reason_is_matched_whole_unless_the_entry_says_prefix(tmp_path):
    start = NEW[:16]
    assert _pytest(tmp_path, SKIPS, [_entry(start)]).returncode == 1
    assert _pytest(tmp_path, SKIPS, [_entry(start, prefix=True)]).returncode == 0


def test_a_skip_inside_the_test_and_a_whole_file_skipped_at_import_are_both_seen(tmp_path):
    inside = '''
def test_decides_late():
    pytest.skip("decided inside the test")
'''
    r = _pytest(tmp_path, inside, [])
    assert r.returncode == 1 and "UNRECORDED SKIP [1] decided inside the test" in r.stdout
    # The call is spelled in two pieces so the pipeline pin that reads every import-or-skip in
    # the test tree (and wants a job to install its package) does not take this one for real.
    whole_file = "pytest.importorskip" + '("no_such_module_for_the_guard")\n\ndef test_never():\n    pass\n'
    r = _pytest(tmp_path, whole_file, [])
    assert r.returncode != 0 and "UNRECORDED SKIP [1] could not import 'no_such_module_for_the_guard'" in r.stdout


def test_an_expected_failure_is_not_a_skip(tmp_path):
    body = '''
@pytest.mark.xfail(reason="known")
def test_known():
    assert False
'''
    r = _pytest(tmp_path, body, [])
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_failing_run_keeps_its_failure_and_still_names_the_skip(tmp_path):
    r = _pytest(tmp_path, SKIPS + "\ndef test_broken():\n    assert 1 == 2\n", [])
    assert r.returncode == 1 and "1 failed" in r.stdout and f"UNRECORDED SKIP [1] {NEW}" in r.stdout


def test_without_the_list_variable_the_plugin_judges_nothing(tmp_path):
    r = _pytest(tmp_path, SKIPS, None)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_list_that_cannot_be_read_stops_the_run_instead_of_passing(tmp_path):
    inner = tmp_path / "test_inner.py"
    inner.write_text("def test_ok():\n    pass\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(PLUGIN_DIR), "PYTEST_ADDOPTS": "-p skip_guard",
           "SKIP_GUARD_LIST": str(tmp_path / "missing.json"), "CI_JOB_NAME": "test:x"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(inner)],
                       capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert r.returncode not in (0, 5) and "cannot read the recorded list" in r.stdout + r.stderr


def test_a_pytest_run_started_by_a_test_inherits_neither_the_list_nor_the_option(tmp_path):
    """A test that starts its own pytest (this file does) must not have that run judged by the
    job's list, nor broken by an option whose module it cannot import."""
    body = '''
def test_the_environment_is_clean():
    assert "SKIP_GUARD_LIST" not in os.environ
    assert "skip_guard" not in os.environ.get("PYTEST_ADDOPTS", "")
'''
    r = _pytest(tmp_path, body, [])
    assert r.returncode == 0, r.stdout + r.stderr
