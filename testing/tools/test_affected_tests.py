"""scripts/verify/affected_tests.py: each changed test runs from its suite's own directory."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "affected_tests", Path(__file__).resolve().parents[2] / "scripts" / "verify" / "affected_tests.py")
assert _SPEC and _SPEC.loader
affected = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(affected)


def test_each_suite_runs_from_its_own_directory_and_a_deleted_path_is_dropped():
    groups, mirrors = affected.plan([
        "aura-core/tests/test_packaging.py",
        "hermes/plugins/tests/test_matrix.py",
        "deploy/tests/test_pipeline.py",
        "bench/tests/__init__.py",
        "testing/tools/test_fetch_video.py",  # renamed away in 2841e890 (T-0812)
        "android-app/tools/notify_test.py",
    ])
    assert groups == {
        "aura-core": ["tests/test_packaging.py"],
        "hermes": ["plugins/tests/test_matrix.py"],
        ".": ["deploy/tests/test_pipeline.py"],
    }
    assert mirrors == ["android-app/tools/notify_test.py"]
