"""Emergency outreach, the house's trigger (T-1013): a smoke, carbon-monoxide or gas alarm turning on
starts the same ``emergency_alert`` command the assistant's tool sends the phone (T-0947,
tests/contracts/emergency.json), once per alarm.

Every sender is a fake: the phone is a ``Wire`` that records the frames put on its socket, the house's
notify service is a list. Nothing here texts, calls or reaches a network."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.api.devices import get_devices  # noqa: E402
from aura.api.server import create_app  # noqa: E402
from aura.auth import DATA_AUTH, ENV_TOKEN, AuthManager  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import emergency  # noqa: E402

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "emergency.json")
                      .read_text(encoding="utf-8"))
PROOF = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "emergency-proof.json")
                   .read_text(encoding="utf-8"))
SYNC = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "emergency-contacts-sync.json")
                  .read_text(encoding="utf-8"))
SECRET = PROOF["secret"]  # the contract's test vector, never a deployed value
NOW = 1700000000.0
CONTACTS = CONTRACT["examples"]["command"]["contacts"]
TWO = [*CONTACTS, {"name": "Ada", "number": "+15550101"}]
OK = CONTRACT["examples"]["answer_ok"]
MANIFEST = [{"id": "emergency_alert", "tier": 1, "description": "the owner's emergency alert",
             "params": {}, "capability": "device", "available": True}]
SMOKE = {"device_class": "smoke", "friendly_name": "Kitchen smoke"}


class Wire:
    """A phone's socket: records each device_command and answers it the way the phone would."""

    def __init__(self, answer=None) -> None:
        self.frames: list[dict] = []
        self.answer = {"status": "ok", "result": OK} if answer is None else answer
        self.link = None

    def sender(self, frame: dict) -> bool:
        self.frames.append(frame)
        if self.answer:
            asyncio.get_running_loop().call_soon(
                self.link.on_result, {"type": "device_result", "command_id": frame["command_id"], **self.answer})
        return True


async def house(tmp_path, options=None, *, phone=True, contacts=CONTACTS, answer=None):
    """A house with the integration up, a fake phone on the channel and a notify list."""
    j = Jarvis(tmp_path)
    notes: list[dict] = []

    async def notify(call):
        notes.append(dict(call.data))

    j.services.register("companion", "notify", notify)
    j.data["emergency_clock"] = lambda: j.data.get("now", NOW)
    await emergency.async_setup(j, {"enabled": True, "contacts_secret": SECRET, **(options or {})})
    wire = Wire(answer)
    if phone:
        wire.link = get_devices(j).register("phone", "Pixel", "android", [], MANIFEST, wire.sender, owner=wire)
    if contacts is not None:
        await put(j, contacts)
        notes.clear()  # the change notice has its own tests; the alarm tests count only the alarm's
    return j, wire, notes


def sign(contacts, issued, secret=SECRET):
    """The plugin's proof, made the way the contract says (and checked against its vector below)."""
    text = f"{PROOF['canonical_prefix']}\n{json.dumps(contacts, sort_keys=True, separators=(',', ':'))}\n{issued}"
    return hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest()


async def put(j, contacts, *, secret=SECRET, step=1):
    """Push ``contacts`` the way the plugin does, one second after the last thing the house saw."""
    j.data["now"] = j.data.get("now", NOW) + step
    issued = int(j.data["now"])
    return await emergency.set_contacts(j, contacts, issued, sign(contacts, issued, secret))


async def alarm(j, entity="binary_sensor.kitchen_smoke", attrs=SMOKE, *, was="off"):
    j.states.set(entity, was, attrs)
    j.states.set(entity, "on", attrs)
    await j.async_block_till_done()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_emergency_a_smoke_alarm_turning_on_sends_the_alert_command_to_the_phone(tmp_path):
    j, wire, notes = await house(tmp_path)
    await alarm(j)
    assert len(wire.frames) == 1
    frame = wire.frames[0]
    assert frame["action"] == "emergency_alert"
    assert frame["params"]["contacts"] == CONTACTS
    assert frame["params"]["countdown_s"] == CONTRACT["limits"]["countdown_s"]
    assert frame["params"]["what"] == "The smoke alarm Kitchen smoke is sounding."
    assert len(frame["params"]["what"]) <= CONTRACT["limits"]["what_max"]
    assert notes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("device_class", ["smoke", "carbon_monoxide", "gas"])
async def test_emergency_each_alarm_class_starts_the_alert(tmp_path, device_class):
    j, wire, _ = await house(tmp_path)
    await alarm(j, "binary_sensor.hall", {"device_class": device_class, "friendly_name": "Hall"})
    assert [f["action"] for f in wire.frames] == ["emergency_alert"]


@pytest.mark.asyncio
async def test_emergency_other_sensors_and_a_boot_state_do_not_start_it(tmp_path):
    j, wire, _ = await house(tmp_path)
    await alarm(j, "binary_sensor.front_door", {"device_class": "door"})
    await alarm(j, "binary_sensor.hall_motion", {"device_class": "motion"})
    await alarm(j, "sensor.smoke_level", {"device_class": "smoke"})
    j.states.set("binary_sensor.boot_smoke", "on", SMOKE)  # restored already on: no off -> on happened
    j.states.set("binary_sensor.gone_smoke", "unavailable", SMOKE)
    j.states.set("binary_sensor.gone_smoke", "on", SMOKE)
    await j.async_block_till_done()
    await asyncio.sleep(0.05)
    assert wire.frames == []


@pytest.mark.asyncio
async def test_emergency_one_alert_per_alarm_a_flapping_sensor_does_not_repeat_it(tmp_path):
    j, wire, _ = await house(tmp_path)
    await alarm(j)
    j.states.set("binary_sensor.kitchen_smoke", "on", {**SMOKE, "battery": 80})  # same alarm, new attribute
    await alarm(j)  # off -> on again inside the cool-down
    assert len(wire.frames) == 1
    j.data["now"] = NOW + emergency.COOLDOWN_S + 10  # a new alarm, long after
    await alarm(j)
    assert len(wire.frames) == 2


@pytest.mark.asyncio
async def test_emergency_a_cancelled_countdown_counts_as_the_alert_so_it_is_not_asked_again(tmp_path):
    j, wire, _ = await house(tmp_path, answer={"status": "ok", "result": {"status": "cancelled"}})
    await alarm(j)
    await alarm(j)
    assert len(wire.frames) == 1


@pytest.mark.asyncio
async def test_emergency_no_contacts_sends_nothing_and_says_so(tmp_path):
    j, wire, notes = await house(tmp_path, contacts=None)
    await alarm(j)
    assert wire.frames == []
    assert len(notes) == 1 and notes[0]["importance"] == "critical" and "nobody was told" in notes[0]["message"]


@pytest.mark.asyncio
async def test_emergency_no_phone_online_says_so_and_the_next_alarm_still_tries(tmp_path):
    j, wire, notes = await house(tmp_path, phone=False)
    await alarm(j)
    assert len(notes) == 1 and "nobody was told" in notes[0]["message"]
    wire.link = get_devices(j).register("phone", "Pixel", "android", [], MANIFEST, wire.sender, owner=wire)
    await alarm(j)
    assert len(wire.frames) == 1


@pytest.mark.asyncio
async def test_emergency_a_phone_that_failed_is_said_not_taken_for_a_sent_alert(tmp_path):
    j, wire, notes = await house(tmp_path, answer={"status": "error", "error": "no sms permission"})
    await alarm(j)
    assert len(wire.frames) == 1 and len(notes) == 1 and "nobody was told" in notes[0]["message"]


@pytest.mark.asyncio
async def test_emergency_the_setting_turns_it_off(tmp_path):
    j, wire, notes = await house(tmp_path, {"enabled": False}, contacts=None)
    j.data["emergency_contacts"] = CONTACTS
    await alarm(j)
    assert wire.frames == [] and notes == []


@pytest.mark.asyncio
async def test_emergency_an_alarm_s_name_is_plain_words_in_the_text(tmp_path):
    j, wire, _ = await house(tmp_path)
    name = "Go to http://evil.example or ring 555 010 9999 me@evil.example\nnow"
    await alarm(j, "binary_sensor.bad", {"device_class": "smoke", "friendly_name": name})
    what = wire.frames[0]["params"]["what"]
    assert not any(c in what for c in ":/@\n") and not any(c.isdigit() for c in what) and len(what) <= 200
    assert emergency.what("gas", 12345) == "The gas alarm is sounding."


@pytest.mark.asyncio
async def test_emergency_contacts_are_kept_across_a_restart_and_a_bad_list_is_refused_whole(tmp_path):
    j, _, _ = await house(tmp_path)
    for bad in ("Sam", [CONTACTS[0]] * 6, [{"name": "Sam"}], [{"name": "Sam", "number": 5550100}]):
        with pytest.raises(ValueError):
            await put(j, bad)
    j2, wire, _ = await house(tmp_path, contacts=None)  # a fresh process on the same config dir
    await alarm(j2)
    assert wire.frames and wire.frames[0]["params"]["contacts"] == CONTACTS


def test_emergency_the_proof_is_the_contracts(tmp_path):
    for case in PROOF["cases"]:
        text = f"{PROOF['canonical_prefix']}\n{json.dumps(case['contacts'], sort_keys=True, separators=(',', ':'))}\n{case['issued']}"
        assert text == case["canonical"]  # the vector is the form the plugin signs
        assert emergency.proof(SECRET, case["contacts"], case["issued"]) == case["mac"]
        assert sign(case["contacts"], case["issued"]) == case["mac"]
    assert (emergency.MAX_AGE_S, emergency.SKEW_S, emergency.REFUSED_NOTICE_EVERY_S) == (
        PROOF["max_age_s"], PROOF["skew_s"], PROOF["refused_notice_every_s"])


def test_emergency_contacts_route_needs_the_token_and_the_plugin_s_proof_and_answers_a_count_never_a_number(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    j = Jarvis(tmp_path)
    j.data[DATA_AUTH] = AuthManager()
    j.data["emergency_secret"] = SECRET
    j.data["emergency_clock"] = lambda: NOW
    _info, token = asyncio.run(j.data[DATA_AUTH].create_token("test-client"))
    issued = int(NOW)
    signed = {"contacts": CONTACTS, "issued": issued, "proof": sign(CONTACTS, issued)}
    with TestClient(create_app(j, static_dir=tmp_path / "no-www")) as client:
        route = "/api/emergency/contacts"
        assert client.post(route, json=signed).status_code == 401  # no token
        auth = {"Authorization": f"Bearer {token}"}
        # Every holder of the house token, and no proof: refused, whatever the list says.
        assert client.post(route, json={"contacts": CONTACTS}, headers=auth).status_code == 403
        assert client.post(route, json={**signed, "proof": "0" * 64}, headers=auth).status_code == 403
        assert client.post(route, json={**signed, "contacts": TWO}, headers=auth).status_code == 403
        done = client.post(route, json=signed, headers=auth)
        assert done.status_code == 200 and done.json() == {"count": 1}
        assert client.post(route, json=signed, headers=auth).status_code == 403  # the same proof again
        bad = [{"name": "Sam"}]
        later = {"contacts": bad, "issued": issued + 1, "proof": sign(bad, issued + 1)}
        assert client.post(route, json=later, headers=auth).status_code == 400  # proved, but not a list


@pytest.mark.asyncio
async def test_emergency_a_proof_that_is_not_the_plugin_s_is_refused_and_the_old_list_keeps_ringing(tmp_path):
    j, wire, _ = await house(tmp_path)
    issued = int(j.data["now"]) + 1
    good = sign(TWO, issued)
    flipped = good[:-1] + ("0" if good[-1] != "0" else "1")
    now = lambda: j.data["now"]  # noqa: E731
    cases = {
        "no proof": (TWO, issued, None),
        "no issue time": (TWO, None, good),
        "a proof over a different list": (TWO, issued, sign(CONTACTS, issued)),
        "a tampered proof": (TWO, issued, flipped),
        "another secret's proof": (TWO, issued, sign(TWO, issued, "some-other-secret")),
        "the empty secret's proof": (TWO, issued, sign(TWO, issued, "")),
        "an issue time the proof was not made for": (TWO, issued + 1, good),
        "a boolean for the issue time": (TWO, True, good),
        "a proof that is a number": (TWO, issued, 5),
        "an expired proof": (TWO, int(now()) - emergency.MAX_AGE_S - 1, sign(TWO, int(now()) - emergency.MAX_AGE_S - 1)),
        "a proof from the future": (TWO, int(now()) + emergency.SKEW_S + 1, sign(TWO, int(now()) + emergency.SKEW_S + 1)),
    }
    for name, (contacts, when, proof) in cases.items():
        with pytest.raises(emergency.Refused):
            await emergency.set_contacts(j, contacts, when, proof)
        assert name
    await alarm(j)
    assert wire.frames[0]["params"]["contacts"] == CONTACTS  # the alarm rings the list the owner approved


@pytest.mark.asyncio
async def test_emergency_a_house_with_no_secret_refuses_even_the_empty_secret_s_proof(tmp_path):
    j, _, _ = await house(tmp_path, {"contacts_secret": ""}, contacts=None)
    for secret in ("", SECRET):
        with pytest.raises(emergency.Refused):
            await emergency.set_contacts(j, CONTACTS, int(NOW), sign(CONTACTS, int(NOW), secret))


@pytest.mark.asyncio
async def test_emergency_a_proof_cannot_be_replayed_or_older_than_the_list_the_house_holds(tmp_path):
    j, _, _ = await house(tmp_path)
    issued = int(j.data["now"]) + 1
    j.data["now"] += 1
    await emergency.set_contacts(j, TWO, issued, sign(TWO, issued))
    with pytest.raises(emergency.Refused):  # the same push again: a replay
        await emergency.set_contacts(j, TWO, issued, sign(TWO, issued))
    # An older proof, still inside its window, for the list it replaced: a rollback.
    with pytest.raises(emergency.Refused):
        await emergency.set_contacts(j, CONTACTS, issued - 1, sign(CONTACTS, issued - 1))
    with pytest.raises(emergency.Refused):  # and one issued at the same second
        await emergency.set_contacts(j, CONTACTS, issued, sign(CONTACTS, issued))
    j2, _, _ = await house(tmp_path, contacts=None)  # a restart keeps the last issue time
    with pytest.raises(emergency.Refused):
        await emergency.set_contacts(j2, TWO, issued, sign(TWO, issued))


@pytest.mark.asyncio
async def test_emergency_a_real_change_tells_the_owner_at_critical_with_the_count_and_no_numbers(tmp_path):
    j, _, notes = await house(tmp_path)
    await put(j, TWO)
    assert len(notes) == 1 and notes[0]["importance"] == "critical" and notes[0]["kind"] == "notify"
    text = notes[0]["message"]
    assert "2" in text and "+1555" not in text and "Sam" not in text and "Ada" not in text
    await put(j, [])  # clearing the list is a change too
    assert len(notes) == 2 and "0" in notes[1]["message"] and notes[1]["importance"] == "critical"


@pytest.mark.asyncio
async def test_emergency_the_same_list_pushed_again_with_a_new_proof_is_not_a_change_so_it_is_quiet(tmp_path):
    j, _, notes = await house(tmp_path)
    await put(j, CONTACTS)
    await put(j, [dict(reversed(list(CONTACTS[0].items())))])  # the same person, keys in another order
    assert notes == []


@pytest.mark.asyncio
async def test_emergency_a_refused_push_tells_the_owner_at_most_once_in_ten_minutes(tmp_path):
    j, _, notes = await house(tmp_path)
    issued = int(j.data["now"]) + 1
    for _ in range(5):
        with pytest.raises(emergency.Refused):
            await emergency.set_contacts(j, TWO, issued, "0" * 64)
    assert len(notes) == 1 and notes[0]["importance"] == "critical"
    assert "+1555" not in notes[0]["message"] and "Sam" not in notes[0]["message"] and "refused" in notes[0]["message"]
    j.data["now"] += emergency.REFUSED_NOTICE_EVERY_S - 5
    with pytest.raises(emergency.Refused):
        await emergency.set_contacts(j, TWO, issued, "0" * 64)
    assert len(notes) == 1
    j.data["now"] += 10
    with pytest.raises(emergency.Refused):
        await emergency.set_contacts(j, TWO, int(j.data["now"]), "0" * 64)
    assert len(notes) == 2


@pytest.mark.asyncio
async def test_emergency_a_notice_that_cannot_be_sent_does_not_undo_an_approved_change(tmp_path):
    j, _, notes = await house(tmp_path)

    async def broken(call):
        raise RuntimeError("the push service is down")

    j.services.register("companion", "notify", broken)
    assert await put(j, TWO) == TWO


class Stub:
    """The owner's flow behind the fallback URL, on loopback: records every request it gets and answers
    with a chosen status (a 3xx carries a Location to ``/elsewhere``), after an optional delay."""

    def __init__(self, status: int = 200, delay: float = 0.0) -> None:
        self.hits: list[tuple[str, bytes]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 -- the http.server hook's name
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                stub.hits.append((self.path, body))
                time.sleep(delay)
                self.send_response(status)
                if 300 <= status < 400:
                    self.send_header("Location", f"http://127.0.0.1:{stub.server.server_port}/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args) -> None:  # keep the test output quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub_factory():
    made: list[Stub] = []

    def make(status: int = 200, delay: float = 0.0) -> Stub:
        made.append(Stub(status, delay))
        return made[-1]

    yield make
    for s in made:
        s.close()


@pytest.mark.asyncio
async def test_emergency_fallback_posts_once_when_no_phone_is_online_and_the_cooldown_holds(
        tmp_path, stub_factory, monkeypatch):
    # A proxy in the environment must not take the post: the stub is the only thing that may see it.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    stub = stub_factory()
    j, wire, notes = await house(tmp_path, {"fallback_url": stub.url}, phone=False)
    await alarm(j)
    await alarm(j)  # a flapping sensor, inside the cool-down
    assert len(stub.hits) == 1 and stub.hits[0][0] == "/hook"
    body = json.loads(stub.hits[0][1])
    assert body == {"what": "The smoke alarm Kitchen smoke is sounding.", "kind": "smoke",
                    "time": "2023-11-14T22:13:21+00:00"}
    sent = stub.hits[0][1].decode()
    assert not any(c["name"] in sent or c["number"] in sent for c in CONTACTS)
    assert wire.frames == []
    # The owner is still told, at critical, that only the fallback was: its answer is no one's confirmation.
    assert len(notes) == 1 and notes[0]["importance"] == "critical"
    assert "fallback" in notes[0]["message"] and "nobody was told" not in notes[0]["message"]
    j.data["now"] = NOW + emergency.COOLDOWN_S + 10  # a new alarm, long after
    await alarm(j)
    assert len(stub.hits) == 2


@pytest.mark.asyncio
async def test_emergency_fallback_unset_posts_nothing_and_the_notice_is_as_before(tmp_path, stub_factory):
    stub = stub_factory()
    for i, options in enumerate(({}, {"fallback_url": ""}, {"fallback_url": "   "}, {"fallback_url": 5})):
        j, _, notes = await house(tmp_path / str(i), options, phone=False)
        await alarm(j)
        assert len(notes) == 1 and "nobody was told" in notes[0]["message"]
        assert "fallback" not in notes[0]["message"]
    assert stub.hits == []


@pytest.mark.asyncio
async def test_emergency_fallback_is_not_used_when_the_phone_was_asked_and_sent_it(tmp_path, stub_factory):
    stub = stub_factory()
    j, wire, notes = await house(tmp_path, {"fallback_url": stub.url})
    await alarm(j)
    assert len(wire.frames) == 1 and stub.hits == [] and notes == []


@pytest.mark.asyncio
async def test_emergency_fallback_covers_a_phone_error_and_missing_contacts(tmp_path, stub_factory):
    stub = stub_factory()
    j, wire, notes = await house(tmp_path, {"fallback_url": stub.url},
                                 answer={"status": "error", "error": "no sms permission"})
    await alarm(j)
    assert len(wire.frames) == 1 and len(stub.hits) == 1
    assert len(notes) == 1 and "no sms permission" in notes[0]["message"] and "fallback" in notes[0]["message"]
    j2, _, notes2 = await house(tmp_path / "b", {"fallback_url": stub.url}, contacts=None)
    await alarm(j2)
    assert len(stub.hits) == 2 and len(notes2) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,said", [(500, "500"), (403, "403"), (302, "redirect")])
async def test_emergency_fallback_that_refuses_or_redirects_is_said_and_not_followed(
        tmp_path, stub_factory, status, said):
    stub = stub_factory(status)
    j, _, notes = await house(tmp_path, {"fallback_url": stub.url}, phone=False)
    await alarm(j)
    assert [p for p, _ in stub.hits] == ["/hook"]  # a redirect is a reason to stop, never a hop
    assert len(notes) == 1 and notes[0]["importance"] == "critical"
    text = notes[0]["message"]
    assert "nobody was told" in text and "fallback" in text and said in text and stub.url not in text
    await alarm(j)  # nothing reached anyone, so the cool-down was not used up: the next alarm tries again
    assert len(stub.hits) == 2


@pytest.mark.asyncio
async def test_emergency_fallback_that_times_out_or_is_unreachable_is_said_and_never_raises(
        tmp_path, stub_factory, monkeypatch):
    monkeypatch.setattr(emergency, "FALLBACK_TIMEOUT_S", 0.3)
    slow = stub_factory(delay=2.0)
    dead = stub_factory()
    dead.close()  # a port nothing listens on
    for i, (url, said) in enumerate(((slow.url, "in time"), (dead.url, "could not be reached"),
                                     ("ftp://127.0.0.1/hook", "http"), ("http://", "could not be reached"))):
        j, _, notes = await house(tmp_path / str(i), {"fallback_url": url}, phone=False)
        await j.data["emergency_outreach"].alert("smoke", "Kitchen smoke")  # no exception reaches the caller
        assert len(notes) == 1 and "nobody was told" in notes[0]["message"] and said in notes[0]["message"]
        assert url not in notes[0]["message"]


# --- the list on the phone (T-1262): handed over when saved and when a phone connects --------------------------

SYNC_ACTION = SYNC["action"]
SYNC_MANIFEST = [{"id": SYNC_ACTION, "tier": SYNC["tier"], "description": "keep the list for the fall watch",
                  "params": {}, "capability": "device", "available": True}]
STORED = {"status": "ok", "result": {"status": "ok", "stored": 1}}


def phone(j, device_id="phone", *, manifest=SYNC_MANIFEST, platform="android", answer=None, token="paired"):
    """A phone on the channel. ``token`` is the id of the credential its socket authenticated with: by
    default the one the pairing flow minted for it (``"paired"``); a test passes another id, or None."""
    wire = Wire(STORED if answer is None else answer)
    token_id = j.data["paired_token_id"] if token == "paired" else token
    wire.link = get_devices(j).register(device_id, device_id, platform, [], manifest, wire.sender, owner=wire,
                                        token_id=token_id)
    return wire


async def settle(j, seconds=0.05):
    await j.async_block_till_done()
    await asyncio.sleep(seconds)


async def sync_house(tmp_path, *, contacts=CONTACTS):
    """A house with the integration up and no phone yet; the connect push waits no time (tests only)."""
    j, _, notes = await house(tmp_path, phone=False, contacts=contacts)
    j.data["emergency_sync_delay_s"] = 0
    j.data[DATA_AUTH] = AuthManager()
    info, _secret = await j.data[DATA_AUTH].create_token("Test Phone", paired=True)
    j.data["paired_token_id"] = info.id
    return j, notes


def sync_frames(wire):
    return [f for f in wire.frames if f["action"] == SYNC_ACTION]


@pytest.mark.asyncio
async def test_emergency_saving_the_list_sends_every_connected_phone_the_stored_list(tmp_path):
    j, _ = await sync_house(tmp_path, contacts=None)
    one, two = phone(j, "one"), phone(j, "two")
    desktop = phone(j, "desk", platform="linux")  # not a phone: the fall watch is the phone's
    old = phone(j, "old", manifest=MANIFEST)  # an app that does not know the action: no prompt thrown at it
    await settle(j)
    for wire in (one, two):
        wire.frames.clear()
    await put(j, TWO)
    await settle(j)
    for wire in (one, two):
        assert [f["params"] for f in sync_frames(wire)] == [{"contacts": TWO}]
        assert sync_frames(wire)[0]["tier"] == SYNC["tier"]
    assert desktop.frames == [] and old.frames == []
    assert emergency.SYNC_ACTION == SYNC_ACTION


@pytest.mark.asyncio
async def test_emergency_a_link_that_is_not_a_paired_phone_gets_nothing_whatever_it_declares(tmp_path, monkeypatch):
    """The platform and the manifest are the link's own words; who it authenticated as is the house's."""
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    j, _ = await sync_house(tmp_path)
    auth = j.data[DATA_AUTH]
    plain, _ = await auth.create_token("a token minted over REST, not by pairing")
    revoked, _ = await auth.create_token("Old Phone", paired=True)
    await auth.revoke(revoked.id)
    monkeypatch.setenv(ENV_TOKEN, "an-environment-token")
    strangers = {
        "an unpaired token": phone(j, "plain", token=plain.id),
        "a token the house never issued": phone(j, "unknown", token="not-a-token-id"),
        "a socket with no recorded token": phone(j, "anonymous", token=None),
        "a revoked pairing": phone(j, "revoked", token=revoked.id),
        "the environment token": phone(j, "env", token="env"),
    }
    await settle(j)
    await put(j, TWO)
    await settle(j)
    for why, wire in strangers.items():
        assert wire.frames == [], why
    good = phone(j, "good")  # the control: the same declarations, a paired token
    await settle(j)
    assert [f["params"] for f in sync_frames(good)] == [{"contacts": TWO}]


@pytest.mark.asyncio
async def test_emergency_the_token_decides_not_the_platform_it_claims(tmp_path):
    j, _ = await sync_house(tmp_path)
    for platform in ("android", "ios", "linux", "unknown"):
        wire = phone(j, f"p-{platform}", platform=platform)
        await settle(j)
        assert bool(sync_frames(wire)) is (platform == "android"), platform


@pytest.mark.asyncio
async def test_emergency_a_phone_that_connects_is_sent_the_stored_list(tmp_path):
    j, _ = await sync_house(tmp_path)
    wire = phone(j)
    assert wire.frames == []  # not before the register reply can have reached it: the phone ignores commands until then
    await settle(j)
    assert [f["params"] for f in sync_frames(wire)] == [{"contacts": CONTACTS}]


@pytest.mark.asyncio
async def test_emergency_the_connect_push_waits_for_the_register_reply(tmp_path):
    j, _ = await sync_house(tmp_path)
    assert emergency.SYNC_DELAY_S > 0  # the production default; the house under test waits less
    j.data["emergency_sync_delay_s"] = 0.3
    wire = phone(j)
    await asyncio.sleep(0.1)
    assert wire.frames == []
    await asyncio.sleep(0.4)
    assert len(sync_frames(wire)) == 1


@pytest.mark.asyncio
async def test_emergency_with_no_list_stored_a_connecting_phone_is_sent_an_empty_one(tmp_path):
    j, _ = await sync_house(tmp_path, contacts=None)
    wire = phone(j)
    await settle(j)
    assert [f["params"] for f in sync_frames(wire)] == [{"contacts": []}]  # so a list the owner removed is forgotten


@pytest.mark.asyncio
async def test_emergency_a_manifest_refresh_does_not_resend_but_a_new_connection_does(tmp_path):
    j, _ = await sync_house(tmp_path)
    wire = phone(j)
    await settle(j)
    assert len(sync_frames(wire)) == 1
    # The app re-registers on the same socket whenever a permission changes: the list has not.
    get_devices(j).register("phone", "phone", "android", [], [*SYNC_MANIFEST, *MANIFEST], wire.sender, owner=wire)
    await settle(j)
    assert len(sync_frames(wire)) == 1
    again = phone(j)  # the phone dropped and came back on a new socket: a fresh install may hold nothing
    await settle(j)
    assert len(sync_frames(again)) == 1


@pytest.mark.asyncio
async def test_emergency_a_refused_push_sends_nothing(tmp_path):
    j, _ = await sync_house(tmp_path)
    wire = phone(j)
    await settle(j)
    wire.frames.clear()
    with pytest.raises(emergency.Refused):
        await emergency.set_contacts(j, TWO, int(j.data["now"]) + 1, "0" * 64)
    with pytest.raises(ValueError):
        await put(j, [{"name": "Test Person", "number": "12"}])
    await settle(j)
    assert wire.frames == []


@pytest.mark.asyncio
async def test_emergency_a_phone_that_fails_or_is_gone_never_breaks_the_save(tmp_path, caplog, monkeypatch):
    assert emergency.SYNC_WAIT_S <= 30  # bounded: a phone that never answers does not pin a task forever
    monkeypatch.setattr(emergency, "SYNC_WAIT_S", 0.2)
    j, _ = await sync_house(tmp_path, contacts=None)
    failing = phone(j, "bad", answer={"status": "error", "error": "refused: Test Person +15550100"})
    silent = phone(j, "silent", answer={})  # never answers: the wait is bounded, not the save
    gone = phone(j, "gone")
    gone.link.connected = False
    await settle(j, 0.4)
    for wire in (failing, silent, gone):
        wire.frames.clear()
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert await put(j, CONTACTS) == CONTACTS  # the owner's save still stands
        await settle(j, 0.4)
    assert j.data["emergency_contacts"] == CONTACTS
    assert len(sync_frames(failing)) == 1 and len(sync_frames(silent)) == 1 and gone.frames == []


@pytest.mark.asyncio
async def test_emergency_the_list_is_never_logged_or_put_in_a_notice(tmp_path, caplog):
    secret_bits = [c[k] for c in TWO for k in ("name", "number")]
    with caplog.at_level(logging.DEBUG):
        j, notes = await sync_house(tmp_path, contacts=None)
        wire = phone(j, answer={"status": "error", "error": "no: " + " ".join(secret_bits)})
        await put(j, TWO)
        await settle(j)
    assert len(sync_frames(wire)) == 2  # on connect (empty) and on save
    text = "\n".join(r.getMessage() + str(r.args) + str(r.exc_info) for r in caplog.records)
    assert not any(bit in text for bit in secret_bits)
    assert not any(bit in json.dumps(notes) for bit in secret_bits)


def test_emergency_the_contract_lists_are_the_houses_rules_and_the_limits_agree():
    assert SYNC["limits"] == {"contacts_max": emergency.CONTACTS_MAX, "name_max": emergency.NAME_MAX,
                              "min_digits": CONTRACT["limits"]["min_digits"]}
    for case in SYNC["accepted"]:
        assert emergency._valid(case["params"]["contacts"]) == case["stored"], case["why"]
    for case in [*SYNC["refused"], SYNC["refused_by_phone_only"][1]]:
        with pytest.raises(ValueError):
            emergency._valid(case["params"].get("contacts"))
        # (the null case has the key; the other phone-only case, no key at all, is the phone's to refuse)


# --- only the house's own push may send the action: no service, automation or sequence can (T-1262) ---

@pytest.mark.asyncio
async def test_emergency_the_generic_device_paths_cannot_send_the_sync_to_a_phone(tmp_path):
    """A house token's service call, an automation step and a sequence step all name the action over the
    same channel the house uses; each is refused and no frame reaches the phone. The refusal is the
    decision asserted, not a stored list."""
    from aura.automation.actions import ScriptRunner
    from aura.integrations import device_control

    j, _ = await sync_house(tmp_path)
    await device_control.async_setup(j, {})
    wire = phone(j)
    await settle(j)
    wire.frames.clear()
    bad = {"contacts": [{"name": "Not The Owner", "number": "+15550177"}]}
    call = {"device_id": "phone", "action": SYNC_ACTION, "params": bad, "reason": "x", "timeout": 1}

    outcome = await j.async_call_service("device_control", "run", call, return_response=True)
    assert outcome["status"] == "denied", outcome

    step = {"service": "device_control.run", "data": call, "response_variable": "r"}
    await ScriptRunner(j).async_run([step])  # a refusal is an answer, not an exception
    seq = await j.async_call_service(
        "device_control", "run_sequence",
        {"steps": [{"device": "phone", "action": SYNC_ACTION, "params": bad}], "reason": "x"},
        return_response=True)
    assert seq.get("status") != "ok" and not seq.get("ok"), seq
    assert wire.frames == []

    # a direct dispatch on the link is the same door, and it is shut too
    direct = await wire.link.dispatch(SYNC_ACTION, bad, reason="x", timeout=1)
    assert direct["status"] == "denied" and wire.frames == []

    # the house's own push is the one way through
    await emergency._push_list(j, wire.link)
    assert [f["params"] for f in sync_frames(wire)] == [{"contacts": CONTACTS}]


def test_emergency_the_house_only_ids_are_one_list_the_phone_and_the_house_share():
    from aura.api.devices import HOUSE_ONLY_ACTIONS

    assert SYNC["house_only_actions"] == [SYNC_ACTION]
    assert HOUSE_ONLY_ACTIONS == frozenset(SYNC["house_only_actions"])
