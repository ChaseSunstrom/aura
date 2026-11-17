"""Fixture setup hooks the runner calls for a task's ``setup.fixture: <name>``.

The runner resolves a fixture name to a callable here and runs it before the task, so a
form-submission or webhook-sink check never sees a previous run's data. Each hook resets the
matching state on the running fixture server (:mod:`bench.fixtures.server`), preferring an
HTTP ``POST /reset/<name>`` (works no matter which process owns the server) and falling back
to the in-process :func:`bench.fixtures.server.reset` (the state is file-backed, so a reset
from another process still clears what the server reads).

Two call shapes are supported so this drops into either loader the harness described:

* ``hooks.setup(name, ctx)`` — dispatch by name.
* ``hooks.<name>(ctx)`` — a function per fixture (``login``, ``forms``, ``sink``, ``inject``).

Each returns a dict (empty here — these fixtures add no template vars), so a return value can
be merged into the task's variables without a special case. A name with no hook is a no-op
that returns ``{}`` rather than an error: a task may name a fixture that only needs its
static pages, which need no reset.

``ctx`` is the harness context (``{work_root, file_root, exec, env, task}``); only ``env`` is
read, for the reset URL. The reset endpoint defaults to loopback because the runner runs on
the host, while tasks reach the same server through ``host.docker.internal`` from the
container.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

#: Fixtures that keep resettable state. Static-only names (pages, docs, assets) are absent
#: and resolve to a no-op.
_RESETTABLE = ("login", "forms", "sink", "inject")


def _reset_url(ctx: dict[str, Any] | None) -> str:
    env = (ctx or {}).get("env") or {}
    base = env.get("JCB_FIXTURE_RESET_URL") or env.get("JCB_FIXTURE_URL") or "http://127.0.0.1:8899"
    return base.rstrip("/")


def reset(name: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Clear ``name``'s recorded state, via the server's HTTP reset, else in-process."""
    if name not in _RESETTABLE:
        return {}
    url = f"{_reset_url(ctx)}/reset/{name}"
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            json.loads(resp.read().decode() or "{}")
        return {}
    except Exception:  # noqa: BLE001 - fall back to a same-host, file-backed reset
        try:
            from bench.fixtures.server import reset as _in_process

            _in_process(name)
        except Exception:  # noqa: BLE001 - a reset that cannot run must not crash the run;
            # the task's own checker (e.g. sink received:false) still catches stale state.
            pass
        return {}


def setup(name: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch a fixture's setup by name (the runner's single entry point)."""
    return reset(name, ctx)


def login(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    return reset("login", ctx)


def forms(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    return reset("forms", ctx)


def sink(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    return reset("sink", ctx)


def inject(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    return reset("inject", ctx)


#: name → setup callable, for a loader that prefers a table to ``getattr``.
SETUP = {"login": login, "forms": forms, "sink": sink, "inject": inject}
