#!/usr/bin/env python3
"""Habits and trial routines, checked without a model: the gate of the "Habits and trials" job.

The logic lives in the aura-hud plugin's ``habits`` module, with the routines it reads;
both are loaded by path the way ``later-check.py`` loads its module (never by import name:
Hermes has its own top-level packages a bare import would find instead).

An agent job with a gate: a last line of ``{"wakeAgent": false}`` skips the model, which is
every day with no new habit and no trial ending.
"""
import importlib.util
import json
import os
import sys
import time

_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_PLUGINS = os.path.join(_HOME, "plugins")
_DIR = os.path.join(_PLUGINS, "aura-hud")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"jarvis_{name}", os.path.join(_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


if not os.path.isfile(os.path.join(_DIR, "habits.py")):
    print(f"- The habit check cannot run: the aura-hud plugin is not installed at {_DIR}.")
    sys.exit(0)
if _PLUGINS not in sys.path:
    sys.path.insert(0, _PLUGINS)

try:
    sys.exit(_load("habits").main(_load("routines")))
except Exception as exc:  # noqa: BLE001 - one sentence for the model, never a traceback
    # Said at most once a day, the same as the other gates.
    stamp = os.path.join(_HOME, "plugin-data", "jarvis-habits", "broken.stamp")
    fresh = os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 24 * 3600
    print(f"habit-check: {type(exc).__name__}: {exc}", file=sys.stderr)
    if fresh:
        print(json.dumps({"wakeAgent": False}))
    else:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        open(stamp, "w").close()
        print(f"- The habit check could not run: {type(exc).__name__}: {exc}")
    sys.exit(0)
