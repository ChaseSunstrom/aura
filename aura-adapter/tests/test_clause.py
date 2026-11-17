"""The first-clause rule, against the table the console reads too (T-0605)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_adapter import clause

_TABLE = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/first-clause.json").read_text())


def test_limits_match_the_table():
    assert (clause.MIN_WORDS, clause.MAX_WORDS) == (_TABLE["minWords"], _TABLE["maxWords"])


def test_the_request_flag_is_the_tables():
    src = (Path(__file__).resolve().parents[1] / "aura_adapter/app.py").read_text()
    assert f'body.get("{_TABLE["requestFlag"]}")' in src


@pytest.mark.parametrize("case", _TABLE["cases"], ids=lambda c: c["why"])
def test_first_clause(case):
    got = clause.split_first_clause(case["text"])
    if case["first"] is None:
        assert got is None
    else:
        assert got == (case["first"], case["rest"])
