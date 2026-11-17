#!/usr/bin/env python3
"""The GPU threshold watchdog, as the scheduler runs it.

``hermes cron create --script gpu-watch.py --no-agent`` resolves the name under
``$HERMES_HOME/scripts``, so this file has to live there — but the logic lives with the
telemetry it reads, in the gpu-telemetry plugin, so that the Grafana/InfluxDB client exists
once in this repo rather than twice. This is the seven lines that join the two: it loads the
plugin's ``watch`` module by path (never by import name — Hermes has its own top-level
``tools`` package that a bare ``import tools`` would find instead) and runs it.

Prints only transitions; empty stdout means the scheduler delivers nothing. See
``hermes/plugins/gpu-telemetry/watch.py`` for the debounce rules.
"""
import importlib.util
import os
import sys

_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_PLUGINS = os.path.join(_HOME, "plugins")
_WATCH = os.path.join(_PLUGINS, "gpu-telemetry", "watch.py")

if not os.path.isfile(_WATCH):
    # A plain sentence, because whatever this prints is delivered to the owner verbatim.
    print(f"GPU watchdog: cannot run — the gpu-telemetry plugin is not installed at {_WATCH}.")
    sys.exit(0)

# The plugins directory itself, because the plugin's `tools` imports `_shared._grafana` —
# the same flat layout Hermes gives a plugin at runtime, which is what makes `_shared`
# importable from a plugin at all.
if _PLUGINS not in sys.path:
    sys.path.insert(0, _PLUGINS)

try:
    _spec = importlib.util.spec_from_file_location("jarvis_gpu_watch", _WATCH)
    _watch = importlib.util.module_from_spec(_spec)
    # Registered before it is executed: @dataclass resolves a field's annotations through
    # sys.modules[cls.__module__], and a module loaded by path that is not registered
    # crashes there with "'NoneType' object has no attribute '__dict__'".
    sys.modules[_spec.name] = _watch
    _spec.loader.exec_module(_watch)
    sys.exit(_watch.main())
except Exception as exc:  # noqa: BLE001 — see below
    # Everything this file prints is delivered verbatim to whatever platform the job
    # targets, so a traceback here is a traceback on the owner's phone. One sentence
    # naming the failure, and exit 0 so the scheduler does not also open an incident for
    # a watchdog that is merely mis-installed.
    print(f"GPU watchdog: cannot run — {type(exc).__name__}: {exc}")
    sys.exit(0)
