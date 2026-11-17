from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("comfyui-imagegen")
tools = plugin.tools


def test_fill_workflow_replaces_placeholders(monkeypatch):
    monkeypatch.setenv("COMFYUI_CHECKPOINT", "sdxl.safetensors")
    g = tools.fill_workflow(tools.load_workflow(), prompt="a red fox", width=512, height=768, seed=7, steps=12)
    assert g["6"]["inputs"]["text"] == "a red fox"
    assert g["5"]["inputs"] == {"batch_size": 1, "height": 768, "width": 512}
    assert g["3"]["inputs"]["seed"] == 7 and g["3"]["inputs"]["steps"] == 12
    assert g["4"]["inputs"]["ckpt_name"] == "sdxl.safetensors"
    assert "{{" not in json.dumps(g)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "http://comfy.test:8188")
    monkeypatch.setenv("COMFYUI_CHECKPOINT", "ckpt.safetensors")
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.3.60"},
                                             "devices": [{"name": "cuda:0 RTX 3090", "vram_free": 20 * 2**30, "vram_total": 24 * 2**30}]})
        if p == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": [1]})
        if p == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["ckpt.safetensors", "other.ckpt"]]}}}})
        if p == "/object_info/UNETLoader":
            return httpx.Response(200, json={"UNETLoader": {"input": {"required": {"unet_name": [[]]}}}})
        if p == "/prompt":
            body = json.loads(request.content)
            assert body["prompt"]["6"]["inputs"]["text"] == "a teapot"
            return httpx.Response(200, json={"prompt_id": "pid-1", "number": 3})
        if p == "/history/pid-1":
            state["polls"] += 1
            if state["polls"] < 2:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"pid-1": {"status": {"status_str": "success", "messages": []},
                                                        "outputs": {"9": {"images": [{"filename": "jarvis_00001_.png", "subfolder": "", "type": "output"}]}}}})
        if p == "/view":
            return httpx.Response(200, content=b"\x89PNG fake")
        return httpx.Response(404)

    monkeypatch.setattr(tools, "_client", lambda timeout=30.0: httpx.Client(base_url="http://comfy.test:8188", transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    return state


def test_status_submit_result(fake):
    st = json.loads(tools.handle_status({}))
    assert st["version"] == "0.3.60" and st["devices"][0]["vram_free_gb"] == 20.0 and st["pending"] == 1
    assert st["checkpoints"] == ["ckpt.safetensors", "other.ckpt"] and st["diffusion_models"] == [] and st["workflow"] == "txt2img"
    out = json.loads(tools.handle_submit({"prompt": "a teapot", "width": 512, "height": 512}))
    assert out["ok"] and out["prompt_id"] == "pid-1" and out["status"] == "success"
    assert out["images"][0]["url"].endswith("/view?filename=jarvis_00001_.png&subfolder=&type=output")
    assert fake["polls"] == 2
    res = json.loads(tools.handle_result({"prompt_id": "pid-1"}))
    assert res["status"] == "success"
    assert tools.download(out["images"][0]) == b"\x89PNG fake"
    assert json.loads(tools.handle_submit({}))["ok"] is False


def test_unconfigured(monkeypatch):
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    assert "not configured" in json.loads(tools.handle_status({}))["error"]


def test_register_gate():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"comfyui_status", "comfyui_submit", "comfyui_result"}
    gate = ctx.hooks["pre_tool_call"]
    assert gate("comfyui_submit", {}, "")["action"] == "approve" and gate("image_generate", {}, "")["action"] == "approve"
    assert gate("comfyui_status", {}, "") is None


def _client_with(monkeypatch, vram_free_gb: float, installed: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.34.0"},
                                             "devices": [{"name": "cuda:0", "vram_free": int(vram_free_gb * 2**30), "vram_total": 24 * 2**30}]})
        if p == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if p == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [installed]}}}})
        return httpx.Response(500, text="generate must not reach the server when not ready")
    monkeypatch.setenv("COMFYUI_URL", "http://comfy.test:8188")
    monkeypatch.delenv("COMFYUI_CHECKPOINT", raising=False)
    monkeypatch.setattr(tools, "_client", lambda timeout=30.0: httpx.Client(base_url="http://comfy.test:8188", transport=httpx.MockTransport(handler)))


def test_generate_refuses_when_the_language_model_holds_the_gpus(monkeypatch):
    """ComfyUI answered 1.1 GB free per GPU with qwen3.8-27b resident: name the swap, do not submit."""
    _client_with(monkeypatch, vram_free_gb=1.1, installed=["sd15.safetensors"])
    out = tools.generate("a red fox")
    assert out["ok"] is False and "qwen3.6-35b" in out["hint"] and "1.1" in out["error"]


def test_generate_refuses_when_no_checkpoint_is_installed(monkeypatch):
    """ComfyUI's models/checkpoints/ was empty; ComfyUI would fail inside the graph otherwise."""
    _client_with(monkeypatch, vram_free_gb=20, installed=[])
    out = tools.generate("a red fox")
    assert out["ok"] is False and "models/checkpoints" in out["hint"]


def test_generate_picks_the_only_installed_checkpoint(monkeypatch):
    _client_with(monkeypatch, vram_free_gb=20, installed=["sd15.safetensors"])
    assert tools.ready_to_generate() == {"ok": True, "checkpoint": "sd15.safetensors", "vram_free_gb": 20.0}


def test_qwen_image_graph_fills_its_loaders_and_distilled_defaults(monkeypatch):
    """The homelab's ComfyUI carries Qwen-Image as UNET+CLIP+VAE (no checkpoint); the 4-step
    distilled UNET wants steps 4 and cfg 1. This graph is what ComfyUI's own template wires."""
    monkeypatch.setenv("COMFYUI_WORKFLOW", "qwen-image")
    monkeypatch.delenv("COMFYUI_STEPS", raising=False)
    g = tools.fill_workflow(tools.load_workflow(), prompt="a red fox", width=1024, height=1024, seed=7)
    assert g["1"]["inputs"]["unet_name"].startswith("qwen_image_2512_fp8_e4m3fn_scaled_4steps")
    assert g["2"]["inputs"] == {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}
    assert g["3"]["inputs"]["vae_name"] == "qwen_image_vae.safetensors"
    assert g["8"]["inputs"]["steps"] == 4 and g["8"]["inputs"]["cfg"] == 1.0 and g["4"]["inputs"]["shift"] == 3.1
    assert g["7"]["inputs"] == {"width": 1024, "height": 1024, "batch_size": 1}
    assert "{{" not in json.dumps(g)


def test_qwen_image_readiness_checks_the_unet_list_not_checkpoints(monkeypatch):
    monkeypatch.setenv("COMFYUI_WORKFLOW", "qwen-image")
    unet = "qwen_image_2512_fp8_e4m3fn_scaled_4steps_v1.0.safetensors"

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/system_stats":
            return httpx.Response(200, json={"system": {}, "devices": [{"name": "cuda:0", "vram_free": 20 * 2**30, "vram_total": 24 * 2**30}]})
        if p == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if p == "/object_info/UNETLoader":
            return httpx.Response(200, json={"UNETLoader": {"input": {"required": {"unet_name": [[unet]]}}}})
        if p == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [[]]}}}})
        return httpx.Response(500)
    monkeypatch.setenv("COMFYUI_URL", "http://comfy.test:8188")
    monkeypatch.setattr(tools, "_client", lambda timeout=30.0: httpx.Client(base_url="http://comfy.test:8188", transport=httpx.MockTransport(handler)))
    assert tools.ready_to_generate() == {"ok": True, "checkpoint": unet, "vram_free_gb": 20.0}
    monkeypatch.setenv("COMFYUI_UNET", "missing.safetensors")
    assert tools.ready_to_generate()["ok"] is False


def _submitting_against(monkeypatch, status: int, body: bytes = b""):
    """A client whose /prompt answers *status*, so the refusal shape can be read back."""
    monkeypatch.setenv("COMFYUI_URL", "https://comfy.test:8188")
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=body))
    monkeypatch.setattr(tools, "_client",
                        lambda timeout=30.0: httpx.Client(base_url="https://comfy.test:8188",
                                                          transport=transport))
    return json.loads(tools.handle_submit({"prompt": "a red sports car"}))


def test_an_empty_gateway_error_gives_the_status_and_a_hint(monkeypatch):
    """The state ComfyUI is actually in on this homelab: a front's 502 with no body.

    The agent gets the number and a fixed sentence, never an empty string (it once reported the
    generator was "approval-gated" instead) and never the front's address (T-1363).
    """
    out = _submitting_against(monkeypatch, 502)
    assert out["ok"] is False and out["http"] == 502
    assert out["error"] == "HTTP 502: the server failed: try again later", out
    assert "comfy.test" not in out["error"]


def test_the_services_own_text_is_not_passed_on(monkeypatch):
    """ComfyUI's body is the server's text, and a hostile one gives orders (T-1363)."""
    out = _submitting_against(monkeypatch, 400, b"invalid prompt: node 3 has no such input")
    assert out == {"ok": False, "http": 400, "error": "HTTP 400: the server refused the request"}


def test_a_status_with_no_body_is_the_status_and_its_hint(monkeypatch):
    out = _submitting_against(monkeypatch, 404)
    assert out["error"].startswith("HTTP 404: ")
