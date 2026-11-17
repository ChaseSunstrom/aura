"""jarvis-calls: the owner's phone calls, placed, answered, declined, screened and summarised.

Registers the ``calls`` toolset. ``calls_status`` is a reader of a stranger's words (listed in
``_shared/_taint.py`` READERS); ``calls_place``, ``calls_answer``, ``calls_decline``, ``calls_back``
and ``calls_rule`` ask (``_shared/_matrix.py``); ``calls_later`` and ``calls_summary`` only write a
bounded note in jarvis-core's memory. The phone is the line and applies its own tiers; the contract is
``tests/contracts/call-event.json``.
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

TOOLSET = "calls"


def register(ctx) -> None:
    for name, schema, handler in (
        ("calls_status", schemas.CALLS_STATUS, tools.handle_status),
        ("calls_place", schemas.CALLS_PLACE, tools.handle_place),
        ("calls_answer", schemas.CALLS_ANSWER, tools.handle_answer),
        ("calls_decline", schemas.CALLS_DECLINE, tools.handle_decline),
        ("calls_back", schemas.CALLS_BACK, tools.handle_back),
        ("calls_later", schemas.CALLS_LATER, tools.handle_later),
        ("calls_rule", schemas.CALLS_RULE, tools.handle_rule),
        ("calls_summary", schemas.CALLS_SUMMARY, tools.handle_summary),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_house.configured, emoji="📞")
