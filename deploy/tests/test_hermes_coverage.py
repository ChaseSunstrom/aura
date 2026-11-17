"""The coverage table is data, and it covers everything (JARVIS_V3_BRIEF Part 4).

`scripts/verify/hermes-coverage.py` is what checks the table against the
*installed* Hermes; these are the checks that hold without one, so a machine with
no Hermes still fails a table that has lost a reason or grown a row with no
decision in it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TABLE = json.loads((ROOT / "tests" / "contracts" / "hermes-coverage.json").read_text())
SECTIONS = ("builtin_toolsets", "plugin_toolsets", "mcp_servers", "gateway_platforms")


def _rows():
    for section in SECTIONS:
        for name, row in TABLE[section].items():
            yield section, name, row


def test_every_row_is_on_or_off_and_says_why():
    for section, name, row in _rows():
        assert row["state"] in ("on", "off"), (section, name)
        why = row["why"].strip()
        # A reason is a sentence, not a word: "off" with "no" attached to it is
        # the default nobody chose wearing a decision's clothes.
        assert len(why) > 25, f"{section}.{name} has no real reason: {why!r}"
        assert why.endswith("."), f"{section}.{name}'s reason is not a sentence: {why!r}"


def test_a_pending_row_says_what_is_pending():
    for section, name, row in _rows():
        if not row["why"].startswith("PENDING:"):
            continue
        assert len(row["why"]) > 60, f"{section}.{name} is pending without saying what is left"


def test_no_cloud_provider_is_ever_on():
    """The no-cloud rule, as a test rather than as a paragraph."""
    forbidden = {"x_search", "yuanbao", "video_gen"}
    for section, name, row in _rows():
        if name in forbidden:
            assert row["state"] == "off", f"{name} is a cloud provider and is on"


#: A plugin's toolset name, where this repo's plugins declare it: a module constant at the start
#: of a line (the scaffold's template writes the same line indented, inside a string).
_TOOLSET_RE = re.compile(r'^TOOLSET\s*=\s*"([\w-]+)"', re.M)


def test_every_toolset_a_plugin_in_the_tree_declares_has_a_row():
    """The installed-Hermes check cannot run in the pipeline (no Hermes there, exit 2), so a new
    plugin's toolset went unlisted until someone ran the script on the house's own machine: four
    had, by T-1443. The tree knows its own plugins without Hermes, so this part is checked here.

    Not covered: a toolset named inline in a register call, a built-in toolset or a platform a
    Hermes upgrade adds. Those still need the script, run where the stack is."""
    declared: dict[str, str] = {}
    for path in sorted((ROOT / "hermes" / "plugins").rglob("*.py")):
        if "tests" not in path.parts:
            for name in _TOOLSET_RE.findall(path.read_text()):
                declared[name] = path.relative_to(ROOT).as_posix()
    assert len(declared) > 15, "the walk found too few plugin toolsets to mean anything"
    missing = {name: where for name, where in declared.items() if name not in TABLE["plugin_toolsets"]}
    assert not missing, f"plugin toolsets with no row in the coverage table: {missing}"


@pytest.mark.parametrize("section", SECTIONS)
def test_a_name_in_two_sections_is_two_decisions(section: str):
    """`homeassistant` is a toolset that is on and a chat platform that is off.

    That is not a contradiction — they are different questions — so what has to
    hold is not that the states agree but that each row says something of its
    own. Two identical reasons under one name is a copy-paste, and a copy-paste
    is how a decision stops being one.
    """
    others = {n for s in SECTIONS if s != section for n in TABLE[s]}
    for name in set(TABLE[section]) & others:
        reasons = [TABLE[s][name]["why"] for s in SECTIONS if name in TABLE[s]]
        assert len(set(reasons)) == len(reasons), f"{name} carries the same reason twice"
