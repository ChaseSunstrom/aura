"""Sensitive approvals need the phone's signature (T-0945).

The approval gate puts a sensitive call to the adapter's desk (``POST /api/approval/proof``),
which shows it on every app and says "once" only for an ``approval.respond`` signed by a key a
paired phone registered. These tests use a real P-256 key, as the phone's Keystore makes.

Not covered here: the phone's fingerprint prompt (Kotlin) and which rows are sensitive (the
matrix's own tests).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from aura_adapter import approval_proof, device_keys
from aura_adapter.app import create_app
from aura_adapter.config import Settings
from aura_adapter.server_requests import RequestBridge

MASTER = "master-key-0123456789abcdef"
RULE = "house:service-call:sensitive:" + "ab" * 16
KEY = "plugin_rule:" + RULE


class Phone:
    """What the phone's Keystore key does, without the fingerprint in front of it."""

    def __init__(self) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = base64.b64encode(self.private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()

    def sign(self, challenge: str) -> str:
        return base64.b64encode(self.private.sign(challenge.encode(), ec.ECDSA(hashes.SHA256()))).decode()


def _respond(app_id: int, **params) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": app_id, "method": "approval.respond",
                       "params": {"session_id": "s1", **params}})


class House:
    """A desk, the frames it showed, and one app's relay bridge."""

    def __init__(self, *phones: Phone) -> None:
        self.keys = [p.public for p in phones]
        self.shown: list[dict] = []
        self.desk = approval_proof.Desk(lambda: self.keys, lambda frame: self.shown.append(json.loads(frame)["params"]))
        self.bridge = RequestBridge(self.desk)

    async def ask(self, wait_s: float = 5.0) -> tuple[asyncio.Task, dict]:
        task = asyncio.ensure_future(self.desk.ask("s1", "ha_call_service", "Unlock the front door?", RULE, wait_s))
        await asyncio.sleep(0)
        return task, self.shown[-1]["payload"]

    def answer(self, **params) -> dict:
        to_up, to_app = self.bridge.from_app(_respond(7, **params))
        assert to_up == []  # an answer to the desk's ask never reaches Hermes
        return json.loads(to_app[0])


def test_a_sensitive_ask_is_shown_as_an_approval_to_confirm_on_the_phone():
    async def go():
        house = House(Phone())
        task, payload = await house.ask()
        # The house's ask, not one conversation's: an app drops events for a session it is not showing.
        assert house.shown[-1]["type"] == "approval.request" and "session_id" not in house.shown[-1]
        assert payload["confirm"] == "phone" and payload["challenge"].startswith(f"{payload['request_id']}\n{KEY}\n")
        assert payload["choices"] == ["once", "deny"] and payload["options"] == ["once", "deny"]
        assert payload["command"] == "<ha_call_service> (plugin approval rule)" and payload["pattern_key"] == KEY
        assert house.desk.shown() == [json.dumps(house.shown[-1] and {"jsonrpc": "2.0", "method": "event", "params": house.shown[-1]}, separators=(",", ":"))]
        task.cancel()
    asyncio.run(go())


def test_the_phones_signature_is_a_yes_and_every_app_takes_the_card_down():
    async def go():
        phone = Phone()
        house = House(phone)
        task, payload = await house.ask()
        assert house.answer(request_id=payload["request_id"], choice="once", signature=phone.sign(payload["challenge"])) == {
            "jsonrpc": "2.0", "id": 7, "result": {"ok": True}}
        assert await task == "once"
        assert house.shown[-1]["type"] == "approval.resolved"
        assert house.shown[-1]["payload"] == {"request_id": payload["request_id"], "choice": "once"}
        assert house.desk.shown() == [] and not house.desk.holds(payload["request_id"])
    asyncio.run(go())


def test_an_allow_without_a_signature_is_refused_and_the_ask_stays_open():
    async def go():
        phone = Phone()
        house = House(phone)
        task, payload = await house.ask()
        rid = payload["request_id"]
        assert house.answer(request_id=rid, choice="once")["error"]["message"] == approval_proof.REFUSED
        assert not task.done() and house.desk.holds(rid)
        # Still open: the phone can answer it properly afterwards.
        house.answer(request_id=rid, choice="once", signature=phone.sign(payload["challenge"]))
        assert await task == "once"
    asyncio.run(go())


@pytest.mark.parametrize("choice", ["session", "always"])
def test_a_signed_allow_is_never_remembered(choice):
    async def go():
        phone = Phone()
        house = House(phone)
        task, payload = await house.ask()
        house.answer(request_id=payload["request_id"], choice=choice, signature=phone.sign(payload["challenge"]))
        assert await task == "once"
    asyncio.run(go())


def test_a_signature_from_another_key_or_for_another_ask_is_refused():
    async def go():
        phone, thief = Phone(), Phone()
        house = House(phone)
        task, payload = await house.ask()
        rid, signed = payload["request_id"], payload["challenge"]
        other, payload2 = await house.ask()
        for bad in (thief.sign(signed), phone.sign(payload2["challenge"]), phone.sign(signed[:-1] + "0"),
                    "", "not base64 !", base64.b64encode(b"junk").decode(), 7, None, "A" * 5000):
            assert "error" in house.answer(request_id=rid, choice="once", signature=bad), bad
        assert not task.done()
        task.cancel(), other.cancel()
    asyncio.run(go())


def test_a_revoked_phones_signature_stops_counting_at_once():
    async def go():
        phone = Phone()
        house = House(phone)
        task, payload = await house.ask()
        house.keys.clear()
        assert "error" in house.answer(request_id=payload["request_id"], choice="once", signature=phone.sign(payload["challenge"]))
        task.cancel()
    asyncio.run(go())


def test_deny_needs_no_signature_and_no_answer_is_a_timeout():
    async def go():
        house = House(Phone())
        task, payload = await house.ask()
        assert house.answer(request_id=payload["request_id"], choice="deny") == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}
        assert await task == "deny"
        task, payload = await house.ask(wait_s=1.0)
        assert await task == "timeout" and house.shown[-1]["payload"]["choice"] == "timeout"
        assert not house.desk.holds(payload["request_id"])
    asyncio.run(go())


def test_an_answer_to_anything_else_is_not_the_desks():
    async def go():
        house = House(Phone())
        task, _ = await house.ask()
        # Hermes' own approvals and unknown ids go the way they always went.
        assert house.bridge.from_app(_respond(7, request_id="q-1", choice="always")) is None
        assert house.bridge.from_app(_respond(7, all=True, choice="once")) is None
        assert not task.done()
        task.cancel()
    asyncio.run(go())


def test_the_desk_holds_only_so_many_and_takes_only_a_marked_key():
    async def go():
        house = House(Phone())
        tasks = [(await house.ask())[0] for _ in range(approval_proof.MAX_OPEN)]
        assert await house.desk.ask("s1", "terminal", "m", RULE) == "deny"
        for t in tasks:
            t.cancel()
        await asyncio.sleep(0)
        with pytest.raises(ValueError):
            await house.desk.ask("s1", "terminal", "m", "house:service-call")
    asyncio.run(go())


def test_the_field_names_are_the_shared_contract():
    spec = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/approval-event.json").read_text())["phone_confirmation"]

    async def go():
        house = House(Phone())
        task, payload = await house.ask()
        assert payload[spec["confirm_field"]] == spec["confirm_value"] and payload["choices"] == spec["choices"]
        assert payload[spec["challenge_field"]] == spec["challenge_format"].format(
            request_id=payload["request_id"], pattern_key=KEY,
            shown=hashlib.sha256(json.dumps([payload["command"], payload["description"]]).encode()).hexdigest())
        task.cancel()
    asyncio.run(go())
    assert approval_proof._MARK.pattern == spec["mark_pattern"]


def test_a_mark_forged_in_the_middle_of_a_key_is_not_a_mark():
    assert approval_proof.challenge({"request_id": "q", "pattern_key": "plugin_rule:x:sensitive:zz"}) is None
    assert approval_proof.challenge({"request_id": "q", "pattern_key": KEY + ":at-home"}) is None
    assert approval_proof.challenge({"request_id": "q", "pattern_key": KEY}).startswith(f"q\n{KEY}\n")
    assert approval_proof.challenge({"request_id": "q"}) is None


# --- the phone's key, on its own device record ----------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(device_keys, "ROOT", tmp_path)
    return TestClient(create_app(Settings(api_key=MASTER, hermes_api_url="http://127.0.0.1:9",
                                          hermes_serve_url="http://127.0.0.1:9")))


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _pair(client: TestClient, name: str) -> dict:
    return client.post("/api/pair", json={"name": name, "kind": "phone"}, headers=_auth(MASTER)).json()


def test_a_paired_phone_registers_its_key_once_and_it_goes_when_the_phone_is_revoked(client: TestClient):
    phone, device = Phone(), _pair(client, "Pixel")
    assert device_keys.approval_keys() == []
    r = client.post("/api/approval/key", json={"public_key": phone.public}, headers=_auth(device["key"]))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert device_keys.approval_keys() == [phone.public]
    # The same key again is the phone starting up; a different one is not the phone.
    assert client.post("/api/approval/key", json={"public_key": phone.public}, headers=_auth(device["key"])).status_code == 200
    other = client.post("/api/approval/key", json={"public_key": Phone().public}, headers=_auth(device["key"]))
    assert other.status_code == 409 and device_keys.approval_keys() == [phone.public]
    # It is never listed back.
    assert "approval" not in json.dumps(client.get("/api/devices/keys", headers=_auth(MASTER)).json())
    client.delete(f"/api/devices/keys/{device['device']['id']}", headers=_auth(MASTER))
    assert device_keys.approval_keys() == []


def test_a_second_phone_is_not_a_second_signer_until_the_first_is_revoked(client: TestClient):
    """A pairing made later (or a leaked phone key) must not add a signer beside the owner's (T-1030)."""
    first, mine = Phone(), _pair(client, "Pixel")
    second, theirs = Phone(), _pair(client, "Another")
    assert client.post("/api/approval/key", json={"public_key": first.public}, headers=_auth(mine["key"])).status_code == 200
    r = client.post("/api/approval/key", json={"public_key": second.public}, headers=_auth(theirs["key"]))
    spec = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/approval-event.json").read_text())
    assert r.status_code == spec["phone_confirmation"]["register"]["other_phone_status"] == 423
    assert "already signs" in r.json()["detail"]
    assert device_keys.approval_keys() == [first.public]
    # The owner's consent is ending the old phone: then the new one is the signer.
    client.delete(f"/api/devices/keys/{mine['device']['id']}", headers=_auth(MASTER))
    assert client.post("/api/approval/key", json={"public_key": second.public}, headers=_auth(theirs["key"])).status_code == 200
    assert device_keys.approval_keys() == [second.public]


def test_two_keys_from_before_the_rule_both_start_up_and_a_desktop_is_not_a_signer(client: TestClient):
    """The phone posts its key on every fresh install of its prefs: the same key is never refused."""
    first, mine = Phone(), _pair(client, "Pixel")
    second, theirs = Phone(), _pair(client, "Another")
    client.post("/api/approval/key", json={"public_key": first.public}, headers=_auth(mine["key"]))
    with device_keys._locked() as keys:  # as a table written before T-1030 could hold
        keys[theirs["device"]["id"]]["approval_key"] = second.public
    for phone, device in ((first, mine), (second, theirs)):
        r = client.post("/api/approval/key", json={"public_key": phone.public}, headers=_auth(device["key"]))
        assert r.status_code == 200
    assert sorted(device_keys.approval_keys()) == sorted([first.public, second.public])


def test_the_challenge_changes_with_the_tool_and_with_the_text_shown():
    """The signature is over what the owner read, not only the ask's id and rule (T-1030)."""
    ask = {"request_id": "proof-1", "pattern_key": KEY, "command": "<ha_call_service> (plugin approval rule)",
           "description": "Unlock the front door?"}
    signed = approval_proof.challenge(ask)
    assert signed is not None and signed.startswith(f"proof-1\n{KEY}\n")
    assert approval_proof.challenge({**ask, "description": "Unlock the back door?"}) != signed
    assert approval_proof.challenge({**ask, "command": "<desktop_run> (plugin approval rule)"}) != signed
    assert approval_proof.challenge(dict(ask)) == signed
    # A newline cannot be moved from one field to the other to say something else under one signature.
    assert approval_proof.challenge({**ask, "command": "a\nb", "description": "c"}) != \
        approval_proof.challenge({**ask, "command": "a", "description": "b\nc"})


def test_the_gate_asks_with_the_master_key_and_is_told_when_no_phone_can_sign(client: TestClient):
    body = {"session_id": "s1", "tool": "ha_call_service", "message": "Unlock?", "rule_key": RULE, "wait_s": 1}
    assert client.post("/api/approval/proof", json=body, headers=_auth(MASTER)).json() == {"choice": "unavailable"}
    phone, device = Phone(), _pair(client, "Pixel")
    # A device key may not put asks on every screen.
    assert client.post("/api/approval/proof", json=body, headers=_auth(device["key"])).status_code == 401
    client.post("/api/approval/key", json={"public_key": phone.public}, headers=_auth(device["key"]))
    assert client.post("/api/approval/proof", json=body, headers=_auth(MASTER)).json() == {"choice": "timeout"}
    assert client.post("/api/approval/proof", json={**body, "rule_key": "house:service-call"},
                       headers=_auth(MASTER)).status_code == 400


def test_a_desktops_key_is_not_behind_a_fingerprint(client: TestClient):
    desktop = client.post("/api/pair", json={"name": "Tower", "kind": "desktop"}, headers=_auth(MASTER)).json()
    r = client.post("/api/approval/key", json={"public_key": Phone().public}, headers=_auth(desktop["key"]))
    assert r.status_code == 403 and device_keys.approval_keys() == []


def test_only_a_device_key_registers_and_only_a_real_p256_key(client: TestClient):
    phone, device = Phone(), _pair(client, "Pixel")
    # The master key names no device: there is no record to put a key on.
    assert client.post("/api/approval/key", json={"public_key": phone.public}, headers=_auth(MASTER)).status_code == 403
    assert client.post("/api/approval/key", json={"public_key": phone.public}).status_code == 401
    for bad in ("", "AAAA", 7, None, base64.b64encode(b"x" * 91).decode()):
        r = client.post("/api/approval/key", json={"public_key": bad}, headers=_auth(device["key"]))
        assert r.status_code == 400, bad
    assert device_keys.approval_keys() == []


def test_a_lost_or_damaged_key_file_is_a_refusal_not_a_tap(client: TestClient, tmp_path: Path):
    """Once a phone has registered, "no key readable" is a fault, not "no phone yet" (T-0945 review)."""
    body = {"session_id": "s1", "tool": "ha_call_service", "message": "Unlock?", "rule_key": RULE, "wait_s": 1}
    phone, device = Phone(), _pair(client, "Pixel")
    client.post("/api/approval/key", json={"public_key": phone.public}, headers=_auth(device["key"]))
    keys = tmp_path / device_keys.FILE
    whole = keys.read_text()
    for broken in ("{half a fi", None):
        keys.write_text(broken) if broken is not None else keys.unlink()
        # "error" is what the gate refuses on; "unavailable" would step down to a one-tap ask.
        assert client.post("/api/approval/proof", json=body, headers=_auth(MASTER)).json() == {"choice": "error"}
    # Revoking the last phone is the owner ending it: the ordinary ask is back.
    keys.write_text(whole)
    client.delete(f"/api/devices/keys/{device['device']['id']}", headers=_auth(MASTER))
    assert client.post("/api/approval/proof", json=body, headers=_auth(MASTER)).json() == {"choice": "unavailable"}

