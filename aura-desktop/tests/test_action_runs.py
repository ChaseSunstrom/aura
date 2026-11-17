"""Every built-in desktop action is run once by a test (T-1418).

``all_actions()`` is the table the PC advertises. Each id in it needs a row in
``ROWS`` below: the params that make it run on a fake machine, and the status it
must return there. A new action with no row fails the walk and names its id; a
row for an action that was removed fails too. Before this file, Sleep shipped a
timeout bug for a while with no test that ran it (T-1375), because nothing forced
an action to have one.

The walk runs on a fake Linux machine with no tools on PATH, no GUI module, no
clipboard module, no browser and no network. Anything that would start a process,
open a socket, or import a real backend is recorded and refused, and the walk
fails if one was reached.

The walk follows the dispatcher (``ActionRegistry``): it asks ``available(ctx)``
first. When that is false the dispatcher answers unsupported with
``unavailable_reason(ctx)`` and never calls ``run()``, so the walk checks the reason
is a non-empty sentence and that no backend was reached, and it does not call
``run()``. When it is true the walk calls ``run()`` and checks the status in the
row. The walk does not prove what ``run()`` answers when it is called on an
unavailable action; test_actions.py pins that (for example
``test_lock_without_a_locker_runs_nothing_and_fails``).

Not covered: what each platform branch does (test_actions.py pins the argv per
platform); whether a real backend exists or works; what the answer is for a
backend that is present. The three behaviour tests at the bottom are the only
places a backend is present, and they fake it.
"""

from __future__ import annotations

import dataclasses
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from typing import Any

import pytest

from aura_desktop.actions import apps, clipboard, inputauto, netinfo
from aura_desktop.actions.base import ActionResult, Status
from aura_desktop.actions.builtins import all_actions
from aura_desktop.config import InputConfig


class RealBackendReached(Exception):
    """Raised by the guard when an action tries a real process, socket or device.

    Actions catch broad ``Exception`` in places (``open_url``, ``move_mouse``), so the
    test does not rely on this propagating: the guard also records the attempt, and the
    walk asserts the record is empty.
    """


#: Each row: id -> (params, status the action must return on the fake machine when
#: available(ctx) is true). An action that is unavailable there is never run, so its
#: status is not read; its row only supplies params.
#: The walk creates ``note.txt`` in the workspace first, so file rows have a file to read.
ROWS: dict[str, tuple[dict[str, Any], Status]] = {
    "get_system_state": ({}, Status.OK),
    "set_volume": ({"level": 30}, Status.UNSUPPORTED),
    "media_control": ({"command": "play_pause"}, Status.UNSUPPORTED),
    "media_now_playing": ({}, Status.UNSUPPORTED),
    "play_media": ({"source": "https://example.test/track"}, Status.UNSUPPORTED),
    "set_brightness": ({"level": 50}, Status.UNSUPPORTED),
    "toggle_dnd": ({"enabled": True}, Status.UNSUPPORTED),
    "set_wallpaper": ({"path": "note.txt"}, Status.ERROR),
    "set_screen_timeout": ({"seconds": 300}, Status.UNSUPPORTED),
    "set_bluetooth": ({"on": True}, Status.UNSUPPORTED),
    # No notifier is installed, so it writes the agent log and still answers ok (by design).
    "notify": ({"title": "Hello", "message": "there"}, Status.OK),
    "lock_screen": ({}, Status.UNSUPPORTED),
    # The machine is in use (5 s idle), so it refuses to lock: an error, not a denial.
    "lock_if_idle": ({"idle_s": 300}, Status.ERROR),
    "sleep": ({}, Status.UNSUPPORTED),
    "launch_app": ({"app": "notes"}, Status.ERROR),
    "open_url": ({"url": "https://example.test/"}, Status.ERROR),
    "list_windows": ({}, Status.UNSUPPORTED),
    "focus_window": ({"title": "Notes"}, Status.UNSUPPORTED),
    "window_control": ({"command": "minimize", "title": "Notes"}, Status.UNSUPPORTED),
    "read_file": ({"path": "note.txt"}, Status.OK),
    "write_file": ({"path": "out.txt", "content": "hi"}, Status.OK),
    "list_dir": ({}, Status.OK),
    "delete_file": ({"path": "note.txt"}, Status.OK),
    "read_clipboard": ({}, Status.UNSUPPORTED),
    "write_clipboard": ({"content": "hi"}, Status.UNSUPPORTED),
    "save_note": ({"title": "Plan", "body": "x"}, Status.ERROR),
    "find_note": ({"query": "plan"}, Status.ERROR),
    # An IP literal in private space is refused by the SSRF check before any socket.
    "http_request": ({"url": "http://10.0.0.9/"}, Status.DENIED),
    "get_network_info": ({}, Status.OK),
    # The cwd is outside the allowed roots, so the refusal comes before any process starts.
    "run_command": ({"command": "echo hi", "cwd": "/"}, Status.ERROR),
    "type_text": ({"text": "hi"}, Status.UNSUPPORTED),
    "click": ({"x": 1, "y": 1}, Status.UNSUPPORTED),
    "move_mouse": ({"x": 1, "y": 1}, Status.UNSUPPORTED),
    "screenshot": ({}, Status.UNSUPPORTED),
    # Needs no input backend. The fake machine never captured, so there is no folder: zero erased, ok.
    "forget_screenshots": ({"minutes": 5}, Status.OK),
    "key_press": ({"keys": "enter"}, Status.UNSUPPORTED),
    "scroll": ({"clicks": 1}, Status.UNSUPPORTED),
    "take_photo": ({}, Status.UNSUPPORTED),
    "record_audio": ({"seconds": 1}, Status.UNSUPPORTED),
}


@pytest.fixture()
def backends(monkeypatch):
    """Refuse every real process, socket, GUI, clipboard, browser and camera.

    Returns the list of attempts. A refused attempt raises :class:`RealBackendReached`
    and is recorded, so a broad ``except Exception`` in an action cannot hide it.
    """
    reached: list[str] = []

    def refuse(name: str):
        def _refuse(*args: Any, **kwargs: Any) -> None:
            reached.append(name)
            raise RealBackendReached(f"{name} would have run for real")

        return _refuse

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse(f"subprocess.{name}"))
    monkeypatch.setattr(os, "system", refuse("os.system"))
    monkeypatch.setattr(os, "startfile", refuse("os.startfile"), raising=False)
    monkeypatch.setattr(socket.socket, "connect", refuse("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", refuse("socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", refuse("socket.getaddrinfo"))
    # The notes hub is a house server: the walk's answer is that it is down. This is a
    # fake, not a refusal of a real call, so it is not recorded as a reached backend.
    def hub_down(request: Any, *args: Any, **kwargs: Any) -> None:
        raise urllib.error.URLError("the walk has no hub")

    monkeypatch.setattr(urllib.request, "urlopen", hub_down)
    # No tool is on PATH, so every ctx.which() answers None.
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    # A missing module is the honest "not installed"; a None entry makes the import fail.
    for module in ("pyautogui", "pyperclip", "PIL", "PIL.ImageGrab", "cv2", "sounddevice"):
        monkeypatch.setitem(sys.modules, module, None)
    # No browser is registered: webbrowser.open answers False, as on a headless box.
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: False)
    # netinfo imports _run by name, so patching system._run would not reach it: every
    # network command it asks for is "not installed" (exit 127).
    monkeypatch.setattr(netinfo, "_run", lambda argv, timeout=8.0: (127, "", "not installed"))
    return reached


def _walk_ctx(ctx, workspace):
    """The walk's context: input and clipboard both off, nobody idle, one file to read."""
    (workspace / "note.txt").write_text("a note", encoding="utf-8")
    ctx.config = dataclasses.replace(ctx.config, input_automation=InputConfig(enabled=False))
    ctx.idle = lambda: 5.0
    return ctx


def test_every_action_has_a_walking_row():
    ids = {action.id for action in all_actions()}
    missing = sorted(ids - set(ROWS))
    gone = sorted(set(ROWS) - ids)
    assert not missing, f"actions with no row in tests/test_action_runs.py: {missing}"
    assert not gone, f"rows for actions that no longer exist: {gone}"


@pytest.mark.parametrize("action", all_actions(), ids=lambda action: action.id)
def test_every_action_runs_once_and_returns_its_answer(action, ctx, workspace, backends):
    if action.id not in ROWS:
        pytest.fail(f"{action.id}: no row in ROWS, so no test runs it")
    params, want = ROWS[action.id]
    walk = _walk_ctx(ctx, workspace)
    if not action.available(walk):
        # What the dispatcher does: unsupported with the reason, and run() is never called.
        reason = action.unavailable_reason(walk)
        assert isinstance(reason, str) and reason.strip(), (
            f"{action.id} is unavailable but gives no reason"
        )
        assert backends == [], f"{action.id} reached a real backend: {backends}"
        return
    result = action.run(walk, dict(params))
    assert isinstance(result, ActionResult), f"{action.id} returned {type(result).__name__}"
    assert backends == [], f"{action.id} reached a real backend: {backends}"
    assert result.status == want, f"{action.id}: {result.status} ({result.error}), wanted {want}"


# --- behaviour: the three actions no other test runs -------------------------
#
# Each fakes one backend and checks the answer and the argv. The walk above only
# checks the no-backend answer; these are the only tests where a backend is present.


def _clean_linux(monkeypatch, *tools: str) -> None:
    """A Linux machine where exactly ``tools`` are on PATH."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}" if name in tools else None
    )


def _record_run(monkeypatch, module, replies):
    """Replace a module's ``_run`` helper; each call gets the next (code, out, err)."""
    calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return replies.pop(0) if replies else (0, "", "")

    monkeypatch.setattr(module, "_run", fake_run)
    return calls


def test_focus_window_activates_by_title_with_wmctrl(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "wmctrl", "xdotool")
    calls = _record_run(monkeypatch, apps, [(0, "", "")])
    result = apps.FocusWindow().run(ctx, {"title": "Notes"})
    assert result.status == Status.OK
    assert calls == [["wmctrl", "-a", "Notes"]]
    assert backends == []


def test_focus_window_by_id_uses_wmctrl_i(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "wmctrl")
    calls = _record_run(monkeypatch, apps, [(0, "", "")])
    result = apps.FocusWindow().run(ctx, {"window_id": "0x01e00003"})
    assert result.status == Status.OK
    assert calls == [["wmctrl", "-i", "-a", "0x01e00003"]]
    assert backends == []


def test_focus_window_falls_back_to_xdotool_without_wmctrl(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "xdotool")
    calls = _record_run(monkeypatch, apps, [(0, "", "")])
    result = apps.FocusWindow().run(ctx, {"title": "Notes"})
    assert result.status == Status.OK
    assert calls == [["xdotool", "search", "--name", "Notes", "windowactivate"]]
    assert backends == []


def test_focus_window_reports_a_failed_activation(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "wmctrl")
    _record_run(monkeypatch, apps, [(1, "", "no such window")])
    result = apps.FocusWindow().run(ctx, {"title": "Gone"})
    assert result.status == Status.ERROR
    assert result.error == "could not focus that window: no such window"
    assert backends == []


def test_focus_window_refuses_a_malformed_id_before_running(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "wmctrl")
    calls = _record_run(monkeypatch, apps, [])
    result = apps.FocusWindow().run(ctx, {"window_id": "0x1; rm -rf /"})
    assert result.status == Status.ERROR
    assert calls == []
    assert backends == []


def _record_clipboard(monkeypatch, returncode=0, stderr=""):
    """Replace subprocess.run for the clipboard module; returns the (argv, kwargs) calls."""
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    return calls


def test_write_clipboard_pipes_the_text_to_xclip(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "xclip")
    calls = _record_clipboard(monkeypatch)
    result = clipboard.WriteClipboard().run(ctx, {"content": "hi"})
    assert result.status == Status.OK
    assert result.data == {"length": 2, "via": "xclip"}
    argv, kwargs = calls[0]
    assert argv == ["xclip", "-selection", "clipboard"]
    assert kwargs["input"] == "hi"
    assert backends == []


def test_write_clipboard_refuses_text_over_the_limit_before_running(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "xclip")
    calls = _record_clipboard(monkeypatch)
    result = clipboard.WriteClipboard().run(
        ctx, {"content": "x" * (clipboard.MAX_CLIPBOARD_CHARS + 1)}
    )
    assert result.status == Status.ERROR
    assert calls == []
    assert backends == []


def test_write_clipboard_refuses_content_that_is_not_text(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "xclip")
    calls = _record_clipboard(monkeypatch)
    result = clipboard.WriteClipboard().run(ctx, {"content": ["not", "text"]})
    assert result.status == Status.ERROR
    assert calls == []
    assert backends == []


def test_write_clipboard_reports_a_failed_copy(ctx, backends, monkeypatch):
    _clean_linux(monkeypatch, "xclip")
    _record_clipboard(monkeypatch, returncode=1, stderr="Can't open display\n")
    result = clipboard.WriteClipboard().run(ctx, {"content": "hi"})
    assert result.status == Status.ERROR
    assert result.error == "clipboard write failed: Can't open display"
    assert backends == []


class _FakeGui:
    """The bits of pyautogui that move_mouse uses: a 1920x1080 screen and a recorded glide."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.moves: list[tuple[int, int, float]] = []
        self.fail = fail

    def size(self) -> tuple[int, int]:
        return (1920, 1080)

    def moveTo(self, x: int, y: int, duration: float = 0.0) -> None:  # noqa: N802 - pyautogui's name
        if self.fail is not None:
            raise self.fail
        self.moves.append((x, y, duration))


def _enable_gui(ctx, monkeypatch, gui: _FakeGui) -> None:
    ctx.config = dataclasses.replace(ctx.config, input_automation=InputConfig(enabled=True))
    monkeypatch.setattr(inputauto, "_pyautogui", lambda: gui)


def test_move_mouse_glides_to_the_position(ctx, backends, monkeypatch):
    gui = _FakeGui()
    _enable_gui(ctx, monkeypatch, gui)
    result = inputauto.MoveMouse().run(ctx, {"x": 100, "y": 200, "duration_s": 1.5})
    assert result.status == Status.OK
    assert result.data == {"position": [100, 200]}
    assert gui.moves == [(100, 200, 1.5)]
    assert backends == []


def test_move_mouse_clamps_the_glide_to_five_seconds(ctx, backends, monkeypatch):
    gui = _FakeGui()
    _enable_gui(ctx, monkeypatch, gui)
    inputauto.MoveMouse().run(ctx, {"x": 1, "y": 1, "duration_s": 99})
    assert gui.moves == [(1, 1, 5.0)]
    assert backends == []


def test_move_mouse_refuses_a_point_off_screen_without_moving(ctx, backends, monkeypatch):
    gui = _FakeGui()
    _enable_gui(ctx, monkeypatch, gui)
    for x, y in ((1920, 10), (-1, 10), (10, 1080)):
        result = inputauto.MoveMouse().run(ctx, {"x": x, "y": y})
        assert result.status == Status.ERROR, (x, y)
        assert "off screen" in result.error
    assert gui.moves == []
    assert backends == []


def test_move_mouse_reports_a_backend_failure(ctx, backends, monkeypatch):
    gui = _FakeGui(fail=RuntimeError("no X display"))
    _enable_gui(ctx, monkeypatch, gui)
    result = inputauto.MoveMouse().run(ctx, {"x": 5, "y": 5})
    assert result.status == Status.ERROR
    assert result.error == "pointer move failed: no X display"
    assert backends == []
