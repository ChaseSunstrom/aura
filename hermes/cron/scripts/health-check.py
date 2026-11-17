#!/usr/bin/env python3
"""What broke, checked without a model: the gate of the "Health check (daily)" job.

The logic lives in the aura-hud plugin's ``health`` module; this loads it by path the way
``loops-check.py`` does (never by import name: Hermes has its own top-level packages).
Stdout is the model's input; a last line of ``{"wakeAgent": false}`` skips the model, which
is every day nothing new broke.
"""
import importlib.util
import json
import os
import sys
import time

_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_HEALTH = os.path.join(_HOME, "plugins", "aura-hud", "health.py")

try:
    if not os.path.isfile(_HEALTH):
        raise FileNotFoundError(f"the aura-hud plugin is not installed at {_HEALTH}")
    _spec = importlib.util.spec_from_file_location("jarvis_health", _HEALTH)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _mod
    _spec.loader.exec_module(_mod)
    sys.exit(_mod.main())
except Exception as exc:  # noqa: BLE001 - one sentence for the model, never a traceback
    # The checker itself being broken is said at most once a day, by a stamp that only ages out.
    stamp = os.path.join(_HOME, "plugin-data", "jarvis-health", "broken.stamp")
    fresh = os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 24 * 3600
    print(f"health-check: {type(exc).__name__}: {exc}", file=sys.stderr)
    if fresh:
        print(json.dumps({"wakeAgent": False}))
    else:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        open(stamp, "w").close()
        print(f"- The health check could not run: {type(exc).__name__}: {exc}")
    sys.exit(0)
