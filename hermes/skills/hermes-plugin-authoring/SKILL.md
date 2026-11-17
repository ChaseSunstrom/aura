---
name: hermes-plugin-authoring
description: "Plugin internals reference: the register(ctx) API, plugin.yaml, hooks, commands. Not the route for a new plugin; build-capability scaffolds one."
version: 0.1.0
author: AURA, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [plugins, hermes-agent, tools, scaffolding, selfext]
    related_skills: [hermes-agent, hermes-agent-skill-authoring]
---

# Hermes Plugin Authoring

Author a **local Hermes plugin** — a package under the plugins dir (e.g. `/opt/data/plugins/<name>/`) that registers tools, hooks, and/or slash commands at load time via a `register(ctx)` entry point. This is the "custom/local-only tool" path the contributor guide points to when you must NOT edit core. Distinct from skills (`SKILL.md`) and from in-repo tools (`tools/*.py` + `registry.register`).

## When to Use

- "Scaffold a plugin", "add a plugin", "build a capability" → **not this skill**. Use `build-capability`:
  `selfext_scaffold` writes `hermes/plugins/<name>/` and `selfext_verify` runs its tests. Hand-writing a
  package under `/opt/data/plugins` is not the route; it is not in the image and is gone on the next deploy.
- This skill is the reference for the plugin **internals** — the `register(ctx)` API, `plugin.yaml`, hooks and
  commands — when you are filling in the handlers of a scaffolded plugin or reading an existing one.
- You need a tool that is local-only and must not touch the hermes-agent repo
- Adding a `pre_tool_call` approval hook or a custom slash command
- Don't use for: skills (use `hermes-agent-skill-authoring`), core in-repo tools, MCP servers

## Prerequisites

- A plugins dir on this host: `/opt/data/plugins/` (profile plugins live under the profile's `plugins/`).
- **Match the house convention first.** Read one sibling plugin before writing — the exact `plugin.yaml` fields and `ctx` method signatures are authoritative from there, not from this skill:
  - `read_file` `/opt/data/plugins/n8n-trigger/plugin.yaml` and `/opt/data/plugins/n8n-trigger/__init__.py`
  - `read_file` `/opt/data/plugins/gpu-telemetry/__init__.py` (toolset + `register_command` shape)

## The API (observed from house plugins)

**`plugin.yaml`** — YAML mapping. Confirmed fields: `name`, `version`, `description`, `author`. Optional: `requires_env` (list of env var names the plugin needs), `hooks` (list of hook events the plugin implements).

**`__init__.py`** — exposes a single `register(ctx) -> None`. `ctx` methods:

- `ctx.register_tool(name=, toolset=, schema=, handler=, check_fn=, emoji=)`
  - `schema` = `{"name": <tool>, "description": <str>, "parameters": {"type": "object", "properties": {...}, "required": [...]}}`
  - `handler(args: dict, **kwargs) -> str` — **must return a JSON string.**
  - `check_fn() -> bool` — tool only appears when true (gate on env/config; `lambda: True` for always-on).
- `ctx.register_hook("pre_tool_call", fn)` — `fn(tool_name, args, task_id="", **kw)` returns `None` (allow) or `{"action": "approve", "message": ..., "rule_key": ...}` (hold for approval). Return `None` for non-gated tools.
- `ctx.register_command(name, handler=, description=, args_hint=)` — custom slash command; `handler(raw_args: str) -> str`.

Conventions: module docstring, `from __future__ import annotations`, a `TOOLSET = "..."` constant, and a `_available()` helper when tools are gated on env vars.

## Procedure

1. **Inspect a sibling** plugin (Prerequisites) and copy its exact field/method shapes. Don't guess the API from memory.
2. **Scaffold** from `templates/plugin-scaffold/` (copy `plugin.yaml` + `__init__.py`), rename `name`/`toolset`/handler, and replace the echo schema+handler with the real tool.
3. **Write `plugin.yaml`** with `name` matching the directory and the plugin's purpose in `description`.
4. **Write `__init__.py`** with `register(ctx)` registering the tool(s). Keep `check_fn` honest — gate on env where it belongs.
5. **Verify** with `scripts/verify_plugin.py <plugin-dir>` (see Verification). Do not report "done" until the handler returns the expected JSON.

## Pitfalls

- **Hyphenated plugin dir names can't be `import`ed.** `jcb-echo` is not a valid Python identifier. Load it with `importlib.util.spec_from_file_location(name, "<dir>/__init__.py")` (this is what `scripts/verify_plugin.py` does). A naive `import jcb_echo` fails.
- **`import yaml` may be missing** in a bare interpreter. To check `plugin.yaml`, `read_file`/`cat` it, or run the verify against a Python that has PyYAML. Don't treat a missing `yaml` as a broken plugin.
- **Target dir may be root-owned.** On this host, pre-created task dirs (e.g. `selfext-plugin-003`) can be `root:root` while you run as `hermes` with no `sudo`. If the dir is **empty**, reclaim it: `rmdir <dir> && mkdir -p <dir>/<subdir>`. Only do this when empty — never `rmdir` a populated dir.
- **Handlers must return a JSON string**, not a dict or bare text. `json.dumps({"success": True, ...})`.
- **`check_fn` controls visibility.** A tool with a false `check_fn` silently disappears from the agent's toolset — if a tool "isn't showing", check the gate first.
- **`name` must match `schema["name"]`.** The verify script asserts this; a mismatch is a real bug.

## Verification

Run the re-runnable smoke test — it loads `__init__.py`, calls `register()` on a mock `ctx`, asserts the tool name matches its schema, and exercises the first registered handler with a sample required arg:

    terminal(command="python3 <skill-dir>/scripts/verify_plugin.py /opt/data/plugins/<name>", timeout=60)

Success output: `tools: [<name>]`, each tool's toolset, `check_fn()` result, and the handler's JSON for a sample input. Non-zero exit means a structural problem (missing `register`, no tools, name mismatch) — fix before reporting done.

Support files:
- `templates/plugin-scaffold/` — known-good minimal echo plugin to copy and modify.
- `scripts/verify_plugin.py` — the smoke test above; pass it the plugin dir path.
