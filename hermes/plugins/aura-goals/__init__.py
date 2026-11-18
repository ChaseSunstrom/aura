"""aura-goals — the /goal sub-commands the TUI-gateway command path lacks.

Verified on Hermes v0.21.0: ``command.dispatch`` routes ``/goal`` to a handler
that knows ``status | pause | resume | clear | <text>`` only. ``/goal draft``,
``/goal show``, ``/goal gate …``, ``/goal wait`` and ``/subgoal`` exist on the
CLI and the messaging gateway but not on that path. Plugin commands *are*
routed, so this plugin registers ``/jgoal`` and ``/subgoal`` and drives the
same ``hermes_cli.goals.GoalManager`` (state lives in SessionDB.state_meta and
every GoalManager re-reads it, so the running loop sees the change on its next
turn boundary).

Plugin command handlers receive only the raw argument string, never the
session, so callers prefix the argument with ``@<session_id>`` (the TUI-gateway
session id the app got from ``session.create``). Goal state is keyed by that
session's ``session_key``; the plugin resolves it in-process from the gateway's
session table and falls back to using the id as the key.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared import _approval_hook, _browser_flush  # noqa: E402
from _shared._approval_hook import guarded_command, split_session  # noqa: E402
from _shared._matrix import read_only_tools  # noqa: E402
from .crew import CrewBudget  # noqa: E402
from .goals import HANDLERS, SCHEMAS, TOOLSET, handle_jgoal  # noqa: E402
from .goals import change_for as goals_change_for  # noqa: E402
from . import sessions  # noqa: E402

__all__ = ["register", "sessions", "split_session"]

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    # A goal's next turn finds its sign-in (T-1545): Hermes stops the browser at every turn end,
    # and without a clean close Chromium lost the last half-minute of cookies.
    _browser_flush.install()
    # The tools: what the agent uses. The command below is what the console
    # dispatches on the person's behalf.
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=HANDLERS[name], emoji="⊙")
    # The chat's own two buttons (T-1325): same gateway methods the console sends.
    for name, schema in sessions.SCHEMAS.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=sessions.HANDLERS[name], emoji="💬")

    # The model switch's card shows the model now and the one asked for (T-1329).
    _approval_hook.CHANGE_DESCRIBERS["chat_model_set"] = sessions.change_for
    # So does the goal's pause and resume: its run state now and after.
    for tool in ("goal_pause", "goal_resume"):
        _approval_hook.CHANGE_DESCRIBERS[tool] = goals_change_for

    # One budget for every helper a goal's turn delegates to (crew.py): Hermes runs
    # pre_tool_call for children's calls too, so refusing there reaches the whole crew.
    # Worker belts from the approval matrix itself, so a tool reclassified there moves belts too:
    # a researcher reads; a planner reads and keeps the task list and the board.
    reads = read_only_tools()
    crew = CrewBudget(belts={
        "researcher": reads,
        "planner": reads | {"todo", "kanban_create", "kanban_link", "kanban_comment"},
    })
    ctx.register_hook("subagent_start", crew.subagent_start)
    ctx.register_hook("pre_tool_call", crew.pre_tool_call)

    def _jgoal(raw_args: str) -> str:
        sid, rest = split_session(raw_args)
        if rest.strip() == "crew stop":
            return ("Stopping every helper of this goal at its next step." if crew.stop(sid)
                    else "This goal has no helpers running.")
        return handle_jgoal(sid, rest)

    # `/subgoal` is a built-in name on the messaging gateway, so a plugin may not
    # register it (the registration is silently rejected). Subgoals live under
    # /jgoal instead: `/jgoal @<sid> subgoal <text> | list | remove <n> | clear`.
    ctx.register_command(
        "jgoal",
        handler=guarded_command(_jgoal, "jgoal", logger=logger),
        description=("JARVIS goal controls: draft <text> | show | subgoal <text>|list|remove <n>|clear | "
                     "gate add <cmd>|list|remove <n>|clear | wait <pid> | unwait | contract <field>: <text> | "
                     "crew stop"),
        args_hint="@<session_id> <subcommand …>",
    )
