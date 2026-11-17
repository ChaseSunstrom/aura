"""A paired device's token cannot call the house's account services (T-1377).

The decision is asserted at both doors, REST and the WebSocket, against the real
`oauth` registration: a token the pairing flow minted is refused on every account
service but `status`; the environment token, a console-minted token and a call with
no caller at all (an automation) still pass. The refusal says nothing about why.
"""

import asyncio
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from aura.api.server import create_app  # noqa: E402
from aura.auth import DATA_AUTH, ENV_TOKEN, AuthManager  # noqa: E402
from aura.bus import Context  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import oauth as oauth_integration  # noqa: E402

RESTRICTED = ("start", "revoke", "read", "test", "write")
BODY = {"provider": "google", "what": "x", "action": "add_task"}
# The paired door (T-1378) answers first on both doors; the service check in `async_call_service`
# is the second layer, tested below by calling it with a paired Context directly.
REFUSED = {"code": "forbidden", "message": "this token may not do this"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    box = Jarvis(tmp_path / "config")
    auth = box.data[DATA_AUTH] = AuthManager()
    secrets = {
        "paired": asyncio.run(auth.create_token("Pixel", paired=True))[1],
        "console": asyncio.run(auth.create_token("console"))[1],
    }
    cfg = {"redirect_base": "https://house.example.test",
           "google": {"client_id": "g", "client_secret": "s"}}
    assert asyncio.run(oauth_integration.async_setup(box, copy.deepcopy(cfg)))
    with TestClient(create_app(box, static_dir=tmp_path / "no-www")) as client:
        yield box, client, secrets


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _ws_call(client, token, service):
    with client.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": token})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"id": 1, "type": "call_service", "domain": "oauth", "service": service,
                      "service_data": dict(BODY), "return_response": True})
        return ws.receive_json()


@pytest.mark.parametrize("service", RESTRICTED)
def test_restricted_service_refuses_a_paired_token_over_rest(rig, service):
    _box, client, tok = rig
    r = client.post(f"/api/services/oauth/{service}", json=BODY, headers=_h(tok["paired"]))
    assert r.status_code == 403 and r.json()["detail"] == REFUSED
    # the control: the same call from a console-minted token is not refused
    ok = client.post(f"/api/services/oauth/{service}", json=BODY, headers=_h(tok["console"]))
    assert ok.status_code != 403


@pytest.mark.parametrize("service", RESTRICTED)
def test_restricted_service_refuses_a_paired_token_on_the_websocket(rig, service):
    _box, client, tok = rig
    frame = _ws_call(client, tok["paired"], service)
    assert frame["success"] is False and frame["error"]["code"] == "forbidden"
    assert frame["error"] == REFUSED
    ok = _ws_call(client, tok["console"], service)
    assert ok["success"] or ok["error"]["code"] != "forbidden"


async def test_paired_token_still_reads_status_in_the_service_layer(rig):
    """The door refuses a paired token the generic route; the service layer alone lets `status` by."""
    from aura.api import common
    box, client, tok = rig
    r = client.post("/api/services/oauth/status", json={}, headers=_h(tok["paired"]))
    assert r.status_code == 403 and r.json()["detail"] == REFUSED
    paired_id = box.data[DATA_AUTH].verify(tok["paired"]).id
    await common.async_call_service(box, "oauth", "status", {}, context=Context(user_id=paired_id))
    with pytest.raises(common.ApiError):
        await common.async_call_service(box, "oauth", "read", dict(BODY), context=Context(user_id=paired_id))


def test_restricted_service_lets_the_environment_token_through(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_TOKEN, "house-env-token")
    box = Jarvis(tmp_path / "config")
    box.data[DATA_AUTH] = AuthManager()
    assert asyncio.run(oauth_integration.async_setup(box, {}))
    with TestClient(create_app(box, static_dir=tmp_path / "no-www")) as client:
        for service in RESTRICTED:
            r = client.post(f"/api/services/oauth/{service}", json=BODY,
                            headers=_h("house-env-token"))
            assert r.status_code != 403
        ok = _ws_call(client, "house-env-token", "test")
        assert ok["success"] or ok["error"]["code"] != "forbidden"


# --- gated domains and services (T-1378) ------------------------------------------------
# lock and notify are whole gated domains; code.run and calendar.create_event are single
# gated services. The same fixed answer, on both doors; the neighbours stay open.
GATED_CALLS = [("lock", "unlock"), ("notify", "send"), ("code", "run"),
               ("calendar", "create_event"), ("Lock", "Unlock"), ("Calendar", "Create_Event")]


def _register(box, calls):
    async def _ok(call):
        return None

    for domain, service in calls:
        box.services.register(domain, service, _ok)


def _ws_gated(client, token, domain, service):
    with client.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": token})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"id": 1, "type": "call_service", "domain": domain, "service": service,
                      "service_data": {}})
        return ws.receive_json()


@pytest.mark.parametrize("domain,service", GATED_CALLS)
def test_gated_service_refuses_a_paired_token_over_rest(rig, domain, service):
    box, client, tok = rig
    _register(box, [("lock", "unlock"), ("notify", "send"), ("code", "run"),
                    ("calendar", "create_event")])
    r = client.post(f"/api/services/{domain}/{service}", json={}, headers=_h(tok["paired"]))
    assert r.status_code == 403 and r.json()["detail"] == REFUSED
    # the control: a console-minted token is not refused
    ok = client.post(f"/api/services/{domain}/{service}", json={}, headers=_h(tok["console"]))
    assert ok.status_code != 403


@pytest.mark.parametrize("domain,service", GATED_CALLS)
def test_gated_service_refuses_a_paired_token_on_the_websocket(rig, domain, service):
    box, client, tok = rig
    _register(box, [("lock", "unlock"), ("notify", "send"), ("code", "run"),
                    ("calendar", "create_event")])
    frame = _ws_gated(client, tok["paired"], domain, service)
    assert frame["success"] is False and frame["error"]["code"] == "forbidden"
    assert frame["error"]["message"] == REFUSED["message"]
    ok = _ws_gated(client, tok["console"], domain, service)
    assert ok["success"] or ok["error"]["code"] != "forbidden"


async def test_gated_service_neighbours_stay_open_in_the_service_layer(rig):
    """code.result is the ungated neighbour of code.run; light.turn_on is no gated domain.

    Asserted on the second layer (`async_call_service` with a paired Context): the door itself
    refuses a paired token the whole generic route.
    """
    from aura.api import common
    box, _client, tok = rig
    _register(box, [("code", "result"), ("light", "turn_on"), ("lock", "unlock"), ("code", "run")])
    ctx = Context(user_id=box.data[DATA_AUTH].verify(tok["paired"]).id)
    for domain, service in (("code", "result"), ("light", "turn_on")):
        await common.async_call_service(box, domain, service, {}, context=ctx)
    for domain, service in (("lock", "unlock"), ("code", "run"), ("Code", "Run")):
        with pytest.raises(common.ApiError):
            await common.async_call_service(box, domain, service, {}, context=ctx)


def test_gated_service_lets_the_environment_token_through_for_paired_checks(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_TOKEN, "house-env-token")
    box = Jarvis(tmp_path / "config")
    box.data[DATA_AUTH] = AuthManager()
    _register(box, [("lock", "unlock"), ("code", "run")])
    with TestClient(create_app(box, static_dir=tmp_path / "no-www")) as client:
        for path in ("lock/unlock", "code/run"):
            r = client.post(f"/api/services/{path}", json={}, headers=_h("house-env-token"))
            assert r.status_code != 403


async def test_gated_service_still_runs_for_a_caller_with_no_identity_not_paired(tmp_path):
    """An automation, a scene or any in-house call carries no token id: lock and notify run."""
    box = Jarvis(tmp_path / "config")
    box.data[DATA_AUTH] = AuthManager()
    seen = []

    async def _ok(call):
        seen.append(call)

    for domain, service in (("lock", "unlock"), ("notify", "send"), ("code", "run")):
        box.services.register(domain, service, _ok)
    from aura.api import common
    for ctx in (None, Context(), Context(user_id=None, origin="automation")):
        for domain, service in (("lock", "unlock"), ("notify", "send"), ("code", "run")):
            await common.async_call_service(box, domain, service, {}, context=ctx)
    assert len(seen) == 9


def test_restricted_service_flag_is_set_for_every_account_service_but_status(rig):
    box, _client, _tok = rig
    flags = {n: s.owner_only for n, s in box.services.services["oauth"].items()}
    assert flags == {**{n: True for n in RESTRICTED}, "status": False}


async def test_restricted_service_still_runs_for_a_caller_with_no_identity(tmp_path):
    """An automation or any in-house call builds no token id: it must keep working."""
    box = Jarvis(tmp_path / "config")
    box.data[DATA_AUTH] = AuthManager()
    assert await oauth_integration.async_setup(box, {})
    for ctx in (None, Context(), Context(user_id=None, origin="automation")):
        out = await box.async_call_service("oauth", "test", {"provider": "google"}, context=ctx,
                                           return_response=True)
        assert out is not None
    from aura.api import common
    res = await common.async_call_service(box, "oauth", "test", {"provider": "google"},
                                          context=Context(user_id=None))
    assert res is not None
