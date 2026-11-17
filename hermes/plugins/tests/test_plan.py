"""One card for a multi-step plan: what an approved plan lets through, and what it never does."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGINS))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _killswitch, _plan, _speaker  # noqa: E402
from _shared._approval_hook import approval_gate, matrix_gate  # noqa: E402

guard = load_plugin("aura-guard")

def _on(entity: str) -> dict:
    return {"domain": "light", "service": "turn_on", "entity_id": entity}


PLAN = {"why": "evening lights", "steps": [
    {"tool": "ha_call_service", "args": _on("light.kitchen")},
    {"tool": "ha_call_service", "args": _on("light.hall")},
]}


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clock = [100.0]
    monkeypatch.setattr(_plan, "_now", lambda: clock[0])
    _plan.reset()
    yield clock
    _plan.reset()


def _call(entity: str, session: str = "s1", **extra):
    return matrix_gate()(tool_name="ha_call_service", args={**_on(entity), **extra},
                         session_id=session)


def test_plan_card_lists_every_step_whole():
    asked = matrix_gate()(tool_name="plan_approve", args=PLAN, session_id="s1")
    assert asked["action"] == "approve"
    assert '"entity_id": "light.kitchen"' in asked["message"]
    assert '"entity_id": "light.hall"' in asked["message"]


def test_plan_every_card_has_its_own_rule_key():
    """Hermes remembers a session/always answer by rule_key; a shared key would let one
    remembered answer approve every later plan without a card."""
    gate = matrix_gate()
    keys = {gate(tool_name="plan_approve", args=PLAN, session_id="s1")["rule_key"] for _ in range(3)}
    assert len(keys) == 3


def test_plan_approved_steps_run_once_each_without_another_card():
    assert _call("light.kitchen")["action"] == "approve"  # no plan yet: a card per call
    out = json.loads(guard.handle_plan_approve(PLAN, session_id="s1"))
    assert out["ok"] and len(out["approved_steps"]) == 2
    assert _call("light.kitchen") is None
    assert _call("light.hall") is None
    assert _call("light.kitchen")["action"] == "approve", "a step is used once"


def test_plan_never_stands_for_another_target_session_or_time(_fresh):
    guard.handle_plan_approve(PLAN, session_id="s1")
    assert _call("lock.front_door")["action"] == "approve", "an approved light is not a door"
    assert _call("lock.front_door", note="light.kitchen")["action"] == "approve", \
        "an approved target smuggled into another argument is not the step"
    assert _call("light.kitchen", brightness=255)["action"] == "approve", "different arguments"
    assert _call("light.kitchen") is None, "the exact step still runs"
    assert _call("light.kitchen", session="s2")["action"] == "approve", "another session has no plan"
    _fresh[0] += _plan.TTL_S + 1
    assert _call("light.kitchen")["action"] == "approve", "the plan lapses"


def test_plan_never_overrides_a_guest(monkeypatch):
    guard.handle_plan_approve(PLAN, session_id="s1")
    monkeypatch.setattr(_speaker, "speaker_of", lambda _s: {"level": "guest", "label": "Ted"})
    assert _call("light.kitchen")["action"] == "block"


def test_plan_does_not_lift_the_kill_switch():
    guard.handle_plan_approve(PLAN, session_id="s1")
    _killswitch.engage(actor="test", reason="drill")
    try:
        assert _call("light.kitchen")["action"] == "block"
    finally:
        _killswitch.release(actor="test")


@pytest.mark.parametrize("bad", [
    {}, {"steps": []}, {"steps": [{"tool": "ha_call_service"}]},
    {"steps": [{"tool": "ha_call_service", "target": "light.kitchen"}]},
    {"steps": [{"tool": "plan_approve", "args": {"x": 1}}]},
    {"steps": [{"tool": "t", "args": {"x": 1}}] * (_plan.MAX_STEPS + 1)},
    {"steps": [{"tool": "t", "args": {"x": "y" * _plan.STEP_MAX}}]},
])
def test_plan_a_malformed_plan_grants_nothing(bad):
    out = json.loads(guard.handle_plan_approve(bad, session_id="s1"))
    assert out["ok"] is False
    assert _call("light.kitchen")["action"] == "approve"


@pytest.mark.parametrize("plugin_first", [True, False])
def test_plan_a_plugins_own_gate_does_not_ask_again_for_a_step(plugin_first):
    """settings_set asks twice -- the matrix and its plugin -- and the live proof showed the
    plugin's card for every step of an approved plan. Either order of the two hooks."""
    plugin = approval_gate(lambda tool, args: {"action": "approve", "message": "plugin", "rule_key": "p"})
    guard.handle_plan_approve(PLAN, session_id="s1")
    args = _on("light.kitchen")
    gate = lambda cid: matrix_gate()(tool_name="ha_call_service", args=args, session_id="s1", tool_call_id=cid)  # noqa: E731
    if plugin_first:
        assert plugin("ha_call_service", args, session_id="s1", tool_call_id="c1") is None
        assert gate("c1") is None
    else:
        assert gate("c1") is None
        assert plugin("ha_call_service", args, session_id="s1", tool_call_id="c1") is None
    # Another target is not the step. (Not a lock: a sensitive call is the matrix gate's alone, T-0945.)
    assert plugin("ha_call_service", _on("light.porch"), session_id="s1", tool_call_id="c2")["action"] == "approve"
    # The same arguments on another call, at once: a replay, not the step.
    assert plugin("ha_call_service", args, session_id="s1", tool_call_id="c3")["action"] == "approve"
    assert gate("c3")["action"] == "approve"


def test_plan_a_tool_only_its_plugin_gates_still_uses_the_step_once():
    """A tool the matrix lets through never reaches consume() there; the plugin's gate must
    use the step itself, or the plan would cover that call for the whole ten minutes."""
    plugin = approval_gate(lambda tool, args: {"action": "approve", "message": "plugin", "rule_key": "p"})
    guard.handle_plan_approve({"steps": [{"tool": "free_tool", "args": {"x": 1}}]}, session_id="s1")
    assert plugin("free_tool", {"x": 1}, session_id="s1", tool_call_id="a") is None
    assert plugin("free_tool", {"x": 1}, session_id="s1", tool_call_id="b")["action"] == "approve"
    assert plugin("free_tool", {"x": 1}, session_id="s1")["action"] == "approve"
