"""Grafana / InfluxDB client for GPU telemetry.

Two ways to reach the numbers, picked at call time:

1. ``INFLUX_URL`` set → query InfluxDB 2.x directly (`POST /api/v2/query`, Flux).
2. otherwise → Grafana's datasource proxy (`POST /api/ds/query`) with ``GRAFANA_TOKEN``;
   the InfluxDB datasource is found by ``GRAFANA_INFLUX_DS`` (uid or name) or the first
   influx datasource.

The measurement/field names are configurable because every exporter names
them differently (nvidia_smi, telegraf's nvidia_smi input, dcgm):
``GPU_MEASUREMENT`` (default ``nvidia_smi``) and the four fields
``GPU_FIELD_UTIL`` (utilization_gpu), ``GPU_FIELD_VRAM_USED`` (memory_used),
``GPU_FIELD_VRAM_TOTAL`` (memory_total), ``GPU_FIELD_TEMP`` (temperature_gpu),
``GPU_FIELD_POWER`` (power_draw); the GPU tag is ``GPU_TAG`` (default ``index``).
All of them are read through the one settings schema (``aura_schema.from_env()``).

The Grafana half of that — token, client, datasource resolution, data-frame
flattening — is ``_shared._grafana``, shared with grafana-dashboards; only the
InfluxDB-direct transport and the GPU-specific Flux and shaping are here.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

import httpx
from aura_schema import from_env

from _shared import _egress, _failure, _grafana

#: metric -> the ``Settings`` attribute that names its exporter field.
FIELDS = {
    "utilization": "gpu_field_util",
    "vram_used": "gpu_field_vram_used",
    "vram_total": "gpu_field_vram_total",
    "temperature": "gpu_field_temp",
    "power": "gpu_field_power",
}


def field(metric: str) -> str:
    return str(getattr(from_env(), FIELDS[metric]))


def bucket() -> str:
    return from_env().influx_bucket


def measurement() -> str:
    return from_env().gpu_measurement


def gpu_tag() -> str:
    return from_env().gpu_tag


def flux_latest(minutes: int) -> str:
    fields = " or ".join(f'r._field == "{field(m)}"' for m in FIELDS)
    return (f'from(bucket: "{bucket()}") |> range(start: -{int(minutes)}m) '
            f'|> filter(fn: (r) => r._measurement == "{measurement()}") '
            f'|> filter(fn: (r) => {fields}) |> last()')


def flux_history(metric: str, minutes: int, points: int = 30) -> str:
    every = max(1, int(minutes) * 60 // max(points, 1))
    return (f'from(bucket: "{bucket()}") |> range(start: -{int(minutes)}m) '
            f'|> filter(fn: (r) => r._measurement == "{measurement()}" and r._field == "{field(metric)}") '
            f'|> aggregateWindow(every: {every}s, fn: mean, createEmpty: false)')


# ------------------------------------------------------------- transports
def _influx_query(flux: str) -> list[dict[str, Any]]:
    cfg = from_env()
    url = _egress.service_url(cfg.influx_url, "INFLUX_URL")
    headers = {"Authorization": f"Token {cfg.influx_token}", "Accept": "application/csv",
               "Content-Type": "application/vnd.flux"}
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{url}/api/v2/query", params={"org": cfg.influx_org}, headers=headers, content=flux)
        r.raise_for_status()
    return parse_annotated_csv(r.text)


def parse_annotated_csv(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in text.replace("\r", "").split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith("#")]
        if not lines:
            continue
        reader = csv.DictReader(io.StringIO("\n".join(lines)))
        for row in reader:
            row.pop("", None)
            rows.append(row)
    return rows


#: The Grafana HTTP layer lives in ``_shared._grafana`` so this plugin and
#: grafana-dashboards cannot drift into two ideas of a token, a datasource or a
#: data frame. The names below stay because they are what this module's call
#: sites — and the tests that swap the transport — address.
_grafana_headers = _grafana.headers
_grafana_client = _grafana.client
frames_to_rows = _grafana.frames_to_rows


def _open_client() -> httpx.Client:
    """Open a Grafana client *through this module's* ``_grafana_client`` global.

    The indirection is a seam, not decoration: the tests replace
    ``tools._grafana_client`` with a MockTransport-backed client, so every
    request below — including the ones the shared module issues on our behalf —
    has to come from the current value of that global rather than from the
    binding captured when this module was imported.
    """
    return _grafana_client()


def grafana_influx_datasource() -> dict[str, Any]:
    """The InfluxDB datasource ``GRAFANA_INFLUX_DS`` names, else the first one."""
    return _grafana.influx_datasource(open_client=_open_client)


def _grafana_query(flux: str) -> list[dict[str, Any]]:
    ds = grafana_influx_datasource()
    payload = _grafana.ds_query(
        [{"refId": "A", "datasource": {"uid": ds["uid"]}, "query": flux, "rawQuery": True}],
        open_client=_open_client)
    return frames_to_rows(payload)


def query(flux: str) -> list[dict[str, Any]]:
    return _influx_query(flux) if from_env().influx_url else _grafana_query(flux)


# ------------------------------------------------------------- shaping
def shape_status(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group `last()` rows per GPU into one record each."""
    by_field = {v[1]: k for k, v in FIELDS.items()}
    for m in FIELDS:
        by_field[field(m)] = m
    gpus: dict[str, dict[str, Any]] = {}
    tag = gpu_tag()
    for r in rows:
        gpu = str(r.get(tag) or r.get("gpu") or r.get("name") or "0")
        metric = by_field.get(str(r.get("_field")))
        if not metric:
            continue
        raw = r.get("_value")
        if raw is None:  # a row without a value crashes float(None); skip it
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        g = gpus.setdefault(gpu, {"gpu": gpu})
        g[metric] = val
        if r.get("_time"):
            g["time"] = r["_time"]
    out = []
    for g in sorted(gpus.values(), key=lambda x: x["gpu"]):
        if "vram_used" in g and "vram_total" in g and g["vram_total"]:
            g["vram_pct"] = round(100 * g["vram_used"] / g["vram_total"], 1)
        out.append(g)
    return {"gpus": out, "count": len(out)}


# ------------------------------------------------------------- handlers
def _safe(fn):
    def handler(params: dict, **kwargs):
        del kwargs
        try:
            return json.dumps(fn(params or {}))
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
            # The shared status-and-hint answer (T-1348): no body, and no request address, which
            # carries the userinfo when GRAFANA_URL/INFLUX_URL was written as https://user:pw@host.
            return json.dumps(_failure.of(exc) or {
                "ok": False, "error": _grafana.redact(f"{exc.__class__.__name__}: {exc}")})
    return handler


def _status(params: dict) -> dict[str, Any]:
    minutes = int(params.get("minutes") or 10)
    rows = query(flux_latest(minutes))
    shaped = shape_status(rows)
    if not shaped["gpus"]:
        return {"ok": False, "error": f"no GPU samples in the last {minutes} min (measurement {measurement()!r} in bucket {bucket()!r})",
                "rows_seen": len(rows)}
    return {"ok": True, **shaped, "source": "influx" if from_env().influx_url else "grafana"}


def _history(params: dict) -> dict[str, Any]:
    metric = str(params.get("metric") or "")
    if metric not in FIELDS or metric == "vram_total":
        return {"ok": False, "error": "metric must be one of utilization, vram_used, temperature, power"}
    minutes = int(params.get("minutes") or 60)
    rows = query(flux_history(metric, minutes))
    tag = gpu_tag()
    series: dict[str, list] = {}
    for r in rows:
        raw = r.get("_value")
        if raw is None:  # a row without a value crashes float(None); skip it
            continue
        try:
            series.setdefault(str(r.get(tag) or "0"), []).append([r.get("_time"), round(float(raw), 2)])
        except (TypeError, ValueError):
            continue
    return {"ok": True, "metric": metric, "minutes": minutes, "series": series}


def _health(params: dict) -> dict[str, Any]:
    with _grafana_client() as c:
        health = c.get("/api/health").raise_for_status().json()
        out: dict[str, Any] = {"ok": True, "grafana": health}
        if _grafana_headers():
            r = c.get("/api/datasources")
            if r.status_code == 200:
                out["datasources"] = [{"name": d.get("name"), "type": d.get("type"), "uid": d.get("uid")} for d in r.json()]
            else:
                out["datasources_error"] = f"HTTP {r.status_code}"
        else:
            out["note"] = "set GRAFANA_TOKEN to list datasources and run queries"
    return out


def _raw(params: dict) -> dict[str, Any]:
    flux = str(params.get("flux") or "").strip()
    if not flux.startswith("from("):
        return {"ok": False, "error": "flux must start with from(bucket: …)"}
    rows = query(flux)[:200]
    return {"ok": True, "rows": rows, "count": len(rows)}


handle_gpu_status = _safe(_status)
handle_gpu_history = _safe(_history)
handle_grafana_health = _safe(_health)
handle_grafana_query = _safe(_raw)
