"""my-plugin — minimal plugin registering a single tool."""
from __future__ import annotations

import json

TOOLSET = "my_toolset"

ECHO_SCHEMA = {
    "name": "my_echo",
    "description": "Return its input unchanged.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to echo back"},
        },
        "required": ["text"],
    },
}


def handle_my_echo(args: dict, **kwargs) -> str:
    return json.dumps({"success": True, "echo": args.get("text", "")})


def register(ctx) -> None:
    ctx.register_tool(
        name="my_echo",
        toolset=TOOLSET,
        schema=ECHO_SCHEMA,
        handler=handle_my_echo,
        check_fn=lambda: True,
        emoji="🔁",
    )
    # Approval gate example (pre_tool_call). Return None to allow, or a dict to hold:
    # def _gate(tool_name: str, args: dict, task_id: str = "", **kw):
    #     if tool_name in {"my_echo"}:
    #         return {"action": "approve", "message": "...", "rule_key": "my:echo"}
    #     return None
    # ctx.register_hook("pre_tool_call", _gate)
    # Custom slash command example:
    # ctx.register_command("myecho", handler=lambda raw: handle_my_echo({"text": raw or ""}),
    #                      description="echo text back", args_hint="[text]")
