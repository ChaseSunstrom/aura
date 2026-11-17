"""eager.py: the car's five hot tools skip the tool-search bridge, nothing else does."""

from __future__ import annotations

import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

eager = load_plugin("jarvis-hud").eager


def fake_tool_search():
    # Hermes' rule for a plugin tool: deferrable unless core.
    return types.SimpleNamespace(is_deferrable_tool_name=lambda name, defer_tools=None: name != "terminal")


def test_eager_car_tools_stay_visible_and_other_plugin_tools_still_defer():
    mod = fake_tool_search()
    assert eager.install(mod)
    for name in eager.EAGER:
        assert not mod.is_deferrable_tool_name(name)
    assert mod.is_deferrable_tool_name("hud_arrange")
    assert not mod.is_deferrable_tool_name("terminal")


def test_eager_install_twice_does_not_stack():
    mod = fake_tool_search()
    eager.install(mod)
    first = mod.is_deferrable_tool_name
    eager.install(mod)
    assert mod.is_deferrable_tool_name is first


def test_eager_hook_missing_degrades_to_deferring_everything():
    assert eager.install(types.SimpleNamespace()) is False


def test_eager_keeps_hud_project_visible_and_soul_sends_charts_to_it():
    # T-0698: production had never called hud_project; it was deferred and SOUL named hud_show.
    mod = fake_tool_search()
    eager.install(mod)
    assert not mod.is_deferrable_tool_name("hud_project")
    soul = (PLUGIN_DIR.parents[1] / "home" / "SOUL.md").read_text()
    rule = soul[soul.index("5. **Say what happened"):soul.index("6. **Standing objectives")]
    assert "`hud_project`" in rule
    for what in ("numbers over time", "places", "comparison"):
        assert what in rule
    hud_show = load_plugin("jarvis-hud").HUD_SHOW["description"]
    assert "hud_project" in hud_show


def test_the_tools_that_refuse_a_turn_that_read_outside_text_need_no_lookup():
    """Seen live, 12 Nov 2026: `tool_describe` ran first, marked the turn, and `complaint_file`
    then refused every time. A tool whose handler refuses a tainted turn must be visible."""
    hud = load_plugin("jarvis-hud")
    source = {"complaint_file": hud.complaints.handle_complaint_file, "later_add": hud.later.handle_later_add}
    import inspect
    for name, handler in source.items():
        assert '.get("outside")' in inspect.getsource(handler), name  # still the rule it is about
        assert name in eager.EAGER, f"{name} refuses a tainted turn but sits behind the lookup that taints it"

