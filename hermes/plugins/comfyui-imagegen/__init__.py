"""comfyui-imagegen — ComfyUI as an image_generate backend plus raw workflow tools."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from aura_schema import from_env

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared._approval_hook import approval_gate  # noqa: E402
from . import schemas, tools  # noqa: E402

TOOLSET = "comfyui"


def _available() -> bool:
    return bool(from_env().comfyui_url)


def _provider_class():
    """Build the ImageGenProvider subclass lazily (the ABC lives in Hermes)."""
    from agent.image_gen_provider import (
        DEFAULT_ASPECT_RATIO,
        ImageGenProvider,
        error_response,
        resolve_aspect_ratio,
        save_b64_image,
        success_response,
    )
    import base64

    SIZES = {"square": (1024, 1024), "landscape": (1216, 832), "portrait": (832, 1216)}

    class ComfyUIImageGenProvider(ImageGenProvider):
        @property
        def name(self) -> str:
            return "comfyui"

        @property
        def display_name(self) -> str:
            return "ComfyUI (local)"

        def is_available(self) -> bool:
            return _available()

        def list_models(self) -> List[Dict[str, Any]]:
            try:
                return [{"id": c, "display": c, "speed": "local GPU", "strengths": "checkpoint", "price": "free"}
                        for c in tools.checkpoints()]
            except Exception:
                return []

        def default_model(self) -> Optional[str]:
            return from_env().comfyui_checkpoint or None

        def get_setup_schema(self) -> Dict[str, Any]:
            return {"name": "ComfyUI (local)", "badge": "local", "tag": "Your own ComfyUI service, no API key",
                    "env_vars": [{"key": "COMFYUI_URL", "prompt": "ComfyUI base URL (http://host:8188)", "url": ""},
                                 {"key": "COMFYUI_CHECKPOINT", "prompt": "Default checkpoint file name", "url": ""}]}

        def capabilities(self) -> Dict[str, Any]:
            return {"modalities": ["text"], "max_reference_images": 0}

        def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, *, image_url=None,
                     reference_image_urls=None, **kwargs: Any) -> Dict[str, Any]:
            prompt = (prompt or "").strip()
            aspect_ratio = resolve_aspect_ratio(aspect_ratio)
            model_id = kwargs.get("model") or self.default_model() or ""
            if not prompt:
                return error_response(error="Prompt is required", error_type="invalid_input", provider=self.name,
                                      prompt="", aspect_ratio=aspect_ratio)
            w, h = SIZES.get(aspect_ratio, SIZES["square"])
            try:
                res = tools.generate(prompt, width=w, height=h, checkpoint=model_id or None)
                if not res.get("ok") or not res.get("images"):
                    return error_response(error=str(res.get("error") or res.get("status") or "no image produced"),
                                          error_type="provider_error", provider=self.name, model=model_id,
                                          prompt=prompt, aspect_ratio=aspect_ratio)
                raw = tools.download(res["images"][0])
                path = save_b64_image(base64.b64encode(raw).decode("ascii"), prefix="comfyui", extension="png")
                return success_response(image=str(path), model=model_id, prompt=prompt, aspect_ratio=aspect_ratio,
                                        provider=self.name, modality="text",
                                        extra={"prompt_id": res.get("prompt_id"), "elapsed_s": res.get("elapsed_s")})
            except Exception as exc:  # the provider contract wants a dict, never an exception
                return error_response(error=str(exc), error_type=type(exc).__name__, provider=self.name,
                                      model=model_id, prompt=prompt, aspect_ratio=aspect_ratio)

    return ComfyUIImageGenProvider


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped COMFYUI_* value names itself at load, not on the first image
    ctx.register_tool(name="comfyui_status", toolset=TOOLSET, schema=schemas.COMFYUI_STATUS,
                      handler=tools.handle_status, check_fn=_available, emoji="🎨")
    ctx.register_tool(name="comfyui_submit", toolset=TOOLSET, schema=schemas.COMFYUI_SUBMIT,
                      handler=tools.handle_submit, check_fn=_available, emoji="🎨")
    ctx.register_tool(name="comfyui_result", toolset=TOOLSET, schema=schemas.COMFYUI_RESULT,
                      handler=tools.handle_result, check_fn=_available, emoji="🎨")

    if hasattr(ctx, "register_image_gen_provider"):
        try:
            ctx.register_image_gen_provider(_provider_class()())
        except Exception:  # Hermes without the image-gen ABC (tests) — the raw tools still work
            pass

    def _gate(tool_name: str, args: dict):
        if tool_name in schemas.DESTRUCTIVE:
            return {"action": "approve", "message": "Image generation occupies the GPU for a while", "rule_key": "comfyui:generate"}
        return None

    ctx.register_hook("pre_tool_call", approval_gate(_gate))
    ctx.register_command("comfy", handler=lambda raw: tools.handle_status({}), description="ComfyUI status, VRAM and queue")
