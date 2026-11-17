"""Per-device adapter keys (T-0569): minted at pairing, listed, revoked one at a time.

A lost phone used to be locked out only by rotating AURA_API_KEY, which every other device
holds. These pin that a revoked device key is refused on every authenticated surface while the
other device and the master key keep working, that only the master key mints, lists or revokes,
and that the file never holds a key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aura_adapter import device_keys
from aura_adapter.app import create_app
from aura_adapter.config import Settings

MASTER = "master-key-0123456789abcdef"


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_000_000.0]
    monkeypatch.setattr(device_keys, "_clock", lambda: now[0])
    return now


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: list[float]) -> TestClient:
    monkeypatch.setattr(device_keys, "ROOT", tmp_path)
    # Nothing listens here: an admitted call fails upstream (502 or an error frame), a refused
    # one never gets that far (401 or a 4401 close).
    settings = Settings(api_key=MASTER, hermes_api_url="http://127.0.0.1:9",
                        hermes_serve_url="http://127.0.0.1:9")
    return TestClient(create_app(settings))


def pair(client: TestClient, name: str, kind: str = "phone") -> dict:
    r = client.post("/api/pair", json={"name": name, "kind": kind}, headers=auth(MASTER))
    assert r.status_code == 200, r.text
    return r.json()


def ws_close_code(client: TestClient, key: str) -> int | None:
    """The close code the socket ends with, or None when it was admitted (it then gets the
    adapter's own error frame, because nothing is upstream)."""
    with client.websocket_connect("/ws", subprotocols=[f"jarvis-key.{key}"]) as ws:
        try:
            ws.receive_text()
        except WebSocketDisconnect as closed:
            return closed.code
    return None


def test_a_revoked_device_is_refused_and_the_others_still_work(client: TestClient) -> None:
    a, b = pair(client, "Pixel"), pair(client, "Tablet")
    assert a["key"] != b["key"] and MASTER not in (a["key"], b["key"])
    r = client.delete(f"/api/devices/keys/{a['device']['id']}", headers=auth(MASTER))
    assert r.status_code == 200 and r.json() == {"ok": True, "id": a["device"]["id"]}

    assert client.get("/api/pair", headers=auth(a["key"])).status_code == 401
    assert client.get("/v1/models", headers=auth(a["key"])).status_code == 401
    assert ws_close_code(client, a["key"]) == 4401

    for key in (b["key"], MASTER):
        assert client.get("/api/pair", headers=auth(key)).status_code == 200
        assert client.get("/v1/models", headers=auth(key)).status_code == 502  # admitted, nothing upstream
        assert ws_close_code(client, key) is None


def test_minting_listing_and_revoking_need_the_master_key(client: TestClient, tmp_path: Path) -> None:
    assert client.post("/api/pair", json={"name": "x"}).status_code == 401
    assert not (tmp_path / device_keys.FILE).exists(), "a refused mint wrote nothing"
    device = pair(client, "Pixel")
    for method, path in (("post", "/api/pair"), ("get", "/api/devices/keys"),
                         ("delete", f"/api/devices/keys/{device['device']['id']}")):
        r = getattr(client, method)(path, headers=auth(device["key"]), **({"json": {"name": "y"}} if method == "post" else {}))
        assert r.status_code == 401, (method, path)
    assert [k["name"] for k in client.get("/api/devices/keys", headers=auth(MASTER)).json()["keys"]] == ["Pixel"]


def test_a_device_key_never_reads_the_master_key(client: TestClient) -> None:
    """GET /api/pair is the phone's probe for "is this the adapter"; with a device key it says
    so without the key, or any device could read the master key and outlive its revoke."""
    device = pair(client, "Pixel")
    probe = client.get("/api/pair", headers=auth(device["key"])).json()
    assert probe["kind"] == "hermes-adapter" and "key" not in probe and MASTER not in json.dumps(probe)
    assert client.get("/api/pair", headers=auth(MASTER)).json()["key"] == MASTER


def test_only_hashes_are_stored_and_a_key_is_shown_once(client: TestClient, tmp_path: Path) -> None:
    device = pair(client, "Pixel", "phone")
    stored = (tmp_path / device_keys.FILE).read_text()
    assert device["key"] not in stored and MASTER not in stored
    assert (tmp_path / device_keys.FILE).stat().st_mode & 0o077 == 0
    listed = client.get("/api/devices/keys", headers=auth(MASTER)).json()["keys"]
    assert listed == [{"id": device["device"]["id"], "name": "Pixel", "kind": "phone",
                       "created": 1_000_000.0, "last_seen": None, "house_token": None}]


def test_last_seen_is_written_at_most_once_a_minute_and_atomically(client: TestClient, clock: list[float],
                                                                   tmp_path: Path, monkeypatch) -> None:
    device = pair(client, "Pixel")
    writes = []
    real = device_keys._write
    monkeypatch.setattr(device_keys, "_write", lambda path, data: (writes.append(1), real(path, data)))
    for _ in range(3):
        assert client.get("/api/pair", headers=auth(device["key"])).status_code == 200
    clock[0] += 59
    client.get("/api/pair", headers=auth(device["key"]))
    assert len(writes) == 1
    clock[0] += 1
    client.get("/api/pair", headers=auth(device["key"]))
    assert len(writes) == 2
    listed = client.get("/api/devices/keys", headers=auth(MASTER)).json()["keys"]
    assert listed[0]["last_seen"] == 1_000_060.0
    assert sorted(p.name for p in tmp_path.iterdir()) == [device_keys.FILE, device_keys.FILE + ".lock"]


def test_a_revoke_racing_a_last_seen_write_is_kept(client: TestClient) -> None:
    """The check reads without the lock; the last-seen write re-reads inside it, so a revoke
    that landed in between is not undone by the write."""
    device = pair(client, "Pixel")
    ident = device_keys.check(device["key"], touch=False)
    assert ident == device["device"]["id"]
    assert device_keys.revoke(ident)
    device_keys.mark_seen(ident)
    assert device_keys.check(device["key"]) is None
    assert device_keys.list_keys() == []


@pytest.mark.parametrize("damage", ["{not json", '{"keys": []}', "[]"])
def test_a_damaged_file_admits_no_device_key_and_the_master_key_still_works(
        client: TestClient, tmp_path: Path, damage: str) -> None:
    device = pair(client, "Pixel")
    (tmp_path / device_keys.FILE).write_text(damage)
    assert client.get("/api/pair", headers=auth(device["key"])).status_code == 401
    assert client.get("/api/pair", headers=auth(MASTER)).status_code == 200


@pytest.mark.parametrize("damage", ["{not json", '{"keys": []}', "[]"])
def test_a_damaged_file_is_never_rewritten_by_a_pairing_or_a_revoke(
        client: TestClient, tmp_path: Path, damage: str) -> None:
    """Fail-closed is right for a check and wrong for a rewrite: writing back what a damaged
    file read as (nothing) would drop every other device's key with it."""
    kept = pair(client, "Pixel")
    (tmp_path / device_keys.FILE).write_text(damage)
    assert client.post("/api/pair", json={"name": "Tablet"}, headers=auth(MASTER)).status_code == 503
    assert client.delete(f"/api/devices/keys/{kept['device']['id']}", headers=auth(MASTER)).status_code == 503
    assert (tmp_path / device_keys.FILE).read_text() == damage


def test_a_revoke_between_the_key_check_and_the_hang_up_list_still_closes_the_socket(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A socket joins its device's hang-up list after its key is checked; a revoke landing in
    between found nothing to close, so the socket looks again once it is on the list."""
    device = pair(client, "Pixel")
    monkeypatch.setattr(device_keys, "describe", lambda ident: None)  # revoked just after the check
    assert ws_close_code(client, device["key"]) == 4401


def test_a_pairing_name_and_kind_are_bounded(client: TestClient) -> None:
    long = pair(client, "x" * 500, "toaster")
    assert len(long["device"]["name"]) == device_keys.MAX_NAME and long["device"]["kind"] == "other"
    assert pair(client, "  ")["device"]["name"] == "device"
    assert client.delete("/api/devices/keys/nope", headers=auth(MASTER)).status_code == 404


def test_a_device_key_says_which_device_it_is(client: TestClient) -> None:
    """The console asks this to admit a phone's page load with its own key, and tags the
    session it opens with the device, so a revoke can end it."""
    device = pair(client, "Pixel")
    probe = client.get("/api/pair", headers=auth(device["key"])).json()
    assert {k: probe["device"][k] for k in ("id", "name", "kind")} == {
        k: device["device"][k] for k in ("id", "name", "kind")}
    assert "device" not in client.get("/api/pair", headers=auth(MASTER)).json()


def test_the_house_token_id_is_kept_with_the_key_so_one_revoke_ends_both(client: TestClient) -> None:
    r = client.post("/api/pair", json={"name": "Pixel", "kind": "phone", "house_token": "tok_9f2c"},  # pragma: allowlist secret
                    headers=auth(MASTER))
    assert r.json()["device"]["house_token"] == "tok_9f2c"
    odd = pair(client, "Tablet") | {}
    r = client.post("/api/pair", json={"name": "x", "house_token": "../../etc"}, headers=auth(MASTER))  # pragma: allowlist secret
    assert r.json()["device"]["house_token"] is None and odd["device"]["house_token"] is None
    assert [k["house_token"] for k in client.get("/api/devices/keys", headers=auth(MASTER)).json()["keys"]] == [
        "tok_9f2c", None, None]


def test_the_kinds_are_the_pairing_contracts() -> None:
    """The console mints by the claim's kind; the adapter keeps the same list
    (tests/contracts/pairing-claim.json), or a kind one end knows would read as "other"."""
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/pairing-claim.json").read_text())
    assert list(device_keys.KINDS) == contract["kinds"]
