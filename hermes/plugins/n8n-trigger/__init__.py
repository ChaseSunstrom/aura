"""n8n-trigger — fire, inspect and author n8n workflows.

Registers the ``n8n`` toolset. Read-only: ``n8n_health``, ``n8n_list_workflows``,
``n8n_executions``, ``n8n_get_workflow``. Behind the approval gate:
``n8n_trigger`` (runs a workflow through its webhook) and the three that write
the workflow store — ``n8n_create_workflow``, ``n8n_update_workflow``,
``n8n_activate_workflow``. The gated names live in one place,
:data:`schemas.DESTRUCTIVE`, and the message each one shows is
:func:`tools.build_approval`.

Nothing here reaches the network at import time, so the package loads — and its
tests run — with no n8n anywhere, the rule the sibling plugins follow.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from aura_schema import from_env  # noqa: E402

from _shared import _n8n_setup  # noqa: E402
from _shared._approval_hook import approval_gate, guarded_command  # noqa: E402
from . import schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

TOOLSET = "n8n"


def _available() -> bool:
    return bool(from_env().n8n_url)


def _api() -> bool:
    return bool(from_env().n8n_api_key)


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped N8N_* value names itself at load
    ctx.register_tool(name="n8n_trigger", toolset=TOOLSET, schema=schemas.N8N_TRIGGER,
                      handler=tools.handle_n8n_trigger, check_fn=_available, emoji="⚡")
    ctx.register_tool(name="n8n_health", toolset=TOOLSET, schema=schemas.N8N_HEALTH,
                      handler=tools.handle_n8n_health, check_fn=_available, emoji="⚡")
    ctx.register_tool(name="n8n_list_workflows", toolset=TOOLSET, schema=schemas.N8N_LIST,
                      handler=tools.handle_n8n_list, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_executions", toolset=TOOLSET, schema=schemas.N8N_EXECUTIONS,
                      handler=tools.handle_n8n_executions, check_fn=_api, emoji="⚡")
    # The four below are the REST API only — a webhook needs no key, a workflow
    # read or write always does, so they gate on `_api` rather than `_available`.
    ctx.register_tool(name="n8n_get_workflow", toolset=TOOLSET, schema=schemas.N8N_GET,
                      handler=tools.handle_n8n_get_workflow, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_execution", toolset=TOOLSET, schema=schemas.N8N_EXECUTION,
                      handler=tools.handle_n8n_execution, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_delete_workflow", toolset=TOOLSET, schema=schemas.N8N_DELETE,
                      handler=tools.handle_n8n_delete_workflow, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_credential_schema", toolset=TOOLSET, schema=schemas.N8N_CREDENTIAL_SCHEMA,
                      handler=tools.handle_n8n_credential_schema, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_credential_create", toolset=TOOLSET, schema=schemas.N8N_CREDENTIAL_CREATE,
                      handler=tools.handle_n8n_credential_create, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_credential_delete", toolset=TOOLSET, schema=schemas.N8N_CREDENTIAL_DELETE,
                      handler=tools.handle_n8n_credential_delete, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_create_workflow", toolset=TOOLSET, schema=schemas.N8N_CREATE,
                      handler=tools.handle_n8n_create_workflow, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_update_workflow", toolset=TOOLSET, schema=schemas.N8N_UPDATE,
                      handler=tools.handle_n8n_update_workflow, check_fn=_api, emoji="⚡")
    ctx.register_tool(name="n8n_activate_workflow", toolset=TOOLSET, schema=schemas.N8N_ACTIVATE,
                      handler=tools.handle_n8n_activate_workflow, check_fn=_api, emoji="⚡")

    ctx.register_hook("pre_tool_call", approval_gate(tools.build_approval))
    # AURA sets n8n up itself: a fresh n8n gets an owner and a key, a reset one a new key
    # (T-1523, _shared/_n8n_setup.py). A thread, so the gateway's start never waits on n8n.
    _n8n_setup.start()
    ctx.register_command("n8n",
                         handler=guarded_command(tools.handle_command, "n8n", logger=logger),
                         description="n8n health, or `list` for workflows", args_hint="[list]")
