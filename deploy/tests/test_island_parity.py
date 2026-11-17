"""The island parity table: every phone island behaviour has a desktop row (T-0696).

``docs/design/ISLAND_PARITY.md`` has one row for each input and state of ``tests/contracts/island.json``
and for the other things the phone's island does. A desktop cell is ``Has:`` (with the files that
do it) or ``Gap:`` (with why). Does not cover whether a ``Has:`` row is true: the rows' files are
checked to exist, and the behaviours themselves are the web, Rust and Android suites'.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "docs/design/ISLAND_PARITY.md"
CONTRACT = ROOT / "tests/contracts/island.json"

#: What the phone's island does that the contract's inputs and states do not name.
EXTRA_ROWS = {"morph", "gesture.flick", "place", "dim", "worn", "summon.type", "summon.talk"}


def _rows() -> dict[str, tuple[str, str, str]]:
    out: dict[str, tuple[str, str, str]] = {}
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] not in ("Row", "---"):
            out[cells[0]] = (cells[1], cells[2], cells[3])
    return out


def test_every_contract_input_and_state_has_a_row():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = (
        {f"input.{k}" for k in contract["inputs"]}
        | {f"state.{k}" for k in contract["states"]}
        | EXTRA_ROWS
    )
    missing = sorted(expected - set(_rows()))
    assert not missing, f"the island has behaviours with no row in {TABLE.name}: {missing}"


def test_every_row_says_what_the_desktop_has_or_names_the_gap():
    bad = {
        row: desktop[:40]
        for row, (phone, desktop, _) in _rows().items()
        if not phone or not re.match(r"(Has|Gap): \S", desktop)
    }
    assert not bad, f"rows with no phone behaviour, or a desktop cell that is neither Has: nor Gap: {bad}"


def test_the_files_a_row_points_at_exist():
    gone = {
        row: path
        for row, (_, _, where) in _rows().items()
        for path in re.findall(r"`([^`]+)`", where)
        if not (ROOT / path).is_file()
    }
    assert not gone, f"rows name files that are not in the tree: {gone}"
    assert all(w for _, _, w in _rows().values()), "a row names no file"


def test_the_typing_key_is_a_has_row_that_names_its_setting():
    desktop = _rows()["summon.type"][1]
    assert desktop.startswith("Has:")
    assert "input.expanded" in _rows() and "AURA_PALETTE_KEY" in _rows()["input.expanded"][1]
