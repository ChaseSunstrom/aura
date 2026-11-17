"""Fixture for the hud-layout tasks: the plugin's remembered state, reset around a run.

The plugin persists two things a previous run would otherwise leak into the next:
``hud.lock_layout`` (hud-layout-002 locks it, deliberately) and JARVIS's per-activity
declines (an undo in 002 makes ``arrange goal-start`` a no-op "kept: declined" in a
later 001). Both are cleared through the plugin's own ``/hud_layout`` command over the
HUD gateway — ``unlock`` and ``forget`` — before the run, and the lock is cleared again
after it so the person's HUD is never left locked by the benchmark.

Without a gateway key (``BENCH_WS_KEY``) the reset is skipped and reported in the
returned vars; the task then runs against whatever state is there, and 002's own
prompt still re-establishes the lock it needs.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: The two verbs a reset dispatches, in order (forget clears preferences and declines).
RESET_VERBS: tuple[str, ...] = ("unlock", "forget")


def _dispatch(ctx: dict[str, Any], verbs: tuple[str, ...]) -> str:
    config = ctx["config"]
    if not getattr(config, "ws_key", ""):
        return "skipped (no BENCH_WS_KEY)"
    from bench.transports import GatewayClient

    with GatewayClient(config.ws_base, config.ws_key) as gw:
        sid = gw.create_session()
        for verb in verbs:
            reply = gw.call("command.dispatch", {"session_id": sid, "name": "hud_layout", "arg": f"@{sid} {verb}"})
            result = reply.get("result") or {}
            if result.get("type") != "plugin":
                # The plugin is not enabled in this image: say so rather than fail setup —
                # the task's own checker will show the tools were never offered.
                return f"hud_layout {verb} not dispatched: {reply.get('error') or result}"
    return "reset " + "+".join(verbs)


def setup(ctx: dict[str, Any]) -> dict[str, str]:
    try:
        detail = _dispatch(ctx, RESET_VERBS)
    except Exception as exc:  # noqa: BLE001 - a reset failure must not error the task row
        _LOGGER.warning("hud_layout fixture setup failed: %s", exc)
        detail = f"reset failed: {exc}"
    return {"hud_layout_reset": detail}


def teardown(ctx: dict[str, Any]) -> None:
    try:
        _dispatch(ctx, ("unlock",))
    except Exception as exc:  # noqa: BLE001 - see setup
        _LOGGER.warning("hud_layout fixture teardown failed: %s", exc)
