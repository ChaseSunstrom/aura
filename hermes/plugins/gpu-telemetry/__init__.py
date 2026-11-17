"""gpu-telemetry — read-only GPU/VRAM/temperature/utilisation via Grafana or InfluxDB."""
from __future__ import annotations

import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_grafana.py.
# This runs before `from . import tools`, which imports `_shared._grafana`.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from aura_schema import from_env  # noqa: E402

from . import schemas, tools  # noqa: E402

TOOLSET = "gpu_telemetry"


def _available() -> bool:
    cfg = from_env()
    return bool(cfg.grafana_url or cfg.influx_url)


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped GRAFANA_*/INFLUX_*/GPU_* value names itself at load
    ctx.register_tool(name="gpu_status", toolset=TOOLSET, schema=schemas.GPU_STATUS,
                      handler=tools.handle_gpu_status, check_fn=_available, emoji="🎛️")
    ctx.register_tool(name="gpu_history", toolset=TOOLSET, schema=schemas.GPU_HISTORY,
                      handler=tools.handle_gpu_history, check_fn=_available, emoji="📈")
    ctx.register_tool(name="grafana_health", toolset=TOOLSET, schema=schemas.GRAFANA_HEALTH,
                      handler=tools.handle_grafana_health, check_fn=lambda: bool(from_env().grafana_url), emoji="📊")
    ctx.register_tool(name="grafana_query", toolset=TOOLSET, schema=schemas.GRAFANA_QUERY,
                      handler=tools.handle_grafana_query, check_fn=_available, emoji="📊")

    def _cmd(raw_args: str) -> str:
        return tools.handle_gpu_status({"minutes": 10})

    ctx.register_command("gpu", handler=_cmd, description="GPU utilisation / VRAM / temperature right now")
