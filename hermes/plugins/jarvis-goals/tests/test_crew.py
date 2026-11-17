"""The crew budget (T-0252): spawns, calls and minutes across a goal's helpers."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# crew.py is pure (no Hermes imports), so it is loaded by path rather than through the plugin.
_spec = importlib.util.spec_from_file_location("jarvis_goals_crew", Path(__file__).resolve().parents[1] / "crew.py")
_crew = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _crew  # dataclasses look their module up while the class is built
_spec.loader.exec_module(_crew)
CrewBudget = _crew.CrewBudget


class Clock:
    t = 0.0

    def __call__(self) -> float:
        return self.t


def crew_with(n_children: int, clock: Clock | None = None, **limits) -> CrewBudget:
    b = CrewBudget(clock or Clock(), **limits)
    for i in range(n_children):
        assert b.pre_tool_call("delegate_task", {"goal": f"part {i}"}, "goal") is None
        b.subagent_start(parent_session_id="goal", child_session_id=f"c{i}")
    return b


def blocked(r) -> bool:
    return isinstance(r, dict) and r.get("action") == "block"


def test_crew_budget_spawns_refuse_the_next_helper_and_a_batch_too_big():
    b = crew_with(2, spawns=3)
    assert b.pre_tool_call("delegate_task", {"goal": "x"}, "goal") is None
    assert blocked(b.pre_tool_call("delegate_task", {"tasks": [{}, {}]}, "goal"))  # 1 left
    assert blocked(b.pre_tool_call("delegate_task", {"goal": "x"}, "goal"))  # the third was spent


def test_crew_budget_a_grandchild_counts_against_the_same_crew():
    b = crew_with(1, spawns=2)
    assert b.pre_tool_call("delegate_task", {"goal": "sub"}, "c0") is None
    b.subagent_start(parent_session_id="c0", child_session_id="g0")
    assert b.root_of("g0") == "goal" and b.crews["goal"].spawns == 2
    assert blocked(b.pre_tool_call("delegate_task", {"goal": "x"}, "g0"))


def test_crew_budget_calls_and_minutes_stop_the_children_not_the_parent():
    clock = Clock()
    b = crew_with(1, clock, calls=2, minutes=1)
    assert b.pre_tool_call("web_search", {}, "c0") is None
    assert b.pre_tool_call("web_search", {}, "c0") is None
    assert blocked(b.pre_tool_call("web_search", {}, "c0"))
    assert b.pre_tool_call("web_search", {}, "goal") is None  # the parent's own turns: goals.max_turns
    b2 = crew_with(1, clock, minutes=1)
    clock.t = 61
    assert blocked(b2.pre_tool_call("read_file", {}, "c0"))


def test_crew_kill_stops_every_child_and_still_lets_the_parent_steer():
    b = crew_with(2)
    assert b.stop("c1")  # from any member: the whole crew
    assert blocked(b.pre_tool_call("web_search", {}, "c0"))
    assert blocked(b.pre_tool_call("delegate_task", {"goal": "more"}, "goal"))
    assert b.pre_tool_call("delegate_task", {"action": "stop", "subagent_id": "c0"}, "goal") is None
    assert not b.stop("nobody")


def test_crew_budget_a_session_with_no_crew_is_never_refused():
    b = CrewBudget(Clock())
    assert b.pre_tool_call("delegate_task", {"goal": "x"}, "solo") is None
    assert b.pre_tool_call("web_search", {}, "solo") is None


def test_crew_budget_an_invented_action_is_a_spawn_not_a_control():
    b = crew_with(1, spawns=1)
    assert blocked(b.pre_tool_call("delegate_task", {"action": "spawn", "goal": "x"}, "goal"))
    assert b.pre_tool_call("delegate_task", {"action": "list"}, "goal") is None


def test_crew_budget_spawns_count_even_when_children_never_report_a_session():
    b = CrewBudget(Clock(), spawns=2)
    assert b.pre_tool_call("delegate_task", {"tasks": [{}, {}]}, "goal") is None
    b.subagent_start(parent_session_id="goal", child_session_id=None)  # Hermes gave it no id
    assert blocked(b.pre_tool_call("delegate_task", {"goal": "x"}, "goal"))


READS = frozenset({"web_search", "read_file"})


def belted() -> CrewBudget:
    b = CrewBudget(Clock(), belts={"researcher": READS, "planner": READS | {"todo"}})
    b.pre_tool_call("delegate_task", {"goal": "x"}, "goal")
    b.subagent_start(parent_session_id="goal", child_session_id="r1", child_goal="[researcher] find flights")
    return b


def test_crew_worker_a_researcher_only_reads():
    b = belted()
    assert b.pre_tool_call("web_search", {}, "r1") is None
    assert blocked(b.pre_tool_call("send_message", {}, "r1"))
    assert b.pre_tool_call("send_message", {}, "goal") is None  # the parent carries no belt


def test_crew_worker_a_grandchild_never_gets_a_wider_belt_than_its_parent():
    b = belted()
    b.pre_tool_call("delegate_task", {"goal": "y"}, "r1")
    b.subagent_start(parent_session_id="r1", child_session_id="p1", child_goal="[planner] plan it")
    assert blocked(b.pre_tool_call("todo", {}, "p1"))  # planner's todo is not on the researcher's belt
    b.subagent_start(parent_session_id="r1", child_session_id="u1", child_goal="no tag")
    assert blocked(b.pre_tool_call("send_message", {}, "u1"))  # inherits the researcher's


def test_crew_worker_an_untagged_or_unknown_type_carries_no_belt():
    b = belted()
    b.subagent_start(parent_session_id="goal", child_session_id="d1", child_goal="[wizard] do it")
    assert b.pre_tool_call("send_message", {}, "d1") is None


def test_crew_budget_and_tag_match_the_console_contract():
    """The console's goals panel shows 'n/8 helpers' and a helper's type from the same table
    (tests/contracts/crew-budget.json); a limit changed here without it fails this."""
    import json

    contract = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts"
                           / "crew-budget.json").read_text())
    assert (_crew.SPAWNS, _crew.CALLS, _crew.MINUTES) == (
        contract["spawns"], contract["calls"], contract["minutes"])
    assert _crew._TAG.pattern == contract["tag"]
    for case in contract["types"]:
        m = _crew._TAG.match(case["goal"])
        assert (m.group(1) if m else None) == case["type"], case["goal"]


def test_crew_graph_a_batch_bigger_than_the_crew_is_refused_before_any_spawn():
    # delegate_task's tasks list is the graph Hermes runs; past 8 nothing starts.
    b = CrewBudget(Clock())
    assert blocked(b.pre_tool_call("delegate_task", {"tasks": [{}] * 9}, "goal"))
    assert "goal" not in b.crews  # nothing was counted, nothing started
    assert b.pre_tool_call("delegate_task", {"tasks": [{}] * 8}, "goal") is None
