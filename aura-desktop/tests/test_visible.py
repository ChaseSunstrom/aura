"""Visible computer use: the pointer glides, the target lights up, the click ripples, then the
real input fires -- and the owner's hand on the mouse or Esc stops it.

No test here sends input to this host's display: the gui is a fake that records what it was told,
and the overlay is a list. What is pinned is the order (path, highlight, ripple, input), the
take-over's latency (under 100 ms from the owner's move to the run stopping), and that only the
owner's word clears it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
import time
from pathlib import Path

import pytest

from aura_desktop.actions import inputauto
from aura_desktop.config import InputConfig
from aura_desktop.visible import DWELL_S, GLIDE_S, TakenOver, Takeover, Visible, cursor_path, ease


class FakeGui:
    """pyautogui's shape. `owner_at` is where the owner's hand puts the pointer, and when."""

    FAILSAFE = False
    PAUSE = 0.0

    def __init__(self, log: list, start=(100, 100)) -> None:
        self.log = log
        self.pos = start
        self.owner: tuple[float, tuple[int, int]] | None = None

    def position(self):
        if self.owner is not None and time.monotonic() >= self.owner[0]:
            self.pos, self.owner = self.owner[1], None
        return self.pos

    def size(self):
        return (1920, 1080)

    def moveTo(self, x, y, duration=0.0, **_kw):
        self.pos = (x, y)
        self.log.append(("move", x, y))

    def click(self, x=None, y=None, button="left", clicks=1, **_kw):
        self.log.append(("click", x, y, button, clicks))

    def write(self, text, interval=0.0, **_kw):
        self.log.append(("write", text))

    def press(self, key, **_kw):
        self.log.append(("press", key))

    def hotkey(self, *keys, **_kw):
        self.log.append(("hotkey", keys))

    def scroll(self, clicks, x=None, y=None, **_kw):
        self.log.append(("scroll", clicks, x, y))


def a_visible(log: list, **kw) -> Visible:
    return Visible(overlay=lambda frame: log.append(("overlay", frame["op"], frame)), **kw)


def ops(log: list) -> list[str]:
    """The log as one word per step, consecutive moves folded into one `path`."""
    out: list[str] = []
    for entry in log:
        word = entry[1] if entry[0] == "overlay" else entry[0]
        if word == "move" and out and out[-1] == "move":
            continue
        out.append(word)
    return out


# --- the path -------------------------------------------------------------------------------


def test_cursor_path_eases_in_and_out_and_ends_on_the_target():
    assert ease(0.0) == pytest.approx(0.0, abs=1e-6) and ease(1.0) == pytest.approx(1.0, abs=1e-6)
    assert ease(0.5) == pytest.approx(0.5, abs=1e-3)
    points = cursor_path((0, 0), (1000, 500))
    assert points[-1] == (1000, 500)
    xs = [p[0] for p in points]
    assert xs == sorted(xs), "a path that doubles back is not a glide"
    steps = [b - a for a, b in zip([0] + xs, xs)]
    middle = steps[len(steps) // 2]
    assert steps[0] < middle and steps[-1] < middle, "slow at both ends, fast in the middle"
    assert len(points) >= 10, "a glide, not a jump"
    assert cursor_path((5, 5), (5, 5))[-1] == (5, 5)


def test_cursor_click_is_path_then_highlight_then_ripple_then_the_input():
    log: list = []
    gui = FakeGui(log)
    a_visible(log).click(gui, 600, 400, button="left", clicks=1, box=[580, 390, 40, 20])
    assert ops(log) == ["path", "move", "highlight", "ripple", "click", "hide"]
    highlight = next(e[2] for e in log if e[:2] == ("overlay", "highlight"))
    assert highlight["box"] == [580, 390, 40, 20], "the target's own box when one is given"
    assert log[-2] == ("click", 600, 400, "left", 1)
    moves = [e for e in log if e[0] == "move"]
    assert moves[-1] == ("move", 600, 400) and len(moves) > 5


def test_cursor_highlight_without_a_box_is_a_touch_target_around_the_point():
    log: list = []
    a_visible(log).move(FakeGui(log), 300, 300, box="not a box")
    box = next(e[2] for e in log if e[:2] == ("overlay", "highlight"))["box"]
    assert box[2] == box[3] and box[0] < 300 < box[0] + box[2] and box[1] < 300 < box[1] + box[3]
    assert "ripple" not in ops(log), "a move does not click"


def test_overlay_typing_shows_a_caret_and_scrolling_shows_its_direction():
    log: list = []
    gui = FakeGui(log)
    visible = a_visible(log)
    visible.type(gui, "hi", interval=0.0, press_enter=True)
    assert ops(log) == ["caret", "write", "write", "press", "hide"]
    assert [e[1] for e in log if e[0] == "write"] == ["h", "i"]
    log.clear()
    visible.scroll(gui, -3, 200, 200)
    assert ops(log) == ["path", "move", "highlight", "scroll", "scroll", "hide"]
    badge = next(e[2] for e in log if e[:2] == ("overlay", "scroll"))
    assert badge["direction"] == "down"
    log.clear()
    visible.press(gui, ["ctrl", "l"], times=1)
    assert ops(log) == ["key", "hotkey", "hide"]


# --- the take-over --------------------------------------------------------------------------


def test_takeover_by_the_owners_pointer_stops_the_run_within_100ms():
    log: list = []
    gui = FakeGui(log)
    moved_at = time.monotonic() + GLIDE_S / 3
    gui.owner = (moved_at, (5, 5))
    visible = a_visible(log)
    with pytest.raises(TakenOver):
        visible.click(gui, 1500, 900, button="left", clicks=1)
    stopped = time.monotonic() - moved_at
    assert stopped < 0.1, f"took {stopped * 1000:.0f} ms"
    assert not any(e[0] == "click" for e in log), "the click never fires"
    assert visible.takeover.paused and visible.takeover.reason == "pointer"
    assert log[-1][:2] == ("overlay", "hide")


def test_takeover_by_esc_from_the_shell_stops_typing_within_100ms():
    log: list = []
    gui = FakeGui(log)
    visible = a_visible(log)
    pressed: dict[str, float] = {}

    def owner_presses_esc():
        time.sleep(DWELL_S + 0.05)  # after the caret badge, mid-text
        pressed["at"] = time.monotonic()
        visible.owner_escape()

    threading.Thread(target=owner_presses_esc).start()
    with pytest.raises(TakenOver):
        visible.type(gui, "x" * 400, interval=0.005, press_enter=False)
    assert time.monotonic() - pressed["at"] < 0.1
    typed = sum(1 for e in log if e[0] == "write")
    assert 0 < typed < 400


def test_takeover_ignores_the_agents_own_moves_and_its_own_escape():
    log: list = []
    gui = FakeGui(log)
    visible = a_visible(log)
    visible.click(gui, 900, 100, button="left", clicks=2)
    visible.press(gui, ["escape"], times=1)
    assert not visible.owner_escape(), "the Esc this agent just pressed is not the owner's"
    assert not visible.takeover.paused
    later = Visible(overlay=lambda frame: None, clock=lambda: time.monotonic() + 5)
    assert later.owner_escape() and later.takeover.paused


def test_takeover_counts_input_since_the_action_began_not_the_approval_click():
    """The owner clicked Allow a moment ago: idle is small but older than the action."""
    log: list = []
    now = [100.0]
    idle = [0.4]

    def sleep(s):
        now[0] += s

    visible = a_visible(log, idle=lambda: idle[0], clock=lambda: now[0], sleep=sleep)
    gui = FakeGui(log)
    visible.move(gui, 120, 120)
    assert not visible.takeover.paused

    def tick_with_a_keystroke(s):
        now[0] += s
        idle[0] = 0.0  # somebody typed just now, and it was not this agent

    visible.sleep = tick_with_a_keystroke
    with pytest.raises(TakenOver):
        visible.press(gui, ["enter"], times=1)
    assert visible.takeover.reason == "input"
    assert ("press", "enter") not in log


def test_takeover_holds_until_the_owner_resumes_and_the_actions_say_so(ctx, monkeypatch):
    log: list = []
    monkeypatch.setattr(inputauto, "_pyautogui", lambda: FakeGui(log))
    ctx.config = dataclasses.replace(ctx.config, input_automation=InputConfig(enabled=True))
    told: list = []
    takeover = Takeover()
    takeover.listen(lambda paused, reason: told.append((paused, reason)))
    ctx.visible = Visible(overlay=lambda frame: log.append(("overlay", frame["op"], frame)),
                          takeover=takeover)
    assert inputauto.Click().available(ctx)
    takeover.trip("escape")
    assert not takeover.trip("pointer"), "a second trip is not news"
    for action in (inputauto.Click(), inputauto.TypeText(), inputauto.Screenshot()):
        assert not action.available(ctx)
        assert "paused" in (action.unavailable_reason(ctx) or "")
    with pytest.raises(TakenOver):
        ctx.visible.click(FakeGui(log), 10, 10, button="left", clicks=1)
    takeover.resume()
    assert told == [(True, "escape"), (False, "")]
    result = inputauto.Click().run(ctx, {"x": 50, "y": 60, "box": [40, 50, 20, 20]})
    assert result.ok
    assert ops(log)[-4:] == ["highlight", "ripple", "click", "hide"]


def test_takeover_mid_action_is_reported_as_a_denial_not_a_crash(ctx, monkeypatch):
    log: list = []
    gui = FakeGui(log)
    gui.owner = (time.monotonic() + GLIDE_S / 3, (1, 1))  # mid-glide, after the run began
    monkeypatch.setattr(inputauto, "_pyautogui", lambda: gui)
    ctx.config = dataclasses.replace(ctx.config, input_automation=InputConfig(enabled=True))
    ctx.visible = Visible()
    result = inputauto.Click().run(ctx, {"x": 1000, "y": 500})
    assert not result.ok and result.status.value == "denied"
    assert "took over" in (result.error or "")


def test_overlay_hides_and_the_activity_says_paused_when_the_owner_takes_over():
    log: list = []
    said: list[str] = []
    visible = a_visible(log, report=said.append)
    visible.move(FakeGui(log), 150, 150)
    assert said == ["moving", "idle"]
    visible.owner_escape()
    assert said[-1] == "paused" and log[-1][:2] == ("overlay", "hide")
    visible.takeover.resume()
    assert said[-1] == "idle"


# --- the shell's side of the socket ----------------------------------------------------------


async def _shell(server):
    from aura_desktop.ipc import IpcServer  # noqa: F401 - the type the server is

    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write((json.dumps({"token": server.token}) + "\n").encode())
    await writer.drain()
    await asyncio.wait_for(reader.readline(), 2)  # the status replay
    return reader, writer


@pytest.mark.asyncio
async def test_takeover_and_resume_frames_from_the_shell_reach_the_latch_and_overlay_frames_go_out():
    from aura_desktop.ipc import IpcServer

    server = IpcServer()
    await server.start()
    seen: list[str] = []
    server.on_takeover = lambda: seen.append("takeover")
    server.on_resume = lambda: seen.append("resume")
    reader, writer = await _shell(server)
    try:
        for kind in ("takeover", "resume", "answer", "nonsense"):
            writer.write((json.dumps({"type": kind}) + "\n").encode())
        await writer.drain()
        for _ in range(50):
            if len(seen) == 2:
                break
            await asyncio.sleep(0.01)
        assert seen == ["takeover", "resume"]
        await server.overlay({"op": "ripple", "at": [1, 2]})
        frame = json.loads(await asyncio.wait_for(reader.readline(), 2))
        assert frame == {"type": "overlay", "op": "ripple", "at": [1, 2]}
        await server.handoff("sess_42")
        frame = json.loads(await asyncio.wait_for(reader.readline(), 2))
        assert frame == {"type": "handoff", "session": "sess_42"}
    finally:
        writer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_handoff_frame_from_the_house_opens_only_a_well_formed_session(config):
    from aura_desktop.channel import DeviceChannel

    channel = DeviceChannel(config, registry=None)
    opened: list[str] = []
    channel.on_handoff = opened.append
    contract = json.loads((Path(__file__).resolve().parents[2]
                           / "tests/contracts/handoff-session.json").read_text())
    for session_id in contract["valid"] + contract["invalid"]:
        await channel._handle_frame({"type": "jarvis_handoff", "session_id": session_id})
    await channel._handle_frame({"type": "jarvis_handoff", "session_id": "ok",
                                 "url": "http://evil.invalid/"})
    assert opened == contract["valid"] + ["ok"], "the session id only: never a URL from the frame"
