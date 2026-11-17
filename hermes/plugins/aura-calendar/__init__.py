"""aura-calendar — the household calendar, through the house.

Registers the ``calendar`` toolset (``calendar_list_calendars`` / ``calendar_events``
read-only; ``calendar_create_event``, ``calendar_move_event`` and ``calendar_rsvp`` behind the
approval gate), and the
``/calendar`` slash command for the next week. jarvis-core's ``calendar:`` integration is the
one CalDAV client; this plugin calls its ``calendar.*`` services on the house REST
(``HASS_URL`` / ``HASS_TOKEN``), so the brief and the model read one configured calendar.

Nothing here reaches the network at import time, so the package loads — and its tests run —
with no house anywhere, the rule the sibling plugins follow.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from aura_schema import from_env  # noqa: E402

from _shared._approval_hook import approval_gate, guarded_command  # noqa: E402
from . import schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

#: Hermes toolset key. `calendar` is free — Hermes owns `file`, not this.
TOOLSET = "calendar"


def register(ctx) -> None:
    # Read the settings once here, where Hermes reports which plugin failed to load: a
    # mistyped value otherwise first surfaces inside a tool call, looking like a house fault.
    from_env()
    for name, schema, handler in (
        ("calendar_list_calendars", schemas.CALENDAR_LIST, tools.handle_list_calendars),
        ("calendar_events", schemas.CALENDAR_EVENTS, tools.handle_events),
        ("calendar_create_event", schemas.CALENDAR_CREATE, tools.handle_create_event),
        ("calendar_move_event", schemas.CALENDAR_MOVE, tools.handle_move_event),
        ("calendar_rsvp", schemas.CALENDAR_RSVP, tools.handle_rsvp),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=tools.available, emoji="📅")

    ctx.register_hook("pre_tool_call", approval_gate(tools.build_approval))
    ctx.register_command(
        "calendar",
        handler=guarded_command(tools.handle_command, "calendar", logger=logger),
        description="The next 7 days of events, or of one named calendar",
        args_hint="[calendar name]",
    )
