"""jarvis-activity — the live view of what the browser and the desktop are doing.

Registers two observers and no tool: the ``pre_tool_call`` hook that opens an
activity the first time a visual tool (the browser toolset, ``computer_use``,
the ``desktop_*`` tools) is called in a session, and the ``post_tool_call``
hook that turns each call into a step and, when the result carries a picture or
a page, a frame. See :mod:`activity` for the throttle and the redaction, and
``tests/contracts/activity-frames.json`` for the wire shape every reader pins.

The pre hook returns ``None`` for every call: it never blocks, asks or modifies.
Hermes takes the first directive any plugin returns, so a hook that answered
anything here would be a second approval gate beside jarvis-guard's.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from . import activity  # noqa: E402

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", activity.pre_tool_call)
    ctx.register_hook("post_tool_call", activity.post_tool_call)
