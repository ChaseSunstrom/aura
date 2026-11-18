"""A Home Assistant fake for the provisioning and bridge tests (T-1523).

It copies what Home Assistant 2026.11.2 did on a throwaway instance (T-1523's log): four
onboarding steps; ``/api/onboarding/users`` answers an ``auth_code`` (and 403 once the user step is
done); ``/auth/token`` turns a code into an access token; the websocket says ``auth_required``,
takes a token, mints long-lived ones, lists states, takes subscriptions and service calls;
``/auth/login_flow`` signs an existing user in and answers a code.
"""
from __future__ import annotations

import asyncio
import json

import httpx

URL = "http://127.0.0.1:8123"


class FakeWs:
    """One websocket session against :class:`FakeHA`; ``recv`` waits like a real socket."""

    def __init__(self, ha: "FakeHA") -> None:
        self.ha = ha
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.inbox.put_nowait(json.dumps({"type": "auth_required", "ha_version": "2026.11.2"}))
        self.authed = False
        self.closed = False

    def push(self, msg: dict) -> None:
        self.inbox.put_nowait(json.dumps(msg))

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        if not self.authed:
            ok = msg.get("type") == "auth" and msg.get("access_token") in self.ha.valid_tokens()
            self.authed = ok
            self.push({"type": "auth_ok" if ok else "auth_invalid"})
            return
        for answer in self.ha.ws_answer(self, msg):
            self.push(answer)

    async def recv(self) -> str:
        raw = await self.inbox.get()
        if raw == "__closed__":
            raise ConnectionError("the fake closed the socket")
        return raw

    async def close(self) -> None:
        self.closed = True

    def drop(self) -> None:
        """HA restarting: the next recv fails."""
        self.inbox.put_nowait("__closed__")


class FakeHA:
    def __init__(self) -> None:
        self.steps = {"user": False, "core_config": False, "analytics": False, "integration": False}
        self.users: dict[str, str] = {}
        self.codes: dict[str, str] = {}
        self.access: set[str] = set()
        self.long_lived: set[str] = set()
        self.minted = 0
        self.states = [{"entity_id": "light.kitchen", "state": "off",
                        "attributes": {"friendly_name": "Kitchen", "supported_color_modes": ["brightness"]}}]
        self.calls: list[dict] = []
        self.subscribers: list[tuple[FakeWs, int, str | None]] = []
        self.sockets: list[FakeWs] = []
        self.flows: list[dict] = []
        # What 2026.11.2's core_config step added on its own (T-1523's live probe).
        self.entries = [{"entry_id": "e1", "domain": "radio_browser", "source": "onboarding"},
                        {"entry_id": "e2", "domain": "google_translate", "source": "onboarding"},
                        {"entry_id": "e3", "domain": "shopping_list", "source": "onboarding"}]
        self.up = True
        # HA 2026.11's HTTP config store (homeassistant/components/http/config.py): the YAML
        # `http:` block is migrated once as a PENDING trial, and reverted to STABLE (the default:
        # every interface) unless an admin promotes it within five minutes.
        self.http_stable: dict = {"server_port": 8123, "cors_allowed_origins": [], "ip_ban_enabled": True,
                                  "created_at": "t0", "error": None, "error_message": None}
        self.http_pending: dict | None = None
        self.http_active = "stable"
        self.restarts = 0
        # HA's entity registry (config/entity_registry/*): an entity with no row is a YAML one.
        self.registry: dict[str, dict] = {}
        self.registry_lookups: list[str] = []

    def register(self, entity_id: str, **row) -> None:
        """A new registry row, announced as HA does (entity_registry_updated, action create)."""
        self.registry[entity_id] = {"entity_id": entity_id, "platform": "demo", "entity_category": None,
                                    "hidden_by": None, "disabled_by": None, **row}
        self._push_event("entity_registry_updated", {"action": "create", "entity_id": entity_id})

    def update_registry(self, entity_id: str, **changes) -> None:
        old = {k: self.registry[entity_id].get(k) for k in changes}
        self.registry[entity_id].update(changes)
        self._push_event("entity_registry_updated",
                         {"action": "update", "entity_id": entity_id, "changes": old})

    def _push_event(self, event_type: str, data: dict) -> None:
        for ws, mid, wanted in self.subscribers:
            if wanted in (None, event_type):
                ws.push({"id": mid, "type": "event", "event": {"event_type": event_type, "data": data}})

    def first_boot_with_yaml_bind(self, host: str) -> None:
        """HA's first start with `server_host` in configuration.yaml: running on a trial."""
        self.http_pending = {**self.http_stable, "server_host": [host], "created_at": "t1"}
        self.http_active = "pending"

    def trial_reverted(self) -> None:
        """Nobody promoted the trial in five minutes: HA restarted on the default."""
        self.http_pending = {**(self.http_pending or {}), "error": "not_promoted"}
        self.http_active = "stable"
        self.restarts += 1

    def bound(self) -> list | None:
        conf = self.http_pending if self.http_active == "pending" else self.http_stable
        return (conf or {}).get("server_host")

    def valid_tokens(self) -> set[str]:
        return self.access | self.long_lived

    def _code(self, user: str) -> str:
        code = f"code-{len(self.codes)}-{user}"
        self.codes[code] = user
        return code

    def http(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        if path == "/api/onboarding" and method == "GET":
            return httpx.Response(200, json=[{"step": k, "done": v} for k, v in self.steps.items()])
        if path == "/api/onboarding/users" and method == "POST":
            if self.steps["user"]:
                return httpx.Response(403, json={"message": "User step already done"})
            body = json.loads(request.content)
            self.users[body["username"]] = body["password"]
            self.steps["user"] = True
            return httpx.Response(200, json={"auth_code": self._code(body["username"])})
        if path == "/auth/token" and method == "POST":
            form = dict(httpx.QueryParams(request.content.decode()))
            if form.get("grant_type") != "authorization_code" or form.get("code") not in self.codes:
                return httpx.Response(400, json={"error": "invalid_request"})
            self.codes.pop(form["code"])
            token = f"access-{len(self.access)}"
            self.access.add(token)
            return httpx.Response(200, json={"access_token": token, "refresh_token": "r",
                                             "token_type": "Bearer", "expires_in": 1800})
        if path.startswith("/api/onboarding/") and method == "POST":
            if bearer not in self.access:
                return httpx.Response(401)
            self.steps[path.rsplit("/", 1)[1]] = True
            return httpx.Response(200, json={})
        if path == "/auth/login_flow" and method == "POST":
            return httpx.Response(200, json={"type": "form", "flow_id": "f1", "step_id": "init"})
        if path == "/auth/login_flow/f1" and method == "POST":
            body = json.loads(request.content)
            if self.users.get(body.get("username")) != body.get("password"):
                return httpx.Response(200, json={"type": "form", "errors": {"base": "invalid_auth"}})
            return httpx.Response(200, json={"type": "create_entry", "result": self._code(body["username"])})
        if path == "/api/config/config_entries/entry" and method == "GET":
            if bearer not in self.valid_tokens():
                return httpx.Response(401)
            return httpx.Response(200, json=self.entries)
        if path.startswith("/api/config/config_entries/entry/") and method == "DELETE":
            if bearer not in self.valid_tokens():
                return httpx.Response(401)
            gone = path.rsplit("/", 1)[1]
            self.entries = [e for e in self.entries if e["entry_id"] != gone]
            return httpx.Response(200, json={"require_restart": False})
        if path == "/api/":
            if bearer in self.valid_tokens():
                return httpx.Response(200, json={"message": "API running."})
            return httpx.Response(401)
        return httpx.Response(404)

    def ws_answer(self, ws: FakeWs, msg: dict) -> list[dict]:
        kind, mid = msg.get("type"), msg.get("id")
        ok = {"id": mid, "type": "result", "success": True}
        if kind == "auth/long_lived_access_token":
            self.minted += 1
            token = f"long-lived-{self.minted}"
            self.long_lived.add(token)
            return [{**ok, "result": token}]
        if kind == "get_states":
            return [{**ok, "result": self.states}]
        if kind == "subscribe_events":
            self.subscribers.append((ws, mid, msg.get("event_type")))
            return [{**ok, "result": None}]
        if kind == "config/entity_registry/list":
            return [{**ok, "result": list(self.registry.values())}]
        if kind == "config/entity_registry/get":
            self.registry_lookups.append(msg["entity_id"])
            row = self.registry.get(msg["entity_id"])
            if row is None:
                return [{"id": mid, "type": "result", "success": False,
                         "error": {"code": "not_found", "message": "Entity not found"}}]
            return [{**ok, "result": row}]
        if kind == "call_service":
            self.calls.append({k: v for k, v in msg.items() if k != "id"})
            return [{**ok, "result": {"context": {"id": "c"}}}]
        if kind == "http/config":
            return [{**ok, "result": {"stable": self.http_stable, "pending": self.http_pending,
                                      "revert_at": None, "active_config_type": self.http_active,
                                      "default": {"server_port": 8123}}}]
        if kind == "http/config/configure":
            config = msg["config"]
            assert not {"created_at", "error", "error_message"} & set(config), "HA's schema has no meta keys"
            # As async_set_pending: a config equal to stable clears the slot; any other is stored
            # with a fresh created_at and no error, and HA restarts whenever the slot changed.
            previous = self.http_pending
            meta = ("created_at", "error", "error_message")
            if config == {k: v for k, v in self.http_stable.items() if k not in meta}:
                self.http_pending = None
            else:
                self.http_pending = {**config, "created_at": f"t{self.restarts + 2}", "error": None,
                                     "error_message": None}
            restart = self.http_pending != previous
            if restart:  # HA restarts itself onto the new trial
                self.http_active = "pending" if self.http_pending else "stable"
                self.restarts += 1
            return [{**ok, "result": {"restart": restart}}]
        if kind == "http/config/promote":
            if not self.http_pending or self.http_pending.get("error"):
                return [{"id": mid, "type": "result", "success": False,
                         "error": {"code": "not_allowed", "message": "No pending HTTP config to promote"}}]
            self.http_stable, self.http_pending, self.http_active = self.http_pending, None, "stable"
            return [{**ok, "result": None}]
        if kind == "config_entries/flow/progress":
            return [{**ok, "result": self.flows}]
        return [{"id": mid, "type": "result", "success": False, "error": {"code": "unknown_command"}}]

    def push_state(self, entity_id: str, state: str, attributes: dict) -> None:
        new = {"entity_id": entity_id, "state": state, "attributes": attributes}
        self.states = [s for s in self.states if s["entity_id"] != entity_id] + [new]
        self._push_event("state_changed", {"entity_id": entity_id, "new_state": new})

    async def connect(self, url: str) -> FakeWs:
        if not self.up:
            raise ConnectionRefusedError("refused")
        assert url == URL.replace("http", "ws", 1) + "/api/websocket"
        ws = FakeWs(self)
        self.sockets.append(ws)
        return ws
