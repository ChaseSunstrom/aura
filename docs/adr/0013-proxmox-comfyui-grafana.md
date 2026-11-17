# ADR-0013: Proxmox stays unwired; ComfyUI is reachable but empty; GPU telemetry rides Grafana without a token

- **Status:** Accepted (ComfyUI live-gen open)
- **Reference:** the migration plan §6 (D13)

## Context

Three homelab surfaces have owner-side constraints: Proxmox API access, a ComfyUI checkpoint, and a Grafana/Influx token.

## Decision

The owner declined to hand the assistant Proxmox API access, so `proxmox-control` stays loaded and unit-tested but its live call is **waived**, not blocked. ComfyUI is reachable (`comfyui_status` live) but has no checkpoint installed, so image generation cannot produce a file until one is dropped into ComfyUI's `models/checkpoints/`. Grafana on the monitor CT allows anonymous `POST /api/ds/query`, so `gpu_status` reads `nvidia_smi` from the InfluxDB datasource with no token.

## Consequences

`GRAFANA_TOKEN` and `INFLUX_URL` stay empty in `.env`; `PROXMOX_URL`/token are absent. The ComfyUI live-generation path stays **open** until the owner installs a checkpoint. GPU telemetry works token-free; a test pins the Grafana 13 frame shape the flattener depends on.
