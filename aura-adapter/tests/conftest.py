"""Make ``aura_schema`` importable when the adapter suite runs from a checkout.

The adapter reads its configuration from the one settings schema (V2 D17). The
runtime image and CI ``pip install ./aura-schema``; a developer's ``.venv`` may
not, so the sibling tree goes on the path here — after any installed copy, which
therefore wins. The adapter package itself is added too, so the suite also runs
from the repo root.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ADAPTER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _ADAPTER_DIR.parent / "aura-schema"
# `aura_adapter` itself resolves through the cwd when the suite runs as CI does
# (`cd aura-adapter && pytest tests`); from the repo root it would not, so the
# package directory goes first.
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))
if _SCHEMA_DIR.is_dir() and str(_SCHEMA_DIR) not in sys.path:
    sys.path.append(str(_SCHEMA_DIR))
# Two suites here import a sibling test module by bare name (`from test_adapter import …`).
# Run beside another tree's tests from the repo root, pytest does not put this directory
# on the path for them, and collection dies with "No module named 'test_adapter'".
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.append(str(_TESTS_DIR))


import datetime  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _midday(monkeypatch):
    """Every test speaks at noon unless it sets the clock itself. Read off the real clock,
    the voice tests failed each night inside the default quiet hours, where a line is made
    softer and never cached."""
    from aura_adapter import app as app_mod

    monkeypatch.setattr(app_mod, "_clock", lambda: datetime.time(12, 0))
