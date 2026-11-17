"""llama-swap-control tests against a fake llama-swap (httpx MockTransport)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("llama-swap-control")
tools = plugin.tools
schemas = plugin.schemas


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "qwen3.8-27b", "status": {"value": "loaded"}},
                {"id": "qwen3.6-35b", "status": {"value": "unloaded"}}]})
        if request.url.path == "/running":
            return httpx.Response(200, json={"running": [{"model": "qwen3.8-27b", "state": "ready", "cmd": "vllm serve …"}]})
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "v250"})
        if request.url.path == "/unload":
            return httpx.Response(200, text="ok")
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            assert body["max_tokens"] == 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "model": body["model"]})
        return httpx.Response(404)

    monkeypatch.setenv("LLAMA_SWAP_URL", "http://swap.test")
    monkeypatch.setattr(tools, "_client", lambda timeout=10.0: httpx.Client(base_url="http://swap.test", transport=httpx.MockTransport(handler)))
    return calls


def test_models_and_status(fake, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-27b")
    out = json.loads(tools.handle_llama_swap_models({}))
    assert out["resident"] == ["qwen3.8-27b"]
    assert {m["id"]: m["state"] for m in out["models"]} == {"qwen3.8-27b": "ready", "qwen3.6-35b": "unloaded"}
    # Every routing row falls back to the default model, so nothing needs a swap: the
    # deployed position D21 settled on, stated by the tool rather than by a comment.
    assert set(out["routing"]) == {"chat", *tools.ROUTING_JOBS}
    assert set(out["routing"].values()) == {"qwen3.8-27b"}
    assert out["needs_swap"] == []
    by_id = {m["id"]: m for m in out["models"]}
    assert "vision" in by_id["qwen3.8-27b"]["routed_jobs"] and by_id["qwen3.6-35b"]["routed_jobs"] == []
    st = json.loads(tools.handle_llama_swap_status({}))
    assert st["resident_model"] == "qwen3.8-27b" and st["version"]["version"] == "v250"


def test_load_and_unload(fake):
    out = json.loads(tools.handle_llama_swap_load({"model": "qwen3.6-35b"}))
    assert out["ok"] and out["model"] == "qwen3.6-35b" and "elapsed_s" in out
    assert ("POST", "/v1/chat/completions") in fake
    assert json.loads(tools.handle_llama_swap_load({}))["ok"] is False
    assert json.loads(tools.handle_llama_swap_unload({}))["ok"] is True
    assert ("GET", "/unload") in fake


def test_unreachable_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("LLAMA_SWAP_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(tools, "_client", lambda timeout=10.0: httpx.Client(base_url="http://127.0.0.1:9", timeout=0.5))
    out = json.loads(tools.handle_llama_swap_status({}))
    assert out["ok"] is False and "ConnectError" in out["error"] and "127.0.0.1" not in out["error"]


def test_register_wires_tools_hook_and_command():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"llama_swap_models", "llama_swap_status", "llama_swap_load", "llama_swap_unload",
                              "model_download", "model_add", "model_settings", "model_delete"}
    assert ctx.hooks["pre_tool_call"]("llama_swap_load", {"model": "x"}, "")["action"] == "approve"
    assert ctx.hooks["pre_tool_call"]("llama_swap_status", {}, "") is None
    assert "llama" in ctx.commands
    for name, entry in ctx.tools.items():
        assert entry["schema"]["name"] == name and entry["toolset"] == "llama_swap"


def test_a_routed_model_that_is_not_resident_is_reported_as_a_swap(fake, monkeypatch):
    """Naming another model for a job is the only thing that costs a cold start, so the
    tool has to say which jobs would pay it before the agent decides to switch."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-27b")
    monkeypatch.setenv("ROUTING_VISION_MODEL", "qwen3.6-35b")
    out = json.loads(tools.handle_llama_swap_models({}))
    assert out["routing"]["vision"] == "qwen3.6-35b"
    assert out["needs_swap"] == ["vision"]
    by_id = {m["id"]: m for m in out["models"]}
    assert by_id["qwen3.6-35b"]["routed_jobs"] == ["vision"]


def test_the_load_tool_no_longer_advertises_a_model_llama_swap_does_not_serve():
    assert "79b" not in schemas.LLAMA_SWAP_LOAD["description"]


def test_the_client_sends_the_inference_key_llama_swap_requires(monkeypatch):
    monkeypatch.setenv("LLAMA_SWAP_URL", "http://10.0.0.9:8080")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    with tools._client() as c:
        assert c.headers["authorization"] == "Bearer llm-key"
    monkeypatch.delenv("LLM_API_KEY")
    with tools._client() as c:
        assert "authorization" not in c.headers
