#!/usr/bin/env python3
"""The watches and the homelab, checked without a model: the gate of the "Watches" job.

The logic lives in the jarvis-hud plugin's ``watches`` module, with the tools that store
the watches; this loads it by path the way ``gpu-watch.py`` loads its plugin (never by
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
_WATCHES = os.path.join(_PLUGINS, "jarvis-hud", "watches.py")

if not os.path.isfile(_WATCHES):
    print(f"- The watches cannot run: the jarvis-hud plugin is not installed at {_WATCHES}.")
    sys.exit(0)
if _PLUGINS not in sys.path:
    sys.path.insert(0, _PLUGINS)

try:
    _spec = importlib.util.spec_from_file_location("jarvis_watches", _WATCHES)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _mod
    _spec.loader.exec_module(_mod)
    sys.exit(_mod.main())
except Exception as exc:  # noqa: BLE001 - one sentence for the model, never a traceback
    # Said at most once a day: a watcher that breaks every ten minutes must not become the
    # hourly n8n alert it replaced. The stamp is cleared by nothing; it only ages out.
    stamp = os.path.join(_HOME, "plugin-data", "jarvis-watches", "broken.stamp")
    fresh = os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 24 * 3600
    print(f"watch-check: {type(exc).__name__}: {exc}", file=sys.stderr)
    if fresh:
        print(json.dumps({"wakeAgent": False}))
    else:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        open(stamp, "w").close()
        print(f"- The watches could not run: {type(exc).__name__}: {exc}")
    sys.exit(0)
