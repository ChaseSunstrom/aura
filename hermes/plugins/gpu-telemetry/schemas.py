"""Model-facing schemas for gpu-telemetry (read-only tools)."""

GPU_STATUS = {
    "name": "gpu_status",
    "description": ("Current GPU telemetry for the AI machine (each RTX 3090): utilisation %, VRAM used/total, "
                    "temperature °C, power W. Reads InfluxDB (Flux) through Grafana's datasource proxy, or InfluxDB "
                    "directly when INFLUX_URL is set. Say which GPU is which."),
    "parameters": {
        "type": "object",
        "properties": {"minutes": {"type": "integer", "description": "Look-back window for the latest sample (default 10)"}},
        "required": [],
    },
}

GPU_HISTORY = {
    "name": "gpu_history",
    "description": "How has the GPU been doing over the last hour, its load or temperature over time: time series of one GPU metric (utilization | vram_used | temperature | power) over the last N minutes, downsampled to ~30 points. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "enum": ["utilization", "vram_used", "temperature", "power"]},
            "minutes": {"type": "integer", "description": "Window in minutes (default 60)"},
        },
        "required": ["metric"],
    },
}

GRAFANA_HEALTH = {
    "name": "grafana_health",
    "description": "Is Grafana up, which datasources does it have: Grafana health/version and the list of datasources (needs a Grafana token for datasources). Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GRAFANA_QUERY = {
    "name": "grafana_query",
    "description": ("Run a raw Flux (InfluxDB) query through Grafana's datasource proxy and return up to 200 rows. "
                    "For anything gpu_status/gpu_history do not cover."),
    "parameters": {
        "type": "object",
        "properties": {"flux": {"type": "string", "description": "A complete Flux query, including from(bucket:…) and range()"}},
        "required": ["flux"],
    },
}
