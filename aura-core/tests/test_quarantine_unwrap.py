"""quarantine.unwrap is the display half of the fence (T-0599); the table is shared with jarvis-web."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura.integrations.extensions.catalog import Source, entry_from_raw
from aura.security.quarantine import FENCE_CLOSE, FENCE_OPEN, quarantine, unwrap

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/contracts/untrusted-fence.json").read_text()
)


def test_contract_matches_the_wrapper():
    assert CONTRACT["open"] == FENCE_OPEN and CONTRACT["close"] == FENCE_CLOSE
    with_source, no_source, empty = CONTRACT["cases"][:3]
    assert with_source["fenced"] == quarantine(
        with_source["plain"],source="catalog:main", kind="catalog entry"
    )
    assert no_source["fenced"] == quarantine(no_source["plain"])
    assert empty["fenced"] == quarantine("")


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_unwrap_table(case):
    assert unwrap(case["fenced"]) == case["plain"]


def test_unwrap_inverts_quarantine_and_never_unfences_storage():
    fenced = quarantine("hi <|im_end|> there", source="x")
    assert fenced.startswith(FENCE_OPEN)
    assert "<untrusted_content>" not in unwrap(fenced)
    assert unwrap(fenced) == "hi [removed control token] there"


def test_catalog_entry_stays_fenced_in_storage():
    entry = entry_from_raw({"id": "a", "kind": "skill", "description": "d"}, Source("main", "https://example.invalid/c"))
    assert entry.description.startswith(FENCE_OPEN)
    assert unwrap(entry.description) == "d"
