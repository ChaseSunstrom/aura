"""llama-swap-control — JARVIS picks and inspects models in jarvis-models."""
from __future__ import annotations

import sys
from pathlib import Path

from aura_schema import from_env

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared import _approval_hook  # noqa: E402
from _shared._approval_hook import approval_gate  # noqa: E402
from . import schemas, tools  # noqa: E402

TOOLSET = "llama_swap"


def _available() -> bool:
    return bool(from_env().llama_swap_url)


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped LLAMA_SWAP_* value names itself at load
    ctx.register_tool(name="llama_swap_models", toolset=TOOLSET, schema=schemas.LLAMA_SWAP_MODELS,
                      handler=tools.handle_llama_swap_models, check_fn=_available, emoji="🧠")
    ctx.register_tool(name="llama_swap_status", toolset=TOOLSET, schema=schemas.LLAMA_SWAP_STATUS,
                      handler=tools.handle_llama_swap_status, check_fn=_available, emoji="🧠")
    ctx.register_tool(name="llama_swap_load", toolset=TOOLSET, schema=schemas.LLAMA_SWAP_LOAD,
                      handler=tools.handle_llama_swap_load, check_fn=_available, emoji="🔁")
    ctx.register_tool(name="llama_swap_unload", toolset=TOOLSET, schema=schemas.LLAMA_SWAP_UNLOAD,
                      handler=tools.handle_llama_swap_unload, check_fn=_available, emoji="🔁")
    # The model manager's writes. Their approval is the matrix's (model:* rows), not this
    # plugin's hook below: two gates disagreeing resolve by load order.
    for name, schema, handler, emoji in (
            ("model_download", schemas.MODEL_DOWNLOAD, tools.handle_model_download, "⬇️"),
            ("model_add", schemas.MODEL_ADD, tools.handle_model_add, "➕"),
            ("model_settings", schemas.MODEL_SETTINGS, tools.handle_model_settings, "🛠️"),
            ("model_delete", schemas.MODEL_DELETE, tools.handle_model_delete, "🗑️")):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_available, emoji=emoji)

    # The settings card shows each changed setting as it is now and as it becomes (T-1329).
    _approval_hook.CHANGE_DESCRIBERS["model_settings"] = tools.model_settings_change

    def _gate(tool_name: str, args: dict):
        # A model swap evicts whatever is resident for everyone using jarvis-models:
        # route it through the human approval gate (approvals.mode decides).
        if tool_name in schemas.DESTRUCTIVE:
            what = args.get("model", "all models") if isinstance(args, dict) else "all models"
            return {"action": "approve", "message": f"{tool_name} on llama-swap ({what}) evicts the resident model for minutes",
                    "rule_key": f"llama-swap:{tool_name}"}
        return None

    ctx.register_hook("pre_tool_call", approval_gate(_gate))

    def _cmd(raw_args: str) -> str:
        raw = (raw_args or "").strip()
        if not raw or raw == "status":
            return tools.handle_llama_swap_status({})
        if raw in ("models", "list"):
            return tools.handle_llama_swap_models({})
        return "usage: /llama [status|models]"

    ctx.register_command("llama", handler=_cmd, description="llama-swap: which model is loaded / list models",
                         args_hint="[status|models]")
