"""AURA sets the stack's Home Assistant up itself (T-1523): onboarding and a long-lived token.

The owner asked for Home Assistant "fully integrated, without having to do anything with HA
myself". A fresh Home Assistant starts with an onboarding wizard and no user, and nothing can
read it without a token. :meth:`HaSetup.ensure` does what the owner would have done:

* a token that HA accepts (one from settings first, else the one AURA keeps) is **adopted**:
  nothing is written;
* a fresh HA gets a user AURA makes, every onboarding step finished (so HA's own page shows no
  wizard), and a long-lived token: **provisioned**;
* a token HA refuses, on an HA whose user AURA made, is replaced by one minted through HA's
  login flow with AURA's own login: **renewed**;
* an HA someone else onboarded, with no token that works, is left alone: **owned_elsewhere**,
  and the detail names the one setting that fixes it.

The login and the token are sealed (Fernet) in ``<config>/.storage/home_assistant.enc`` with
the key beside it, both 0600, the way the OAuth connectors keep theirs. The login is written
before the user exists, so a crash between the two cannot strand a user nobody knows the
password of.

On an HA AURA onboarded, the page is also held to ``bind``: HA 2026.11 runs the YAML
``server_host`` as a five-minute trial and then reverts to every interface unless an admin
promotes it, so :meth:`HaSetup._hold_bind` promotes the trial, or stages the bind again (HA
restarts onto it) and promotes it on the next check.

Not covered: an HA someone else owns (AURA never guesses or resets a password, nor touches its
HTTP config); signing the owner in to HA's own page (the login is sealed, not shown; AURA's
console is the page); the minute after HA's first start, before AURA has run, when the trial
already binds where the YAML says.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx
from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: The user AURA makes in a fresh Home Assistant.
USERNAME = "aura"
#: HA's onboarding steps after the user, with the bodies HA 2026.11 took (T-1523's probe).
LATER_STEPS = ("core_config", "analytics", "integration")
#: How long one answer may take; HA's first onboarding call runs migrations.
TIMEOUT = 30.0
#: What HA 2026.11's core_config step sets up by itself that reaches the internet with no setting
#: saying so (radio-browser.info's server list on start; Google's speech service on use): T-0343.
CALLS_OUT = frozenset({"radio_browser", "google_translate"})
#: How long a first onboarding watches for them; the step adds them seconds after it answers.
PRUNE_WAIT_S = 20.0
#: Ten years: HA's own default for a long-lived token from the profile page.
LIFESPAN_DAYS = 3650

Connect = Callable[[str], Awaitable[Any]]


class HaAuthError(RuntimeError):
    """HA's socket refused a token (a RuntimeError, not an OSError: it is an answer, not an outage)."""


async def ws_connect(url: str) -> Any:
    """The real socket. get_states on a big house runs past websockets' 1 MiB default."""
    from websockets.asyncio.client import connect

    return await connect(url, max_size=16 * 1024 * 1024, open_timeout=TIMEOUT)


def ws_url(url: str) -> str:
    return url.replace("http", "ws", 1) + "/api/websocket"


async def ws_auth(ws: Any, token: str) -> None:
    """HA's handshake: ``auth_required``, then the token, then ``auth_ok`` or a RuntimeError."""
    hello = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
    if hello.get("type") != "auth_required":
        raise RuntimeError(f"Home Assistant opened with {hello.get('type')!r}, not auth_required")
    await ws.send(json.dumps({"type": "auth", "access_token": token}))
    answer = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
    if answer.get("type") != "auth_ok":
        raise HaAuthError("Home Assistant refused the token")


class HaSetup:
    """One Home Assistant's credentials: what they are, whether they work, how to get new ones."""

    def __init__(self, jarvis: "Jarvis", url: str, *, token: str = "",
                 transport: httpx.AsyncBaseTransport | None = None, connect: Connect | None = None,
                 bind: str = "") -> None:
        self.url = url.strip().rstrip("/")
        self.bind = bind.strip()
        self.client_id = self.url + "/"  # HA wants a URL; its own origin is one it trusts
        self.configured_token = token.strip()
        self.transport = transport
        self.connect = connect or ws_connect
        self.dir = Path(jarvis.config_dir) / ".storage"
        self.key_path = self.dir / "home_assistant.key"
        self.sealed_path = self.dir / "home_assistant.enc"
        self._sealed = self._load()
        self.token = self.configured_token or str(self._sealed.get("token") or "")
        self._last: dict[str, Any] = {"state": "never", "detail": "not checked yet"}
        self._lock = asyncio.Lock()

    # --- the sealed file -----------------------------------------------------------------------
    def _fernet(self, create: bool) -> Fernet | None:
        if create and not self.key_path.exists():
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                # O_EXCL: two starts cannot each mint a key and orphan the other's file.
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key())
            except FileExistsError:
                pass
        try:
            return Fernet(self.key_path.read_bytes().strip())
        except (OSError, ValueError):
            return None

    def _load(self) -> dict[str, Any]:
        fernet = self._fernet(create=False)
        if fernet is None:
            return {}
        try:
            data = json.loads(fernet.decrypt(self.sealed_path.read_bytes()))
        except FileNotFoundError:
            return {}
        except (InvalidToken, ValueError, OSError):
            _LOGGER.warning("home_assistant: the sealed login could not be read; starting without it")
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, **changes: Any) -> None:
        fernet = self._fernet(create=True)
        if fernet is None:
            raise RuntimeError("the key for AURA's Home Assistant login could not be made")
        self._sealed = {**self._sealed, **changes}
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), prefix="home_assistant.", suffix=".tmp")  # 0600
        with os.fdopen(fd, "wb") as handle:
            handle.write(fernet.encrypt(json.dumps(self._sealed).encode()))
        os.replace(tmp, self.sealed_path)

    # --- Home Assistant's API ------------------------------------------------------------------
    async def _works(self, client: httpx.AsyncClient, token: str) -> bool:
        if not token:
            return False
        r = await client.get("/api/", headers={"Authorization": f"Bearer {token}"})
        return r.status_code == 200

    async def _exchange(self, client: httpx.AsyncClient, code: str) -> str:
        r = await client.post("/auth/token", data={"grant_type": "authorization_code", "code": code,
                                                   "client_id": self.client_id})
        access = r.json().get("access_token") if r.status_code == 200 else None
        if not isinstance(access, str) or not access:
            raise RuntimeError(f"Home Assistant gave no access token ({r.status_code})")
        return access

    async def _login(self, client: httpx.AsyncClient, login: dict[str, Any]) -> str:
        """HA's own login form, as its page drives it: a flow, then the username and password."""
        r = await client.post("/auth/login_flow", json={
            "client_id": self.client_id, "handler": ["homeassistant", None],
            "redirect_uri": self.client_id + "?auth_callback=1"})
        flow = r.json().get("flow_id") if r.status_code == 200 else None
        if not flow:
            raise RuntimeError(f"Home Assistant opened no login flow ({r.status_code})")
        r = await client.post(f"/auth/login_flow/{flow}", json={
            "username": login["username"], "password": login["password"], "client_id": self.client_id})
        body = r.json() if r.status_code == 200 else {}
        if body.get("type") != "create_entry" or not body.get("result"):
            raise RuntimeError("Home Assistant refused AURA's login")
        return await self._exchange(client, str(body["result"]))

    async def _finish_onboarding(self, client: httpx.AsyncClient, access: str) -> None:
        r = await client.get("/api/onboarding")
        done = {row.get("step") for row in r.json() if row.get("done")} if r.status_code == 200 else set()
        for step in LATER_STEPS:
            if step in done:
                continue
            body = ({"client_id": self.client_id, "redirect_uri": self.client_id + "?auth_callback=1"}
                    if step == "integration" else {})
            r = await client.post(f"/api/onboarding/{step}", json=body,
                                  headers={"Authorization": f"Bearer {access}"})
            if r.status_code != 200:
                raise RuntimeError(f"Home Assistant refused onboarding step {step} ({r.status_code})")

    async def _mint(self, access: str) -> str:
        ws = await self.connect(ws_url(self.url))
        try:
            await ws_auth(ws, access)
            # HA refuses a second long-lived token with a client name it already has: a renewal
            # needs its own name, and the time says which one is current on HA's profile page.
            name = "AURA " + time.strftime("%Y-%m-%d %H:%M", time.gmtime())
            await ws.send(json.dumps({"id": 1, "type": "auth/long_lived_access_token",
                                      "client_name": name, "lifespan": LIFESPAN_DAYS}))
            while True:
                answer = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
                if answer.get("id") == 1 and answer.get("type") == "result":
                    break
        finally:
            await ws.close()
        token = answer.get("result")
        if not answer.get("success") or not isinstance(token, str) or not token:
            raise RuntimeError("Home Assistant made no long-lived token")
        return token

    async def _ws_calls(self, *msgs: dict[str, Any]) -> list[dict[str, Any]]:
        """Send each message on one authenticated socket, in order; their answers."""
        ws = await self.connect(ws_url(self.url))
        answers: list[dict[str, Any]] = []
        try:
            await ws_auth(ws, self.token)
            for mid, msg in enumerate(msgs, start=1):
                await ws.send(json.dumps({"id": mid, **msg}))
                while True:
                    answer = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
                    if answer.get("id") == mid and answer.get("type") == "result":
                        answers.append(answer)
                        break
        finally:
            await ws.close()
        return answers

    async def _hold_bind(self) -> str:
        """Keep HA's page on ``bind``: ``held``, ``promoted``, or ``restarting`` (staged; HA restarts
        onto it, and the next check promotes it within HA's five-minute trial)."""
        want = [self.bind]
        (answer,) = await self._ws_calls({"type": "http/config"})
        if not answer.get("success"):
            raise RuntimeError("Home Assistant would not show its HTTP config")
        conf = answer.get("result") or {}
        stable, pending = conf.get("stable") or {}, conf.get("pending")
        if conf.get("active_config_type") == "pending" and isinstance(pending, dict) \
                and not pending.get("error") and pending.get("server_host") == want:
            (done,) = await self._ws_calls({"type": "http/config/promote"})
            if not done.get("success"):
                raise RuntimeError("Home Assistant would not keep its page's address")
            _LOGGER.info("home_assistant: its page is held to the configured bind address")
            return "promoted"
        if conf.get("active_config_type") == "stable" and stable.get("server_host") == want:
            return "held"
        staged = {k: v for k, v in stable.items() if k not in ("created_at", "error", "error_message")}
        (done,) = await self._ws_calls({"type": "http/config/configure",
                                        "config": {**staged, "server_host": want}})
        if not done.get("success"):
            raise RuntimeError("Home Assistant refused the bind address "
                               f"({(done.get('error') or {}).get('message') or 'no reason given'})")
        _LOGGER.warning("home_assistant: its page answered beyond the configured bind address; "
                        "AURA set it back and Home Assistant is restarting")
        return "restarting"

    async def _prune(self, client: httpx.AsyncClient) -> set[str]:
        """Remove what HA's onboarding set up by itself that reaches the internet unasked.

        Every run looks, because the core_config step adds them in the background, after it
        answered. Only an entry whose source is ``onboarding`` goes: one the owner added is theirs.
        """
        auth = {"Authorization": f"Bearer {self.token}"}
        removed: set[str] = set()
        r = await client.get("/api/config/config_entries/entry", headers=auth)
        if r.status_code != 200:
            return removed
        for entry in r.json():
            if entry.get("source") == "onboarding" and entry.get("domain") in CALLS_OUT:
                gone = await client.delete(f"/api/config/config_entries/entry/{entry['entry_id']}", headers=auth)
                if gone.status_code == 200:
                    removed.add(entry["domain"])
                    _LOGGER.info("home_assistant: removed %s, which onboarding set up and which calls out",
                                 entry["domain"])
        return removed

    async def _ensure(self, client: httpx.AsyncClient) -> dict[str, str]:
        if await self._works(client, self.configured_token):
            self.token = self.configured_token
            return {"state": "adopted", "detail": "Home Assistant accepts the token from settings"}
        kept = str(self._sealed.get("token") or "")
        if await self._works(client, kept):
            self.token = kept
            return {"state": "adopted", "detail": "Home Assistant accepts the token AURA keeps"}
        r = await client.get("/api/onboarding")
        if r.status_code != 200:
            raise RuntimeError(f"Home Assistant's onboarding answered {r.status_code}")
        user_done = any(row.get("step") == "user" and row.get("done") for row in r.json())
        if not user_done:
            login = {"username": USERNAME, "password": secrets.token_urlsafe(24)}
            self._save(login=login)  # before the user exists: never a user nobody can sign in as
            r = await client.post("/api/onboarding/users", json={
                "client_id": self.client_id, "name": "AURA", "language": "en", **login})
            code = r.json().get("auth_code") if r.status_code == 200 else None
            if not code:
                raise RuntimeError(f"Home Assistant refused the onboarding ({r.status_code})")
            access = await self._exchange(client, str(code))
            state, detail = "provisioned", "AURA onboarded Home Assistant and holds a long-lived token"
        elif isinstance(self._sealed.get("login"), dict):
            access = await self._login(client, self._sealed["login"])
            state, detail = "renewed", "Home Assistant refused the old token; AURA minted a new one"
        else:
            return {"state": "owned_elsewhere",
                    "detail": "Home Assistant was set up by someone else and AURA has no token it "
                              "accepts: make a long-lived token (HA: your profile, Security) and set "
                              "home_assistant.token (AURA_HA_TOKEN)"}
        await self._finish_onboarding(client, access)
        self.token = await self._mint(access)
        self._save(token=self.token)
        return {"state": state, "detail": detail}

    async def ensure(self) -> dict[str, Any]:
        """Make Home Assistant usable by AURA if it can be, and say what was found.

        Never raises; the answer never carries a secret.
        """
        async with self._lock:
            removed: set[str] = set()
            try:
                async with httpx.AsyncClient(base_url=self.url, timeout=TIMEOUT, transport=self.transport,
                                             follow_redirects=False) as client:
                    out = await self._ensure(client)
                    # Only on an HA AURA onboarded: on someone else's, what its onboarding set up is theirs.
                    if out["state"] in ("adopted", "provisioned", "renewed") and isinstance(
                            self._sealed.get("login"), dict):
                        removed = await self._prune(client)
                    # Right after onboarding the step is still adding them: watch for a while.
                    deadline = time.monotonic() + (PRUNE_WAIT_S if out["state"] == "provisioned" else 0)
                    while not CALLS_OUT <= removed and time.monotonic() < deadline:
                        await asyncio.sleep(2.0)
                        removed |= await self._prune(client)
                # Only on an HA AURA onboarded: someone else's HA's page is theirs to place.
                if self.bind and out["state"] in ("adopted", "provisioned", "renewed") and isinstance(
                        self._sealed.get("login"), dict):
                    out["http"] = await self._hold_bind()
            except (httpx.HTTPError, OSError, asyncio.TimeoutError, ValueError) as exc:
                out = {"state": "unreachable", "detail": f"Home Assistant did not answer ({type(exc).__name__})"}
            except (RuntimeError, KeyError) as exc:
                out = {"state": "failed", "detail": str(exc)}
            except Exception as exc:  # noqa: BLE001 - a websocket library error is still "it did not answer"
                out = {"state": "unreachable", "detail": f"Home Assistant did not answer ({type(exc).__name__})"}
            if out["state"] == "owned_elsewhere":
                # Someone onboarded HA before AURA could: expected only on an HA the owner brought,
                # and the race AURA_HA_BIND's loopback default exists to stop on the stack's own.
                _LOGGER.warning("home_assistant setup: %s (%s)", out["state"], out["detail"])
            elif out["state"] in ("provisioned", "renewed", "failed"):
                _LOGGER.info("home_assistant setup: %s (%s)", out["state"], out["detail"])
            self._last = {**out, "url": self.url, "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            return dict(self._last)

    def status(self) -> dict[str, Any]:
        """The last :meth:`ensure` answer; no secret in it."""
        return {**self._last, "url": self.url, "has_token": bool(self.token)}
