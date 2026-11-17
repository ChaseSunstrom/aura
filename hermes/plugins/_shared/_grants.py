"""Standing "always" approvals that run out, and can be taken back (T-0570).

Hermes remembers an ``always`` answer by the card's pattern key (``plugin_rule:<rule_key>``) in
the profile's ``command_allowlist``, for ever, and gives a plugin no way to remove one (it loads
the set once per home, so editing its config from here would not take until a restart). The key
is ours to shape, so this module puts a token in it: every gate returns ``<base>:g<6 hex>`` and
then its scope suffixes (``:after-outside``, ``:at-home``). The token is derived from a secret and
a generation per base key; expiring or revoking a grant bumps the generation, after which the key
Hermes remembered never comes up again and the next call asks.

The file is ``<HERMES_HOME>/state/approval-grants.json``:
``{"secret": hex, "gen": {base: n}, "grants": {base: {"tool", "at", "scopes"}}}``. It holds base
keys, tool names and times, never a call's arguments.

Fails closed: a missing, unreadable or corrupt file means a new secret, so every token changes and
every standing answer is asked once more; so does a key given before this module existed (it has
no token at all). Only a human's ``always`` is recorded: Hermes' smart mode answers with
``smart_<verdict>`` and ``decided_by="aux_llm"``, and the model cannot answer an approval.

A file that cannot be used costs only the remembering: the gate gets a key no call repeats, so
every call asks (a raising gate would stop every call in the process), and an ``always`` that
fails to record is voided (its generation bumped) so Hermes' copy never matches.

Not covered: Hermes' own list still grows by one dead key per expiry or revoke (about 240 lines a
year at a 30-day ttl and ~20 rules); answering ``always`` again on another scope of a granted rule
restarts that rule's clock for every scope; two processes minting a secret at the same moment can
lose one answer, which is one more ask, never one fewer; an ``always`` whose record and void both
fail (the file unwritable for both) stands without a clock until the file's secret changes.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ._jsonstore import read_json, write_json

STATE_REL = "state/approval-grants.json"
DAY_S = 86400
# DOTALL and \Z: a base is built from call arguments, which may hold a newline; a key that did
# not parse would be an "always" with no clock.
_TOKEN = re.compile(r"^(?P<base>.+?):g(?P<tok>[0-9a-f]{6})(?P<scopes>(?::[a-z-]+)*)\Z", re.S)
PREFIX = "plugin_rule:"
#: The clock every function reads when not handed ``now``; the tests replace it.
_clock = time.time
_LOGGER = logging.getLogger(__name__)


def _path() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / STATE_REL


def ttl_s() -> float:
    from aura_schema import from_env
    return float(from_env().approvals_always_ttl_days) * DAY_S


@contextmanager
def _state() -> Iterator[dict[str, Any]]:
    """Read, let the caller change, write back; under one lock across processes."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(path)
        before = json.dumps(data, sort_keys=True)
        if not isinstance(data.get("secret"), str) or not isinstance(data.get("gen"), dict) \
                or not isinstance(data.get("grants"), dict):
            data = {"secret": secrets.token_hex(16), "gen": {}, "grants": {}}
        yield data
        if json.dumps(data, sort_keys=True) != before:
            write_json(path, data)


def _token(data: dict[str, Any], base: str) -> str:
    seed = f"{data['secret']}:{base}:{int(data['gen'].get(base, 0))}"
    return hashlib.sha256(seed.encode()).hexdigest()[:6]


def _bump(data: dict[str, Any], base: str) -> None:
    data["gen"][base] = int(data["gen"].get(base, 0)) + 1
    data["grants"].pop(base, None)


def key_for(base: str, now: float | None = None) -> str:
    """``base`` with its current token. A grant past its ttl is dropped here, so the key the
    gate hands Hermes is a new one and the call asks."""
    at = _clock() if now is None else now
    try:
        with _state() as data:
            grant = data["grants"].get(base)
            if grant and at - float(grant.get("at", 0)) >= ttl_s():
                _bump(data, base)
            return f"{base}:g{_token(data, base)}"
    except Exception:
        _LOGGER.exception("approval grant file unusable; %s asks under a one-off key", base[:80])
        return f"{base}:g{secrets.token_hex(3)}"


def parse(key: str) -> tuple[str, str, list[str]] | None:
    """(base, token, scopes) of a gate's key, with or without Hermes' ``plugin_rule:``; None
    for a key with no token (one given before this module, or not a plugin rule)."""
    m = _TOKEN.match(key[len(PREFIX):] if key.startswith(PREFIX) else key)
    if not m:
        return None
    return m["base"], m["tok"], [s for s in m["scopes"].split(":") if s]


def record(pattern_key: str, tool: str, now: float | None = None) -> bool:
    """Start the clock on a human's ``always``. A key whose token is not the current one (expired
    or revoked while the card was open) records nothing: Hermes keeps it, and it never matches."""
    got = parse(pattern_key)
    if got is None:
        return False
    base, tok, scopes = got
    with _state() as data:
        if tok != _token(data, base):
            return False
        old = data["grants"].get(base) or {}
        data["grants"][base] = {"tool": str(tool or "")[:80],
                                "at": _clock() if now is None else now,
                                "scopes": sorted(set(old.get("scopes", [])) | set(scopes))}
    return True


def void(pattern_key: str) -> None:
    """Make a key Hermes may now hold never match again: for an ``always`` that did not record."""
    got = parse(pattern_key)
    if got is not None:
        with _state() as data:
            _bump(data, got[0])


def revoke(base: str) -> bool:
    """Take a standing answer back at once, for every scope of it. True if one was live."""
    with _state() as data:
        live = base in data["grants"]
        if live:
            _bump(data, base)
    return live


def live(now: float | None = None) -> list[dict[str, Any]]:
    """Every standing answer still in force, oldest first: [{tool, base_key, scopes,
    granted_at, expires_at}], times in epoch seconds. Values are as recorded, so a caller that
    shows them treats them as text. A lapsed grant is retired here too (its generation bumped),
    so rules that never come up again do not pile up in the file."""
    at = _clock() if now is None else now
    ttl = ttl_s()
    with _state() as data:
        for base, g in list(data["grants"].items()):
            if at - float(g.get("at", 0)) >= ttl:
                _bump(data, base)
        rows = [{"tool": g.get("tool", ""), "base_key": base, "scopes": list(g.get("scopes", [])),
                 "granted_at": float(g.get("at", 0)), "expires_at": float(g.get("at", 0)) + ttl}
                for base, g in data["grants"].items()]
    return sorted((r for r in rows if r["expires_at"] > at), key=lambda r: r["granted_at"])
