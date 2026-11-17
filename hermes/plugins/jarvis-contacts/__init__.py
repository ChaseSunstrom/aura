"""jarvis-contacts — the household address books over CardDAV.

Registers the ``contacts`` toolset: ``contacts_search`` and ``contacts_get``,
both read-only. There is deliberately **no write tool**. A contact write is not
worth its blast radius: cards are synced to every phone in the house within
minutes and CardDAV has no undo, so a wrong ``FN`` or a clobbered card
propagates faster than anyone can notice it, and the upside — saving a person
one edit in their own address book app — does not pay for that. Rewriting a
card also silently drops every property this parser does not read (PHOTO,
ADDR, BDAY, X-* fields the phone wrote), which is data loss no approval prompt
would describe accurately.

With no writing tool there is no ``pre_tool_call`` hook here; the plugin still
registers cleanly, and the HTTP/XML/vCard work is in ``_shared._dav``, shared
with jarvis-calendar and jarvis-files.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_dav.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from aura_schema import from_env  # noqa: E402

from _shared import _dav  # noqa: E402
from . import schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

#: Hermes toolset key.
TOOLSET = "contacts"


def register(ctx) -> None:
    # Read the settings once here, where Hermes reports which plugin failed to
    # load: a mistyped DAV_VERIFY_SSL otherwise first surfaces inside a tool
    # call, looking like a server fault.
    from_env()
    ctx.register_tool(name="contacts_search", toolset=TOOLSET, schema=schemas.CONTACTS_SEARCH,
                      handler=tools.handle_search, check_fn=_dav.available, emoji="👤")
    ctx.register_tool(name="contacts_get", toolset=TOOLSET, schema=schemas.CONTACTS_GET,
                      handler=tools.handle_get, check_fn=_dav.available, emoji="👤")
