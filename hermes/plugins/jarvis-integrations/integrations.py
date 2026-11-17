"""jarvis-integrations — browse and install what Hermes already knows how to fetch.

Three catalogues (plugins, skills, MCP servers) reached one way: the agent uses
the ``integration_*`` tools, the apps and a clicking person use the
``/jintegrations`` slash command, and both land on the same normalised JSON.
:mod:`catalogs` is the network side (shells out to ``hermes``); this module is
the contract side — the kinds/actions/row shape, argument validation, command
parsing, the approval-message the gate shows before anything changes an install,
and the audit line every change writes.

The machine contract both this module and the console tests read is
``tests/contracts/hermes-integrations.json``. The constants below (KINDS,
ACTIONS, GATED_TOOLS, ROW_FIELDS, TOOLS, COMMAND_NAME, TOOLSET, AUDIT_LOG,
COMMAND_SHAPES) are this plugin's copy of it, so the plugin works when copied
into ``~/.hermes/plugins/`` away from the repo; the test reads the JSON and
fails if they drift.

What is gated: read actions (``search``, ``inspect``, ``list``) run freely;
anything that changes an install (``install``, ``update``, ``remove``,
``enable``/``disable``) goes through the ``pre_tool_call`` approval hook on the
tool path (see :func:`build_approval`). The command path is a person clicking in
the console, so it is not gated, but — like the tool path — it writes an audit
line, so every change has a record of who did what.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from _shared._approval_hook import split_session
from . import catalogs

_LOGGER = logging.getLogger(__name__)

#: The three catalogues. ``all`` is accepted by search/list only (merges them).
KINDS: list[str] = ["plugin", "skill", "mcp"]
#: Every action the command and the tools expose.
ACTIONS: list[str] = ["search", "inspect", "list", "install", "update", "remove", "enable"]
#: The command verbs (``enable``/``disable`` both map to the enable tool).
COMMAND_VERBS: list[str] = ["search", "inspect", "list", "install", "update", "remove",
                            "enable", "disable"]
#: Tools whose call must pass the approval gate — the ones that change an install.
GATED_TOOLS: list[str] = ["integration_install", "integration_update",
                          "integration_remove", "integration_enable"]
#: The seven fields every row carries (mirrors :data:`catalogs.ROW_FIELDS`).
ROW_FIELDS: list[str] = list(catalogs.ROW_FIELDS)
#: The toolset the seven tools live under (a plugin toolset key; on by default).
TOOLSET: str = "integrations"
#: The slash command name.
COMMAND_NAME: str = "jintegrations"
#: All seven tools, in registration order.
TOOLS: list[str] = ["integration_search", "integration_inspect", "integration_list",
                    "integration_install", "integration_update", "integration_remove",
                    "integration_enable"]
#: Audit log, relative to HERMES_HOME; one JSON line per change (both paths).
AUDIT_LOG: str = "logs/integrations.jsonl"
#: Command argument shapes, echoed in usage and mirrored in the contract.
COMMAND_SHAPES: dict[str, str] = {
    "search": "@<sid> search <kind> <query>",
    "inspect": "@<sid> inspect <kind> <id>",
    "list": "@<sid> list <kind>",
    "install": "@<sid> install <kind> <id>",
    "update": "@<sid> update <kind> <id>",
    "remove": "@<sid> remove <kind> <id>",
    "enable": "@<sid> enable <kind> <id>",
    "disable": "@<sid> disable <kind> <id>",
}

USAGE = (
    "usage: /jintegrations @<sid> "
    "search|list <kind> [query] | inspect|install|update|remove|enable|disable <kind> <id> "
    f"(kind: {', '.join(KINDS)}, or 'all' for search/list)"
)

# What the approval message says the fetch will do, per kind — the honest
# worst case, shown even if the best-effort source/trust lookup comes back empty.
_FETCH_DESC: dict[str, str] = {
    "plugin": "clone and install a Hermes plugin (code that runs inside the gateway)",
    "skill": "download a SKILL.md and its files into the skills volume",
    "mcp": "add an MCP server (an external process or endpoint that exposes tools)",
}
_STATIC_SOURCE: dict[str, str] = {
    "plugin": "the community plugin index / a git URL you named",
    "skill": "skills.sh / ClawHub / a well-known skills endpoint",
    "mcp": "the Nous-approved MCP catalog",
}
_STATIC_TRUST: dict[str, str] = {
    "plugin": "unaudited (indexed ≠ audited)",
    "skill": "community (scanned, not audited)",
    "mcp": "Nous-reviewed",
}


class ArgError(ValueError):
    """A tool or command called with an argument that cannot be honoured."""


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
def _hermes_home() -> Path:
    """HERMES_HOME (env, else ``~/.hermes``) — the runtime's own resolution.

    Read at call time, not import time, so a test can point it at a tmp dir.
    """
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def audit_path() -> Path:
    return _hermes_home() / AUDIT_LOG


def audit(actor: str, session: str, action: str, kind: str, mid: str) -> None:
    """Append one change to the audit log; a logging failure never fails the op.

    Only mutating actions are logged (install/update/remove/enable/disable) —
    a browse does not change an install, so it leaves no line.
    """
    line = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": actor, "session": session or "", "action": action,
        "kind": kind, "id": mid,
    }
    try:
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except OSError:  # an unwritable log must not sink the install it records
        _LOGGER.exception("integrations audit write failed: %s", line)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _check_kind(kind: str, *, allow_all: bool) -> str:
    kind = (kind or "").strip().lower()
    if kind in KINDS or (allow_all and kind == "all"):
        return kind
    extra = " (or 'all')" if allow_all else ""
    raise ArgError(f"unknown kind {kind!r}; valid: {', '.join(KINDS)}{extra}")


def _check_id(mid: str) -> str:
    mid = (mid or "").strip()
    if not mid:
        raise ArgError("an id is required")
    return mid


# --------------------------------------------------------------------------- #
# Actions (shared by the tools and the command)
# --------------------------------------------------------------------------- #
def _ok_rows(kind: str, action: str, rows: list[dict[str, Any]], **extra: Any) -> str:
    return json.dumps({"ok": True, "kind": kind, "action": action,
                       "count": len(rows), "rows": rows, **extra})


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra})


def do_search(kind: str, query: str) -> str:
    kind = _check_kind(kind, allow_all=True)
    query = (query or "").strip()
    if kind == "all":
        rows: list[dict[str, Any]] = []
        for k in KINDS:
            rows.extend(_search_one(k, query))
        return _ok_rows("all", "search", rows)
    return _ok_rows(kind, "search", _search_one(kind, query))


def _search_one(kind: str, query: str) -> list[dict[str, Any]]:
    if kind == "plugin":
        return catalogs.plugin_search(query)
    if kind == "skill":
        return catalogs.skill_search(query)
    return catalogs.mcp_search(query)


def do_list(kind: str) -> str:
    kind = _check_kind(kind, allow_all=True)
    if kind == "all":
        rows: list[dict[str, Any]] = []
        for k in KINDS:
            rows.extend(_list_one(k))
        return _ok_rows("all", "list", rows)
    return _ok_rows(kind, "list", _list_one(kind))


def _list_one(kind: str) -> list[dict[str, Any]]:
    if kind == "plugin":
        return catalogs.plugin_list()
    if kind == "skill":
        return catalogs.skill_list()
    return catalogs.mcp_list()


def do_inspect(kind: str, mid: str) -> str:
    kind = _check_kind(kind, allow_all=False)
    mid = _check_id(mid)
    if kind == "plugin":
        res = catalogs.plugin_inspect(mid)
    elif kind == "skill":
        res = catalogs.skill_inspect(mid)
    else:
        res = catalogs.mcp_inspect(mid)
    res.setdefault("action", "inspect")
    return json.dumps(res)


def do_mutation(action: str, kind: str, mid: str, *, actor: str, session: str,
                enabled: bool = True) -> str:
    """Run install/update/remove/enable and write the audit line."""
    kind = _check_kind(kind, allow_all=False)
    mid = _check_id(mid)
    if action == "install":
        res = catalogs.install(kind, mid)
    elif action == "update":
        res = catalogs.update(kind, mid)
    elif action == "remove":
        res = catalogs.remove(kind, mid)
    elif action == "enable":
        res = catalogs.set_enabled(kind, mid, enabled)
    else:
        raise ArgError(f"unknown mutation {action!r}")
    # The audit records intent (who asked for what), whether or not the CLI
    # step succeeded — a failed install is still an attempt worth a line.
    audit(actor, session, res.get("action", action), kind, mid)
    return json.dumps(res)


# --------------------------------------------------------------------------- #
# Approval message (built for the pre_tool_call gate; network is best-effort)
# --------------------------------------------------------------------------- #
def build_approval(tool_name: str, args: dict, *,
                   resolve: Callable[[str, str], dict] | None = None) -> dict | None:
    """The approval directive for a gated tool, or None to let it through.

    Names the kind, the id, its source/hub, its trust level and what the fetch
    will do — so the person approving sees what code is about to land and from
    where. ``source``/``trust`` are refined by a best-effort lookup (``resolve``)
    and fall back to the static per-kind description when it comes back empty, so
    the message is complete even with the network down.
    """
    if tool_name not in GATED_TOOLS:
        return None
    args = args if isinstance(args, dict) else {}
    kind = str(args.get("kind", "")).strip().lower()
    mid = str(args.get("id", "")).strip() or "?"
    action = _tool_action(tool_name, args)
    fetch = _FETCH_DESC.get(kind, "change an installed integration")
    source = _STATIC_SOURCE.get(kind, "an unknown source")
    trust = _STATIC_TRUST.get(kind, "unknown")

    if kind in KINDS and mid != "?":
        resolver = resolve if resolve is not None else catalogs.describe
        try:
            info = resolver(kind, mid) or {}
        except Exception:  # never let enrichment break the gate
            info = {}
        source = info.get("source") or source
        trust = info.get("trust") or trust

    verb = {"install": "install", "update": "update", "remove": "remove",
            "enable": "enable", "disable": "disable"}.get(action, action)
    message = (
        f"{verb} {kind or 'integration'} '{mid}' "
        f"(source: {source}; trust: {trust}). This will {fetch}."
    )
    return {"action": "approve", "message": message,
            "rule_key": f"integrations:{action}:{kind}"}


def _tool_action(tool_name: str, args: dict) -> str:
    if tool_name == "integration_enable":
        return "enable" if bool(args.get("enabled", True)) else "disable"
    return {"integration_install": "install", "integration_update": "update",
            "integration_remove": "remove"}.get(tool_name, "change")


# --------------------------------------------------------------------------- #
# Tool handlers  (registry calls handler(args, session_id=…, task_id=…, …))
# --------------------------------------------------------------------------- #
def handle_search(args: dict, **kwargs: Any) -> str:
    try:
        return do_search(str(args.get("kind", "")), str(args.get("query", "")))
    except ArgError as exc:
        return _err(str(exc))
    except catalogs.CatalogError as exc:
        return _err(f"catalogue error: {exc}")


def handle_inspect(args: dict, **kwargs: Any) -> str:
    try:
        return do_inspect(str(args.get("kind", "")), str(args.get("id", "")))
    except ArgError as exc:
        return _err(str(exc))
    except catalogs.CatalogError as exc:
        return _err(f"catalogue error: {exc}")


def handle_list(args: dict, **kwargs: Any) -> str:
    try:
        return do_list(str(args.get("kind", "")))
    except ArgError as exc:
        return _err(str(exc))
    except catalogs.CatalogError as exc:
        return _err(f"catalogue error: {exc}")


def _mutation_handler(action: str):
    def handler(args: dict, **kwargs: Any) -> str:
        session = str(kwargs.get("session_id") or "")
        enabled = bool(args.get("enabled", True)) if action == "enable" else True
        try:
            return do_mutation(action, str(args.get("kind", "")), str(args.get("id", "")),
                               actor="tool", session=session, enabled=enabled)
        except ArgError as exc:
            return _err(str(exc))
        except catalogs.CatalogError as exc:
            return _err(f"catalogue error: {exc}")
    return handler


handle_install = _mutation_handler("install")
handle_update = _mutation_handler("update")
handle_remove = _mutation_handler("remove")
handle_enable = _mutation_handler("enable")


# --------------------------------------------------------------------------- #
# Slash command
# --------------------------------------------------------------------------- #
def handle_command(raw_args: str) -> str:
    # `@<sid> <rest>` — the app prefixes the arg with the session id session.create
    # returned, because plugin command handlers are never handed the session.
    sid, rest = split_session(raw_args)
    if sid is None:
        return USAGE + " (prefix the argument with @<session_id>)"
    verb, _, tail = rest.partition(" ")
    verb = verb.lower().strip()
    tail = tail.strip()
    if verb == "":
        return USAGE
    if verb not in COMMAND_VERBS:
        return _err(f"unknown verb {verb!r}; valid: {', '.join(COMMAND_VERBS)}")

    kind, _, arg = tail.partition(" ")
    kind = kind.strip()
    arg = arg.strip()

    try:
        if verb == "search":
            return do_search(kind, arg)
        if verb == "list":
            # `list <kind>` — a bare `list` with the kind omitted is ambiguous.
            return do_list(kind or "all") if not arg else do_list(kind)
        if verb == "inspect":
            return do_inspect(kind, arg)
        # mutations
        enabled = verb != "disable"
        action = "enable" if verb in ("enable", "disable") else verb
        return do_mutation(action, kind, arg, actor="command", session=sid, enabled=enabled)
    except ArgError as exc:
        return _err(str(exc))
    except catalogs.CatalogError as exc:
        return _err(f"catalogue error: {exc}")
