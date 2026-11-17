"""Command handlers for /jgoal and /subgoal.

Pure functions over ``hermes_cli.goals.GoalManager``; the plugin's
``register()`` wraps them. Every handler returns the text the surface shows.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# The replies reach the console and the chat platforms alike, so their
# lifecycle glyphs are the shared templates' (which are Hermes' own, pinned):
# the plugins root is on sys.path by the time this module is imported — the
# package's __init__ puts it there before importing this.
from _shared import _messages


def _goals():
    """Import Hermes' goal module lazily so the plugin package imports without Hermes on the path."""
    from hermes_cli import goals as mod

    return mod

USAGE_JGOAL = (
    "usage: /jgoal @<session_id> draft <objective> | show | contract <field>: <text> | "
    "subgoal <text> | subgoal list | subgoal remove <n> | subgoal clear | "
    "gate add <command> | gate list | gate remove <n> | gate clear | wait <pid> [reason] | unwait"
)
USAGE_SUBGOAL = "usage: /jgoal @<session_id> subgoal <text> | subgoal list | subgoal remove <n> | subgoal clear"

_FIELDS = {"outcome", "verification", "constraints", "boundaries", "stop_when"}


def _most_recent_active_session() -> Optional[str]:
    """Fallback when the caller gave no @session: the newest active goal."""
    try:
        from hermes_state import SessionDB

        db = SessionDB()
        rows = db.list_state_meta_prefix("goal:") if hasattr(db, "list_state_meta_prefix") else []
    except Exception:
        return None
    best: tuple[float, str] | None = None
    for key, value in rows or []:
        sid = str(key).split(":", 1)[-1]
        try:
            import json

            data = json.loads(value)
        except Exception:
            continue
        if data.get("status") != "active":
            continue
        ts = float(data.get("updated_at") or data.get("created_at") or 0)
        if best is None or ts > best[0]:
            best = (ts, sid)
    return best[1] if best else None


def _session_key(sid: str) -> str:
    """Map a TUI-gateway session id to the key the goal loop stores state under.

    The gateway keeps goal state under ``session["session_key"]``; the apps only
    know the short id from ``session.create``. Resolve it from the live session
    table when this plugin runs inside ``hermes serve``; otherwise the id itself
    is the key (CLI / messaging gateway sessions).
    """
    try:
        from tui_gateway.server import _sessions

        entry = _sessions.get(sid)
        if isinstance(entry, dict) and entry.get("session_key"):
            return str(entry["session_key"])
    except Exception:
        pass
    return sid


def _manager(sid: Optional[str]) -> tuple[Optional[Any], str]:
    sid = sid or _most_recent_active_session()
    if not sid:
        return None, "No session given (prefix the argument with @<session_id>) and no active goal found."
    return _goals().GoalManager(session_id=_session_key(sid)), ""


def handle_jgoal(sid: Optional[str], rest: str) -> str:
    if not rest:
        return USAGE_JGOAL
    mgr, err = _manager(sid)
    if mgr is None:
        return err
    head, _, tail = rest.partition(" ")
    head = head.lower()
    tail = tail.strip()

    if head == "draft":
        if not tail:
            return "usage: /jgoal @<sid> draft <objective>"
        g = _goals()
        contract = g.draft_contract(tail)
        if contract is None or contract.is_empty():
            # Drafting never blocks setting a goal (same rule as the CLI).
            headline, inline = g.parse_contract(tail)
            state = mgr.set(headline or tail, contract=inline)
            return _messages.goal(
                "set", f"Goal set (free-form; drafting unavailable) ({state.max_turns}-turn budget): {state.goal}")
        state = mgr.set(tail, contract=contract)
        return (_messages.goal("set", f"Goal set ({state.max_turns}-turn budget): {state.goal}") + "\n"
                f"{mgr.render_contract()}\n"
                "Send any message (or /goal resume) to start the first turn.")

    if head == "show":
        if not mgr.has_goal():
            return "No active goal."
        return f"{mgr.status_line()}\n{mgr.render_contract()}\n{mgr.render_subgoals()}\n{mgr.render_gates()}".strip()

    if head == "contract":
        m = re.match(r"^(\w+)\s*:\s*(.+)$", tail, re.S)
        if not m or m.group(1).lower() not in _FIELDS:
            return f"usage: /jgoal @<sid> contract <{'|'.join(sorted(_FIELDS))}>: <text>"
        if not mgr.has_goal():
            return "No active goal."
        GoalContract = _goals().GoalContract
        current = mgr.state.contract if mgr.state and getattr(mgr.state, "contract", None) else GoalContract()
        data = current.to_dict() if hasattr(current, "to_dict") else {}
        data[m.group(1).lower()] = m.group(2).strip()
        mgr.set_contract(GoalContract.from_dict(data))
        return f"Contract updated.\n{mgr.render_contract()}"

    if head == "gate":
        sub, _, arg = tail.partition(" ")
        sub = sub.lower()
        arg = arg.strip()
        if sub == "add":
            if not arg:
                return "usage: /jgoal @<sid> gate add <shell command>"
            try:
                gate = mgr.add_gate(arg)
            except RuntimeError as exc:
                return str(exc)
            return f"Gate added: `{getattr(gate, 'command', arg)}` (must exit 0 before the goal can be judged done)\n{mgr.render_gates()}"
        if sub in ("", "list"):
            return mgr.render_gates() if mgr.has_goal() else "No active goal."
        if sub == "remove":
            try:
                return mgr.remove_gate(int(arg))
            except (ValueError, RuntimeError) as exc:
                return f"usage: /jgoal @<sid> gate remove <n> ({exc})"
        if sub == "clear":
            n = mgr.clear_gates()
            return f"Removed {n} gate(s)."
        return "usage: /jgoal @<sid> gate add <cmd> | list | remove <n> | clear"

    if head == "subgoal":
        return handle_subgoal(sid, tail)

    if head == "wait":
        pid_s, _, reason = tail.partition(" ")
        try:
            pid = int(pid_s)
        except ValueError:
            return "usage: /jgoal @<sid> wait <pid> [reason]"
        state = mgr.wait_on(pid, reason.strip())
        return _messages.goal("parked", f"Goal parked on pid {pid}: {state.goal}")

    if head == "unwait":
        # "Continues", not "wait barrier cleared": the person understands a goal
        # going on, not the loop's internal gate (GLASS_HUD §11).
        return _messages.goal("continuing", "Goal continues.") if mgr.stop_waiting() else "Goal was not waiting."

    return USAGE_JGOAL


def handle_subgoal(sid: Optional[str], rest: str) -> str:
    mgr, err = _manager(sid)
    if mgr is None:
        return err
    if not mgr.has_goal():
        return "No active goal. Set one with /goal <text> first."
    if not rest or rest.strip().lower() == "list":
        return mgr.render_subgoals() or "No subgoals."
    head, _, tail = rest.partition(" ")
    if head.lower() == "remove":
        try:
            return mgr.remove_subgoal(int(tail.strip()))
        except ValueError:
            return USAGE_SUBGOAL
    if head.lower() == "clear":
        return f"Removed {mgr.clear_subgoals()} subgoal(s)."
    return mgr.add_subgoal(rest)


# --------------------------------------------------------------------------- #
# Tools: the goal as JARVIS can set it from inside a turn
# --------------------------------------------------------------------------- #

TOOLSET = "aura-goals"

#: What the agent is told the tools do. Plain: ``/goal`` is a slash command the
#: PERSON types in the TUI, never something the agent can issue — SOUL.md rule 6
#: said "set it with /goal" and JARVIS, correctly, answered that it had no way
#: to (2026-09-09: "the /goal command is a session feature that users invoke
#: directly, not something I can trigger through tools"). These are the way.
SCHEMAS: dict[str, dict] = {
    "goal_set": {
        "name": "goal_set",
        "description": (
            "Set a standing goal for this session: work that outlives the turn. The goal loop "
            "then judges each turn boundary and continues until the goal is achieved, paused or "
            "cleared. Give the objective as one sentence; add shell gates that must exit 0 before "
            "the goal can be judged done. Drafted into a contract (outcome, acceptance, out of "
            "scope) unless draft is false."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "What done looks like, in one sentence."},
                "gates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Shell commands that must exit 0 before the goal can be judged achieved.",
                },
                "draft": {"type": "boolean", "description": "Draft a contract first (default true)."},
            },
            "required": ["objective"],
        },
    },
    "goal_status": {
        "name": "goal_status",
        "description": "The active goal for this session, its contract, subgoals and gates — or that there is none.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "goal_clear": {
        "name": "goal_clear",
        "description": "Clear this session's goal: the loop stops judging and continuing.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "goal_pause": {
        "name": "goal_pause",
        "description": (
            "Pause this session's goal: the loop stops continuing it until it is resumed. The "
            "contract, subgoals and gates are kept. Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "goal_resume": {
        "name": "goal_resume",
        "description": (
            "Resume this session's paused goal: the loop picks it up at the end of this turn with a "
            "fresh turn budget. A goal that is done or cleared is not brought back; set a new one. "
            "Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _tool_manager(kwargs: dict) -> tuple[Optional[Any], str]:
    # The gateway hands a tool its session as `session_id`; the goal loop keys
    # state by the session_key, which `_session_key` resolves from the live
    # session table (and passes through unchanged for CLI/messaging sessions).
    sid = kwargs.get("session_id") or kwargs.get("session_key")
    return _manager(str(sid) if sid else None)


def handle_goal_set(args: dict, **kwargs) -> str:
    objective = str((args or {}).get("objective") or "").strip()
    if not objective:
        return "goal_set needs an objective."
    mgr, err = _tool_manager(kwargs)
    if mgr is None:
        return err
    out = handle_jgoal(None, f"draft {objective}") if (args or {}).get("draft", True) else ""
    if not (args or {}).get("draft", True):
        state = mgr.set(objective)
        out = _messages.goal("set", f"Goal set ({state.max_turns}-turn budget): {state.goal}")
    else:
        # `handle_jgoal(None, …)` found the manager by most-recent-active; the
        # tool knows its session, so drive that manager instead.
        g = _goals()
        contract = g.draft_contract(objective)
        if contract is None or contract.is_empty():
            headline, inline = g.parse_contract(objective)
            state = mgr.set(headline or objective, contract=inline)
            out = _messages.goal("set", f"Goal set (free-form; drafting unavailable) ({state.max_turns}-turn budget): {state.goal}")
        else:
            state = mgr.set(objective, contract=contract)
            out = _messages.goal("set", f"Goal set ({state.max_turns}-turn budget): {state.goal}") + "\n" + mgr.render_contract()
    for command in (args or {}).get("gates") or []:
        command = str(command).strip()
        if not command:
            continue
        try:
            mgr.add_gate(command)
            out += f"\nGate added: `{command}`"
        except RuntimeError as exc:
            out += f"\nGate not added: {exc}"
    return out + "\nThe loop judges at the end of this turn and continues from there."


def handle_goal_status(args: dict, **kwargs) -> str:
    del args
    mgr, err = _tool_manager(kwargs)
    if mgr is None:
        return err
    if not mgr.has_goal():
        return "No active goal."
    return f"{mgr.status_line()}\n{mgr.render_contract()}\n{mgr.render_subgoals()}\n{mgr.render_gates()}".strip()


def handle_goal_clear(args: dict, **kwargs) -> str:
    del args
    mgr, err = _tool_manager(kwargs)
    if mgr is None:
        return err
    if not mgr.has_goal():
        return "No active goal."
    mgr.clear()
    return "Goal cleared."


def _own_goal(kwargs: dict) -> Optional[Any]:
    """The manager of the calling session only. Not `_tool_manager`: its most-recent-active
    fallback would pause or resume a goal that belongs to another session."""
    sid = kwargs.get("session_id") or kwargs.get("session_key")
    return _goals().GoalManager(session_id=_session_key(str(sid))) if sid else None


def handle_goal_pause(args: dict, **kwargs) -> str:
    """What the console's `/goal pause` does (Hermes' `mgr.pause`), minus its arguments."""
    del args
    mgr = _own_goal(kwargs)
    if mgr is None:
        return "No session to pause a goal in."
    if not mgr.has_goal():
        return "No active goal."
    if mgr.state.status == "paused":
        return f"Goal is already paused: {mgr.state.goal}"
    state = mgr.pause(reason="paused on request")
    return f"⏸ Goal paused: {state.goal}"


def handle_goal_resume(args: dict, **kwargs) -> str:
    """What the console's `/goal resume` does to the state (Hermes' `mgr.resume`). The console
    then sends a continuation message; a tool cannot start a turn, so the loop's own judge at
    the end of this one does. Hermes' resume would also revive a done goal: not here."""
    del args
    mgr = _own_goal(kwargs)
    if mgr is None:
        return "No session to resume a goal in."
    if not mgr.has_goal():
        return "No goal to resume."
    if mgr.state.status != "paused":
        return f"Goal is already running: {mgr.state.goal}"
    state = mgr.resume()
    return f"▶ Goal resumed: {state.goal}\nThe loop continues it at the end of this turn."


def change_for(tool_name: str, args: dict, session: str = "") -> Optional[dict]:
    """The approval card's Now and Becomes for ``goal_pause`` and ``goal_resume`` (T-1329), or None.

    The goal's run state is read from the calling session's own manager, as the handler reads it
    (a local read, no call out). ``known`` is false when the session has no goal, the state is
    unreadable, or the call would change nothing (pausing a paused goal): the card then shows no
    values. Never raises. Not claimed: that the state is still this one when the person answers.
    """
    if tool_name not in ("goal_pause", "goal_resume"):
        return None
    key = "run state of the standing goal"
    try:
        mgr = _own_goal({"session_id": session})
        if mgr is None or not mgr.has_goal():
            return {"key": key, "known": False}
        now = str(mgr.state.status or "").strip()
    except Exception:
        return {"key": key, "known": False}
    after = "paused" if tool_name == "goal_pause" else "active"
    return {"key": key, "before": now, "after": after, "known": bool(now) and now != after, "secret": False}


HANDLERS = {"goal_set": handle_goal_set, "goal_status": handle_goal_status, "goal_clear": handle_goal_clear,
            "goal_pause": handle_goal_pause, "goal_resume": handle_goal_resume}
