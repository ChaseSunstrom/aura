"""The HUD event channel the layout plugin emits on — the TUI gateway's ``_emit``.

Layout events ride the same channel the ``jarvis-hud`` panels do (see that
plugin's ``hud.py`` for the long version of why): a tool handler is called with
the agent's durable ``session_key``, the ``/hud_layout`` command with the short
id the app got from ``session.create``, and ``tui_gateway.server._emit`` routes
by the short id. :func:`canonical_key` folds both onto the durable key so state
one surface wrote is the state the other reads; :func:`emit` maps the durable
key back to the short id at send time. Off the gateway (tests, a bare
``api_server`` run) there is no session table: the key is its own state key and
nothing is delivered, which the tools report as ``delivered: false`` rather than
failing — the layout state is still kept for a client that joins later.

Not a copy of ``jarvis-hud``'s helpers on purpose: Hermes loads each plugin
under its own module name, so a cross-plugin import would bind this plugin to
how the loader names its sibling; the sibling's tests pin its copy and this
plugin's tests pin this one.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)


def _gateway() -> Optional[Any]:
    """The live ``tui_gateway.server`` module, or None when not running under it."""
    try:
        from tui_gateway import server
    except Exception:
        return None
    return server


def canonical_key(raw: Optional[str]) -> str:
    """The durable session key for either id the plugin is handed (see module doc)."""
    raw = raw or ""
    srv = _gateway()
    entry = srv._sessions.get(raw) if srv is not None else None
    if isinstance(entry, dict) and entry.get("session_key"):
        return str(entry["session_key"])
    return raw


def emit(state_key: str, event: str, payload: dict) -> bool:
    """Send one event to the session's HUD clients; True only when a transport took it.

    A wedged transport is logged and reported as undelivered, never raised: the
    layout change has already been recorded and a tool that failed here would
    make the agent retry a change that did happen.
    """
    srv = _gateway()
    if srv is None or not state_key:
        return False
    sessions = srv._sessions
    sid = state_key if state_key in sessions else next(
        (s for s, e in list(sessions.items())
         if isinstance(e, dict) and e.get("session_key") == state_key),
        None,
    )
    if sid is None:
        return False
    try:
        srv._emit(event, sid, payload)
    except Exception:
        _LOGGER.exception("hud layout emit failed: event=%s", event)
        return False
    return True
