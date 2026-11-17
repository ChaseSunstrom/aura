"""aura-settings — reading and changing JARVIS's settings.

Registers the ``settings`` toolset (``settings_list`` / ``settings_get`` /
``settings_set``), a ``pre_tool_call`` gate that routes ``settings_set`` through
the human approval door, and the ``/jsettings`` slash command the apps dispatch.
See :mod:`settings` for the allow-list, validation, the two write paths
(config.yaml for a Hermes key, the override file for an env leaf), the
propose/apply step the command path goes through, and the audit log. Everything Hermes-specific is imported lazily inside :mod:`settings`,
so this package imports (and its tests run) without Hermes on the path — the rule
the sibling plugins follow.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared import _approval_hook, _levels  # noqa: E402
from _shared._approval_hook import approval_gate, guarded_command  # noqa: E402
from . import settings  # noqa: E402

logger = logging.getLogger(__name__)

# Spell the allow-list into the tool descriptions so the model does not guess a
# key or a value shape, waste a turn on the validation error, and only then read
# the schema. Built from the contract table.
def _describe(spec: dict) -> str:
    kind = spec["type"]
    if kind == "int":
        return f"int {spec.get('min')}..{spec.get('max')}"
    if kind == "enum":
        return "one of " + "|".join(spec["values"])
    if kind == "list":
        return "list of strings"
    return kind


_KEYS_LINE = "; ".join(f"{k} ({_describe(v)})" for k, v in settings.KEYS.items())

SETTINGS_LIST = {
    "name": "settings_list",
    "description": (
        "List Hermes's allow-listed settings you may read or change, with each one's "
        "current value, type, whether a restart is needed, and what it does. Call this "
        "before settings_set so you use a real key and a valid value."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SETTINGS_FIND = {
    "name": "settings_find",
    "description": (
        "Find which setting a person means from how they said it. Give the phrase in "
        "their words — \"stop it talking at night\", \"how long before it gives up\" — and "
        "this returns the allow-listed keys that match, with each one's current value and "
        "what it does. Use it when you know what someone wants changed but not which key "
        "holds it; then call settings_set with the key it returns. It searches only keys "
        "that can actually be written, so anything it suggests can be set."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phrase": {
                "type": "string",
                "description": "What the person wants changed, in their own words.",
            }
        },
        "required": ["phrase"],
    },
}

SETTINGS_GET = {
    "name": "settings_get",
    "description": (
        "Read one Hermes setting's current value and whether it came from config or a "
        f"default. key is one of: {_KEYS_LINE}."
    ),
    "parameters": {
        "type": "object",
        "properties": {"key": {"type": "string", "description": "The setting key."}},
        "required": ["key"],
    },
}

SETTINGS_SET = {
    "name": "settings_set",
    "description": (
        "Change one setting (Hermes's own or the house's: settings_find returns both). A setting "
        "that is not about security just changes and is logged; one about auth, approval rules or "
        "device access asks the owner first. Use it only when the person asked for the change. "
        "value is JSON of the key's type "
        "(30, \"smart\", [\"web\",\"browser\"]). Returns the previous and new value and "
        f"whether a restart is needed. key is one of: {_KEYS_LINE}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "The setting key."},
            "value": {"description": "New value, JSON of the key's type (int/string/enum/list)."},
        },
        "required": ["key", "value"],
    },
}


SETTINGS_UNDO = {
    "name": "settings_undo",
    "description": (
        "Take back the newest settings change (or the newest to key): puts back the value it "
        "replaced. NEEDS APPROVAL, like settings_set. Use it when the person says undo that, "
        "put it back, or change it back. One level: a second undo takes the first back. "
        "A secret cannot be undone."
    ),
    "parameters": {
        "type": "object",
        "properties": {"key": {"type": "string", "description": "Only this key's newest change."}},
    },
}


CAPABILITY_SET = {
    "name": "capability_set",
    "description": (
        "Set how much JARVIS may do in one area (a capability) without asking: off (nothing, reads "
        "included), read (look, never change), ask (every change asks) or allowed (a change the "
        "matrix would ask about stops asking; guests, the kill switch, a first message to someone "
        "new and anything destructive still hold). ALWAYS ASKS the owner. Use it only when the "
        "person asked. `/hud capabilities` lists them with their tools. "
        f"capability is one of: {', '.join(_levels.CAPABILITIES)}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {"type": "string", "enum": list(_levels.CAPABILITIES)},
            "level": {"type": "string", "enum": list(_levels.LEVELS)},
        },
        "required": ["capability", "level"],
    },
}


SETUP_GAPS = {
    "name": "setup_gaps",
    "description": (
        "What is not set up: every installed capability (mail, calendar, contacts, files, Jellyfin, "
        "Proxmox, ...) whose tools are hidden because a required setting is unset, with each "
        "setting's name, what it is and where it is set. Use it when the owner asks what is "
        "missing or why something cannot be done, before guessing. With expiry: true it also "
        "says when the GitLab token and each https host's TLS certificate lapse, who reads "
        "each, and a warning line for any within three weeks or unreadable (a few seconds of "
        "network). Never returns a value."
    ),
    "parameters": {"type": "object", "properties": {
        "expiry": {"type": "boolean", "description": "Also check what is about to expire."},
    }},
}


def register(ctx) -> None:
    ctx.register_tool(name="settings_list", toolset=settings.TOOLSET, schema=SETTINGS_LIST,
                      handler=settings.handle_settings_list, emoji="⚙️")
    ctx.register_tool(name="settings_find", toolset=settings.TOOLSET, schema=SETTINGS_FIND,
                      handler=settings.handle_settings_find, emoji="⚙️")
    ctx.register_tool(name="settings_get", toolset=settings.TOOLSET, schema=SETTINGS_GET,
                      handler=settings.handle_settings_get, emoji="⚙️")
    ctx.register_tool(name="settings_set", toolset=settings.TOOLSET, schema=SETTINGS_SET,
                      handler=settings.handle_settings_set, emoji="✅")
    ctx.register_tool(name="settings_undo", toolset=settings.TOOLSET, schema=SETTINGS_UNDO,
                      handler=settings.handle_settings_undo, emoji="↩️")
    ctx.register_tool(name="capability_set", toolset=settings.TOOLSET, schema=CAPABILITY_SET,
                      handler=settings.handle_capability_set, emoji="🔐")
    ctx.register_tool(name="setup_gaps", toolset=settings.TOOLSET, schema=SETUP_GAPS,
                      handler=settings.handle_setup_gaps, emoji="🧩")

    # settings_set writes config.yaml for everyone; route it through the
    # approval door (approvals.mode decides) with the key and both values.
    ctx.register_hook("pre_tool_call", approval_gate(settings.approval_for))
    # A security setting is asked about by the matrix gate, not the hook above (T-0945). It
    # cannot read a value, so it is told here what the call would change (T-1317).
    for tool in ("settings_set", "settings_undo"):
        _approval_hook.CHANGE_DESCRIBERS[tool] = settings.change_for

    ctx.register_command(
        settings.COMMAND_NAME,
        handler=guarded_command(lambda raw: settings.handle_command(raw), "jsettings", logger=logger),
        description=("Read/change JARVIS settings: get <key> | set <key> <json> | list | "
                     "apply <id> | discard <id> | proposals"),
        args_hint="@<session_id> get <key> | set <key> <json> | list | apply <id> | discard <id> | proposals",
    )
