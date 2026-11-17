---
name: homelab-telemetry
description: "Use when telemetry tools fail or lack values: GPU/InfluxDB via Grafana, and house sensor min/max over a period via jarvis-core REST history."
version: 0.1.0
---

# Homelab telemetry via Grafana + InfluxDB

The homelab metrics live in InfluxDB on the monitor host (bucket `proxmox`). InfluxDB binds
loopback on that host, so the only way in from the JARVIS box is through the Grafana API.
This is the fallback when the deferred telemetry tools (`gpu_status`, `gpu_history`,
`comfyui_status`) error out or come back empty. Reads are free — no approval gate.

## Discovery (do at runtime; don't hardcode)

GET `$GRAFANA_URL/api/datasources` with `Authorization: Bearer $GRAFANA_TOKEN`. **Not from
the terminal**: since 2026-09-18 the shell is a sandbox with no network and no environment
(ADR-0045), so the token is not there and the host is unreachable. This route runs only where
the terminal backend is `local`, or once a tool carries the token; from the sandbox the typed
tools are the only way, and when they fail the honest answer is that the reading is
unavailable, not a shell command that fails too.

From the influxdb entry take: `uid`, `jsonData.organization`, `jsonData.defaultBucket`,
and `url` (tells you the InfluxDB host/port). Verified 2026-09-02: uid `ffu3pie841zwgb`,
org `homelab`, bucket `proxmox`, InfluxDB at `http://localhost:8086`, Grafana 13.2.0.

`INFLUX_URL` / `INFLUX_TOKEN` may be unset in the JARVIS environment — do not wait on them.
The Grafana route needs only `GRAFANA_URL` + `GRAFANA_TOKEN` (both set in the JARVIS env).

## The query route that works

POST `$GRAFANA_URL/api/ds/query` with `Authorization: Bearer $GRAFANA_TOKEN`,
`Content-Type: application/json`:

```json
{"queries":[{"refId":"A","datasource":{"uid":"<uid>","type":"influxdb"},"query":"<flux>","queryType":"flux"}],"from":"now-10m","to":"now"}
```

Inventory of live measurements (safe probe, no approval needed):

```
from(bucket: "proxmox") |> range(start: -10m) |> group(columns: ["_measurement"]) |> count() |> filter(fn: (r) => r._value > 0)
```

Latest per-GPU values (field names follow the influxdb exporter — read the `schema.fields`
in the response, don't assume):

```
from(bucket: "proxmox") |> range(start: -2m) |> filter(fn: (r) => r._measurement == "nvidia_smi") |> last()
```

The response is Grafana frames: `results.A.frames[]`, each with `schema.fields` (name +
labels) and `data.values` columns. Reduce with Python (json → walk frames → zip values);
don't eyeball the raw JSON.

Known measurements (verified 2026-09-02): `nvidia_smi` (per-GPU VRAM/utilisation),
`nvidia_smi_process` (per-process VRAM), `cpustat`, `memory`, `nics`, `blockstat`, `system`.

## House sensor history (jarvis-core REST, not Grafana)

House readings (temperatures, thermostat, etc.) live in jarvis-core, not InfluxDB. When the
`house_history` tool comes back with only state transitions (e.g. hvac mode `heat`) and no
numeric values — which is exactly how it is on this install — answer "what was the high/low
this week?" through the REST history API instead. Reads are free.

From the JARVIS container the service is on the docker bridge gateway (`172.17.0.1`) or
`host.docker.internal`, port 8080. Auth: `Authorization: Bearer $AURA_TOKEN` (set in the
JARVIS env; `GET /api/` → `{"state":"RUNNING"}` is the discovery probe).

GET `http://172.17.0.1:8080/api/history/period/<start, epoch seconds>?filter_entity_id=climate.thermostat&now=<now, epoch ms>`
with the bearer header — the same caveat as above: not from the sandboxed terminal.

Shape: list of days, each `[{entity_id, states:[{state, attributes, last_updated}, ...]}]`
(some builds return flat state objects — handle both). Climate entities carry the reading in
`attributes.current_temperature` (setpoint in `temperature`); plain sensors carry it in `state`.
Reduce in Python, not by eye; `scripts/query_history.py` does the whole job:
`python3 scripts/query_history.py <entity_id> [hours]` → count, max/min with timestamps, daily maxima.

Known mapping on this install: the greenhouse thermostat is `climate.thermostat` (setpoint 21°C,
user target 24°C). `house_areas` returns areas with empty entity lists and no greenhouse area.

Stale-data heuristic: if every sensor comes back as one constant value across the entire
window, suspect a dead feed rather than a perfectly stable sensor — say so to the user instead
of reporting the constant as fact.

## Pitfalls

- The raw InfluxDB proxy path (`/api/datasources/proxy/<uid>/api/v2/query?org=...`) returns
  `{"message":"Not found"}` on this Grafana build even with the org param. Don't burn time on
  it — `/api/ds/query` is the working route.
- Direct InfluxDB from the JARVIS host (port 8086, http or https) is unreachable — loopback
  only on the monitor host. Route through Grafana.
- A per-GPU detail query can trip the approval gate. If it does, stop and ask; the
  measurement-inventory query is the safe probe that needs no extra consent.
- An HTTP 502 from `comfyui_status` means the ComfyUI service is down (dead upstream behind
  the proxy), not a network problem — check `llama_swap_status` for what holds VRAM meanwhile
  (a loaded LLM eats most of the 48 GB pool, so "free VRAM" must be read with that in mind).
