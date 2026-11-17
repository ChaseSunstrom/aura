"""The owner's mirrored texts: a bounded in-memory window, read and search, and routing by house state
(T-0615). The shapes are tests/contracts/message-mirror.json, which the phone's and the plugin's suites read too."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura import messages  # noqa: E402
from aura.api.devices import ConnectedDevices, DATA_DEVICES, get_presence  # noqa: E402

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "message-mirror.json")
                      .read_text(encoding="utf-8"))
EX, LIM = CONTRACT["examples"], CONTRACT["limits"]


class Wire:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def sender(self, frame: dict) -> bool:
        self.frames.append(frame)
        return True


def world():
    fired: list = []
    jarvis = SimpleNamespace(bus=SimpleNamespace(fire=lambda *a, **k: fired.append(a)), data={})
    hub = ConnectedDevices(jarvis)
    jarvis.data[DATA_DEVICES] = hub
    wires = {}
    for device_id, name, platform in (("phone", "Pixel", "android"), ("desk", "Workstation", "desktop")):
        wires[device_id] = Wire()
        hub.register(device_id, name, platform, [], [], wires[device_id].sender, owner=wires[device_id])
    fired.clear()
    return jarvis, wires, fired


def msg(**over):
    return {**EX["message_mirror"], **over}


def test_messages_a_text_reaches_every_desktop_as_the_contract_says_and_never_the_bus():
    jarvis, wires, fired = world()
    assert messages.relay(jarvis, "phone", EX["message_mirror"]) == 1
    assert wires["desk"].frames[-1] == EX["frame"]
    assert not [f for f in wires["phone"].frames if f.get("type") == "message_mirror"]
    assert fired == []


def test_messages_only_a_phone_may_raise_one():
    jarvis, wires, _ = world()
    assert messages.relay(jarvis, "desk", EX["message_mirror"]) == 0
    assert messages.window(jarvis).read() == []


def test_messages_what_the_contract_refuses_is_not_kept():
    for case in CONTRACT["refused"]:
        jarvis, wires, _ = world()
        assert messages.relay(jarvis, "phone", case["data"]) == 0, case["name"]
        assert messages.window(jarvis).read() == [], case["name"]
        assert not wires["desk"].frames, case["name"]


def test_messages_a_repeat_of_the_same_id_is_kept_once():
    jarvis, wires, _ = world()
    messages.relay(jarvis, "phone", EX["message_mirror"])
    assert messages.relay(jarvis, "phone", EX["message_mirror"]) == 0
    assert len(messages.window(jarvis).read(EX["message_mirror"]["conversation"])) == 1


def test_messages_routing_table_holds_or_passes_by_house_state():
    for case in CONTRACT["routing"]:
        jarvis, wires, _ = world()
        presence = get_presence(jarvis)
        presence.register("phone", "Pixel", "android")
        presence.set_dnd(case["dnd"])
        presence.update("phone", driving=case["driving"], asleep=case["asleep"])
        sent = messages.relay(jarvis, "phone", msg(urgent=case["urgent"], emergency=case["emergency"]))
        assert (sent == 1) is case["pass"], case["name"]
        # held or passed, the owner can still ask for it
        assert len(messages.window(jarvis).read()) == 1, case["name"]


def test_messages_emergency_needs_an_urgent_person_and_a_word():
    for case in CONTRACT["emergency_cases"]:
        got = messages.is_emergency(case["urgent"], case["text"])
        assert got is case["emergency"], case["name"]


def test_messages_the_window_is_bounded_by_count_conversations_and_age():
    now = [1000.0]
    w = messages.MessageWindow(clock=lambda: now[0])
    for i in range(LIM["per_conversation"] + 10):
        w.add(msg(id=f"m{i}", text=f"t{i}"))
    kept = w.read(EX["message_mirror"]["conversation"], limit=1000)
    assert len(kept) == LIM["per_conversation"] and kept[-1]["text"] == f"t{LIM['per_conversation'] + 9}"
    for i in range(LIM["conversations"] + 5):
        w.add(msg(id=f"c{i}", conversation=f"sms:+1{i:04d}"))
    assert len(w.conversations()) <= LIM["conversations"]
    now[0] += LIM["max_age_s"] + 1
    assert w.read(limit=1000) == [] and w.conversations() == []


def test_messages_text_and_names_are_clipped_to_the_contract():
    w = messages.MessageWindow()
    w.add(msg(text="x" * 5000, **{"from": "n" * 500}))
    got = w.read()[0]
    assert len(got["text"]) == LIM["text_max"] and len(got["from"]) == LIM["from_max"]


def test_messages_search_finds_text_and_sender_case_blind_and_is_capped():
    w = messages.MessageWindow()
    w.add(msg(id="a", text="Lunch at one", **{"from": "Anna"}))
    w.add(msg(id="b", conversation="sms:+1999", text="Parcel arrived", **{"from": "Bob"}))
    assert [m["id"] for m in w.search("LUNCH")] == ["a"]
    assert [m["id"] for m in w.search("bob")] == ["b"]
    assert w.search("") == []
    for i in range(40):
        w.add(msg(id=f"z{i}", text="needle"))
    assert len(w.search("needle")) == LIM["search_results"]


def test_messages_the_text_is_never_logged(caplog):
    jarvis, _, _ = world()
    with caplog.at_level(logging.DEBUG):
        messages.relay(jarvis, "phone", msg(text="a very private sentence"))
    assert "private sentence" not in caplog.text


def test_messages_the_read_and_search_routes_are_wired_and_the_event_is_routed():
    from aura.api import rest, websocket

    paths = {getattr(route, "path", "") for route in rest.api_router.routes}
    assert {"/api/messages", "/api/messages/search"} <= paths
    assert websocket.MESSAGE_EVENT == CONTRACT["event"]


def test_messages_the_window_is_memory_only():
    src = (Path(messages.__file__)).read_text(encoding="utf-8")
    assert "open(" not in src and "write_text" not in src and "sqlite" not in src
