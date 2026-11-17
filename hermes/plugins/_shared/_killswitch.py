"""The kill switch: one file that stops JARVIS acting, readable from anywhere.

The switch is a file, ``<HERMES_HOME>/state/kill-switch.json``, and not a
Hermes setting, a database row or an in-process flag. Each of those was
rejected for a reason this module has to keep true:

* **not a setting** — ``settings_set`` is a tool, and a tool the model can call
  is a switch the model can flip. The allow-list in ``jarvis-settings`` does not
  name it and must never name it (``tests/test_killswitch.py`` pins that).
* **not in-process** — a kill switch that needs a restart is not a kill switch.
  Every read hits the filesystem, so a host-side ``scripts/jarvis-kill engage``
  stops the *running* gateway's next tool call, mid-goal, with no signal and no
  cooperation from the agent loop.
* **not a lock file** — engaging must be a positive, attributable act. The file
  records who engaged it, when and why, so the audit log and the HUD can say
  more than "off".

What engaging does is decided in :mod:`_matrix`: every verdict that is not
``free`` becomes ``deny`` while the switch is on, so reads keep working (the
owner can still ask what happened) and nothing reaches the world. The tools
that could *remove this file* are denied outright by matrix rows, which is what
makes "the model cannot flip it back" a property of the gate rather than a
hope: releasing needs a writer that is not the model, i.e. the host script or
an operator with a shell on the box.

Deliberately not claimed: this does not stop a turn already inside a tool
handler (the gate runs before dispatch, not during), and it does not revoke a
credential. It stops the *next* action, which is the property the drill in
``docs/runbooks/kill-switch.md`` demonstrates.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Relative to ``HERMES_HOME``. ``state/`` is Hermes' own runtime-state dir and
#: rides the backed-up volume, so an engaged switch survives a container restart
#: — a switch that a `docker compose up -d` silently released would be worse
#: than none.
STATE_REL: str = "state/kill-switch.json"

#: Written into every record so a reader can tell a hand-edited file from ours.
SCHEMA: str = "jarvis.killswitch.v1"

_LOCK = threading.Lock()

#: The tool families the owner can stop one at a time (T-0575), by tool-name prefix or name.
#: Each is engage-only from the model, like the whole switch; releasing is the host's.
FAMILIES: dict[str, tuple[str, ...]] = {
    "mail": ("mail_",),
    "phone": ("phone_",),
    "house": ("ha_", "house_"),
    "shell": ("terminal", "execute_code", "process"),
}


def family_of(tool: str) -> str | None:
    """The family `tool` belongs to, or None for a tool in none of them."""
    for name, marks in FAMILIES.items():
        if any(tool == m or (m.endswith("_") and tool.startswith(m)) for m in marks):
            return name
    return None


def _hermes_home() -> Path:
    """``HERMES_HOME`` (env, else ``~/.hermes``), resolved at call time.

    Call time, not import time: the plugin is imported once per gateway process
    but the tests point ``HERMES_HOME`` at a tmp dir per test, and a cached path
    would make every test after the first write to the developer's real home.
    """
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def state_path() -> Path:
    return _hermes_home() / STATE_REL


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read() -> dict[str, Any]:
    """The switch's full state. Always a dict; never raises.

    A missing file means released — that is the only sane default for a box
    that has never engaged it. A file that exists but cannot be parsed is
    treated as **engaged**: a corrupt kill switch must fail towards stopping,
    because the alternative is an agent that acts freely because its brake was
    unreadable.
    """
    path = state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"engaged": False}
    except OSError as exc:
        _LOGGER.error("kill switch unreadable at %s (%s) — treating as ENGAGED", path, exc)
        return {"engaged": True, "reason": f"kill-switch file unreadable: {exc}"}
    try:
        data = json.loads(raw)
    except ValueError:
        _LOGGER.error("kill switch at %s is not JSON — treating as ENGAGED", path)
        return {"engaged": True, "reason": "kill-switch file is not valid JSON"}
    if not isinstance(data, dict):
        return {"engaged": True, "reason": "kill-switch file is not an object"}
    data["engaged"] = bool(data.get("engaged"))
    return data


def engaged() -> bool:
    """The hot path the gate calls before every tool call."""
    return bool(read().get("engaged"))


def engage(*, actor: str, reason: str = "", family: str | None = None) -> dict[str, Any]:
    """Turn the switch on, or only one of [FAMILIES]. Idempotent: engaging what is already
    engaged keeps the original actor and timestamp, so the record says who really stopped it."""
    if family is not None and family not in FAMILIES:
        raise ValueError(f"no tool family {family!r}; one of {', '.join(FAMILIES)}")
    with _LOCK:
        current = read()
        if family is not None:
            families = dict(current.get("families") or {})
            if family not in families:
                families[family] = {"actor": actor, "reason": reason, "at": _now()}
                current = {"schema": SCHEMA, **current, "families": families}
                _write(current)
            return current
        if current.get("engaged"):
            return current
        record = {
            "schema": SCHEMA,
            "engaged": True,
            "actor": actor,
            "reason": reason,
            "at": _now(),
            # A family stopped before stays stopped: the whole switch is on top of it.
            **({"families": current["families"]} if current.get("families") else {}),
        }
        _write(record)
        return record


def release(*, actor: str, family: str | None = None) -> dict[str, Any]:
    """Turn the switch off (or only one family's stop), keeping the release attributable.

    Releasing the whole switch releases every family with it, as `scripts/jarvis-kill
    release` does: "release" means JARVIS may act again. One family is released by name.

    There is no ``release`` path reachable from a tool: the matrix denies the
    tools that could write this file while the switch is engaged, and nothing
    registers this function as a tool handler. The callers are the host script
    and the plugin's operator-only command.
    """
    with _LOCK:
        previous = read()
        if family is not None:
            families = {k: v for k, v in (previous.get("families") or {}).items() if k != family}
            record = {**previous, "schema": SCHEMA, "families": families}
            _write(record)
            return record
        record = {
            "schema": SCHEMA,
            "engaged": False,
            "actor": actor,
            "released_at": _now(),
            "previous": {k: v for k, v in previous.items() if k != "schema"},
        }
        _write(record)
        return record


def _write(record: dict[str, Any]) -> None:
    """Atomic replace, so a reader never sees a half-written switch.

    A torn file would read as "not JSON" and therefore as engaged — safe, but it
    would strand the box until someone with a shell noticed. The temp file lives
    in the same directory so ``replace`` stays on one filesystem.
    """
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # The adapter reads this file as another uid through a read-only mount of the same
    # volume, and its reader fails towards ENGAGED: a record the gateway's umask made
    # private would show the owner a stopped assistant that was running (seen on
    # 2026-09-06, PermissionError in GET /api/status). The directory's traverse bit is
    # the entrypoint's job; the file's read bit is this writer's.
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def describe() -> str:
    """One line for a slash command, the HUD's status text and the host script."""
    state = read()
    stopped = sorted(state.get("families") or {})
    also = f"; stopped on their own: {', '.join(stopped)}" if stopped else ""
    if not state.get("engaged"):
        actor = state.get("actor")
        return f"kill switch RELEASED{f' by {actor}' if actor else ''}{also}"
    bits = [f"engaged by {state.get('actor') or 'unknown'}"]
    if state.get("at"):
        bits.append(f"at {state['at']}")
    if state.get("reason"):
        bits.append(f"— {state['reason']}")
    return "kill switch ENGAGED (" + " ".join(bits) + ")" + also
