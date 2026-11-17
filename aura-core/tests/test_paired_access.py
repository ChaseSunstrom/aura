"""A paired device's token is denied by default at both auth doors (T-1378).

The decision is asserted, not stored state: a paired token is refused on every REST
route and WebSocket command that is not on `aura.paired_access`'s two tables, with
the one fixed answer; the same request from the environment token or a console-minted
token is never refused. A paired token cannot mint another token, so the walk-around
(mint an ordinary token, use that) is closed at the first step.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from aura.api.server import create_app  # noqa: E402
from aura.api.websocket import WebSocketHandler  # noqa: E402
from aura.auth import DATA_AUTH, ENV_TOKEN, AuthManager  # noqa: E402
from aura.bus import Context  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.paired_access import (  # noqa: E402
    REFUSED_CODE,
    REFUSED_MESSAGE,
    REST_ROUTES,
    WS_COMMANDS,
    WS_EVENTS,
)

REFUSED = {"code": REFUSED_CODE, "message": REFUSED_MESSAGE}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_TOKEN, "house-env-token")
    box = Jarvis(tmp_path / "config")
    auth = box.data[DATA_AUTH] = AuthManager()
    info, paired = asyncio.run(auth.create_token("Pixel", paired=True))
    tokens = {
        "paired": paired,
        "paired_id": info.id,
        "console": asyncio.run(auth.create_token("console"))[1],
        "env": "house-env-token",
    }
    with TestClient(create_app(box, static_dir=tmp_path / "no-www")) as client:
        yield box, client, tokens


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _ws(client, token, frame):
    with client.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": token})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"id": 1, **frame})
        return ws.receive_json()


def _refused_ws(frame):
    return frame["success"] is False and frame["error"] == REFUSED


# What a paired token must not do. Each row is (what, REST call, WebSocket frame); a row
# without a WebSocket twin has none. Bodies are only plausible: the refusal comes from the
# door before the handler reads them, and the console token control only has to be "not 403".
DENIED = [
    ("token mint", ("POST", "/api/auth/tokens", {"name": "x"}), None),
    ("token list", ("GET", "/api/auth/tokens", None), {"type": "config/token/list"}),
    ("token delete", ("DELETE", "/api/auth/tokens/nope", None),
     {"type": "config/token/revoke", "token_id": "nope"}),
    ("generic service call", ("POST", "/api/services/light/turn_on", {}),
     {"type": "call_service", "domain": "light", "service": "turn_on", "service_data": {}}),
    ("automation create", ("POST", "/api/config/automation/create", {"alias": "x"}),
     {"type": "config/automation/create", "automation": {"alias": "x"}}),
    ("schedule add", ("POST", "/api/schedule", {"domain": "lock", "service": "unlock"}),
     {"type": "jarvis/schedule/add", "domain": "lock", "service": "unlock"}),
    ("tool create", ("POST", "/api/config/tool/create", {"name": "x"}),
     {"type": "config/tool/create", "tool": {"name": "x"}}),
    ("tools call", ("POST", "/api/tools/call", {"name": "x"}),
     {"type": "jarvis/tools/call", "name": "x"}),
    ("state write", ("POST", "/api/states/sensor.x", {"state": "on"}), None),
    ("event fire", ("POST", "/api/events/anything", {}),
     {"type": "fire_event", "event_type": "anything"}),
    ("settings write", ("POST", "/api/config/settings/set", {"key": "x", "value": 1}),
     {"type": "config/settings/set", "key": "x", "value": 1}),
    ("states read", ("GET", "/api/states", None), {"type": "get_states"}),
]


@pytest.mark.parametrize("what,rest,_ws_frame", DENIED, ids=[d[0] for d in DENIED])
def test_paired_token_is_denied_by_default_over_rest(rig, what, rest, _ws_frame):
    _box, client, tok = rig
    method, path, body = rest
    r = client.request(method, path, json=body, headers=_h(tok["paired"]))
    assert r.status_code == 403 and r.json()["detail"] == REFUSED
    # the controls: neither the console-minted nor the environment token is refused
    for who in ("console", "env"):
        ok = client.request(method, path, json=body, headers=_h(tok[who]))
        assert ok.status_code != 403, (who, ok.text)


@pytest.mark.parametrize(
    "what,frame", [(d[0], d[2]) for d in DENIED if d[2]], ids=[d[0] for d in DENIED if d[2]]
)
def test_paired_token_is_denied_by_default_on_the_websocket(rig, what, frame):
    _box, client, tok = rig
    assert _refused_ws(_ws(client, tok["paired"], frame))
    for who in ("console", "env"):
        ok = _ws(client, tok[who], frame)
        assert ok["success"] or ok["error"]["code"] != REFUSED_CODE, (who, ok)


def test_approver_flag_is_listed_and_only_a_non_paired_token_can_change_it(rig):
    """T-1380: the owner's switch for "this device may hear and answer approvals"."""
    box, client, tok = rig
    paired_id = tok["paired_id"]
    row = {r["id"]: r for r in client.get("/api/auth/tokens", headers=_h(tok["console"])).json()}
    assert row[paired_id]["approver"] is True  # a pairing starts on
    # the paired device itself, over both doors, cannot change it (its own flag or anyone's)
    r = client.patch(f"/api/auth/tokens/{paired_id}", json={"approver": False}, headers=_h(tok["paired"]))
    assert r.status_code == 403 and r.json()["detail"] == REFUSED
    mine = _ws(client, tok["paired"], {"type": "config/token/update", "token_id": paired_id, "approver": False})
    assert _refused_ws(mine)
    assert box.data[DATA_AUTH].is_approver(paired_id)  # nothing moved
    # a console token and the environment token can, over REST and the socket
    for who in ("console", "env"):
        off = client.patch(f"/api/auth/tokens/{paired_id}", json={"approver": False}, headers=_h(tok[who]))
        assert off.status_code == 200 and off.json()["approver"] is False, who
        assert not box.data[DATA_AUTH].is_approver(paired_id)
        listed = {r["id"]: r for r in client.get("/api/auth/tokens", headers=_h(tok[who])).json()}
        assert listed[paired_id]["approver"] is False
        back = _ws(client, tok[who], {"type": "config/token/update", "token_id": paired_id, "approver": True})
        assert back["success"] and back["result"]["approver"] is True
        assert box.data[DATA_AUTH].is_approver(paired_id)
    # the socket's own list shows it too
    listed = _ws(client, tok["console"], {"type": "config/token/list"})["result"]
    assert {r["id"]: r["approver"] for r in listed}[paired_id] is True


def _current_ids(rows):
    return [r["id"] for r in rows if r["current"]]


def test_token_list_marks_exactly_the_callers_own_token_over_rest_and_the_socket(rig):
    """T-1407: the console withholds revoke from its own key alone, so the house says which that is."""
    box, client, tok = rig
    mine = next(i.id for i in box.data[DATA_AUTH].list_tokens() if i.name == "console")
    other = asyncio.run(box.data[DATA_AUTH].create_token("second console"))
    # the second console key is connected and not paired: the case the console used to guess at
    for door in ("rest", "ws"):
        for who, expect in ((tok["console"], mine), (other[1], other[0].id)):
            rows = (
                client.get("/api/auth/tokens", headers=_h(who)).json()
                if door == "rest"
                else _ws(client, who, {"type": "config/token/list"})["result"]
            )
            assert all(type(r["current"]) is bool for r in rows), (door, rows)
            assert _current_ids(rows) == [expect], (door, rows)
    # the environment token is not a listed row, so no row is the caller's
    rest_env = client.get("/api/auth/tokens", headers=_h(tok["env"])).json()
    ws_env = _ws(client, tok["env"], {"type": "config/token/list"})["result"]
    assert _current_ids(rest_env) == [] and _current_ids(ws_env) == []


def test_rest_token_list_has_the_same_fields_as_the_sockets_and_says_what_is_connected(rig):
    """T-1413: one helper builds both lists, so a field added to one cannot be missing from the other."""
    box, client, tok = rig
    mine = next(i.id for i in box.data[DATA_AUTH].list_tokens() if i.name == "console")
    with client.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": tok["console"]})
        assert ws.receive_json()["type"] == "auth_ok"
        # asked while the console's socket is open: its key is the one a socket holds
        rest = client.get("/api/auth/tokens", headers=_h(tok["console"])).json()
        ws.send_json({"id": 1, "type": "config/token/list"})
        sock = ws.receive_json()["result"]
    assert rest == sock
    assert [r["id"] for r in rest if r["connected"]] == [mine]


def test_token_list_never_carries_token_text_whoever_asks_and_whichever_is_current(rig):
    box, client, tok = rig
    secrets = {tok["console"], tok["paired"], tok["env"]}
    digests = {i.token_hash for i in box.data[DATA_AUTH].list_tokens()}
    for who in (tok["console"], tok["env"]):  # a paired token is refused the list outright
        for rows in (
            client.get("/api/auth/tokens", headers=_h(who)).json(),
            _ws(client, who, {"type": "config/token/list"})["result"],
        ):
            text = repr(rows)
            assert not any(s in text for s in secrets | digests), who
            assert all(set(r) <= {
                "id", "name", "created_at", "last_used_at", "paired", "approver", "connected", "current"
            } for r in rows)


@pytest.mark.parametrize("body", [{}, {"approver": "false"}, {"approver": 0}, {"approver": None}])
def test_approver_flag_takes_a_real_boolean_only_and_a_known_token(rig, body):
    box, client, tok = rig
    r = client.patch(f"/api/auth/tokens/{tok['paired_id']}", json=body, headers=_h(tok["console"]))
    assert r.status_code == 400 and box.data[DATA_AUTH].is_approver(tok["paired_id"])
    gone = client.patch("/api/auth/tokens/nope", json={"approver": False}, headers=_h(tok["console"]))
    assert gone.status_code == 404


def test_paired_token_cannot_mint_a_token_so_the_walk_around_is_closed(rig):
    """A token minted by a console token is not paired; a paired token can mint nothing."""
    box, client, tok = rig
    r = client.post("/api/auth/tokens", json={"name": "x"}, headers=_h(tok["paired"]))
    assert r.status_code == 403
    minted = client.post("/api/auth/tokens", json={"name": "ordinary"}, headers=_h(tok["console"]))
    assert minted.status_code == 200 and minted.json()["paired"] is False
    # the ordinary token, minted by a caller that was not paired, is not limited
    again = client.get("/api/states", headers=_h(minted.json()["access_token"]))
    assert again.status_code == 200


# What a paired token still does: the device channel, the assistant turn and the
# device's own REST reads. Not refused is all this asserts; the handlers' own tests
# (device channel, pairing, voice) cover what they answer.
def test_paired_token_still_reaches_what_the_clients_use_on_the_websocket(rig):
    _box, client, tok = rig
    for frame in (
        {"type": "ping"},
        {"type": "jarvis/tasks/list"},
        {"type": "jarvis/notes/list"},
        {"type": "jarvis/notifications/list"},
        {"type": "jarvis/surface/list"},
        {"type": "assist_pipeline/pipeline/list"},
        {"type": "subscribe_events", "event_type": "jarvis_tool_started"},
    ):
        answer = _ws(client, tok["paired"], frame)
        assert answer.get("success") is not False or answer["error"]["code"] != REFUSED_CODE, frame


def test_paired_token_still_reaches_what_the_clients_use_over_rest(rig):
    _box, client, tok = rig
    for method, path in (("GET", "/api/notes"), ("GET", "/api/voice/speaker"),
                         ("GET", "/api/maps/regions"), ("GET", "/api/models/nope.bin"),
                         ("GET", "/api/notes/nope")):
        r = client.request(method, path, headers=_h(tok["paired"]))
        assert r.status_code != 403, (path, r.text)


def test_paired_token_refusal_is_the_same_for_a_route_that_does_not_exist(rig):
    _box, client, tok = rig
    r = client.get("/api/no/such/route", headers=_h(tok["paired"]))
    assert r.status_code in (403, 404)
    assert r.status_code != 200


_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options"})


def _served(app) -> set[tuple[str, str]]:
    """Every (METHOD, path) the app serves, read from its OpenAPI document.

    Not a walk of `app.routes`: fastapi 0.163 keeps an included router as one nested entry
    there, so the walk saw no `/api` route at all. The allowlist test went red on it and the
    client-call scan below passed for nothing (T-1459). This does not cover a route kept out
    of the schema (the webhook's HEAD probe) or the WebSocket.
    """
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method in _HTTP_METHODS
    }


def test_paired_token_allowlist_names_only_things_that_exist(rig):
    """So the tables cannot rot: a renamed command or route fails here, not silently."""
    box, client, _tok = rig
    assert set(WS_COMMANDS) <= set(WebSocketHandler._HANDLERS)
    assert set(REST_ROUTES) <= _served(client.app)
    assert all(WS_COMMANDS.values()) and all(REST_ROUTES.values())


async def test_paired_token_context_is_refused_an_owner_only_service_by_the_registry(tmp_path):
    """Second layer: a context carried into the registry (an automation a paired token ran)."""
    box = Jarvis(tmp_path / "config")
    auth = box.data[DATA_AUTH] = AuthManager()
    info, _secret = await auth.create_token("Pixel", paired=True)
    other, _ = await auth.create_token("console")
    seen = []

    async def _ok(call):
        seen.append(call)

    box.services.register("acct", "write", _ok, owner_only=True)
    box.services.register("acct", "read", _ok)
    with pytest.raises(PermissionError):
        await box.async_call_service("acct", "write", {}, context=Context(user_id=info.id))
    # the neighbours: an ordinary token, no identity, and an ungated service all run
    await box.async_call_service("acct", "write", {}, context=Context(user_id=other.id))
    await box.async_call_service("acct", "write", {}, context=None)
    await box.async_call_service("acct", "write", {}, context=Context(user_id=None))
    await box.async_call_service("acct", "read", {}, context=Context(user_id=info.id))
    assert len(seen) == 4


# --- the contract with the clients ------------------------------------------------------
# The Python half of a shared table: the clients' own source is the other side. A command or
# a route a client sends that is not on the allowlist is a feature that works for the owner's
# console and silently fails on a paired phone; an allowlist row no client sends is reach
# nobody uses. Either way this fails, naming the row (T-1378).
_ROOT = Path(__file__).resolve().parents[2]
_CLIENT_DIRS = (_ROOT / "android-app/app/src/main/kotlin", _ROOT / "aura-desktop/aura_desktop")
#: Routes that share a literal prefix with a call a client does make, but are not the call: the
#: console's own writes (edit or delete a note, add or drop a map region) and the API root. The
#: scan matches on prefix, not verb, so a NEW route under a client's prefix fails until someone
#: decides here, on purpose, that no client sends it.
_NOT_CORE_CALLS: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/api/"),
    ("PUT", "/api/notes/{note_id}"),
    ("DELETE", "/api/notes/{note_id}"),
    ("POST", "/api/notes/{note_id}/append"),
    ("POST", "/api/maps/regions"),
    ("DELETE", "/api/maps/regions/{region}"),
})


def _client_source() -> str:
    parts = []
    for base in _CLIENT_DIRS:
        for path in sorted([*base.rglob("*.kt"), *base.rglob("*.py")]):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _literal_prefix(template: str) -> str:
    return template.split("{", 1)[0].rstrip("/")


def test_every_command_a_client_sends_is_on_the_paired_allowlist(rig):
    source = _client_source()
    assert source, "the client sources were not found: the contract scan would pass for nothing"
    sent = {name for name in WebSocketHandler._HANDLERS if f'"{name}"' in source}
    assert sent - set(WS_COMMANDS) == set(), "a client sends a command a paired token is refused"
    assert set(WS_COMMANDS) - sent == set(), "an allowlist row no client sends"


def test_every_core_route_a_client_calls_is_on_the_paired_allowlist(rig):
    box, client, _tok = rig
    source = _client_source()
    listed = {(m, p) for (m, p) in REST_ROUTES}
    missing = set()
    scanned = 0
    for method, path in _served(client.app):
        if not path.startswith("/api/") or path == "/api/websocket":
            continue
        scanned += 1
        prefix = _literal_prefix(path)
        if (method, path) not in listed and (method, path) not in _NOT_CORE_CALLS and (
            f'"{prefix}"' in source or f'"{prefix}/' in source or f'"{prefix}?' in source
        ):
            missing.add((method, path))
    # An empty route table is a pass for nothing, which is how this scan once went quiet.
    assert scanned > len(listed), "the scan saw too few routes to mean anything"
    assert missing == set(), sorted(missing)


def test_every_paired_route_row_is_called_by_a_client(rig):
    source = _client_source()
    dead = [key for key in REST_ROUTES if _literal_prefix(key[1]) not in source]
    assert dead == []


#: The two feeds the phone watches outside the activity strip: TaskBoard.EVENTS (the added and
#: updated ones are the strip's too) and SurfaceWatch.EVENT. Not in the contract table because
#: they are not rows; the scan below finds each in the client source.
_FEED_EVENTS = frozenset({"jarvis_task_removed", "jarvis_surface_changed"})


def test_a_paired_socket_hears_exactly_the_events_a_client_subscribes_to(rig):
    """The event table is the strip's vocabulary (`tests/contracts/activity_rows.json`, which
    the Android and console mirror tests pin to their own code) plus the two feeds, and every
    row is a literal in a client source. The desktop agent subscribes to none, so a row can be
    here only for the phone. A new event for a paired socket is a decision made in two places."""
    strip = set(json.loads((_ROOT / "tests/contracts/activity_rows.json").read_text())["events"])
    assert set(WS_EVENTS) == strip | _FEED_EVENTS
    source = _client_source()
    assert source, "the client sources were not found: the scan would pass for nothing"
    assert [e for e in WS_EVENTS if f'"{e}"' not in source] == []
