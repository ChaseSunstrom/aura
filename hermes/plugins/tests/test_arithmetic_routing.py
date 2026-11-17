"""T-0522: "what is 7 times 12" reaches `calculate`, not `python3 -c` or `execute_code`.

A live one-shot turn (2026-11-13) answered 84 through `execute_code` and `terminal` and never
called `calculate`, which is free and in-process. Both shell routes are gated, so a plain sum
parked on an approval. The tool was offered on every platform, so the persona is what had to
say where arithmetic goes.

Not covered: that the model obeys the line. That is the live turn
(`make golden-turn PROMPT='what is 7 times 12' EXPECT_TOOL=calculate`), which needs a deploy.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import _matrix  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = yaml.safe_load((ROOT / "deploy/config.template.yaml").read_text(encoding="utf-8"))
SOUL = (ROOT / "hermes/home/SOUL.md").read_text(encoding="utf-8")


def test_arithmetic_goes_to_calculate_on_every_platform_that_has_the_terminal():
    """`calculate` lives in the plugin's `hud` toolset. Hermes adds a plugin toolset to a
    platform whose saved list is only composites (checked against Hermes 0.21.0 with this
    template's lists: cli, api_server, tui, and the default-listed platforms all carry it),
    but a list that names a builtin toolset such as `terminal` is read as explicit and
    drops every plugin toolset not named in it. So such a list must name `hud`."""
    lists = TEMPLATE["platform_toolsets"]
    assert lists, "no platform_toolsets in the template"
    for platform, names in lists.items():
        explicit = [n for n in names if not n.startswith("hermes-") and n != "kanban"]
        if explicit:
            assert "hud" in names, f"{platform} lists {explicit} without hud: calculate is dropped"
    assert "aura-hud" in TEMPLATE["plugins"]["enabled"]


def test_arithmetic_goes_to_calculate_and_it_is_free_where_the_shell_is_not():
    rule = _matrix.decide("calculate", {"expression": "7*12"})
    assert rule.free
    assert not _matrix.decide("terminal", {"command": "python3 -c 'print(7*12)'"}).free
    assert not _matrix.decide("execute_code", {"code": "print(7*12)"}).free


def test_arithmetic_goes_to_calculate_in_the_persona():
    """The persona names the tool and both shell routes it replaces."""
    rule = next((p for p in re.split(r"\n(?=\d+[a-z]?\. )", SOUL) if "`calculate`" in p), "")
    assert rule, "SOUL.md never names `calculate`"
    assert "`terminal`" in rule and "`execute_code`" in rule, rule
    assert re.search(r"never", rule, re.I), rule
