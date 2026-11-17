"""The console's changing actions have an askable twin, or a stated reason, or are a counted gap.

`tests/contracts/console-actions.json` is read by this file and by
`jarvis-web/src/lib/hud/actionParity.test.ts`. The console side finds every changing call in
the console's sources; this side checks that each tool a row names is registered by a plugin
and has a row in the approval matrix, and prints the gaps.

It does not check that the named tool does what the console action does (a tool can exist and
cover less), and it cannot see a console action the console test missed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from _shared import _approval_hook, _matrix  # noqa: E402
from test_matrix import _registered_plugin_tools  # noqa: E402

REGISTRY = json.loads((HERE.parents[2] / "tests/contracts/console-actions.json").read_text())
ACTIONS = REGISTRY["actions"]


def test_every_named_tool_exists_and_has_a_matrix_row():
    registered = _registered_plugin_tools()
    classified = set(_matrix.known_tools())
    problems = []
    for action in ACTIONS:
        for tool in action.get("tools", []):
            if tool not in registered:
                problems.append(f"{action['id']}: {tool} is registered by no plugin")
            if tool not in classified:
                problems.append(f"{action['id']}: {tool} has no row in _shared/_matrix.py")
    assert not problems, "\n".join(problems)


def test_every_named_tool_shows_before_and_after_or_says_why_not():
    """T-1329: an asked-for change that replaces a value shows the value now and the value it
    becomes. Each tool a row names has a change describer (the approval card's Now/Becomes
    lines) or is listed under ``no_prior_value`` with the reason it has none; both is a lie,
    neither is the gap this test exists to close.

    Not checked: that a describer reads the right value (each plugin's own tests do), nor
    that a ``no_prior_value`` reason is true; it only has to be written down.
    """
    _registered_plugin_tools()  # registering every plugin is what fills CHANGE_DESCRIBERS
    named = {tool for action in ACTIONS for tool in action.get("tools", [])}
    describers = set(_approval_hook.CHANGE_DESCRIBERS)
    none = {t: why for t, why in REGISTRY["no_prior_value"].items() if not t.startswith("_")}
    problems = [f"{t}: no change describer and not in no_prior_value" for t in sorted(named - describers - set(none))]
    problems += [f"{t}: in no_prior_value but also has a describer" for t in sorted(describers & set(none))]
    problems += [f"{t}: in no_prior_value but no action names it" for t in sorted(set(none) - named)]
    problems += [f"{t}: no_prior_value needs a reason" for t, why in sorted(none.items()) if len(why) < 20]
    assert not problems, "\n".join(problems)


def test_the_console_draws_rows_for_every_tool_that_has_a_describer():
    """``change.tools`` in approval-event.json is what the console draws Now/Becomes rows for;
    a describer the console does not know would show its three lines as plain text."""
    _registered_plugin_tools()
    contract = json.loads((HERE.parents[2] / "tests/contracts/approval-event.json").read_text())
    assert sorted(_approval_hook.CHANGE_DESCRIBERS) == sorted(contract["change"]["tools"])


def test_rows_are_well_formed():
    for action in ACTIONS:
        status = action["status"]
        assert status in {"tool", "human-only", "gap"}, action["id"]
        assert action["calls"], action["id"]
        # A row is one thing: a tool row names tools, and the other two do not.
        assert bool(action.get("tools")) == (status == "tool"), action["id"]
        if status == "human-only":
            assert len(action.get("reason", "")) > 20, action["id"]
        if status == "gap":
            assert len(action.get("note", "")) > 10, action["id"]


def test_gaps_are_listed_and_counted():
    gaps = [a for a in ACTIONS if a["status"] == "gap"]
    print(f"\nconsole actions with no askable twin ({len(gaps)}):")
    for gap in gaps:
        print(f"  {gap['id']}: {gap['what']} -> {gap['note']}")
    assert len(gaps) == REGISTRY["pinned_gaps"], (
        "the gap count moved: close a gap by giving its row a tool (and lower pinned_gaps), "
        "or raise pinned_gaps with the new gap's row"
    )
