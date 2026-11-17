"""A phone's one-time code reaches the owner's desktops and nothing else (T-0135).

The shapes are tests/contracts/code-relay.json, which the desktop's suite reads too.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.api.devices import ConnectedDevices  # noqa: E402

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "code-relay.json")
                      .read_text(encoding="utf-8"))
EX = CONTRACT["examples"]


class Wire:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def sender(self, frame: dict) -> bool:
        self.frames.append(frame)
        return True


def world():
    fired: list = []
    hub = ConnectedDevices(SimpleNamespace(bus=SimpleNamespace(fire=lambda *a, **k: fired.append(a))))
    wires = {}
    for device_id, name, platform in (("phone", "Pixel", "android"), ("desk", "Workstation", "desktop"),
                                      ("laptop", "Laptop", "desktop")):
        wires[device_id] = Wire()
        hub.register(device_id, name, platform, [], [], wires[device_id].sender, owner=wires[device_id])
    fired.clear()  # registrations are announced; only what the relay fires matters
    return hub, wires, fired


def test_otp_a_code_goes_to_every_desktop_as_the_contract_says_and_never_to_a_phone():
    hub, wires, fired = world()
    assert hub.relay_code("phone", EX["otp_received"]) == 2
    assert wires["desk"].frames[-1] == EX["show_code"] == wires["laptop"].frames[-1]
    assert not [f for f in wires["phone"].frames if f.get("type") == "show_code"]
    assert fired == []  # never the bus: what is on it is recorded


def test_otp_a_repeat_of_the_same_message_is_dropped_while_the_first_is_live():
    hub, wires, _ = world()
    hub.relay_code("phone", EX["otp_received"])
    assert hub.relay_code("phone", EX["otp_received"]) == 0
    assert len([f for f in wires["desk"].frames if f.get("type") == "show_code"]) == 1


def test_otp_what_the_contract_refuses_reaches_no_desktop():
    for case in CONTRACT["refused"]:
        hub, wires, _ = world()
        assert hub.relay_code("phone", case["data"]) == 0, case["name"]
        assert not wires["desk"].frames, case["name"]


def test_otp_the_lifetime_is_held_to_the_contract_bounds():
    ttl = CONTRACT["ttl_s"]
    for asked, want in ((5, ttl["min"]), (9999, ttl["max"]), ("x", ttl["default"]), (None, ttl["default"])):
        hub, wires, _ = world()
        hub.relay_code("phone", {**EX["otp_received"], "ttl_s": asked})
        assert wires["desk"].frames[-1]["ttl_s"] == want, asked


def test_otp_done_on_one_desktop_dismisses_it_on_the_others():
    hub, wires, _ = world()
    hub.relay_code("phone", EX["otp_received"])
    hub.code_done("desk", EX["code_done"]["msg_id"])
    assert wires["laptop"].frames[-1] == EX["show_code_dismiss"]
    assert wires["desk"].frames[-1]["type"] == "show_code"


def test_otp_redact_the_code_is_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    hub, _, _ = world()
    hub.relay_code("phone", EX["otp_received"])
    assert caplog.records and EX["otp_received"]["code"] not in caplog.text


def test_otp_only_a_phone_can_raise_a_code():
    hub, wires, _ = world()
    assert hub.relay_code("desk", EX["otp_received"]) == 0
    assert hub.relay_code("nobody", EX["otp_received"]) == 0
    assert not wires["laptop"].frames


def test_otp_a_phone_or_an_unknown_code_cannot_end_a_code_on_the_desktops(monkeypatch):
    hub, wires, _ = world()
    hub.code_done("desk", "never-shown")
    assert not wires["laptop"].frames
    hub.relay_code("phone", EX["otp_received"])
    hub.code_done("phone", EX["code_done"]["msg_id"])
    assert wires["laptop"].frames[-1]["type"] == "show_code"
    # Expired: nothing left to end.
    import aura.api.devices as devices
    later = devices.time.monotonic() + CONTRACT["ttl_s"]["max"] + 1
    monkeypatch.setattr(devices.time, "monotonic", lambda: later)
    hub.code_done("desk", EX["code_done"]["msg_id"])
    assert wires["laptop"].frames[-1]["type"] == "show_code"
