"""AURA sets n8n up itself (T-1523): the first-run owner, an API key, and the key where the tools read it.

The owner asked for n8n "fully integrated, without having to do anything with it myself". The
stack's n8n starts with no owner, and its public API (every ``n8n_*`` tool past the webhook)
needs a key that only an owner can mint in n8n's UI. So :func:`ensure` does what the owner would:

* a key that is set and that n8n accepts is **adopted**: nothing is written;
* a fresh n8n (``showSetupOnFirstLoad``) gets an owner AURA made, a login, and one API key with
  every scope the owner role may grant: **provisioned**;
* a key n8n refuses, on an n8n whose owner AURA made earlier, is replaced by a new one minted
  with AURA's own login: **renewed** (n8n's key store was reset, or the key was revoked);
* an n8n somebody else owns, with no working key, is left alone: **owned_elsewhere**, and the
  detail says the one thing that fixes it (a key in settings).

The key lands where ADR-0044 keeps an operator's env values, ``$HERMES_HOME/.storage/
environment.json``, which the entrypoint exports on every start, and in this process's
environment, which the tools read per call: it works now, not after a restart. The login AURA
made is kept beside it in ``n8n-owner.json``, 0600, so the owner can sign in to n8n's editor and
AURA can renew the key; it is written *before* the owner is created, so a crash between the two
cannot strand an owner nobody knows the password of.

Two n8n behaviours shape the client (seen on 2.46.6, T-1523's log): the ``n8n-auth`` cookie is
``Secure``, so a client on plain http (the stack's ``http://n8n:5678``) must send it back by hand;
and n8n binds a session to the ``browser-id`` header it logged in with.

Not covered: an n8n owned by someone else (AURA does not guess or reset a password); two
gateway processes writing the override file at the same instant as the settings plugin (the
lock below is this module's, not the settings plugin's; a first run is the only write).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from aura_schema import from_env

from _shared import _egress

_LOGGER = logging.getLogger(__name__)

#: The env name the n8n tools read their key from (``aura_schema`` ``n8n.api_key``).
KEY_ENV = "N8N_API_KEY"
#: The owner AURA creates. ``.invalid`` can never be a real mailbox (RFC 2606): n8n needs an
#: address, and nobody should ever receive mail meant for this one.
OWNER = {"email": "aura@aura.invalid", "firstName": "AURA", "lastName": "House"}
#: How often the gateway checks again: a reset n8n volume is noticed within this.
INTERVAL_S = 600.0


def _storage(home: Path) -> Path:
    return Path(home) / ".storage"


def login_file(home: Path) -> Path:
    return _storage(home) / "n8n-owner.json"


def _status_file(home: Path) -> Path:
    return _storage(home) / "n8n-setup.json"


def _write(path: Path, data: dict, mode: int) -> None:
    """Atomic, created at ``mode`` before a byte is written (both files carry secrets)."""
    path.parent.mkdir(mode=0o711, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store_key(home: Path, key: str) -> None:
    """The key into the override file (0640: the adapter, in the gateway's group, reads it) and
    into this process, so the tools have it on their next call."""
    path = _storage(home) / "environment.json"
    data = _read(path)
    data[KEY_ENV] = key
    _write(path, data, 0o640)
    os.environ[KEY_ENV] = key


class _Session:
    """One n8n login: the Secure cookie sent by hand, and the browser-id it is bound to."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.browser = uuid.uuid4().hex
        self.cookie = ""

    def _headers(self) -> dict[str, str]:
        headers = {"browser-id": self.browser}
        if self.cookie:
            headers["cookie"] = f"n8n-auth={self.cookie}"
        return headers

    def post(self, path: str, body: dict) -> httpx.Response:
        r = self.client.post(path, json=body, headers=self._headers())
        self.cookie = r.cookies.get("n8n-auth") or self.cookie
        return r

    def get(self, path: str) -> httpx.Response:
        return self.client.get(path, headers=self._headers())


def _key_works(client: httpx.Client, key: str) -> bool:
    if not key:
        return False
    r = client.get("/api/v1/workflows", params={"limit": 1}, headers={"X-N8N-API-KEY": key})
    return r.status_code == 200


def _mint(session: _Session, login: dict) -> str:
    """Log in as ``login`` and mint one key with every scope the owner may grant."""
    r = session.post("/rest/login", {"emailOrLdapLoginId": login["email"], "password": login["password"]})
    if r.status_code != 200:
        raise RuntimeError(f"n8n refused AURA's login ({r.status_code})")
    r = session.get("/rest/api-keys/scopes")
    scopes = r.json().get("data") if r.status_code == 200 else None
    if not isinstance(scopes, list) or not scopes:
        raise RuntimeError(f"n8n listed no API key scopes ({r.status_code})")
    # n8n keeps UNIQUE(userId, label), revoked keys' rows included: a second "AURA" was a 500, so
    # a renewal could never succeed. The time says which key is current in n8n's settings page.
    label = "AURA " + time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " " + secrets.token_hex(2)
    r = session.post("/rest/api-keys", {"label": label, "scopes": scopes, "expiresAt": None})
    key = (r.json().get("data") or {}).get("rawApiKey") if r.status_code == 200 else None
    if not isinstance(key, str) or not key:
        raise RuntimeError(f"n8n made no API key ({r.status_code})")
    return key


def _ensure(home: Path, client: httpx.Client) -> dict[str, Any]:
    key = from_env().n8n_api_key
    if _key_works(client, key):
        return {"state": "adopted", "detail": "n8n accepts the key AURA has"}
    # Each gateway process runs this keeper. The one that minted holds the key in its environment;
    # the others, started before it, find it only in the override file. Without this they took the
    # key for refused and minted again (production, T-1523).
    stored = str(_read(_storage(home) / "environment.json").get(KEY_ENV) or "")
    if stored and stored != key and _key_works(client, stored):
        os.environ[KEY_ENV] = stored
        return {"state": "adopted", "detail": "n8n accepts the key AURA keeps"}
    settings = client.get("/rest/settings")
    if settings.status_code != 200:
        # A proxy in front of n8n that cannot reach it answers 502 here. Read as "not fresh", that
        # sent the owner to make a key for an n8n nobody had set up (production, T-1523).
        return {"state": "unreachable",
                "detail": f"n8n's settings answered {settings.status_code} at N8N_URL: check that "
                          "it reaches n8n (the stack's own is http://n8n:5678)"}
    fresh = bool(((settings.json().get("data") or {}).get("userManagement") or {})
                 .get("showSetupOnFirstLoad"))
    session = _Session(client)
    if fresh:
        login = {**OWNER, "password": "Aa1" + secrets.token_urlsafe(24),
                 "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write(login_file(home), login, 0o600)  # before the owner exists: never an orphan owner
        r = session.post("/rest/owner/setup", {k: login[k] for k in ("email", "firstName", "lastName", "password")})
        if r.status_code != 200:
            raise RuntimeError(f"n8n refused the owner setup ({r.status_code})")
        _store_key(home, _mint(session, login))
        return {"state": "provisioned", "detail": "AURA made n8n's owner and an API key"}
    login = _read(login_file(home))
    if login.get("email") and login.get("password"):
        _store_key(home, _mint(session, login))
        return {"state": "renewed", "detail": "n8n refused the old key; AURA minted a new one"}
    return {"state": "owned_elsewhere",
            "detail": "n8n has an owner AURA did not make and no key that works: set n8n.api_key "
                      "(n8n: Settings, n8n API) and AURA uses it"}


def ensure(*, home: Path | None = None, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Make n8n usable by AURA if it can be, and say what it found. Never raises; never returns a secret."""
    home = Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    try:
        base = _egress.service_url(from_env().n8n_url, "N8N_URL")
    except _egress.ServiceUrlError as exc:  # unset, or an address the token may not go to
        return _record(home, {"state": "off", "detail": str(exc)})
    _storage(home).mkdir(mode=0o711, parents=True, exist_ok=True)
    with open(_storage(home) / "n8n-setup.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one gateway process provisions; the others then adopt
        try:
            with httpx.Client(base_url=base, timeout=20, transport=transport,
                              verify=from_env().n8n_verify_ssl) as client:
                out = _ensure(home, client)
        except (httpx.HTTPError, ValueError) as exc:
            out = {"state": "unreachable", "detail": f"n8n did not answer ({exc.__class__.__name__})"}
        except RuntimeError as exc:
            out = {"state": "failed", "detail": str(exc)}
    if out["state"] in ("provisioned", "renewed", "failed", "owned_elsewhere"):
        _LOGGER.info("n8n setup: %s (%s)", out["state"], out["detail"])
    return _record(home, out)


def _record(home: Path, out: dict[str, Any]) -> dict[str, Any]:
    out = {**out, "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "login_file": str(login_file(home))}
    try:
        _write(_status_file(home), out, 0o640)
    except OSError:
        pass  # a read-only home still answers the caller
    return out


def status(home: Path | None = None) -> dict[str, Any]:
    """The last :func:`ensure` answer (no secret in it), or ``never`` before the first run."""
    home = Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return _read(_status_file(home)) or {"state": "never", "detail": "not checked yet",
                                          "login_file": str(login_file(home))}


_keeper: threading.Thread | None = None
_keeper_lock = threading.Lock()
#: The first check waits this long: the gateway is still loading, and n8n may still be starting.
FIRST_S = 20.0


#: How soon after an n8n that did not answer: the gateway is up before n8n on a fresh stack, and
#: a ten-minute wait left the n8n tools off that long on every first start.
RETRY_S = 60.0


def _keep(stop: threading.Event, first: float, interval: float, retry: float = RETRY_S) -> None:
    if stop.wait(first):
        return
    while True:
        out = ensure()
        settled = out.get("state") not in ("unreachable", "failed")
        if stop.wait(interval if settled else min(retry, interval)):
            return


def start(*, first: float = FIRST_S, interval: float = INTERVAL_S) -> threading.Event | None:
    """Start the keeper once per process; the stop event, or None when one already runs.

    Off when n8n.url is unset (nothing to keep). Never raises: the n8n plugin must load either way.
    """
    global _keeper
    if not str(from_env().n8n_url or "").strip():
        return None
    with _keeper_lock:
        if _keeper is not None and _keeper.is_alive():
            return None
        stop = threading.Event()
        try:
            _keeper = threading.Thread(target=_keep, args=(stop, first, interval),
                                       name="aura-n8n-setup", daemon=True)
            _keeper.start()
        except Exception as exc:  # noqa: BLE001 - a helper thread must never stop a plugin loading
            _LOGGER.warning("the n8n setup keeper could not start (%s)", exc.__class__.__name__)
            return None
        return stop


__all__ = ["FIRST_S", "INTERVAL_S", "KEY_ENV", "OWNER", "ensure", "login_file", "start", "status"]
