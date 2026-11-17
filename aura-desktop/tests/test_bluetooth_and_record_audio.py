"""set_bluetooth (NOTIFY) and record_audio (CONFIRM): the phone's tiers, per-OS argv, no shell.

``_run`` and ``which`` are canned, so no test touches a real radio, microphone or ffmpeg.
Not covered: whether bluetoothctl/blueutil/WinRT actually flip the radio, or ffmpeg's
pulse/avfoundation/dshow inputs hear anything; those are the OS's.
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from aura_desktop.actions import camera, system
from aura_desktop.actions.base import Status
from aura_desktop.actions.builtins import TIER_TABLE
from aura_desktop.policy import ActionTier


def _pretend(monkeypatch, ctx, module, os_name, tools, list_out="", code=0, err=""):
    """Pretend to be ``os_name`` with ``tools`` on PATH; ffmpeg writes a few bytes to its output."""
    monkeypatch.setattr(platform, "system", lambda: os_name)
    monkeypatch.setattr(ctx, "which", lambda *names: next((n for n in names if n in tools), None))
    seen: list[list[str]] = []

    def run(argv, timeout=8.0):
        seen.append(argv)
        if "-list_devices" in argv:
            return 1, "", list_out
        if code == 0 and module is camera:
            Path(argv[-1]).write_bytes(b"\x00\x00\x00\x18ftypM4A ")
        return code, "", err

    monkeypatch.setattr(module, "_run", run)
    return seen


def test_set_bluetooth_and_record_audio_tiers_match_the_phone():
    assert TIER_TABLE["set_bluetooth"] == ActionTier.NOTIFY == system.SetBluetooth().tier
    assert TIER_TABLE["record_audio"] == ActionTier.CONFIRM == camera.RecordAudio().tier


@pytest.mark.parametrize("os_name, tool, on_argv, off_argv", [
    ("Linux", "bluetoothctl", ["bluetoothctl", "power", "on"], ["bluetoothctl", "power", "off"]),
    ("Darwin", "blueutil", ["blueutil", "--power", "1"], ["blueutil", "--power", "0"]),
])
def test_set_bluetooth_uses_the_platform_tool(ctx, monkeypatch, os_name, tool, on_argv, off_argv):
    seen = _pretend(monkeypatch, ctx, system, os_name, {tool})
    action = system.SetBluetooth()
    assert action.available(ctx)
    on = action.run(ctx, {"on": True})
    off = action.run(ctx, {"on": False})
    assert on.ok and on.data["on"] is True and off.ok and off.data["on"] is False
    assert seen == [on_argv, off_argv]


def test_set_bluetooth_on_windows_asks_winrt_for_the_radio_state(ctx, monkeypatch):
    seen = _pretend(monkeypatch, ctx, system, "Windows", {"powershell"})
    assert system.SetBluetooth().run(ctx, {"on": False}).ok
    argv = seen[0]
    assert argv[:3] == ["powershell", "-NoProfile", "-Command"]
    assert "Windows.Devices.Radios.Radio" in argv[3] and "SetStateAsync('Off')" in argv[3]


@pytest.mark.parametrize("bad", [None, "maybe", 1, "on; reboot"])
def test_set_bluetooth_refuses_a_state_that_is_not_a_bool(ctx, monkeypatch, bad):
    seen = _pretend(monkeypatch, ctx, system, "Linux", {"bluetoothctl"})
    assert not system.SetBluetooth().run(ctx, {"on": bad}).ok
    assert seen == []


def test_set_bluetooth_without_a_tool_is_unsupported_with_a_hint(ctx, monkeypatch):
    seen = _pretend(monkeypatch, ctx, system, "Darwin", set())
    action = system.SetBluetooth()
    assert not action.available(ctx)
    assert "blueutil" in (action.unavailable_reason(ctx) or "")
    assert action.run(ctx, {"on": True}).status == Status.UNSUPPORTED
    assert seen == []


def test_set_bluetooth_reports_the_tool_failing(ctx, monkeypatch):
    _pretend(monkeypatch, ctx, system, "Linux", {"bluetoothctl"}, code=1, err="No default controller available")
    result = system.SetBluetooth().run(ctx, {"on": True})
    assert not result.ok and "No default controller" in (result.error or "")


def test_record_audio_on_linux_records_pulse_into_the_workspace(ctx, monkeypatch, workspace):
    seen = _pretend(monkeypatch, ctx, camera, "Linux", {"ffmpeg"})
    result = camera.RecordAudio().run(ctx, {"seconds": 5, "path": "memos/standup"})
    assert result.ok, result.error
    argv = seen[0]
    assert argv[argv.index("-f") + 1] == "pulse"
    assert argv[argv.index("-t") + 1] == "5"
    dest = Path(argv[-1])
    assert dest.name == "standup.m4a" and dest.parent.name == "memos"
    assert dest.resolve().is_relative_to(Path(workspace).resolve())
    assert result.data["path"] == "memos/standup.m4a" and result.data["seconds"] == 5


def test_record_audio_defaults_to_the_recordings_folder(ctx, monkeypatch):
    seen = _pretend(monkeypatch, ctx, camera, "Darwin", {"ffmpeg"})
    result = camera.RecordAudio().run(ctx, {"seconds": "3"})
    assert result.ok, result.error
    argv = seen[0]
    assert argv[argv.index("-f") + 1] == "avfoundation" and argv[argv.index("-i") + 1] == ":0"
    assert result.data["path"].startswith("recordings/") and result.data["path"].endswith(".m4a")


def test_record_audio_on_windows_names_the_first_dshow_microphone(ctx, monkeypatch):
    listing = (
        '[dshow @ 0] "Integrated Camera" (video)\n'
        '[dshow @ 0] "Microphone (Realtek)" (audio)\n'
        '[dshow @ 0]   Alternative name "@device_cm_x"\n'
    )
    seen = _pretend(monkeypatch, ctx, camera, "Windows", {"ffmpeg"}, listing)
    assert camera.RecordAudio().run(ctx, {"seconds": 1}).ok
    argv = seen[-1]
    assert argv[argv.index("-i") + 1] == "audio=Microphone (Realtek)"


def test_record_audio_on_windows_reads_the_old_dshow_listing(ctx, monkeypatch):
    listing = (
        "[dshow @ 0] DirectShow video devices\n"
        '[dshow @ 0]  "Integrated Camera"\n'
        "[dshow @ 0] DirectShow audio devices\n"
        '[dshow @ 0]  "Line In"\n'
        '[dshow @ 0]     Alternative name "@device_cm_y"\n'
    )
    seen = _pretend(monkeypatch, ctx, camera, "Windows", {"ffmpeg"}, listing)
    assert camera.RecordAudio().run(ctx, {"seconds": 1}).ok
    assert "audio=Line In" in seen[-1]


def test_record_audio_on_windows_with_no_microphone_fails(ctx, monkeypatch):
    seen = _pretend(monkeypatch, ctx, camera, "Windows", {"ffmpeg"}, '[dshow @ 0] "Cam" (video)\n')
    result = camera.RecordAudio().run(ctx, {"seconds": 1})
    assert not result.ok and "microphone" in (result.error or "")
    assert len(seen) == 1  # the listing only


@pytest.mark.parametrize("params", [
    {}, {"seconds": 0}, {"seconds": 301}, {"seconds": True}, {"seconds": "5; rm -rf ~"},
    {"seconds": 5, "path": "../../etc/evil"}, {"seconds": 5, "path": "/tmp/evil"},
    {"seconds": 5, "path": "~/evil"},
])
def test_record_audio_refuses_bad_seconds_and_paths_outside_the_workspace(ctx, monkeypatch, params):
    seen = _pretend(monkeypatch, ctx, camera, "Linux", {"ffmpeg"})
    assert not camera.RecordAudio().run(ctx, params).ok
    assert seen == []


def test_record_audio_without_ffmpeg_is_unsupported(ctx, monkeypatch):
    seen = _pretend(monkeypatch, ctx, camera, "Linux", set())
    action = camera.RecordAudio()
    assert not action.available(ctx)
    assert "ffmpeg" in (action.unavailable_reason(ctx) or "")
    assert action.run(ctx, {"seconds": 5}).status == Status.UNSUPPORTED
    assert seen == []


def test_record_audio_reports_an_ffmpeg_failure(ctx, monkeypatch):
    _pretend(monkeypatch, ctx, camera, "Linux", {"ffmpeg"}, code=1, err="Connection refused")
    result = camera.RecordAudio().run(ctx, {"seconds": 2})
    assert not result.ok and "Connection refused" in (result.error or "")


def test_record_audio_gives_ffmpeg_and_the_action_time_beyond_the_recording(ctx, monkeypatch):
    seen_timeouts: list[float] = []
    _pretend(monkeypatch, ctx, camera, "Linux", {"ffmpeg"})
    inner = camera._run

    def run(argv, timeout=8.0):
        seen_timeouts.append(timeout)
        return inner(argv, timeout)

    monkeypatch.setattr(camera, "_run", run)
    assert camera.RecordAudio().run(ctx, {"seconds": 300}).ok
    assert seen_timeouts[0] > 300
    assert camera.RecordAudio().timeout_s > seen_timeouts[0]
