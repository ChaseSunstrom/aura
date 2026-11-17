"""The desktop end of a one-time code from the phone (T-0135, tests/contracts/code-relay.json)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from aura_desktop import codepopup
from aura_desktop.codepopup import CodeBoard, CodePopups, autofill, copy

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "code-relay.json")
                      .read_text(encoding="utf-8"))
SHOW = CONTRACT["examples"]["show_code"]


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Clip:
    def __init__(self) -> None:
        self.value = ""

    def copy(self, text: str) -> None:
        self.value = text

    def paste(self) -> str:
        return self.value


class Gui:
    def __init__(self) -> None:
        self.typed: list[str] = []

    def write(self, text: str, interval: float = 0.0) -> None:
        self.typed.append(text)


def test_code_popup_takes_the_contract_frame_once_and_lets_it_expire():
    clock = Clock()
    board = CodeBoard(clock)
    item = board.show(SHOW)
    assert item and (item.code, item.sender, item.device) == ("482913", "Google", "Pixel")
    assert board.show(SHOW) is None  # a repeat
    clock.t += SHOW["ttl_s"]
    assert [c.msg_id for c in board.expire()] == [SHOW["msg_id"]]


def test_code_popup_refuses_what_the_contract_refuses_and_clamps_the_lifetime():
    for case in CONTRACT["refused"]:
        assert CodeBoard().show({"type": "show_code", **case["data"]}) is None, case["name"]
    clock = Clock()
    item = CodeBoard(clock).show({**SHOW, "ttl_s": 99999})
    assert item and item.expires_at == clock.t + CONTRACT["ttl_s"]["max"]


def test_code_popup_copy_clears_the_clipboard_later_only_if_the_code_is_still_on_it():
    later: list = []
    clip = Clip()
    assert copy("482913", clip, timer=lambda s, fn: later.append((s, fn)))
    assert clip.value == "482913" and later[0][0] == codepopup.CLIPBOARD_CLEAR_S
    later[0][1]()
    assert clip.value == ""
    copy("482913", clip, timer=lambda s, fn: later.append((s, fn)))
    clip.copy("something the owner copied since")
    later[-1][1]()
    assert clip.value == "something the owner copied since"


def test_code_popup_autofill_types_only_into_the_window_focused_before_the_popup():
    gui = Gui()
    focused: list = []
    assert autofill("482913", "win-7", gui, focus=lambda w: focused.append(w) or True)
    assert focused == ["win-7"] and gui.typed == ["482913"]
    # The window is gone: nothing is typed anywhere.
    assert not autofill("482913", "win-7", gui, focus=lambda w: False)
    assert not autofill("482913", None, gui, focus=lambda w: True)
    assert gui.typed == ["482913"]


def test_code_popup_dismiss_from_another_desktop_closes_it_without_sending_done(monkeypatch):
    sent: list = []
    popups = CodePopups(sent.append)
    monkeypatch.setattr(codepopup, "focused_window", lambda: None)
    monkeypatch.setattr(popups, "_show", lambda item, target: None)
    popups.handle(dict(SHOW))
    assert SHOW["msg_id"] in popups.board.codes
    closed: list = []
    popups._close[SHOW["msg_id"]] = lambda: closed.append(True)
    popups.handle(CONTRACT["examples"]["show_code_dismiss"])
    assert closed == [True] and not popups.board.codes and sent == []


def test_code_popup_redact_the_code_is_never_logged(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    popups = CodePopups(lambda _m: None)
    monkeypatch.setattr(codepopup, "focused_window", lambda: None)
    monkeypatch.setattr(popups, "_show", lambda item, target: logging.getLogger("x").debug("shown"))
    popups.handle(dict(SHOW))
    copy(SHOW["code"], Clip(), timer=lambda s, fn: None)
    assert SHOW["code"] not in caplog.text


async def test_code_popup_the_channel_hands_both_code_frames_to_the_popups(config, make_registry):
    from aura_desktop.channel import DeviceChannel

    channel = DeviceChannel(config, make_registry([]))
    seen: list = []
    channel.codes = type("Codes", (), {"handle": lambda self, frame: seen.append(frame["type"])})()
    await channel._handle_frame(dict(SHOW))
    await channel._handle_frame(CONTRACT["examples"]["show_code_dismiss"])
    assert seen == ["show_code", "show_code_dismiss"]


def test_code_popup_the_timer_ring_dissolves_over_the_codes_lifetime():
    clock = Clock()
    item = CodeBoard(clock).show(SHOW)
    assert item and item.ring(clock.t) == 360.0
    assert item.ring(clock.t + SHOW["ttl_s"] / 2) == 180.0
    assert item.ring(clock.t + SHOW["ttl_s"] + 5) == 0.0
