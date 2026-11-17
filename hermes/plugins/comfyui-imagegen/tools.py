"""ComfyUI HTTP client: submit a workflow (API-format JSON), poll history, fetch images.

Endpoints (ComfyUI standalone service):
* ``GET  /system_stats``            — version, devices (VRAM free/total)
* ``GET  /queue``                   — running / pending prompts
* ``POST /prompt`` ``{prompt, client_id}`` → ``{prompt_id, number}``
* ``GET  /history/{prompt_id}``      — outputs once finished
* ``GET  /view?filename=&subfolder=&type=`` — image bytes
* ``GET  /object_info/CheckpointLoaderSimple`` — available checkpoints

The default text-to-image graph (``data/txt2img.json``) is a plain
CheckpointLoaderSimple → CLIPTextEncode ×2 → EmptyLatentImage → KSampler →
VAEDecode → SaveImage pipeline with ``{{PROMPT}}``-style placeholders; a custom
graph can be given with ``COMFYUI_WORKFLOW`` (path to an API-format JSON).

Every ``COMFYUI_*`` value is read through the one settings schema
(``aura_schema.from_env()``), so the names, types and defaults live there.
"""
from __future__ import annotations

import copy
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from aura_schema import from_env

from _shared import _egress, _failure

HERE = Path(__file__).parent
DEFAULT_WORKFLOW = HERE / "data" / "txt2img.json"


def base_url() -> str:
    return from_env().comfyui_url.rstrip("/")


def _client(timeout: float = 30.0) -> httpx.Client:
    # The homelab's ComfyUI sits behind a self-signed HTTPS front (ai:8188), so a
    # verify switch like the Grafana client's.
    cfg = from_env()
    return httpx.Client(base_url=_egress.service_url(cfg.comfyui_url, "COMFYUI_URL"), timeout=timeout,
                        verify=cfg.comfyui_verify_ssl)


def system_stats() -> dict[str, Any]:
    with _client(10) as c:
        stats = c.get("/system_stats").raise_for_status().json()
        queue = c.get("/queue").raise_for_status().json()
    devices = [{"name": d.get("name"), "vram_free_gb": round(float(d.get("vram_free") or 0) / 2**30, 1),
                "vram_total_gb": round(float(d.get("vram_total") or 0) / 2**30, 1)} for d in stats.get("devices", [])]
    return {"ok": True, "version": (stats.get("system") or {}).get("comfyui_version"), "devices": devices,
            "running": len(queue.get("queue_running", [])), "pending": len(queue.get("queue_pending", []))}


def checkpoints() -> list[str]:
    with _client(10) as c:
        info = c.get("/object_info/CheckpointLoaderSimple").raise_for_status().json()
    try:
        return list(info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0])
    except (KeyError, IndexError, TypeError):
        return []


#: Shipped graphs by name. ``qwen-image`` is the homelab's: a UNET/CLIP/VAE trio
#: (Qwen-Image 2512, its 4-step distilled variant needs no LoRA), not a checkpoint.
WORKFLOWS = {"txt2img": DEFAULT_WORKFLOW, "qwen-image": Path(__file__).parent / "data" / "qwen-image.json"}
QWEN_DEFAULTS = {
    "COMFYUI_UNET": "qwen_image_2512_fp8_e4m3fn_scaled_4steps_v1.0.safetensors",
    "COMFYUI_CLIP": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "COMFYUI_VAE": "qwen_image_vae.safetensors",
}


def workflow_name() -> str:
    return from_env().comfyui_workflow or "txt2img"


def load_workflow() -> dict[str, Any]:
    name = workflow_name()
    path = WORKFLOWS.get(name) or Path(name)   # a bare shipped name, or a file the owner dropped in
    return json.loads(path.read_text())


def unets() -> list[str]:
    with _client(10) as c:
        info = c.get("/object_info/UNETLoader").raise_for_status().json()
    try:
        return list(info["UNETLoader"]["input"]["required"]["unet_name"][0])
    except (KeyError, IndexError, TypeError):
        return []


def fill_workflow(graph: dict[str, Any], *, prompt: str, negative: str = "", width: int = 1024, height: int = 1024,
                  seed: int | None = None, steps: int | None = None, checkpoint: str | None = None) -> dict[str, Any]:
    """Replace placeholders anywhere in the graph; pure, returns a copy."""
    cfg = from_env()
    qwen = workflow_name() == "qwen-image"
    subs = {
        "{{PROMPT}}": prompt, "{{NEGATIVE}}": negative or cfg.comfyui_negative,
        "{{WIDTH}}": int(width), "{{HEIGHT}}": int(height),
        "{{SEED}}": int(seed if seed is not None else int.from_bytes(os.urandom(4), "big")),
        "{{STEPS}}": int(steps or cfg.comfyui_steps or 20),
        "{{CHECKPOINT}}": checkpoint or cfg.comfyui_checkpoint,
        # The Qwen-Image graph: a distilled 4-step model wants steps 4 and cfg 1; AuraFlow shift 3.1 is ComfyUI's template value.
        "{{UNET}}": checkpoint or cfg.comfyui_unet or QWEN_DEFAULTS["COMFYUI_UNET"],
        "{{CLIP}}": cfg.comfyui_clip or QWEN_DEFAULTS["COMFYUI_CLIP"],
        "{{VAE}}": cfg.comfyui_vae or QWEN_DEFAULTS["COMFYUI_VAE"],
        "{{CFG}}": float(cfg.comfyui_cfg if cfg.comfyui_cfg is not None else (1.0 if qwen else 7)),
        "{{SHIFT}}": float(cfg.comfyui_shift),
    }
    if qwen and not steps and cfg.comfyui_steps is None:
        subs["{{STEPS}}"] = 4

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str) and node in subs:
            return subs[node]
        if isinstance(node, str):
            out = node
            for k, v in subs.items():
                if k in out:
                    out = out.replace(k, str(v))
            return out
        return node

    return walk(copy.deepcopy(graph))


def submit(graph: dict[str, Any]) -> dict[str, Any]:
    client_id = f"jarvis-{uuid.uuid4().hex[:8]}"
    with _client() as c:
        r = c.post("/prompt", json={"prompt": graph, "client_id": client_id})
        if r.status_code >= 400:
            # Status and a fixed hint (T-1348): ComfyUI's own text and the address are not passed
            # on. A 502 from a front with nothing behind it reads "the server failed".
            return _failure.status_answer(r.status_code, r.headers.get("retry-after"))
        data = r.json()
    return {"ok": True, "prompt_id": data.get("prompt_id"), "number": data.get("number"), "client_id": client_id}


def result(prompt_id: str) -> dict[str, Any]:
    with _client() as c:
        hist = c.get(f"/history/{prompt_id}").raise_for_status().json()
    entry = hist.get(prompt_id)
    if not entry:
        return {"ok": True, "prompt_id": prompt_id, "status": "pending", "images": []}
    status = (entry.get("status") or {}).get("status_str") or "completed"
    images = []
    for node_out in (entry.get("outputs") or {}).values():
        for im in node_out.get("images", []) or []:
            images.append({"filename": im.get("filename"), "subfolder": im.get("subfolder", ""), "type": im.get("type", "output"),
                           "url": f"{base_url()}/view?filename={im.get('filename')}&subfolder={im.get('subfolder', '')}&type={im.get('type', 'output')}"})
    return {"ok": status != "error", "prompt_id": prompt_id, "status": status, "images": images,
            "messages": [m for m in ((entry.get("status") or {}).get("messages") or [])][-3:]}


def wait_for(prompt_id: str, timeout: float = 600.0, poll: float = 2.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = result(prompt_id)
        if res.get("status") != "pending":
            return res
        time.sleep(poll)
    return {"ok": False, "prompt_id": prompt_id, "status": "timeout", "images": []}


def download(image: dict[str, Any]) -> bytes:
    with _client(60) as c:
        r = c.get("/view", params={"filename": image["filename"], "subfolder": image.get("subfolder", ""),
                                   "type": image.get("type", "output")})
        r.raise_for_status()
    return r.content


def ready_to_generate(checkpoint: str | None = None) -> dict[str, Any]:
    """Refuse early, with the fix named, instead of letting ComfyUI fail mid-graph.

    Two things the homelab actually got wrong: the GPUs were full because the resident
    language model takes 95 % of them (load ``qwen3.6-35b`` first, it leaves room), and
    ComfyUI's ``models/checkpoints/`` was empty. Does not check that the named
    checkpoint fits the free memory — ComfyUI reports that itself.
    """
    cfg = from_env()
    need = float(cfg.comfyui_min_vram_gb)
    stats = system_stats()
    free = max((d.get("vram_free_gb") or 0) for d in stats.get("devices") or [{}]) if stats.get("devices") else None
    if free is not None and free < need:
        return {"ok": False, "error": f"only {free} GB of VRAM free (need {need:g}); the language model holds the GPUs",
                "hint": "call llama_swap_load with model qwen3.6-35b first, then retry; the default model reloads on the next chat turn"}
    if workflow_name() == "qwen-image":
        have = unets()
        want = checkpoint or cfg.comfyui_unet or QWEN_DEFAULTS["COMFYUI_UNET"]
        if want not in have:
            return {"ok": False, "error": f"diffusion model {want!r} is not installed (models/diffusion_models/)", "installed": have}
        return {"ok": True, "checkpoint": want, "vram_free_gb": free}
    have = checkpoints()
    want = checkpoint or cfg.comfyui_checkpoint
    if not want and not have:
        return {"ok": False, "error": "no checkpoint model is installed in ComfyUI (models/checkpoints/ is empty)",
                "hint": "put a .safetensors checkpoint in ComfyUI's models/checkpoints/, or set COMFYUI_CHECKPOINT"}
    if want and have and want not in have:
        return {"ok": False, "error": f"checkpoint {want!r} is not installed", "installed": have}
    return {"ok": True, "checkpoint": want or have[0], "vram_free_gb": free}


def generate(prompt: str, **kw) -> dict[str, Any]:
    ready = ready_to_generate(kw.get("checkpoint"))
    if not ready["ok"]:
        return ready
    kw["checkpoint"] = ready["checkpoint"]
    graph = fill_workflow(load_workflow(), prompt=prompt, **kw)
    sub = submit(graph)
    if not sub.get("ok"):
        return sub
    t0 = time.time()
    res = wait_for(sub["prompt_id"], timeout=float(from_env().comfyui_timeout))
    res["elapsed_s"] = round(time.time() - t0, 1)
    return res


# ------------------------------------------------------------- handlers
def _safe(fn):
    def handler(params: dict, **kwargs):
        del kwargs
        if not base_url():
            return json.dumps({"ok": False, "error": "ComfyUI is not configured (COMFYUI_URL)"})
        try:
            return json.dumps(fn(params or {}))
        except (httpx.HTTPError, ValueError, TypeError, OSError) as exc:
            return json.dumps(_failure.of(exc) or {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"})
    return handler


def _submit(p: dict) -> dict[str, Any]:
    if p.get("workflow"):
        graph = p["workflow"] if isinstance(p["workflow"], dict) else json.loads(p["workflow"])
    else:
        prompt = str(p.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "prompt (or workflow) is required"}
        graph = fill_workflow(load_workflow(), prompt=prompt, negative=str(p.get("negative") or ""),
                              width=int(p.get("width") or 1024), height=int(p.get("height") or 1024),
                              seed=p.get("seed"), steps=p.get("steps"), checkpoint=p.get("checkpoint"))
    out = submit(graph)
    if out.get("ok") and p.get("wait", True):
        out.update(wait_for(out["prompt_id"], timeout=float(p.get("timeout") or from_env().comfyui_timeout)))
    return out


handle_status = _safe(lambda p: {**system_stats(), "workflow": workflow_name(), "checkpoints": checkpoints(), "diffusion_models": unets()})
handle_submit = _safe(_submit)
handle_result = _safe(lambda p: result(str(p.get("prompt_id") or "")))
