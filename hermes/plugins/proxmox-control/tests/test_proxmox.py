from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("proxmox-control")
tools = plugin.tools


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("PROXMOX_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "jarvis@pve!agent")
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "s3cret")
    state = {"109": "running", "polls": 0}
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        p = request.url.path
        if p == "/api2/json/nodes":
            return httpx.Response(200, json={"data": [{"node": "pve", "status": "online", "cpu": 0.12, "mem": 8 * 2**30, "maxmem": 64 * 2**30, "uptime": 7200}]})
        if p == "/api2/json/cluster/resources":
            return httpx.Response(200, json={"data": [
                {"vmid": 109, "name": "jarvis", "type": "lxc", "node": "pve", "status": state["109"], "cpu": 0.02, "mem": 2**30, "maxmem": 16 * 2**30, "uptime": 3600},
                {"vmid": 100, "name": "AI", "type": "qemu", "node": "pve", "status": "running", "cpu": 0.5, "mem": 0, "maxmem": 0, "uptime": 0}]})
        if p == "/api2/json/nodes/pve/lxc/109/status/current":
            return httpx.Response(200, json={"data": {"name": "jarvis", "status": state["109"], "uptime": 3600, "cpus": 4}})
        if p == "/api2/json/nodes/pve/lxc/109/status/shutdown":
            state["109"] = "stopped"
            return httpx.Response(200, json={"data": "UPID:pve:0001:task"})
        if p == "/api2/json/nodes/pve/tasks/UPID:pve:0001:task/status":
            state["polls"] += 1
            return httpx.Response(200, json={"data": {"status": "stopped", "exitstatus": "OK"}})
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(tools, "_client", lambda timeout=20.0: httpx.Client(base_url="https://pve.test:8006/api2/json",
                                                                             headers={"Authorization": "PVEAPIToken=jarvis@pve!agent=s3cret"},
                                                                             transport=httpx.MockTransport(handler)))
    return seen, state


def test_inventory(fake):
    seen, _ = fake
    n = json.loads(tools.handle_nodes({}))
    assert n["nodes"][0] == {"node": "pve", "status": "online", "cpu_pct": 12.0, "mem_used_gb": 8.0, "mem_total_gb": 64.0, "uptime_h": 2.0}
    assert seen[0][2] == "PVEAPIToken=jarvis@pve!agent=s3cret"
    g = json.loads(tools.handle_guests({}))
    assert [x["vmid"] for x in g["guests"]] == [100, 109] and g["guests"][1]["type"] == "lxc"
    st = json.loads(tools.handle_guest_status({"vmid": 109}))
    assert st["node"] == "pve" and st["type"] == "lxc" and st["status"] == "running"
    assert json.loads(tools.handle_guest_status({"vmid": 999}))["ok"] is False


def test_action_polls_task_and_reports_state(fake):
    _, state = fake
    out = json.loads(tools.handle_guest_action({"vmid": 109, "action": "shutdown"}))
    assert out["ok"] and out["upid"] == "UPID:pve:0001:task" and out["task_status"] == "OK" and out["state_after"] == "stopped"
    assert state["polls"] == 1
    assert json.loads(tools.handle_guest_action({"vmid": 109, "action": "explode"}))["ok"] is False


def test_unconfigured_is_a_clear_error(monkeypatch):
    for k in ("PROXMOX_URL", "PROXMOX_TOKEN_ID", "PROXMOX_TOKEN_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert "not configured" in json.loads(tools.handle_nodes({}))["error"]


def test_gate_only_for_disruptive_actions():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    gate = ctx.hooks["pre_tool_call"]
    assert gate("proxmox_guest_action", {"vmid": 109, "action": "stop"}, "")["action"] == "approve"
    assert gate("proxmox_guest_action", {"vmid": 109, "action": "start"}, "") is None
    assert gate("proxmox_guests", {}, "") is None
    assert set(ctx.tools) == {"proxmox_nodes", "proxmox_guests", "proxmox_guest_status", "proxmox_guest_action"}
