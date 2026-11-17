"""Permission levels per capability: Off / Read / Ask first / Allowed (v2 §3, T-0608).

A capability is a family of tools (mail, calendar, house, ...). Its level lives in
``<HERMES_HOME>/state/capability-levels.json``, beside the kill switch and read the same way: on
every call, so a change reaches the running gateway at once. :func:`_matrix.decide` applies it
after the matrix and before the kill switch, so the order of authority is: a DENY row, then the
kill switch, then the level, then the matrix as written. A level never beats the first two.

* **off**     every tool of the capability is refused, reads included.
* **read**    reads run; every write is refused.
* **ask**     reads run; every write asks, including the ones the matrix lets through
              (``mail_draft``, a write inside the files area).
* **allowed** a write the matrix asks about on every call stops asking. Only that: see
              :func:`_liftable` for what stays asking.
* unset       the matrix as written.

Fail closed: an unreadable file, or a level that is not one of the four, reads as ``ask`` (a write
never runs unasked, a read still works). Setting a level is :func:`set_level`, reachable from a
tool only as ``capability_set``, which the matrix always asks about.

Deliberately not claimed: a level is the owner's blanket yes for the *kinds* of call listed in
:func:`_liftable`; it does not know that the turn read someone else's words first (``decide`` has
no session), so an injected "send this" is as free as a typed one once mail is Allowed.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from . import _killswitch

_LOGGER = logging.getLogger(__name__)

STATE_REL: str = "state/capability-levels.json"
SCHEMA: str = "jarvis.capability-levels.v1"
LEVELS: tuple[str, ...] = ("off", "read", "ask", "allowed")

#: Capability -> tool names or prefixes (a prefix ends in ``_``). A tool in none is not governed
#: by a level. Left out on purpose: n8n and Proxmox (a credential, a VM), the desktop, and the
#: settings, integrations and self-extension tools: those keep the matrix's own rows.
CAPABILITIES: dict[str, tuple[str, ...]] = {
    "mail": ("mail_",),
    "texts": ("texts_",),
    "calls": ("calls_",),
    "calendar": ("calendar_",),
    "contacts": ("contacts_",),
    "files": ("fileshare_",),
    "house": ("ha_", "house_"),
    "phone": ("phone_",),
    "media": ("jellyfin_", "plex_"),
    "shell": ("terminal", "process", "execute_code"),
    "web": ("web_", "browser_"),
}

#: Asks that Allowed never lifts, whatever the capability: code that can call every other tool,
#: an answer the organiser sees, a delete, and what changes what the house does by itself.
NEVER_LIFT: frozenset[str] = frozenset({
    "execute_code", "calendar_rsvp", "fileshare_delete", "calls_rule",
    "house_automation_control", "house_automation_create", "house_device_action",
})

#: ``ha_call_service`` is lifted only for these domains. A lock, a garage cover, an alarm and a
#: camera are not on it, nor is a call that names no domain: an allowlist fails closed.
LIFT_DOMAINS: frozenset[str] = frozenset({"light", "scene", "media_player", "fan"})

_LOCK = threading.Lock()


def state_path():
    return _killswitch.state_path().with_name("capability-levels.json")


def capability_of(tool: str) -> str | None:
    for cap, marks in CAPABILITIES.items():
        if any(tool == m or (m.endswith("_") and tool.startswith(m)) for m in marks):
            return cap
    return None


def _load() -> tuple[dict[str, str], bool]:
    """The stored levels as written, and whether the file was unreadable. Never raises."""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError):
        _LOGGER.error("capability levels at %s unreadable -- writes will ask", state_path())
        return {}, True
    levels = data.get("levels") if isinstance(data, dict) else None
    if not isinstance(levels, dict):
        return {}, True
    return {str(k): str(v) for k, v in levels.items()}, False


def read() -> dict[str, str]:
    """capability -> level for every capability that has one; a bad value or file reads as ask."""
    stored, broken = _load()
    if broken:
        return {cap: "ask" for cap in CAPABILITIES}
    return {cap: (stored[cap] if stored[cap] in LEVELS else "ask")
            for cap in CAPABILITIES if cap in stored}


def set_level(capability: str, level: str, *, actor: str) -> str | None:
    """Store ``level`` for ``capability``; returns the previous level (None if unset)."""
    if capability not in CAPABILITIES:
        raise ValueError(f"no capability {capability!r}; one of {', '.join(CAPABILITIES)}")
    if level not in LEVELS:
        raise ValueError(f"no level {level!r}; one of {', '.join(LEVELS)}")
    with _LOCK:
        stored, _broken = _load()  # a broken file is replaced by a clean one
        previous = read().get(capability)
        stored[capability] = level
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": SCHEMA, "levels": stored, "actor": actor},
                                  indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)  # the adapter reads the volume as another uid
        os.replace(tmp, path)
    return previous


def _liftable(tool: str, args: dict[str, Any], matched: list[Any]) -> bool:
    """May Allowed stop an ASK for this call? Only when every floor below holds.

    1. the matrix asked about *every* call of the tool (a row with no argument shape): a row
       that asks because this call looks unusual (a destructive command, a path outside the
       work area, an upload) keeps asking;
    2. the tool is not on :data:`NEVER_LIFT`, and ``ha_call_service`` is for a plain domain;
    3. a guest could not use the tool freely anyway (the gate still refuses a guest);
    4. nobody in it is a first-time recipient: the owner's yes to mail is not a yes to a stranger.
    """
    from . import _approval_hook, _matrix

    if not matched or tool in NEVER_LIFT or _matrix.guest_may(tool):
        return False
    if any(r.verdict == _matrix.ASK and (r.arg or r.check or r.matches) for r in matched):
        return False
    if tool == "ha_call_service" and str(args.get("domain") or "").strip().lower() not in LIFT_DOMAINS:
        return False
    try:
        return not _approval_hook._first_time(_approval_hook.recipients(tool, args))
    except Exception:  # noqa: BLE001 - an audit log that cannot be read cannot vouch for anyone
        return False


def apply(tool: str, args: dict[str, Any], verdict: str, reason: str,
          matched: list[Any], is_read: bool) -> tuple[str, str, str] | None:
    """The level's verdict ``(verdict, reason, rule_key)`` for a call, or None to leave it.

    ``verdict`` is what the matrix said; a DENY is never touched. ``is_read`` is the matrix's
    own test (every row that permits the call is a read row), the same one the kill switch uses.
    """
    cap = capability_of(tool)
    level = read().get(cap) if cap else None
    if level is None or verdict == "deny":
        return None
    if level == "off":
        return "deny", f"{cap} is switched off: {reason}", f"level:{cap}:off"
    if level == "read":
        return None if is_read else ("deny", f"{cap} is read-only: {reason}", f"level:{cap}:read")
    if level == "ask":
        if is_read or verdict != "free":
            return None
        return "ask", f"{cap} asks first: {reason}", f"level:{cap}:ask"
    if verdict == "ask" and _liftable(tool, args, matched):
        return "free", f"{cap} is allowed: {reason}", f"level:{cap}:allowed"
    return None


def describe() -> dict[str, Any]:
    """What ``/hud capabilities`` prints: every capability, its level (None = the matrix as
    written) and its tools, each marked as a write or not. Shape: tests/contracts/capability-levels.json."""
    from . import _matrix

    levels = read()
    reads = _matrix.read_only_tools()
    tools = sorted(_matrix.known_tools())
    return {
        "levels": list(LEVELS),
        "capabilities": [
            {"id": cap, "level": levels.get(cap),
             "tools": [{"name": t, "write": t not in reads} for t in tools if capability_of(t) == cap]}
            for cap in CAPABILITIES
        ],
    }
