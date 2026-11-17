"""jarvis-jellyfin — the household media server, browsable and controllable.

Registers the ``jellyfin`` toolset (``jellyfin_libraries`` / ``jellyfin_search``
/ ``jellyfin_resume`` / ``jellyfin_sessions`` read-only, ``jellyfin_play``
behind the approval gate), the ``/jellyfin`` slash command, and the
``pre_tool_call`` hook. The HTTP work lives in :mod:`tools`.

Every tool needs the API key as well as the URL, so one ``check_fn`` covers all
five: a server URL with no token would offer tools that can only ever answer
401.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from aura_schema import from_env

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared._approval_hook import approval_gate, guarded_command  # noqa: E402
from . import schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

TOOLSET = "jellyfin"


def _available() -> bool:
    cfg = from_env()
    return bool(cfg.jellyfin_url and cfg.jellyfin_token)


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped JELLYFIN_* value names itself at load
    for name, schema, handler, emoji in (
        ("jellyfin_libraries", schemas.JELLYFIN_LIBRARIES, tools.handle_libraries, "🎬"),
        ("jellyfin_search", schemas.JELLYFIN_SEARCH, tools.handle_search, "🔎"),
        ("jellyfin_resume", schemas.JELLYFIN_RESUME, tools.handle_resume, "⏯️"),
        ("jellyfin_sessions", schemas.JELLYFIN_SESSIONS, tools.handle_sessions, "📺"),
        ("jellyfin_play", schemas.JELLYFIN_PLAY, tools.handle_play, "▶️"),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_available, emoji=emoji)

    ctx.register_hook("pre_tool_call", approval_gate(tools.approval_for))
    ctx.register_command(
        "jellyfin",
        handler=guarded_command(tools.command, "jellyfin", logger=logger),
        description="Jellyfin libraries and any active playback",
        args_hint="",
    )
