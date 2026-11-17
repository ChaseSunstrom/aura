"""grafana-dashboards — read what is on the Grafana boards, and what they say.

Registers the ``grafana_dashboards`` toolset (``grafana_search_dashboards`` /
``grafana_get_dashboard`` / ``grafana_panel_query``) and the ``/dashboards``
slash command. The HTTP, token and datasource work is ``_shared._grafana``,
shared with gpu-telemetry, so there is one Grafana client in the repo.

Every tool here is read-only and the plugin registers **no** ``pre_tool_call``
hook, deliberately. Grafana's dashboard API updates a board by PUTting the whole
document, so a write tool that reconstructed one field wrong would replace
somebody's layout, thresholds, variables and annotations wholesale — and an
approval prompt reading "update the homelab dashboard" would not show the owner
any of that. A board is edited in Grafana, by a person who can see the diff.

Nothing here reaches the network at import time, so the package loads — and its
tests run — with no Grafana anywhere, the rule the sibling plugins follow.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_grafana.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from aura_schema import from_env  # noqa: E402

from _shared import _grafana  # noqa: E402
from _shared._approval_hook import guarded_command  # noqa: E402
from . import schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

TOOLSET = "grafana_dashboards"


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped GRAFANA_* value names itself at load
    ctx.register_tool(name="grafana_search_dashboards", toolset=TOOLSET,
                      schema=schemas.GRAFANA_SEARCH_DASHBOARDS,
                      handler=tools.handle_search_dashboards,
                      check_fn=_grafana.available, emoji="📊")
    ctx.register_tool(name="grafana_get_dashboard", toolset=TOOLSET,
                      schema=schemas.GRAFANA_GET_DASHBOARD,
                      handler=tools.handle_get_dashboard, check_fn=_grafana.available, emoji="📊")
    ctx.register_tool(name="grafana_panel_query", toolset=TOOLSET,
                      schema=schemas.GRAFANA_PANEL_QUERY,
                      handler=tools.handle_panel_query, check_fn=_grafana.available, emoji="📈")

    # No register_hook("pre_tool_call"): see the module docstring — nothing here
    # changes Grafana, and a gate on a read is a prompt the owner learns to
    # dismiss without reading.
    ctx.register_command(
        "dashboards",
        handler=guarded_command(tools.handle_command, "dashboards", logger=logger),
        description="The Grafana dashboard titles, optionally filtered by title text",
        args_hint="[text]",
    )
