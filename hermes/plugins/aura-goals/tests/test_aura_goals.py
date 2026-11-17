"""Unit tests for the aura-goals plugin (no Hermes runtime needed).

``hermes_cli.goals`` is replaced by a small fake so the command grammar and the
session-key mapping are tested in isolation; the real GoalManager is exercised
by the live smoke test (scripts/smoke.sh) against a running gateway.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


class _State:
    def __init__(self, goal: str, max_turns: int = 20):
        self.goal = goal
        self.max_turns = max_turns
        self.contract = None
        self.status = "active"


class _Contract:
    def __init__(self, **fields):
        self.fields = {k: v for k, v in fields.items() if v}

    def is_empty(self):
        return not self.fields

    def to_dict(self):
        return dict(self.fields)

    @classmethod
    def from_dict(cls, data):
        return cls(**(data or {}))


class FakeGoalManager:
    instances: dict[str, "FakeGoalManager"] = {}

    def __new__(cls, session_id: str, **_):
        inst = cls.instances.get(session_id)
        if inst is None:
            inst = super().__new__(cls)
            inst.session_id = session_id
            inst._state = None
            inst.subgoals = []
            inst.gates = []
            inst.waiting = False
            cls.instances[session_id] = inst
        return inst

    def clear(self):
        self._state = None

    def has_goal(self):
        # Hermes': a done or cleared goal is not one a person can pause or resume.
        return self._state is not None and self._state.status in {"active", "paused"}

    def pause(self, reason="user-paused"):
        if not self._state:
            return None
        self._state.status = "paused"
        return self._state

    def resume(self, **_):
        if not self._state:
            return None
        self._state.status = "active"
        return self._state

    @property
    def state(self):
        return self._state

    def set(self, goal, *, max_turns=None, contract=None):
        self._state = _State(goal)
        self._state.contract = contract
        return self._state

    def set_contract(self, contract):
        self._state.contract = contract
        return self._state

    def status_line(self):
        return f"⊙ Goal (active, 0/20 turns): {self._state.goal}"

    def render_contract(self):
        c = self._state.contract
        return "(no contract)" if c is None or c.is_empty() else " ".join(f"{k}={v}" for k, v in c.to_dict().items())

    def add_subgoal(self, text):
        self.subgoals.append(text)
        return f"Added subgoal {len(self.subgoals)}: {text}"

    def remove_subgoal(self, n):
        return f"Removed subgoal {n}: {self.subgoals.pop(n - 1)}"

    def clear_subgoals(self):
        n = len(self.subgoals)
        self.subgoals.clear()
        return n

    def render_subgoals(self):
        return "\n".join(f"- {i + 1}. {s}" for i, s in enumerate(self.subgoals))

    def add_gate(self, command, **_):
        if not self.has_goal():
            raise RuntimeError("no active goal")
        self.gates.append(command)
        return types.SimpleNamespace(command=command)

    def remove_gate(self, n):
        return f"Removed gate {n}: {self.gates.pop(n - 1)}"

    def clear_gates(self):
        n = len(self.gates)
        self.gates.clear()
        return n

    def render_gates(self):
        return "\n".join(f"- {i + 1}. $ {g}" for i, g in enumerate(self.gates))

    def wait_on(self, pid, reason=""):
        self.waiting = True
        return self._state

    def stop_waiting(self):
        was = self.waiting
        self.waiting = False
        return was


@pytest.fixture
def plugin(monkeypatch):
    fake = types.ModuleType("hermes_cli.goals")
    fake.GoalManager = FakeGoalManager
    fake.GoalContract = _Contract
    fake.draft_contract = lambda objective, timeout=None: _Contract(outcome=f"OUT:{objective}", verification="pytest passes")
    fake.parse_contract = lambda text: (text, _Contract())
    pkg = types.ModuleType("hermes_cli")
    pkg.goals = fake
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.goals", fake)
    # a fake tui_gateway.server session table: short id → session_key
    server = types.ModuleType("tui_gateway.server")
    server._sessions = {"abc123": {"session_key": "20260902_015412_31226c"}}
    tg = types.ModuleType("tui_gateway")
    tg.server = server
    monkeypatch.setitem(sys.modules, "tui_gateway", tg)
    monkeypatch.setitem(sys.modules, "tui_gateway.server", server)
    monkeypatch.syspath_prepend(str(PLUGIN_DIR.parent))
    FakeGoalManager.instances.clear()
    from _plugin_loader import load_plugin

    return load_plugin("aura-goals")


class Ctx:
    def __init__(self):
        self.tools = {}
        self.commands = {}
        self.hooks = {}

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_tool(self, name, toolset, schema, handler, **_):
        self.tools[name] = handler


def test_split_session(plugin):
    assert plugin.split_session("@abc123 draft build x") == ("abc123", "draft build x")
    assert plugin.split_session("draft build x") == (None, "draft build x")
    assert plugin.split_session("@s1") == ("s1", "")


def test_registers_only_jgoal(plugin):
    ctx = Ctx()
    plugin.register(ctx)
    assert list(ctx.commands) == ["jgoal"]  # /subgoal is a reserved built-in


def test_crew_budget_is_wired_and_jgoal_crew_stop_reaches_it(plugin):
    ctx = Ctx()
    plugin.register(ctx)
    assert set(ctx.hooks) == {"subagent_start", "pre_tool_call"}
    jgoal = ctx.commands["jgoal"]
    assert "no helpers" in jgoal("@g1 crew stop")
    ctx.hooks["subagent_start"][0](parent_session_id="g1", child_session_id="c1")
    assert "Stopping" in jgoal("@g1 crew stop")
    refused = ctx.hooks["pre_tool_call"][0](tool_name="web_search", args={}, session_id="c1")
    assert refused and refused["action"] == "block"


def test_the_agent_has_a_goal_tool_of_its_own(plugin):
    """`/goal` is a slash command the PERSON types; JARVIS, asked to "start a goal",
    rightly said it had no way to (2026-09-09). The tools are that way, keyed by
    the session the gateway hands the tool."""
    ctx = Ctx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"goal_set", "goal_status", "goal_clear", "goal_pause", "goal_resume",
                              "session_delete", "chat_model_set"}  # the last two: T-1325
    FakeGoalManager.instances.clear()
    out = ctx.tools["goal_set"]({"objective": "ship the widget", "gates": ["make test"]}, session_id="abc123")
    assert "Goal set" in out and "ship the widget" in out and "Gate added: `make test`" in out
    assert "20260902_015412_31226c" in FakeGoalManager.instances  # keyed like the loop, from the gateway's table
    assert "ship the widget" in ctx.tools["goal_status"]({}, session_id="abc123")
    assert ctx.tools["goal_clear"]({}, session_id="abc123") == "Goal cleared."


def test_session_id_maps_to_gateway_session_key(plugin):
    ctx = Ctx()
    plugin.register(ctx)
    out = ctx.commands["jgoal"]("@abc123 draft ship the widget")
    assert "⊙ Goal set" in out and "OUT:ship the widget" in out
    assert "20260902_015412_31226c" in FakeGoalManager.instances  # keyed like the loop
    assert "abc123" not in FakeGoalManager.instances


def test_subgoal_and_gate_grammar(plugin):
    ctx = Ctx()
    plugin.register(ctx)
    j = ctx.commands["jgoal"]
    assert "No active goal" in j("@zzz subgoal add tests")
    j("@zzz draft do the thing")
    assert j("@zzz subgoal add tests") == "Added subgoal 1: add tests"
    assert j("@zzz subgoal list") == "- 1. add tests"
    assert "Gate added: `pytest -q`" in j("@zzz gate add pytest -q")
    assert j("@zzz gate list") == "- 1. $ pytest -q"
    assert "1. $ pytest -q" in j("@zzz show") and "- 1. add tests" in j("@zzz show")
    assert j("@zzz gate remove 1").startswith("Removed gate 1")
    assert j("@zzz subgoal clear") == "Removed 1 subgoal(s)."
    assert "usage:" in j("@zzz gate bogus")


def test_contract_field_and_wait(plugin):
    ctx = Ctx()
    plugin.register(ctx)
    j = ctx.commands["jgoal"]
    j("@w draft fix the build")
    assert "verification=pytest -q passes" in j("@w contract verification: pytest -q passes")
    assert "usage" in j("@w contract nonsense: x")
    assert j("@w wait 4242 waiting on CI").startswith("⏳ Goal parked on pid 4242")
    assert j("@w unwait") == "↻ Goal continues."
    assert j("@w unwait") == "Goal was not waiting."


def _goal_state(session_key="20260902_015412_31226c"):
    return FakeGoalManager.instances[session_key].state


def test_goal_pause_stops_the_goal_and_goal_resume_restarts_it(plugin):
    """T-1315: the console's `/goal pause` and `/goal resume`, from inside a turn: the same
    GoalManager calls, keyed by the session the gateway hands the tool."""
    ctx = Ctx()
    plugin.register(ctx)
    ctx.tools["goal_set"]({"objective": "ship the widget", "gates": ["make test"]}, session_id="abc123")
    out = ctx.tools["goal_pause"]({}, session_id="abc123")
    assert out == "⏸ Goal paused: ship the widget"
    assert _goal_state().status == "paused"
    assert FakeGoalManager.instances["20260902_015412_31226c"].gates == ["make test"], "a pause keeps its gates"
    out = ctx.tools["goal_resume"]({}, session_id="abc123")
    assert out.startswith("▶ Goal resumed: ship the widget")
    assert _goal_state().status == "active"


def test_goal_pause_and_resume_with_nothing_to_pause_change_nothing(plugin):
    ctx = Ctx()
    plugin.register(ctx)
    assert ctx.tools["goal_pause"]({}, session_id="abc123") == "No active goal."
    assert ctx.tools["goal_resume"]({}, session_id="abc123") == "No goal to resume."
    ctx.tools["goal_set"]({"objective": "ship the widget"}, session_id="abc123")
    assert "already running" in ctx.tools["goal_resume"]({}, session_id="abc123")
    ctx.tools["goal_pause"]({}, session_id="abc123")
    assert "already paused" in ctx.tools["goal_pause"]({}, session_id="abc123")
    assert _goal_state().status == "paused"


def test_goal_resume_never_revives_a_goal_that_is_done_or_cleared(plugin):
    """Hermes' own `resume()` flips any state to active, so a finished goal (and its spent
    gates) would run again. A model asking to "resume" must not bring one back."""
    ctx = Ctx()
    plugin.register(ctx)
    ctx.tools["goal_set"]({"objective": "ship the widget"}, session_id="abc123")
    _goal_state().status = "done"
    assert ctx.tools["goal_resume"]({}, session_id="abc123") == "No goal to resume."
    assert ctx.tools["goal_pause"]({}, session_id="abc123") == "No active goal."
    assert _goal_state().status == "done"
    ctx.tools["goal_clear"]({}, session_id="abc123")
    assert ctx.tools["goal_resume"]({}, session_id="abc123") == "No goal to resume."


def test_goal_pause_and_resume_need_a_session_and_ignore_arguments(plugin):
    """No session is a refusal even when some other session has a goal running (the
    most-recent fallback `goal_clear` has would pause someone else's); arguments the model
    invents (a goal text, a reset) change nothing: the tools take none."""
    ctx = Ctx()
    plugin.register(ctx)
    ctx.tools["goal_set"]({"objective": "ship the widget"}, session_id="abc123")
    assert "No session" in ctx.tools["goal_pause"]({})
    assert "No session" in ctx.tools["goal_resume"]({})
    assert _goal_state().status == "active"
    ctx.tools["goal_pause"]({"objective": "something else", "reset_budget": True}, session_id="abc123")
    assert _goal_state().goal == "ship the widget"
    for name in ("goal_pause", "goal_resume"):
        assert plugin.goals.SCHEMAS[name]["parameters"]["properties"] == {}


def test_handler_never_raises(plugin):
    ctx = Ctx()
    plugin.register(ctx)
    j = ctx.commands["jgoal"]
    FakeGoalManager.instances["boom"] = types.SimpleNamespace()  # a broken entry → attribute errors inside
    assert j("@boom show").startswith("jgoal error:")


def _ask(plugin, monkeypatch, tool, session="abc123"):
    """The matrix gate's verdict for a goal tool, with this plugin's describers registered (T-1329)."""
    from _shared import _approval_hook

    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: None)
    monkeypatch.setattr(_approval_hook, "CHANGE_DESCRIBERS", {})
    plugin.register(Ctx())
    return _approval_hook.matrix_gate()(tool_name=tool, args={}, session_id=session)


def test_the_pause_and_resume_cards_show_the_run_state_now_and_after(plugin, monkeypatch):
    from _shared import _messages

    ctx = Ctx()
    plugin.register(ctx)
    ctx.tools["goal_set"]({"objective": "ship the widget"}, session_id="abc123")
    v = _ask(plugin, monkeypatch, "goal_pause")
    assert v["action"] == "approve"
    assert _messages.parse_change(v["message"]) == {
        "key": "run state of the standing goal", "before": "active", "after": "paused"}
    ctx.tools["goal_pause"]({}, session_id="abc123")
    v = _ask(plugin, monkeypatch, "goal_resume")
    assert _messages.parse_change(v["message"])["before"] == "paused"
    assert _messages.parse_change(v["message"])["after"] == "active"


def test_a_card_with_nothing_to_change_shows_no_lines_and_is_still_asked(plugin, monkeypatch):
    from _shared import _messages

    # No goal, then a goal already in the state asked for, then a finished one, then no session.
    for setup, tool, session in (
        (None, "goal_pause", "abc123"),
        ("active", "goal_resume", "abc123"),
        ("done", "goal_pause", "abc123"),
        (None, "goal_pause", ""),
    ):
        FakeGoalManager.instances.clear()
        if setup:
            ctx = Ctx()
            plugin.register(ctx)
            ctx.tools["goal_set"]({"objective": "ship the widget"}, session_id="abc123")
            _goal_state().status = setup
        v = _ask(plugin, monkeypatch, tool, session)
        assert v["action"] == "approve", (setup, tool)
        assert _messages.parse_change(v["message"]) is None, (setup, tool)
        assert _messages.parse_card(v["message"]) is not None


def test_a_goal_manager_that_raises_still_asks_with_no_lines(plugin, monkeypatch):
    from _shared import _messages

    FakeGoalManager.instances["20260902_015412_31226c"] = types.SimpleNamespace()  # no has_goal()
    v = _ask(plugin, monkeypatch, "goal_pause")
    assert v["action"] == "approve" and _messages.parse_change(v["message"]) is None
    assert plugin.goals.change_for("goal_set", {}, "abc123") is None
