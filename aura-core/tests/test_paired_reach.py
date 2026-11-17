"""What a paired device's token can still reach through the commands it is allowed (T-1378, round 2).

`test_paired_access.py` covers the door: a paired token may send only what the clients
send. This module covers the reach behind that door: the voice identity, the event feed,
approvals, the device registry and the push frames. Each test asserts the decision the
server made (a refusal, a frame that was or was not delivered), never a stored copy of it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from aura.api import speaker as speaker_api  # noqa: E402
from aura.api.devices import get_devices  # noqa: E402
from aura.api.server import create_app  # noqa: E402
from aura.auth import DATA_AUTH, ENV_TOKEN, AuthManager  # noqa: E402
from aura.bus import Context  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import voice as voice_integration  # noqa: E402
from aura.paired_access import REFUSED_CODE, REFUSED_MESSAGE  # noqa: E402
from aura.tools import (  # noqa: E402
    EVENT_APPROVAL_REQUIRED,
    TIER_APPROVAL,
    Tool,
    ToolRegistry,
)
from test_api_companion import Session  # noqa: E402
from test_speaker_gate import FakeStt, FakeTts  # noqa: E402

pytestmark = pytest.mark.asyncio


# --- the house ------------------------------------------------------------------------
@pytest.fixture
async def house(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    box = Jarvis(tmp_path)
    await box.async_setup({"companion": {}})
    box.data[DATA_AUTH] = auth = AuthManager()
    box.data["voice_stt_client"] = FakeStt()
    box.data["voice_tts_client"] = FakeTts()
    await voice_integration.async_setup(box, {"speaker": {"mode": "observe"}})
    box.tokens = {  # type: ignore[attr-defined]
        "paired": await auth.create_token("Pixel", paired=True),
        "other": await auth.create_token("Tablet", paired=True),
        "console": await auth.create_token("console"),
    }
    yield box
    await box.async_stop()


def _secret(box, who):
    return box.tokens[who][1]


def _id(box, who):
    return box.tokens[who][0].id


async def _open(box, who):
    return await Session(box, _secret(box, who)).open()


def _refused(frame):
    return frame["success"] is False and frame["error"]["code"] == REFUSED_CODE


# --- 1. voice identity ------------------------------------------------------------------
async def test_paired_token_cannot_make_anyone_an_owner_or_touch_the_owner(house):
    await speaker_api.async_person(house, "owner", "owner")
    await speaker_api.async_person(house, "Ted", "member")
    for call in (
        speaker_api.async_person(house, "Eve", "owner", paired=True),  # a new owner
        speaker_api.async_person(house, "Ted", "owner", paired=True),  # promoted to owner
        speaker_api.async_person(house, "owner", "guest", paired=True),  # the owner demoted
        speaker_api.async_person(house, "owner", None, rename="stranger", paired=True),
    ):
        with pytest.raises(speaker_api.EnrolError) as refused:
            await call
        assert refused.value.status == 403 and str(refused.value) == REFUSED_MESSAGE
    # what the People screen really does for a person who is not the owner
    done = await speaker_api.async_person(house, "Ted", "guest", paired=True)
    assert done["level"] == "guest"
    done = await speaker_api.async_person(house, "Pat", "member", paired=True)
    assert done["level"] == "member"


async def test_paired_token_cannot_enrol_into_an_owner_label_that_exists(house):
    await speaker_api.async_person(house, "owner", "owner")
    await speaker_api.async_person(house, "Ted", "member")
    # the label is absent (so it defaults to the owner's) or names the owner outright
    for label in (None, "owner", "Owner"):
        with pytest.raises(speaker_api.EnrolError) as refused:
            await speaker_api.async_enrol(house, b"", "audio/wav", label=label, paired=True)
        assert refused.value.status == 403 and str(refused.value) == REFUSED_MESSAGE
    # a person who is not the owner is passed on to the sample itself, which is empty here
    with pytest.raises(speaker_api.EnrolError) as passed:
        await speaker_api.async_enrol(house, b"", "audio/wav", label="Ted", paired=True)
    assert passed.value.status != 403


async def test_the_owner_token_enrols_into_the_owner_label_where_a_paired_token_is_refused(
    house, tmp_path, monkeypatch
):
    """T-1393: the owner re-enrols from the signed-in console, so closing the phone out of
    the owner's voiceprint (T-1378) must not close that path too.

    The console relay (`jarvis-web/src/lib/server/speakerRelay.ts`) sends the server-held
    `AURA_TOKEN` once the console is unlocked; the house never treats that token as paired.
    The same sample, to the same route, with no label (so the owner's): the owner's token
    adds it, a paired token is refused and the profile stays as it was.
    """
    from synth_voice import OWNER, RATE

    from aura.voice.audio import wav_bytes

    monkeypatch.setenv(ENV_TOKEN, "server-held-owner-token-for-tests")
    await speaker_api.async_person(house, "owner", "owner")
    before = speaker_api.status(house, "owner")["samples"]
    sample = wav_bytes(OWNER.utterance(seconds=2.5, seed=0), RATE, 2, 1)
    with TestClient(create_app(house, static_dir=tmp_path / "no-www")) as client:

        def enrol(secret):
            return client.post(
                "/api/voice/speaker/enrol",
                content=sample,
                headers={"Authorization": f"Bearer {secret}", "Content-Type": "audio/wav"},
            )

        refused = enrol(_secret(house, "paired"))
        assert refused.status_code == 403 and refused.json()["detail"] == REFUSED_MESSAGE
        assert speaker_api.status(house, "owner")["samples"] == before
        for secret in ("server-held-owner-token-for-tests", _secret(house, "console")):
            done = enrol(secret)
            assert done.status_code == 200, done.text
        assert speaker_api.status(house, "owner")["samples"] == before + 2


async def test_paired_token_may_enrol_the_first_owner_of_a_house_with_none(house):
    with pytest.raises(speaker_api.EnrolError) as passed:
        await speaker_api.async_enrol(house, b"", "audio/wav", label=None, paired=True)
    assert passed.value.status != 403


async def test_paired_token_cannot_forget_everyone_or_the_owner(house):
    await speaker_api.async_person(house, "owner", "owner")
    await speaker_api.async_person(house, "Ted", "member")
    for label in (None, "owner"):
        with pytest.raises(speaker_api.EnrolError) as refused:
            await speaker_api.async_forget(house, label, paired=True)
        assert refused.value.status == 403 and str(refused.value) == REFUSED_MESSAGE
    forgot = await speaker_api.async_forget(house, "Ted", paired=True)
    assert [p["label"] for p in forgot["people"]] == ["owner"]
    # the controls: the console's own call is not limited
    assert (await speaker_api.async_forget(house, None))["people"] == []


async def test_the_voice_routes_hand_the_paired_flag_to_the_rules(house, tmp_path):
    """The REST handlers, not just the functions: a paired token is refused at the route."""
    await speaker_api.async_person(house, "owner", "owner")
    with TestClient(create_app(house, static_dir=tmp_path / "no-www")) as client:
        paired = {"Authorization": f"Bearer {_secret(house, 'paired')}"}
        console = {"Authorization": f"Bearer {_secret(house, 'console')}"}
        body = {"label": "Eve", "level": "owner"}
        refused = client.post("/api/voice/speaker/people", json=body, headers=paired)
        assert refused.status_code == 403 and refused.json()["detail"] == REFUSED_MESSAGE
        assert client.delete("/api/voice/speaker", headers=paired).status_code == 403
        assert client.post("/api/voice/speaker/enrol", headers=paired).status_code == 403
        # the control: the same call from a console-minted token goes through
        assert client.post("/api/voice/speaker/people", json=body, headers=console).status_code == 200


async def test_the_enrolling_route_answers_a_paired_caller_with_progress_only(house, tmp_path):
    with TestClient(create_app(house, static_dir=tmp_path / "no-www")) as client:
        paired = {"Authorization": f"Bearer {_secret(house, 'paired')}"}
        reply = client.post("/api/voice/speaker/enrolling", headers=paired)
        assert reply.status_code == 200
        assert set(reply.json()) == {"enrolling", "window_seconds"}


async def test_a_paired_token_cannot_keep_the_house_deaf_by_marking_enrolment_forever(house, monkeypatch):
    """`enrolling` makes every pipeline turn yield, so a stolen token calling it every few
    seconds would silence the voice assistant. A paired run is bounded; the owner's is not."""
    from types import SimpleNamespace

    now = [1000.0]
    monkeypatch.setattr(speaker_api, "time", SimpleNamespace(monotonic=lambda: now[0]))
    deaf = 0
    for second in range(3600):  # an hour, a call every 15 seconds, deafness sampled each second
        if second % 15 == 0:
            speaker_api.mark_enrolling(house, paired=True)
        deaf += 1 if speaker_api.enrolling(house) else 0
        now[0] += 1
    assert deaf < 3600 * 0.15
    # the same cadence from the owner's own token is the console enrolling: honoured throughout
    for _ in range(int(3600 / 15)):
        speaker_api.mark_enrolling(house)
        assert speaker_api.enrolling(house)
        now[0] += 15
    # a person enrolling on a phone (five phrases, well inside the bound) is never cut short
    now[0] += 3600
    for _ in range(5):
        speaker_api.mark_enrolling(house, paired=True)
        assert speaker_api.enrolling(house)
        now[0] += 15


# --- 2. the event feed ------------------------------------------------------------------------
async def test_paired_socket_cannot_subscribe_to_everything_or_to_what_the_clients_do_not_read(house):
    phone = await _open(house, "paired")
    for frame in (
        {"id": 1, "type": "subscribe_events"},
        {"id": 2, "type": "subscribe_events", "event_type": "*"},
        {"id": 3, "type": "subscribe_events", "event_type": ""},
        {"id": 4, "type": "subscribe_events", "event_type": "jarvis_device_registered"},
        {"id": 5, "type": "subscribe_events", "event_type": "jarvis_consent_pending"},
    ):
        assert _refused(await phone.command(frame)), frame
    # what the phone's activity rows and task board really listen to
    for n, event_type in enumerate(("jarvis_tool_started", "jarvis_task_updated", "jarvis_notification")):
        assert (await phone.command({"id": 10 + n, "type": "subscribe_events", "event_type": event_type}))["success"]
    await phone.close()


async def test_a_console_token_may_still_subscribe_to_everything(house):
    console = await _open(house, "console")
    assert (await console.command({"id": 1, "type": "subscribe_events"}))["success"]
    await console.close()


async def test_a_paired_socket_hears_every_approval_it_may_answer(house):
    # The phone is the owner's approval surface: the house's own token and automations raise the
    # request, so it has to hear all of them (a stolen token is revoked, not filtered).
    phone = await _open(house, "paired")
    assert (await phone.command({"id": 1, "type": "subscribe_events", "event_type": EVENT_APPROVAL_REQUIRED}))["success"]
    house.bus.fire(EVENT_APPROVAL_REQUIRED, {"id": "theirs"}, Context(user_id=_id(house, "other")))
    house.bus.fire(EVENT_APPROVAL_REQUIRED, {"id": "nobodys"}, Context())
    house.bus.fire(EVENT_APPROVAL_REQUIRED, {"id": "mine"}, Context(user_id=_id(house, "paired")))
    await phone.settle()
    heard = [f["event"]["data"]["id"] for f in phone.ws.sent if f.get("type") == "event"]
    assert heard == ["theirs", "nobodys", "mine"]
    await phone.close()


# --- 3. approvals ---------------------------------------------------------------------------------
@pytest.fixture
async def held(house):
    """Two requests the safety gate is holding: one raised for the paired phone's token, one for another's."""
    ran: list[str] = []
    registry = ToolRegistry(house)

    async def handler(args, context=None):
        ran.append(args["which"])
        return {"status": "ok"}

    registry.register(Tool(name="do_it", description="x", handler=handler, tier=TIER_APPROVAL))
    house.data["llm_tools"] = registry

    async def approve(call):  # what the llm integration registers, without its model client
        return await registry.approve_request(str(call.get("request_id") or ""), bool(call.get("approved")))

    house.services.register("llm", "approve", approve, supports_response=True)
    for which, who in (("mine", "paired"), ("theirs", "other")):
        await registry.call("do_it", {"which": which}, Context(user_id=_id(house, who)))
    by_which = {r["arguments"]["which"]: r["request_id"] for r in registry.pending_requests()}
    return ran, by_which


async def test_a_paired_token_may_answer_any_held_request(house, held):
    # The phone answers requests the house's token or an automation raised, so the answer is not
    # bound to the raiser. What a stolen token can do with that is the runbook's "revoke it".
    ran, ids = held
    phone = await _open(house, "paired")
    theirs = await phone.command({"id": 1, "type": "jarvis/approve", "request_id": ids["theirs"], "approved": True})
    assert theirs["success"] and theirs["result"]["status"] == "executed" and ran == ["theirs"]
    own = await phone.command({"id": 2, "type": "jarvis/approve", "request_id": ids["mine"], "approved": True})
    assert own["success"] and ran == ["theirs", "mine"]
    # an id nobody holds gets the service's own answer, not the paired refusal
    unknown = await phone.command({"id": 3, "type": "jarvis/approve", "request_id": "no-such-id", "approved": True})
    assert not _refused(unknown)
    await phone.close()


async def test_a_console_token_still_answers_any_request(house, held):
    ran, ids = held
    console = await _open(house, "console")
    done = await console.command({"id": 1, "type": "jarvis/approve", "request_id": ids["theirs"], "approved": True})
    assert done["success"] and ran == ["theirs"]
    await console.close()


# --- 3b. the owner's approver switch (T-1380) ---------------------------------------------------------
APPROVAL_EVENTS = ("jarvis_approval_required", "jarvis_approval_resolved")


async def _approver(box, who, on):
    assert await box.data[DATA_AUTH].set_approver(_id(box, who), on)


async def test_approver_off_a_paired_token_cannot_answer_a_held_request(house, held):
    ran, ids = held
    await _approver(house, "paired", False)
    phone = await _open(house, "paired")
    for n, which in enumerate(("mine", "theirs")):
        frame = await phone.command(
            {"id": n + 1, "type": "jarvis/approve", "request_id": ids[which], "approved": True}
        )
        assert _refused(frame), which
    assert ran == []  # the decision, not a stored flag: the held tools did not run
    # a device the owner left on still answers the same request
    other = await _open(house, "other")
    assert (await other.command(
        {"id": 1, "type": "jarvis/approve", "request_id": ids["theirs"], "approved": True}
    ))["success"] and ran == ["theirs"]
    await phone.close()
    await other.close()


async def test_approver_off_a_paired_token_cannot_hear_approvals_but_hears_the_rest(house):
    await _approver(house, "paired", False)
    phone = await _open(house, "paired")
    for n, event_type in enumerate(APPROVAL_EVENTS):
        frame = await phone.command({"id": n + 1, "type": "subscribe_events", "event_type": event_type})
        assert _refused(frame), event_type
    assert (await phone.command({"id": 9, "type": "subscribe_events", "event_type": "jarvis_notification"}))["success"]
    await phone.close()


async def test_approver_switched_off_mid_socket_stops_the_feed_and_the_answers(house, held):
    ran, ids = held
    phone = await _open(house, "paired")
    for n, event_type in enumerate(APPROVAL_EVENTS):
        assert (await phone.command({"id": n + 1, "type": "subscribe_events", "event_type": event_type}))["success"]
    house.bus.fire(EVENT_APPROVAL_REQUIRED, {"id": "before"}, Context())
    await phone.settle()
    await _approver(house, "paired", False)
    house.bus.fire(EVENT_APPROVAL_REQUIRED, {"id": "after"}, Context())
    house.bus.fire("jarvis_approval_resolved", {"id": "after"}, Context())
    await phone.settle()
    heard = [f["event"]["data"]["id"] for f in phone.ws.sent if f.get("type") == "event"]
    assert heard == ["before"]
    assert _refused(await phone.command(
        {"id": 5, "type": "jarvis/approve", "request_id": ids["mine"], "approved": True}
    )) and ran == []
    await _approver(house, "paired", True)  # the owner turns it back on: both work again
    house.bus.fire(EVENT_APPROVAL_REQUIRED, {"id": "again"}, Context())
    await phone.settle()
    assert [f["event"]["data"]["id"] for f in phone.ws.sent if f.get("type") == "event"] == ["before", "again"]
    assert (await phone.command(
        {"id": 6, "type": "jarvis/approve", "request_id": ids["mine"], "approved": True}
    ))["success"]
    await phone.close()


async def test_approver_off_also_refuses_a_coding_jobs_held_edit(house, monkeypatch):
    # No special case: Jarvis Code's gate answers through the same command, so the same switch
    # refuses it before the gate is even asked.
    from aura.integrations import code

    asked = []
    monkeypatch.setattr(code, "resolve_approval", lambda j, rid, decided: asked.append(rid) or True)
    await _approver(house, "paired", False)
    phone = await _open(house, "paired")
    assert _refused(await phone.command({"id": 1, "type": "jarvis/approve", "request_id": "job-edit", "approved": True}))
    assert asked == []
    await phone.close()
    on = await _open(house, "other")
    assert (await on.command({"id": 1, "type": "jarvis/approve", "request_id": "job-edit", "approved": True}))["success"]
    assert asked == ["job-edit"]
    await on.close()


async def test_approver_flag_does_not_touch_an_owner_token(house, held):
    ran, ids = held
    await _approver(house, "console", False)  # meaningless for a non-paired token
    console = await _open(house, "console")
    assert (await console.command({"id": 1, "type": "subscribe_events", "event_type": EVENT_APPROVAL_REQUIRED}))["success"]
    assert (await console.command(
        {"id": 2, "type": "jarvis/approve", "request_id": ids["mine"], "approved": True}
    ))["success"] and ran == ["mine"]
    await console.close()


async def test_approver_is_on_for_a_new_pairing_and_for_a_token_stored_before_the_flag(house, tmp_path):
    from aura.store import Store

    auth = house.data[DATA_AUTH]
    assert all(info.approver for info in auth.list_tokens())
    # a token document written before T-1380 has no `approver` key at all
    store = Store(tmp_path, "auth")
    await store.save({"tokens": [
        {"id": "oldphone", "name": "Old", "token_hash": "x" * 64, "paired": True},
        {"id": "muted", "name": "Muted", "token_hash": "y" * 64, "paired": True, "approver": False},
        {"id": "junk", "name": "Junk", "token_hash": "z" * 64, "paired": True, "approver": "no"},
    ]})
    loaded = await AuthManager(store).async_load()
    assert [loaded.is_approver(i) for i in ("oldphone", "muted", "junk")] == [True, False, True]
    await loaded.set_approver("muted", True)
    assert (await AuthManager(store).async_load()).is_approver("muted")  # it survives a restart


# --- 4. the device registry ---------------------------------------------------------------------------
async def test_a_paired_token_cannot_take_over_a_device_another_token_registered(house):
    first = await _open(house, "other")
    assert (await first.register("pixel-1"))["success"]
    thief = await _open(house, "paired")
    assert _refused(await thief.register("pixel-1"))
    # the owner's own token may take a device over
    console = await _open(house, "console")
    assert (await console.register("pixel-1"))["success"]
    for session in (first, thief, console):
        await session.close()


async def test_a_paired_token_cannot_take_over_a_link_stored_without_a_token_id(house):
    # a link nobody can say a token made (no `token_id`): there is no holder to ask is_live of,
    # and "no holder" must not read as "free" (T-1379)
    sent = []

    async def sender(frame):
        sent.append(frame)

    get_devices(house).register("pixel-3", "Hall tablet", "android", ["device"], None, sender)
    thief = await _open(house, "paired")
    assert _refused(await thief.register("pixel-3"))
    console = await _open(house, "console")
    assert (await console.register("pixel-3"))["success"]  # the owner's token still may
    # the neighbour: an id nobody holds is free to a paired token
    assert (await thief.register("pixel-4"))["success"]
    for session in (thief, console):
        await session.close()


async def test_a_paired_token_may_register_again_and_after_its_old_token_was_revoked(house):
    first = await _open(house, "other")
    assert (await first.register("pixel-2"))["success"]
    assert (await first.register("pixel-2"))["success"]  # a capability refresh
    await house.data[DATA_AUTH].revoke(_id(house, "other"))  # the phone was re-paired
    again = await _open(house, "paired")
    assert (await again.register("pixel-2"))["success"]
    for session in (first, again):
        await session.close()


# --- 5. push frames ---------------------------------------------------------------------------------------
async def test_a_paired_device_event_is_always_untrusted(house):
    seen = []
    house.bus.listen("jarvis_device_event", lambda e: seen.append(e.data))
    phone = await _open(house, "paired")
    assert (await phone.register("pixel-3"))["success"]
    phone.push({"type": "device_event", "event": "door_knock", "data": {"x": 1}, "trust": "trusted"})
    phone.push({"type": "device_event", "event": "other", "data": {}})
    await phone.settle()
    assert [d["trust"] for d in seen] == ["untrusted", "untrusted"]
    await phone.close()


async def test_a_console_minted_device_keeps_the_trust_it_sent(house):
    seen = []
    house.bus.listen("jarvis_device_event", lambda e: seen.append(e.data))
    box = await _open(house, "console")
    assert (await box.register("bridge-1"))["success"]
    box.push({"type": "device_event", "event": "heartbeat", "data": {}})
    await box.settle()
    assert [d["trust"] for d in seen] == ["trusted"]
    await box.close()


# --- 8. the scheduling service, reached indirectly -----------------------------------------------------------
async def test_a_paired_token_cannot_schedule_through_the_generic_service_call(house):
    phone = await _open(house, "paired")
    frame = await phone.command(
        {"id": 1, "type": "call_service", "domain": "schedule", "service": "add",
         "service_data": {"kind": "service", "domain": "lock", "service": "unlock"}}
    )
    assert _refused(frame)
    await phone.close()


async def test_the_schedule_writes_are_owner_only_services_for_a_paired_identity(house):
    from aura.services import PairedRefused

    house.services.register("schedule", "add", lambda call: {"status": "ok"}, owner_only=True)
    with pytest.raises(PairedRefused):
        await house.services.async_call(
            "schedule", "add", {}, blocking=True, context=Context(user_id=_id(house, "paired"))
        )
    assert asyncio.iscoroutinefunction(house.services.async_call)
