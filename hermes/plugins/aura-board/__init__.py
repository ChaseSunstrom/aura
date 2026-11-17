"""aura-board — the numbers behind the console's Board.

Registers two observers and no tool: after a model call or a tool call, :func:`board.after`
rewrites ``$HERMES_HOME/board/board.json`` from state.db when the last one is ten seconds
old, on a thread of its own. Both hooks return ``None``: they never block, ask or modify.
"""
from __future__ import annotations

import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: board.py reads _shared/_probes.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from . import board  # noqa: E402


def register(ctx) -> None:
    ctx.register_hook("post_llm_call", board.after)
    ctx.register_hook("post_tool_call", board.after)
    board.after()  # a board as soon as Hermes starts, not at the first turn
