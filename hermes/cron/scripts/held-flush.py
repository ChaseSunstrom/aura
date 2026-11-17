#!/usr/bin/env python3
"""Sends the pushes the interruption budget held, as one digest, once nothing holds them.

A no-agent job: ``flush_held`` delivers through the house itself, so this prints nothing
(output under the ``house`` delivery would only be recorded). Loaded by path from the
smart-home plugin like ``gpu-watch.py``, never by import name.
"""
import importlib.util
import os
import sys

_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_PLUGINS = os.path.join(_HOME, "plugins")
_TOOLS = os.path.join(_PLUGINS, "smart-home", "tools.py")

if not os.path.isfile(_TOOLS):
    sys.exit(0)
if _PLUGINS not in sys.path:
    sys.path.insert(0, _PLUGINS)
try:
    _spec = importlib.util.spec_from_file_location("jarvis_held_flush", _TOOLS)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _mod
    _spec.loader.exec_module(_mod)
    _mod.flush_held()
except Exception as exc:  # noqa: BLE001 - a flush that cannot run must not page anyone
    print(f"held-flush: {type(exc).__name__}: {exc}", file=sys.stderr)
