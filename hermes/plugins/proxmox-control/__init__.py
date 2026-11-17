"""proxmox-control — LXC/VM status and lifecycle through the Proxmox API."""
from __future__ import annotations

import sys
from pathlib import Path

from aura_schema import from_env

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared._approval_hook import approval_gate  # noqa: E402
from . import schemas, tools  # noqa: E402

TOOLSET = "proxmox"


def _available() -> bool:
    cfg = from_env()
    return bool(cfg.proxmox_url and cfg.proxmox_token_id and cfg.proxmox_token_secret)


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped PROXMOX_* value names itself at load
    ctx.register_tool(name="proxmox_nodes", toolset=TOOLSET, schema=schemas.PROXMOX_NODES,
                      handler=tools.handle_nodes, check_fn=_available, emoji="🖥️")
    ctx.register_tool(name="proxmox_guests", toolset=TOOLSET, schema=schemas.PROXMOX_GUESTS,
                      handler=tools.handle_guests, check_fn=_available, emoji="🖥️")
    ctx.register_tool(name="proxmox_guest_status", toolset=TOOLSET, schema=schemas.PROXMOX_GUEST_STATUS,
                      handler=tools.handle_guest_status, check_fn=_available, emoji="🖥️")
    ctx.register_tool(name="proxmox_guest_action", toolset=TOOLSET, schema=schemas.PROXMOX_GUEST_ACTION,
                      handler=tools.handle_guest_action, check_fn=_available, emoji="⚠️")

    def _gate(tool_name: str, args: dict):
        if tool_name in schemas.DESTRUCTIVE:
            a = args if isinstance(args, dict) else {}
            if a.get("action") in ("start", "resume"):
                return None  # bringing a guest up is not destructive
            return {"action": "approve", "message": f"Proxmox {a.get('action')} on vmid {a.get('vmid')} interrupts that guest",
                    "rule_key": f"proxmox:{a.get('action')}:{a.get('vmid')}"}
        return None

    ctx.register_hook("pre_tool_call", approval_gate(_gate))
    ctx.register_command("pve", handler=lambda raw: tools.handle_guests({"node": raw.strip() or None}),
                         description="Proxmox guests and their state", args_hint="[node]")
