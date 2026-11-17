"""The tool an approval prompt is about, against the table both languages read.

``_shared/_approval_event.py`` is the parse; ``aura-web/src/lib/hermes/
approvalEvent.ts`` is the console's copy of it, because the approval card is
drawn in the browser and the audit log is written in Python. Two
implementations of an undocumented string format is two chances to disagree
about what the agent asked permission for, so neither file holds the cases:
``tests/contracts/approval-event.json`` does, and both suites read it.

What that buys is narrow and worth saying: it catches the two copies drifting
from *each other*. It does not catch both of them drifting from Hermes. That is
``aura-guard/tests/test_guard.py``'s job — it drives a real approval through
the installed Hermes and parses the event a gateway callback actually receives,
so a change to Hermes' wording fails there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# `_shared/` is reachable only via the plugins root, the way Hermes' loader puts
# it there for each plugin: see `_shared/_approval_hook.py`'s module docstring.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import _approval_event  # noqa: E402

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "tests/contracts/approval-event.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
CASES = CONTRACT["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_contract_case_is_answered(case: dict) -> None:
    assert _approval_event.tool_of(case["event"]) == case["tool"]


def test_the_contract_has_cases_to_answer() -> None:
    """A contract file that loses its cases must fail, not pass vacuously."""
    assert len(CASES) > 8


def test_the_contract_describes_the_rendering_the_parse_depends_on() -> None:
    """The two strings the parse reads are named in the contract, not only in code.

    If Hermes changes ``display_target``, the contract is where the new wording
    is written down and both implementations are corrected from.
    """
    rendering = CONTRACT["rendering"]
    assert rendering["plugin_rule_command"] == "<tool_name> (plugin approval rule)"
    assert rendering["pattern_key_prefix"] == _approval_event._PLUGIN_RULE_PREFIX
    assert CONTRACT["structured_fields_tried_first"] == ["tool", "tool_name"]


def test_a_non_dict_event_names_no_tool() -> None:
    """The benchmark hands it whatever came off the wire, including None."""
    assert _approval_event.tool_of(None) == ""  # type: ignore[arg-type]
    assert _approval_event.tool_of([]) == ""  # type: ignore[arg-type]
    assert _approval_event.tool_of({"command": 42}) == ""
