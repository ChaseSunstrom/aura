"""jarvis-personas — which of the two is primary, which theme is worn, and how the screens learn it.

Registers the ``jarvis-personas`` toolset (``persona_switch`` / ``persona_status``,
``theme_draft`` / ``theme_apply``),
the ``/persona`` command, a system-prompt section carrying the active persona's
soul, and a ``pre_llm_call`` hook for sessions whose prompt predates a switch.
:mod:`personas` says why SOUL.md is never rewritten.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared._approval_hook import guarded_command, split_session  # noqa: E402
from . import personas  # noqa: E402

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    for name, schema in personas.SCHEMAS.items():
        ctx.register_tool(name=name, toolset=personas.TOOLSET, schema=schema,
                          handler=personas.HANDLERS[name], emoji="◐")

    def _persona(raw_args: str) -> str:
        # The console prefixes every plugin command with @<session_id>; a switch
        # is global, so the prefix is accepted and ignored.
        _sid, rest = split_session(raw_args)
        return personas.handle_command(rest)

    ctx.register_command(
        "persona",
        handler=guarded_command(_persona, "persona", logger=logger),
        description="Who is primary: /persona status | /persona <AURA|JARVIS>",
        args_hint="[@<session_id>] status | <name>",
    )
    # The soul of whoever is primary, frozen into each new session's prompt;
    # the hook covers a session rendered before a switch. Both are optional on
    # a Hermes without them, so the tools still register.
    if hasattr(ctx, "register_system_prompt_section"):
        ctx.register_system_prompt_section("jarvis-persona", personas.render_section, position="after_memory")
    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_llm_call", personas.pre_llm_call)
