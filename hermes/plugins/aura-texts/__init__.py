"""aura-texts: the owner's mirrored texts, read, search and reply.

Registers the ``texts`` toolset: ``texts_read`` and ``texts_search`` (read-only; their results are
other people's words, listed in ``_shared/_taint.py`` READERS) and ``texts_reply`` (asks every time,
see ``_shared/_matrix.py``). The window lives in jarvis-core and the send happens on the phone; this
plugin only asks them.
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

TOOLSET = "texts"


def register(ctx) -> None:
    for name, schema, handler in (
        ("texts_read", schemas.TEXTS_READ, tools.handle_read),
        ("texts_search", schemas.TEXTS_SEARCH, tools.handle_search),
        ("texts_reply", schemas.TEXTS_REPLY, tools.handle_reply),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_house.configured, emoji="💬")
