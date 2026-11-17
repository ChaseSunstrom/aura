"""jarvis-emergency: the owner's emergency contacts, and the alert that texts and calls them.

Registers the ``emergency`` toolset. ``emergency_contacts_list`` reads; ``emergency_contacts_set`` asks
and is sensitive (the phone's fingerprint, ``_shared/_matrix.py``): a prompt cannot add a number.
``emergency_alert`` is free because it is bounded: it names no recipient, reaches the stored list only,
runs behind the phone's cancellable countdown and fires once in ten minutes. The phone is the sender;
the contract is ``tests/contracts/emergency.json``. Not covered: a house alarm or a fall starting it
(T-1013), and the emergency number, which this never contacts.
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

TOOLSET = "emergency"


def register(ctx) -> None:
    for name, schema, handler in (
        ("emergency_contacts_list", schemas.EMERGENCY_CONTACTS_LIST, tools.handle_contacts_list),
        ("emergency_contacts_set", schemas.EMERGENCY_CONTACTS_SET, tools.handle_contacts_set),
        ("emergency_alert", schemas.EMERGENCY_ALERT, tools.handle_alert),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_house.configured, emoji="🆘")
