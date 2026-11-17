"""smart-home — areas, automations, paired devices and notifications over jarvis-core."""
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

TOOLSET = "smart_home"


def _available() -> bool:
    return bool(from_env().house_token)


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped HASS_* value names itself at load
    for name, schema, handler, emoji in (
        ("house_areas", schemas.HOUSE_AREAS, tools.handle_areas, "🏠"),
        ("house_automations", schemas.HOUSE_AUTOMATIONS, tools.handle_automations, "🏠"),
        ("house_automation_control", schemas.HOUSE_AUTOMATION_CONTROL, tools.handle_automation_control, "🏠"),
        ("house_automation_create", schemas.HOUSE_AUTOMATION_CREATE, tools.handle_automation_create, "🏠"),
        ("house_devices", schemas.HOUSE_DEVICES, tools.handle_devices, "📱"),
        ("house_device_action", schemas.HOUSE_DEVICE_ACTION, tools.handle_device_action, "📱"),
        ("house_tell_user", schemas.HOUSE_TELL_USER, tools.handle_tell_user, "🔔"),
        ("house_history", schemas.HOUSE_HISTORY, tools.handle_history, "📜"),
        ("house_since", schemas.HOUSE_SINCE, tools.handle_since, "📜"),
        ("house_timer", schemas.HOUSE_TIMER, tools.handle_timer, "⏲"),
        ("house_undo", schemas.HOUSE_UNDO, tools.handle_undo, "↩"),
        ("voice_person_forget", schemas.VOICE_PERSON_FORGET, tools.handle_voice_person_forget, "🗣"),
        ("device_name", schemas.DEVICE_NAME, tools.handle_device_name, "🏷"),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, check_fn=_available, emoji=emoji)

    # The naming card shows the name the house has for that address and the one asked for (T-1329).
    _approval_hook.CHANGE_DESCRIBERS["device_name"] = tools.device_name_change

    def _gate(tool_name: str, args: dict):
        if tool_name in schemas.DESTRUCTIVE:
            a = args if isinstance(args, dict) else {}
            what = a.get("automation_id") or a.get("alias") or f"{a.get('device_id')}:{a.get('action')}"
            return {"action": "approve", "message": f"{tool_name} on {what} changes the house or acts on a device",
                    "rule_key": f"smart-home:{tool_name}"}
        return None

    ctx.register_hook("pre_tool_call", approval_gate(_gate))
    ctx.register_command("house", handler=lambda raw: tools.handle_areas({}) if raw.strip() != "automations" else tools.handle_automations({}),
                         description="House areas (or `automations`)", args_hint="[automations]")
