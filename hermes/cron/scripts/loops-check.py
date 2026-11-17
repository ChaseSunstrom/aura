#!/usr/bin/env python3
"""The open loops, checked without a model: the gate of the "Open loops (evening)" job.

The logic lives in the jarvis-hud plugin's ``loops`` module; this loads it by path the way
``later-check.py`` does (never by import name: Hermes has its own top-level packages).
Stdout is the model's input; a last line of ``{"wakeAgent": false}`` skips the model, which
is every evening with nothing open.
"""
import importlib.util
import json
import os
import sys

_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_LOOPS = os.path.join(_HOME, "plugins", "jarvis-hud", "loops.py")

if not os.path.isfile(_LOOPS):
    print(f"- The open loops cannot be read: the jarvis-hud plugin is not installed at {_LOOPS}.")
    sys.exit(0)

try:
    _spec = importlib.util.spec_from_file_location("jarvis_loops", _LOOPS)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _mod
    _spec.loader.exec_module(_mod)
    sys.exit(_mod.main())
except Exception as exc:  # noqa: BLE001 - never a traceback in the model's input
    # A broken sweep is not worth waking the model every evening for.
    print(f"loops-check: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(json.dumps({"wakeAgent": False}))
    sys.exit(0)
