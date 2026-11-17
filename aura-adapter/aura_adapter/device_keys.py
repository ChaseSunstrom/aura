"""Per-device adapter keys (T-0569): one key per paired device, so one can be revoked alone.

Before this every phone held ``AURA_API_KEY`` itself, and locking out a lost one meant
rotating the key on every other device. A device key is minted at pairing (``POST /api/pair``,
master key only), shown once, and stored only as its SHA-256; ``AURA_API_KEY`` stays the
owner's break-glass key and the only one that can mint, list or revoke.

The file lives on its own volume that only the adapter mounts: Hermes can write its own home,
where a planted hash would be a working key, and the artifacts volume has a sweeper.

Not covered: a request already admitted when its key is revoked finishes (the check reads
without the lock); a lost or damaged file admits no device key, so those devices re-pair
(the master key is unaffected), and a damaged one refuses pairing and revoking until it is
moved aside, rather than be overwritten; nothing here rotates the master key.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_LOGGER = logging.getLogger(__name__)

ROOT = Path("/opt/adapter-state")
FILE = "device-keys.json"
#: A phone checks in on every request; last-seen is written at most this often per key, so
#: the file is not rewritten under a lock on every call.
TOUCH_EVERY_S = 60.0
MAX_NAME = 64
KINDS = ("phone", "desktop", "other")
#: jarvis-core's token id for the same device, so one revoke in the console ends both.
HOUSE_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: Present once any phone has registered an approval key (see ``approval_keys_lost``).
MARK = "approval-key.registered"

_clock = time.time


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Damaged(OSError):
    """The file is there but unreadable: rewriting it would drop every other device's key."""


def _read(strict: bool = False) -> dict[str, dict[str, Any]]:
    """The live records by id; a missing or damaged file is no records (fail closed), and
    with ``strict`` (a rewrite) a damaged one raises, so the routes answer 503 instead."""
    try:
        data = json.loads((ROOT / FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if strict:
            raise Damaged(f"cannot read {ROOT / FILE}: {exc}") from exc
        _LOGGER.error("device keys: cannot read %s, admitting no device key: %s", ROOT / FILE, exc)
        return {}
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, dict):
        if strict:
            raise Damaged(f"{ROOT / FILE} is not a key table")
        _LOGGER.error("device keys: %s is not a key table, admitting no device key", ROOT / FILE)
        return {}
    return {k: v for k, v in keys.items() if isinstance(v, dict) and isinstance(v.get("hash"), str)}


def _write(path: Path, data: dict[str, Any]) -> None:
    # Temp file and rename in the same directory: a reader never sees half a file.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".device-keys.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextmanager
def _locked() -> Iterator[dict[str, dict[str, Any]]]:
    """The records, re-read under the lock and written back when the block ends."""
    with open(ROOT / (FILE + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        keys = _read(strict=True)
        yield keys
        _write(ROOT / FILE, {"version": 1, "keys": keys})


def _public(ident: str, rec: dict[str, Any]) -> dict[str, Any]:
    return {"id": ident, "name": rec.get("name"), "kind": rec.get("kind"),
            "created": rec.get("created"), "last_seen": rec.get("last_seen"),
            "house_token": rec.get("house_token")}


def mint(name: str, kind: str, house_token: str = "") -> tuple[dict[str, Any], str]:
    """A new device and its key; the key is returned here and never again."""
    name = "".join(c for c in name if c.isprintable()).strip()[:MAX_NAME] or "device"
    kind = kind if kind in KINDS else "other"
    key, ident = secrets.token_urlsafe(32), secrets.token_hex(8)
    rec = {"name": name, "kind": kind, "hash": _hash(key), "created": _clock(), "last_seen": None,
           "house_token": house_token if HOUSE_TOKEN.fullmatch(house_token) else None}
    with _locked() as keys:
        keys[ident] = rec
    return _public(ident, rec), key


def check(presented: str, touch: bool = True) -> str | None:
    """The id of the live device holding ``presented``, or None."""
    digest = _hash(presented)
    for ident, rec in _read().items():
        if hmac.compare_digest(digest, rec["hash"]):
            seen = rec.get("last_seen")
            if touch and not (isinstance(seen, (int, float)) and _clock() - seen < TOUCH_EVERY_S):
                try:
                    mark_seen(ident)
                except OSError as exc:  # a full disk must not refuse a good key
                    _LOGGER.warning("device keys: cannot record last-seen: %s", exc)
            return ident
    return None


def mark_seen(ident: str) -> None:
    """Record that ``ident`` was just seen, unless it was revoked since the check read it."""
    with _locked() as keys:
        if ident in keys:
            keys[ident]["last_seen"] = _clock()


def describe(ident: str) -> dict[str, Any] | None:
    rec = _read().get(ident)
    return _public(ident, rec) if rec else None


def list_keys() -> list[dict[str, Any]]:
    return sorted((_public(i, r) for i, r in _read().items()), key=lambda d: d["created"] or 0)


def set_approval_key(ident: str, public: str) -> str:
    """Put the phone's approval signing key on its record: "ok", "conflict", "second-signer", "not-a-phone" or "gone".

    First one wins. A device key that leaked could otherwise swap in a key of its own and
    sign sensitive approvals without the phone; a phone whose Keystore key was lost re-pairs.
    Does not remove a second key registered before this rule: both keep signing until one is revoked.
    """
    with _locked() as keys:
        rec = keys.get(ident)
        if rec is None:
            return "gone"
        if rec.get("kind") != "phone":
            # A desktop's or a script's key is not behind a fingerprint.
            return "not-a-phone"
        if rec.get("approval_key") not in (None, public):
            return "conflict"
        if rec.get("approval_key") == public:
            # The phone starting up again; nothing changes, whoever else signs.
            return "ok"
        if any(i != ident and r.get("kind") == "phone" and isinstance(r.get("approval_key"), str)
               for i, r in keys.items()):
            # One signer (T-1030): a pairing made later, or a leaked phone key, would otherwise
            # sign beside the owner's phone unasked. Revoking that phone is the owner's yes.
            return "second-signer"
        rec["approval_key"] = public
        # Beside the table, not in it: it has to outlive the table being lost or damaged.
        (ROOT / MARK).touch()
        return "ok"


def approval_keys() -> list[str]:
    """Every live device's approval signing key; a revoked device's went with its record."""
    return [r["approval_key"] for r in _read().values()
            if r.get("kind") == "phone" and isinstance(r.get("approval_key"), str)]


def approval_keys_lost() -> bool:
    """A phone registered a signing key once and none can be read now: a lost or damaged table.

    The gate refuses then, where "no phone has a key yet" steps down to a tap. Does not cover a
    volume lost whole (the mark goes with it, and so does every pairing), or a table restored
    from before the key was registered while the mark stayed: that refuses until the phone
    registers again or its record is revoked.
    """
    return (ROOT / MARK).exists() and not approval_keys()


def revoke(ident: str) -> bool:
    with _locked() as keys:
        gone = keys.pop(ident, None) is not None
        if gone and not any(isinstance(r.get("approval_key"), str) for r in keys.values()):
            # The owner ended the last signing phone on purpose: the ordinary ask is back.
            (ROOT / MARK).unlink(missing_ok=True)
        return gone
