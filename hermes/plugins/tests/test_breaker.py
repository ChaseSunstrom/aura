"""The circuit breaker: a dead homelab service is said once, not retried into timeouts.

Driven through the real guard hooks (``matrix_gate`` before a call, ``audit_outcome_hook``
after it) because that is the only place it lives: a breaker test against the module alone
would pass with the hooks never calling it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import _breaker  # noqa: E402
from _shared._approval_hook import audit_outcome_hook, matrix_gate  # noqa: E402

DOWN = "ConnectError: [Errno 111] Connection refused"


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clock = [1000.0]
    monkeypatch.setattr(_breaker, "_now", lambda: clock[0])
    _breaker.reset()
    yield clock
    _breaker.reset()


def _after(tool: str, error: str = "") -> None:
    result = json.dumps({"ok": False, "error": error} if error else {"ok": True})
    audit_outcome_hook()(tool_name=tool, args={}, result=result, session_id="s",
                         status="error" if error else "ok",
                         error_type="tool_error" if error else "", error_message=error)


def _before(tool: str):
    """The gate's directive when the breaker holds the call, else None (whatever the matrix says)."""
    out = matrix_gate()(tool_name=tool, args={}, session_id="s")
    return out if out and out["action"] == "block" and "unreachable" in out["message"] else None


def test_three_connection_failures_open_it_and_the_model_is_told_why(_fresh):
    for _ in range(_breaker.THRESHOLD):
        assert _before("comfyui_status") is None
        _after("comfyui_status", DOWN)
    blocked = _before("comfyui_status")
    assert blocked["action"] == "block"
    assert "ComfyUI" in blocked["message"] and "Connection refused" in blocked["message"]
    # Every tool of the service, not only the one that failed.
    assert _before("comfyui_submit")["action"] == "block"
    # Another service is untouched.
    assert _before("jellyfin_search") is None


def test_after_the_cooldown_one_call_is_tried_and_a_success_closes_it(_fresh):
    for _ in range(_breaker.THRESHOLD):
        _after("proxmox_nodes", "ConnectTimeout: timed out")
    assert _before("proxmox_nodes")["action"] == "block"
    _fresh[0] += _breaker.COOLDOWN_S + 1
    assert _before("proxmox_nodes") is None  # half-open: one try
    _after("proxmox_nodes", "ConnectTimeout: timed out")
    assert _before("proxmox_nodes")["action"] == "block"  # one more failure reopens it
    _fresh[0] += _breaker.COOLDOWN_S + 1
    _after("proxmox_nodes")
    assert _before("proxmox_nodes") is None
    _after("proxmox_nodes", "ConnectTimeout: timed out")
    assert _before("proxmox_nodes") is None  # closed again: back to counting from one


@pytest.mark.parametrize("error", [
    "ValueError: workflow must be a JSON object",   # the model's arguments
    "HTTP 404 from http://comfy/history/x",         # the service answered
    "HTTP 401 from http://comfy/prompt",
])
def test_an_error_the_service_answered_never_opens_it(_fresh, error):
    for _ in range(_breaker.THRESHOLD + 2):
        _after("comfyui_submit", error)
    assert _before("comfyui_submit") is None


@pytest.mark.parametrize("error", [
    "ReadTimeout: timed out", "HTTP 503 from http://n8n/webhook/x", "HTTP 502 from http://jf/Items",
    "RemoteProtocolError: Server disconnected without sending a response.",
])
def test_the_shapes_a_dead_service_takes_count(_fresh, error):
    assert _breaker.is_outage(error)


def test_a_success_between_failures_starts_the_count_again(_fresh):
    for _ in range(_breaker.THRESHOLD - 1):
        _after("n8n_list_workflows", DOWN)
    _after("n8n_list_workflows")
    _after("n8n_list_workflows", DOWN)
    assert _before("n8n_list_workflows") is None


def test_a_tool_of_no_known_service_is_never_held(_fresh):
    for _ in range(_breaker.THRESHOLD + 1):
        _after("terminal", DOWN)
    assert _breaker.service_of("terminal") is None and _before("terminal") is None
