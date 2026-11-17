"""Proxmox VE REST client (API token auth).

``Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>`` against
``{PROXMOX_URL}/api2/json``. Self-signed certificates are common on PVE, so
``PROXMOX_VERIFY_SSL`` is honoured. Every ``PROXMOX_*`` value is read through the
one settings schema (``aura_schema.from_env()``).
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from aura_schema import from_env

from _shared import _egress, _failure


def base_url() -> str:
    return from_env().proxmox_url.rstrip("/")


def _client(timeout: float = 20.0) -> httpx.Client:
    cfg = from_env()
    token = f"PVEAPIToken={cfg.proxmox_token_id}={cfg.proxmox_token_secret}"
    base = _egress.service_url(cfg.proxmox_url, "PROXMOX_URL")
    return httpx.Client(base_url=f"{base}/api2/json", headers={"Authorization": token}, timeout=timeout,
                        verify=cfg.proxmox_verify_ssl)


def _get(c: httpx.Client, path: str, **params) -> Any:
    return c.get(path, params=params or None).raise_for_status().json().get("data")


def nodes() -> dict[str, Any]:
    with _client() as c:
        data = _get(c, "/nodes") or []
    return {"ok": True, "nodes": [
        {"node": n.get("node"), "status": n.get("status"), "cpu_pct": round(100 * float(n.get("cpu") or 0), 1),
         "mem_used_gb": round(float(n.get("mem") or 0) / 2**30, 1), "mem_total_gb": round(float(n.get("maxmem") or 0) / 2**30, 1),
         "uptime_h": round(float(n.get("uptime") or 0) / 3600, 1)}
        for n in data]}


def guests(node: str | None = None) -> dict[str, Any]:
    with _client() as c:
        data = _get(c, "/cluster/resources", type="vm") or []
    out = []
    for g in data:
        if node and g.get("node") != node:
            continue
        out.append({"vmid": g.get("vmid"), "name": g.get("name"), "type": g.get("type"), "node": g.get("node"),
                    "status": g.get("status"), "cpu_pct": round(100 * float(g.get("cpu") or 0), 1),
                    "mem_used_gb": round(float(g.get("mem") or 0) / 2**30, 2),
                    "mem_max_gb": round(float(g.get("maxmem") or 0) / 2**30, 1),
                    "uptime_h": round(float(g.get("uptime") or 0) / 3600, 1)})
    out.sort(key=lambda g: (str(g["node"]), int(g["vmid"] or 0)))
    return {"ok": True, "guests": out, "count": len(out)}


def _locate(c: httpx.Client, vmid: int) -> tuple[str, str]:
    for g in _get(c, "/cluster/resources", type="vm") or []:
        if int(g.get("vmid") or -1) == int(vmid):
            return str(g["node"]), str(g["type"])
    raise RuntimeError(f"no guest with vmid {vmid}")


def guest_status(vmid: int) -> dict[str, Any]:
    with _client() as c:
        node, kind = _locate(c, vmid)
        st = _get(c, f"/nodes/{node}/{kind}/{vmid}/status/current") or {}
    keep = {k: st.get(k) for k in ("name", "status", "uptime", "cpu", "cpus", "mem", "maxmem", "disk", "maxdisk",
                                   "netin", "netout", "ha", "lock", "qmpstatus") if k in st}
    return {"ok": True, "vmid": vmid, "node": node, "type": kind, **keep}


ACTIONS = {"start", "stop", "shutdown", "reboot", "suspend", "resume"}


def guest_action(vmid: int, action: str, wait: float = 60.0) -> dict[str, Any]:
    if action not in ACTIONS:
        return {"ok": False, "error": f"action must be one of {sorted(ACTIONS)}"}
    with _client(timeout=60) as c:
        node, kind = _locate(c, vmid)
        r = c.post(f"/nodes/{node}/{kind}/{vmid}/status/{action}").raise_for_status().json()
        upid = r.get("data")
        result: dict[str, Any] = {"ok": True, "vmid": vmid, "node": node, "type": kind, "action": action, "upid": upid}
        deadline = time.time() + wait
        while upid and time.time() < deadline:
            task = _get(c, f"/nodes/{node}/tasks/{upid}/status") or {}
            if task.get("status") == "stopped":
                result["task_status"] = task.get("exitstatus")
                result["ok"] = task.get("exitstatus") == "OK"
                break
            time.sleep(1.0)
        result["state_after"] = (_get(c, f"/nodes/{node}/{kind}/{vmid}/status/current") or {}).get("status")
    return result


def _safe(fn):
    def handler(params: dict, **kwargs):
        del kwargs
        cfg = from_env()
        if not (cfg.proxmox_url and cfg.proxmox_token_id and cfg.proxmox_token_secret):
            return json.dumps({"ok": False, "error": "Proxmox is not configured (PROXMOX_URL, PROXMOX_TOKEN_ID, PROXMOX_TOKEN_SECRET)"})
        try:
            return json.dumps(fn(params or {}))
        except (httpx.HTTPError, RuntimeError, ValueError, TypeError, OverflowError) as exc:  # JSON may carry Infinity
            # A server failure is the shared status-and-hint answer (T-1348): never the body
            # or the request address.
            return json.dumps(_failure.of(exc) or {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"})
    return handler


handle_nodes = _safe(lambda p: nodes())
handle_guests = _safe(lambda p: guests(p.get("node")))
handle_guest_status = _safe(lambda p: guest_status(int(p["vmid"])))
handle_guest_action = _safe(lambda p: guest_action(int(p["vmid"]), str(p.get("action") or "")))
