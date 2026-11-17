#!/usr/bin/env python3
"""Smoke-test a Hermes plugin dir.

Loads <dir>/__init__.py via importlib (hyphenated dir names are not valid
Python identifiers), calls register(ctx) on a mock context, asserts the tool
name matches its schema, and exercises the first registered handler with a
sample required argument.

Usage:
    python3 verify_plugin.py /path/to/plugin-dir

Exit 0 on success, non-zero on a structural problem.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys


class _MockCtx:
    """Stands in for Hermes' PluginContext.

    An *object*, not a mapping: ``register(ctx)`` calls ``ctx.register_tool(...)``,
    so a dict of callables raises ``AttributeError: 'dict' object has no attribute
    'register_tool'`` on the first line of every plugin — including this skill's own
    scaffold template. ``__getattr__`` swallows the rest of the ctx API (``ctx.llm``,
    ``ctx.get_config``, ``register_image_gen_provider``, whatever a later Hermes adds)
    so a plugin that touches one is still verifiable here; this script checks the
    tool/hook/command surface, it does not emulate the runtime.
    """

    def __init__(self) -> None:
        self.state = {"tools": {}, "hooks": {}, "commands": {}}

    def register_tool(self, name, toolset, schema, handler, check_fn=None, **kw):
        assert name == schema.get("name"), (
            f"tool name {name!r} != schema['name'] {schema.get('name')!r}"
        )
        self.state["tools"][name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "check_fn": check_fn,
        }

    def register_hook(self, event, fn=None, **kw):
        self.state["hooks"].setdefault(event, []).append(fn)

    def register_command(self, name, handler=None, description=None, args_hint=None, **kw):
        self.state["commands"][name] = {"handler": handler, "description": description}

    def __getattr__(self, _name):
        return lambda *a, **kw: None


def _sample_value(prop: dict):
    t = prop.get("type", "string")
    if t == "string":
        return "verify-echo"
    if t == "boolean":
        return True
    if t in ("integer", "number"):
        return 1
    if t in ("object", "array"):
        return {}
    return "verify"


def main(argv):
    if len(argv) != 2:
        print("usage: verify_plugin.py <plugin-dir>", file=sys.stderr)
        return 2
    d = pathlib.Path(argv[1])
    init = d / "__init__.py"
    if not init.is_file():
        print(f"no __init__.py in {d}", file=sys.stderr)
        return 2

    # submodule_search_locations + __path__ + sys.modules: a plugin that splits itself
    # into schemas.py/tools.py does `from . import tools`, which needs the loaded module
    # to BE a package and to be findable by name. Without all three the import raises
    # "No module named 'plugin_under_test'" and a perfectly good plugin looks broken —
    # which is how every house plugin failed this script.
    spec = importlib.util.spec_from_file_location(
        "plugin_under_test", str(init), submodule_search_locations=[str(d)]
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [str(d)]
    sys.modules["plugin_under_test"] = mod
    spec.loader.exec_module(mod)

    if not hasattr(mod, "register"):
        print("module has no register(ctx) entry point", file=sys.stderr)
        return 2

    ctx = _MockCtx()
    mod.register(ctx)
    tools = ctx.state["tools"]
    if not tools:
        print("register() did not register any tools", file=sys.stderr)
        return 2

    print(f"tools: {list(tools)}")
    if ctx.state["hooks"]:
        print(f"hooks: {list(ctx.state['hooks'])}")
    if ctx.state["commands"]:
        print(f"commands: {list(ctx.state['commands'])}")

    name, t = next(iter(tools.items()))
    print(f"  - {name} (toolset={t['toolset']})")
    if t["check_fn"] is not None:
        print(f"    check_fn() -> {t['check_fn']()}")

    props = t["schema"].get("parameters", {}).get("properties", {})
    args = {r: _sample_value(props.get(r, {})) for r in t["schema"].get("parameters", {}).get("required", [])}
    result = t["handler"](args)
    print(f"    handler({args!r}) -> {result}")
    try:
        json.loads(result)
    except (TypeError, ValueError) as e:
        print(f"handler did not return a JSON string: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
