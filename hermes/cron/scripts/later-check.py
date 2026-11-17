#!/usr/bin/env python3
"""The later queue, checked without a model: the gate of the "Later queue" job.

The logic lives in the jarvis-hud plugin's ``later`` module, with the tools that store
the later queue; this loads it by path the way ``gpu-watch.py`` loads its plugin (never by
import name: Hermes has its own top-level packages a bare import would find instead).

This job is an agent job with a gate, not a no-agent one: its stdout is the model's input,
and a last line of ``{"wakeAgent": false}`` skips the model entirely, which is every pass
where nothing tripped.
"""
import importlib.util
import json
import os
import sys
import time

_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_PLUGINS = os.path.join(_HOME, "plugins")
_LATER = os.path.join(_PLUGINS, "jarvis-hud", "later.py")

if not os.path.isfile(_LATER):
    print(f"- The later queue cannot run: the jarvis-hud plugin is not installed at {_LATER}.")
    sys.exit(0)
if _PLUGINS not in sys.path:
    sys.path.insert(0, _PLUGINS)

try:
    _spec = importlib.util.spec_from_file_location("jarvis_later", _LATER)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _mod
    _spec.loader.exec_module(_mod)
    sys.exit(_mod.main())
except Exception as exc:  # noqa: BLE001 - one sentence for the model, never a traceback
    # Said at most once a day: a watcher that breaks every ten minutes must not become the
    # hourly n8n alert it replaced. The stamp is cleared by nothing; it only ages out.
    stamp = os.path.join(_HOME, "plugin-data", "jarvis-later", "broken.stamp")
    fresh = os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 24 * 3600
    print(f"later-check: {type(exc).__name__}: {exc}", file=sys.stderr)
    if fresh:
        print(json.dumps({"wakeAgent": False}))
    else:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        open(stamp, "w").close()
        print(f"- The later queue could not run: {type(exc).__name__}: {exc}")
    sys.exit(0)
