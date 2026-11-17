from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("gpu-telemetry")
tools = plugin.tools

CSV = (
    "#group,false,false,true,true,false,false,true,true,true\n"
    "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,double,string,string,string\n"
    "#default,_result,,,,,,,,\n"
    ",result,table,_start,_stop,_time,_value,_field,_measurement,index\n"
    ",,0,2026-09-02T00:00:00Z,2026-09-02T01:00:00Z,2026-09-02T00:59:50Z,37,utilization_gpu,nvidia_smi,0\n"
    ",,1,2026-09-02T00:00:00Z,2026-09-02T01:00:00Z,2026-09-02T00:59:50Z,21500,memory_used,nvidia_smi,0\n"
    ",,2,2026-09-02T00:00:00Z,2026-09-02T01:00:00Z,2026-09-02T00:59:50Z,24576,memory_total,nvidia_smi,0\n"
    ",,3,2026-09-02T00:00:00Z,2026-09-02T01:00:00Z,2026-09-02T00:59:50Z,61,temperature_gpu,nvidia_smi,0\n"
    ",,4,2026-09-02T00:00:00Z,2026-09-02T01:00:00Z,2026-09-02T00:59:50Z,180.5,power_draw,nvidia_smi,0\n"
    ",,5,2026-09-02T00:00:00Z,2026-09-02T01:00:00Z,2026-09-02T00:59:50Z,12,utilization_gpu,nvidia_smi,1\n"
    "\n"
)


def test_parse_annotated_csv_and_shape():
    rows = tools.parse_annotated_csv(CSV)
    assert len(rows) == 6 and rows[0]["_field"] == "utilization_gpu" and rows[0]["index"] == "0"
    shaped = tools.shape_status(rows)
    assert shaped["count"] == 2
    g0 = shaped["gpus"][0]
    assert g0["gpu"] == "0" and g0["utilization"] == 37.0 and g0["vram_pct"] == 87.5 and g0["temperature"] == 61.0
    assert shaped["gpus"][1] == {"gpu": "1", "utilization": 12.0, "time": "2026-09-02T00:59:50Z"}


def test_flux_queries_use_env_names(monkeypatch):
    monkeypatch.setenv("INFLUX_BUCKET", "gpu")
    monkeypatch.setenv("GPU_MEASUREMENT", "dcgm")
    monkeypatch.setenv("GPU_FIELD_TEMP", "DCGM_FI_DEV_GPU_TEMP")
    q = tools.flux_latest(5)
    assert 'from(bucket: "gpu")' in q and '"dcgm"' in q and 'DCGM_FI_DEV_GPU_TEMP' in q and "last()" in q
    h = tools.flux_history("temperature", 60)
    assert "aggregateWindow(every: 120s" in h


def test_status_via_influx_direct(monkeypatch):
    monkeypatch.setenv("INFLUX_URL", "http://10.0.0.9:8086")
    monkeypatch.setenv("INFLUX_TOKEN", "t")
    monkeypatch.setenv("INFLUX_ORG", "home")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, text=CSV)

    Orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: Orig(transport=httpx.MockTransport(handler)))
    out = json.loads(tools.handle_gpu_status({"minutes": 3}))
    assert out["ok"] and out["source"] == "influx" and out["count"] == 2
    assert seen == {"auth": "Token t", "path": "/api/v2/query"}


def test_status_via_grafana_proxy(monkeypatch):
    monkeypatch.delenv("INFLUX_URL", raising=False)
    monkeypatch.setenv("GRAFANA_URL", "https://monitor.test")
    monkeypatch.setenv("GRAFANA_TOKEN", "glsa_x")
    frames = {"results": {"A": {"frames": [
        {"schema": {"fields": [{"name": "Time"}, {"name": "Value", "labels": {"_field": "utilization_gpu", "index": "0"}}]},
         "data": {"values": [[1756770000000], [42.0]]}},
        {"schema": {"fields": [{"name": "Time"}, {"name": "Value", "labels": {"_field": "temperature_gpu", "index": "0"}}]},
         "data": {"values": [[1756770000000], [58.0]]}},
    ]}}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/datasources":
            assert request.headers["authorization"] == "Bearer glsa_x"
            return httpx.Response(200, json=[{"uid": "abc", "name": "InfluxDB", "type": "influxdb"}])
        if request.url.path == "/api/ds/query":
            body = json.loads(request.content)
            assert body["queries"][0]["datasource"]["uid"] == "abc"
            return httpx.Response(200, json=frames)
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"database": "ok", "version": "13.1.1"})
        return httpx.Response(404)

    monkeypatch.setattr(tools, "_grafana_client", lambda: httpx.Client(base_url="https://monitor.test",
                                                                       headers={"Authorization": "Bearer glsa_x"},
                                                                       transport=httpx.MockTransport(handler)))
    out = json.loads(tools.handle_gpu_status({}))
    assert out["ok"] and out["source"] == "grafana" and out["gpus"][0]["utilization"] == 42.0 and out["gpus"][0]["temperature"] == 58.0
    health = json.loads(tools.handle_grafana_health({}))
    assert health["grafana"]["version"] == "13.1.1" and health["datasources"][0]["uid"] == "abc"


def test_errors_are_reported(monkeypatch):
    monkeypatch.delenv("INFLUX_URL", raising=False)
    monkeypatch.setenv("GRAFANA_URL", "https://monitor.test")
    monkeypatch.setattr(tools, "_grafana_client", lambda: httpx.Client(base_url="https://monitor.test",
                                                                       transport=httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized"))))
    out = json.loads(tools.handle_gpu_status({}))
    assert out["ok"] is False and "HTTP 401" in out["error"]
    assert json.loads(tools.handle_grafana_query({"flux": "drop everything"}))["ok"] is False
    assert json.loads(tools.handle_gpu_history({"metric": "vram_total"}))["ok"] is False


def test_register():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"gpu_status", "gpu_history", "grafana_health", "grafana_query"}
    assert "gpu" in ctx.commands and "pre_tool_call" not in ctx.hooks  # read-only, no gate


def test_grafana_13_names_the_value_column_after_the_field():
    """Grafana 13's frame for a Flux `last()` carries the field name as the column name and no
    `_field`/`_value` at all; the monitor CT answered exactly this and the shaper saw no GPU."""
    payload = {"results": {"A": {"frames": [
        {"schema": {"fields": [{"name": "Time", "labels": {}},
                               {"name": "memory_used", "labels": {"index": "0", "name": "NVIDIA GeForce RTX 3090"}}]},
         "data": {"values": [[1788375450000], [22996]]}},
        {"schema": {"fields": [{"name": "Time", "labels": {}},
                               {"name": "memory_total", "labels": {"index": "0", "name": "NVIDIA GeForce RTX 3090"}}]},
         "data": {"values": [[1788375450000], [24576]]}},
    ]}}}
    rows = tools.frames_to_rows(payload)
    assert {r["_field"] for r in rows} == {"memory_used", "memory_total"}
    shaped = tools.shape_status(rows)
    assert shaped["count"] == 1 and shaped["gpus"][0]["vram_pct"] == 93.6
