"""Tell every open console something changed: one event on each live HUD session.

``tui_gateway.server`` exists only inside the Hermes HUD gateway (``hermes serve``); elsewhere
(the API server, a cron run, the tests) nothing is reached and the count is zero. Does not
queue for a console that connects later: a new session reads current state on its own.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


def broadcast(event: str, payload: dict) -> int:
    """Emit on every live HUD session; the count reached. Zero off the gateway."""
    try:
        from tui_gateway.server import _emit, _sessions
    except Exception:
        return 0
    reached = 0
    for sid in list(_sessions.keys()):
        try:
            _emit(event, sid, payload)
            reached += 1
        except Exception:  # a wedged transport must not fail the change that announced it
            _LOGGER.exception("%s emit failed: sid=%s", event, sid)
    return reached
