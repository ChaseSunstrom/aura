"""take_photo: one frame from the webcam through ffmpeg, asked about every time.

``_run`` and ``which`` are canned, so no test touches a real camera or ffmpeg.
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from aura_desktop.actions import camera
from aura_desktop.actions.base import Status
from aura_desktop.actions.builtins import TIER_TABLE
from aura_desktop.policy import ActionTier


def _fake(monkeypatch, ctx, system, tools, list_out=""):
    """Pretend to be ``system`` with ``tools`` on PATH; ffmpeg writes a tiny JPEG."""
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(ctx, "which", lambda *names: next((n for n in names if n in tools), None))
    seen: list[list[str]] = []

    def run(argv, timeout=8.0):
        seen.append(argv)
        if "-list_devices" in argv:
            return 1, "", list_out
        try:
            from PIL import Image
        except ImportError:  # CI's desktop job has no Pillow; the action then returns the path only
            with open(argv[-1], "wb") as fh:
                fh.write(b"\xff\xd8\xff\xe0" + bytes(16) + b"\xff\xd9")
            return 0, "", ""
        Image.new("RGB", (64, 48), "red").save(argv[-1], format="JPEG")
        return 0, "", ""

    monkeypatch.setattr(camera, "_run", run)
    return seen


def test_take_photo_is_tier_confirm_like_the_phone():
    assert TIER_TABLE["take_photo"] == ActionTier.CONFIRM
    assert camera.TakePhoto().tier == ActionTier.CONFIRM


def test_take_photo_on_linux_reads_v4l2_into_the_workspace(ctx, monkeypatch, workspace):
    seen = _fake(monkeypatch, ctx, "Linux", {"ffmpeg"})
    result = camera.TakePhoto().run(ctx, {"camera": 2, "filename": "desk"})
    assert result.ok, result.error
    argv = seen[0]
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-f") + 1] == "v4l2"
    assert argv[argv.index("-i") + 1] == "/dev/video2"
    dest = Path(argv[-1])
    assert dest.name == "desk.jpg"
    assert dest.resolve().is_relative_to(Path(workspace).resolve())
    assert result.data["path"].endswith("desk.jpg")
    try:
        import PIL  # noqa: F401
    except ImportError:  # no Pillow: the path stands and the reply says why there is no picture
        assert "inline_unavailable" in result.data
    else:
        assert result.data["image_jpeg_base64"] and result.data["image_size"] == [64, 48]
    assert result.data["_untrusted"] is True


def test_take_photo_on_macos_uses_avfoundation(ctx, monkeypatch):
    seen = _fake(monkeypatch, ctx, "Darwin", {"ffmpeg"})
    assert camera.TakePhoto().run(ctx, {"camera": 1}).ok
    argv = seen[0]
    assert argv[argv.index("-f") + 1] == "avfoundation"
    assert argv[argv.index("-i") + 1] == "1"


def test_take_photo_on_windows_names_the_dshow_device(ctx, monkeypatch):
    listing = (
        '[dshow @ 0] "Integrated Camera" (video)\n'
        '[dshow @ 0]   Alternative name "@device_pnp_x"\n'
        '[dshow @ 0] "Microphone (Realtek)" (audio)\n'
        '[dshow @ 0] "OBS Virtual Camera" (video)\n'
    )
    seen = _fake(monkeypatch, ctx, "Windows", {"ffmpeg"}, listing)
    assert camera.TakePhoto().run(ctx, {"camera": 1}).ok
    argv = seen[-1]
    assert argv[argv.index("-f") + 1] == "dshow"
    assert argv[argv.index("-i") + 1] == "video=OBS Virtual Camera"
    # An index past the cameras present is an error, not a guess.
    assert not camera.TakePhoto().run(ctx, {"camera": 5}).ok


@pytest.mark.parametrize("bad", [-1, 10, "0; rm -rf ~", "../x", True, 1.5, None, [0]])
def test_take_photo_refuses_a_camera_that_is_not_an_index(ctx, monkeypatch, bad):
    seen = _fake(monkeypatch, ctx, "Linux", {"ffmpeg"})
    params = {"camera": bad} if bad is not None else {"camera": None}
    result = camera.TakePhoto().run(ctx, params)
    if bad is None:  # omitted/null means the first camera
        assert result.ok
        return
    assert not result.ok and seen == []


def test_take_photo_keeps_the_file_inside_the_workspace(ctx, monkeypatch):
    seen = _fake(monkeypatch, ctx, "Linux", {"ffmpeg"})
    for name in ("../../etc/evil", "/tmp/evil"):
        assert not camera.TakePhoto().run(ctx, {"filename": name}).ok
    assert seen == []


def test_take_photo_without_ffmpeg_is_unsupported(ctx, monkeypatch):
    seen = _fake(monkeypatch, ctx, "Linux", set())
    action = camera.TakePhoto()
    assert not action.available(ctx)
    assert "ffmpeg" in (action.unavailable_reason(ctx) or "")
    assert action.run(ctx, {}).status == Status.UNSUPPORTED
    assert seen == []


def test_take_photo_reports_an_ffmpeg_failure(ctx, monkeypatch):
    _fake(monkeypatch, ctx, "Linux", {"ffmpeg"})
    monkeypatch.setattr(camera, "_run", lambda argv, timeout=8.0: (1, "", "No such device"))
    result = camera.TakePhoto().run(ctx, {})
    assert not result.ok and "No such device" in (result.error or "")
