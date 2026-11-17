"""aura-connect: connect the owner's accounts (Google, GitHub, Microsoft 365, Slack, Discord,
GitLab, Spotify, Notion, Todoist), read-only apart from one write: adding a Todoist task.

Registers the ``connect`` toolset: ``connect_start`` and ``connect_revoke`` (both
ask, see ``_shared/_matrix.py``), ``connect_status`` (read-only), ``connect_read`` (one
named read-only call of a connected account; its text is marked as outside text) and
``connect_write`` (the one named write, Todoist add-task; asks every time). The OAuth
flow, the encrypted tokens and the refresh all live in jarvis-core; this plugin
only asks it, so a token never reaches the model.
"""
from __future__ import annotations

import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_dav.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared import _house  # noqa: E402
from . import schemas, tools  # noqa: E402

TOOLSET = "connect"


def register(ctx) -> None:
    for name, schema, handler in (
        ("connect_start", schemas.CONNECT_START, tools.handle_start),
        ("connect_status", schemas.CONNECT_STATUS, tools.handle_status),
        ("connect_revoke", schemas.CONNECT_REVOKE, tools.handle_revoke),
        ("connect_read", schemas.CONNECT_READ, tools.handle_read),
        ("connect_write", schemas.CONNECT_WRITE, tools.handle_write),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_house.configured, emoji="🔗")
