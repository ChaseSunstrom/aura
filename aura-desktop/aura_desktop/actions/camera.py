"""take_photo: one still frame from a webcam, through ffmpeg.

Tier 3, asked about every single time, like the phone's ``take_photo``: a
camera sees the room and whoever is in it, and a remembered "yes" would let a
model look whenever it liked.

ffmpeg is the one tool that reads a camera on all three systems (v4l2 on
Linux, avfoundation on macOS, dshow on Windows), so it is the only route;
without it the action answers ``unsupported``. The argv is fixed and the
camera is an integer 0-9, never a string from the model: no shell, no device
path the model chose. The frame lands only in the workspace, and comes back as
a path plus the same inline JPEG ``screenshot`` returns. Its pixels are
untrusted data (a sign held up to the lens is text a stranger wrote).

Does not cover: picking a resolution, warming up an auto-exposure camera (the
first frame of some webcams is dark), or a camera another program holds open.

record_audio is the microphone's twin, Tier 3 like the phone's: ffmpeg reads
the default PulseAudio/PipeWire source on Linux, the first avfoundation audio
device on macOS and the first dshow microphone on Windows, for 1-300 seconds,
into an AAC .m4a under the workspace. Not covered: choosing a microphone, a
Linux box with ALSA only (no pulse server), or an ffmpeg built without pulse.
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult
from .inputauto import _inline
from .paths import ScopeError, safe_join
from .system import _run

__all__ = ["RecordAudio", "TakePhoto"]

_HINT = "take_photo needs ffmpeg on PATH (apt install ffmpeg, brew install ffmpeg, winget install ffmpeg)"
_PHOTO_DIR = "photos"
_DSHOW_NEW = re.compile(r'"([^"]+)"\s+\((video|audio)\)')
_DSHOW_OLD = re.compile(r'\]\s+"([^"]+)"\s*$')


def _camera_index(raw: Any) -> int | None:
    """0-9 from an int or a digit string; anything else is None (refused)."""
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
    else:
        return None
    return value if 0 <= value <= 9 else None


def _dshow_devices(listing: str, kind: str = "video") -> list[str]:
    """``kind`` ("video" or "audio") device names from ``ffmpeg -list_devices true -f dshow``.

    New ffmpeg tags each line ``(video)``/``(audio)``; old ffmpeg lists them under a
    "DirectShow video devices" / "DirectShow audio devices" header instead, so both
    shapes are read.
    """
    names = [n for n, k in _DSHOW_NEW.findall(listing) if k == kind]
    if names:
        return names
    section = False
    for line in listing.splitlines():
        if "DirectShow " in line and " devices" in line:
            section = f"DirectShow {kind} devices" in line
        elif section and "Alternative name" not in line:
            match = _DSHOW_OLD.search(line)
            if match:
                names.append(match.group(1))
    return names


class TakePhoto(Action):
    id = "take_photo"
    tier = ActionTier.CONFIRM
    description = "Take a photo with this machine's webcam and save it in the workspace."
    params_schema = {
        "camera": "int (optional): which camera, 0-9, default 0 (the first)",
        "filename": "string (optional): name for the JPEG, default a timestamp",
        "inline": "bool (optional): also return the picture as a JPEG, base64, default true",
        "max_side": "int (optional): longest side of the inline picture, default 1024, at most 2560",
    }
    capability = "camera"
    timeout_s = 30.0

    def available(self, ctx: ActionContext) -> bool:
        return ctx.which("ffmpeg") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return _HINT

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        ffmpeg = ctx.which("ffmpeg")
        if ffmpeg is None:
            return ActionResult.unsupported(_HINT)
        index = _camera_index(params.get("camera"))
        if index is None:
            return ActionResult.failed("camera must be an integer from 0 to 9")

        name = self.str_param(params, "filename") or f"photo-{int(time.time())}.jpg"
        if "/" in name or "\\" in name:
            # A bare name: a sub-folder ffmpeg would not create, or a Windows
            # separator safe_join does not split on, is refused up front.
            return ActionResult.failed("filename must be a plain file name, not a path")
        if not name.lower().endswith((".jpg", ".jpeg")):
            name += ".jpg"
        try:
            base = ctx.scope.resolve(_PHOTO_DIR, allow_root=True)
            if not base.allowed or base.path is None:
                return ActionResult.failed(f"photo directory rejected: {base.reason}")
            base.path.mkdir(parents=True, exist_ok=True)
            destination = safe_join(base.path, name)
        except ScopeError as exc:
            return ActionResult.failed(f"filename rejected: {exc}")
        except OSError as exc:
            return ActionResult.failed(f"could not prepare the photo directory: {exc}")

        system = ctx.system
        if system == "Linux":
            source = ["-f", "v4l2", "-i", f"/dev/video{index}"]
        elif system == "Darwin":
            source = ["-f", "avfoundation", "-framerate", "30", "-i", str(index)]
        elif system == "Windows":
            _, _, listing = _run(
                [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
            )
            cameras = _dshow_devices(listing)
            if index >= len(cameras):
                return ActionResult.failed(
                    f"no camera {index}: this machine has {len(cameras)} ({', '.join(cameras) or 'none'})"
                )
            source = ["-f", "dshow", "-i", f"video={cameras[index]}"]
        else:
            return ActionResult.unsupported(f"take_photo is not supported on {system}")

        argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *source,
                "-frames:v", "1", str(destination)]
        code, _, err = _run(argv, timeout=20.0)
        if code != 0 or not destination.exists():
            return ActionResult.failed(f"the camera did not give a picture: {err.strip() or code}")

        relative = ctx.scope.resolve(str(destination))
        payload: dict[str, Any] = {
            "path": relative.relative if relative.allowed else str(destination),
            "absolute_path": str(destination),
            "bytes": destination.stat().st_size,
            "camera": index,
        }
        if self.bool_param(params, "inline", True):
            try:
                from PIL import Image

                with Image.open(destination) as image:
                    payload.update(
                        _inline(image, max(320, min(self.int_param(params, "max_side", 1024), 2560)))
                    )
            except Exception:  # noqa: BLE001 - no Pillow, or a frame it cannot read: the path still stands
                payload["inline_unavailable"] = "Pillow could not read the frame; fetch the file by path"
        return ActionResult.untrusted(payload)


_AUDIO_HINT = "record_audio needs ffmpeg on PATH (apt install ffmpeg, brew install ffmpeg, winget install ffmpeg)"
_MAX_SECONDS = 300
#: ffmpeg's own budget beyond the recording: opening the device and finishing the file.
_AUDIO_SLACK_S = 15.0


class RecordAudio(Action):
    id = "record_audio"
    tier = ActionTier.CONFIRM  # as on the phone: a microphone hears the room and whoever is in it
    description = "Record from this machine's microphone for a few seconds into an .m4a in the workspace."
    params_schema = {
        "seconds": f"int 1-{_MAX_SECONDS}: how long",
        "path": "string (optional): the file to write in the workspace (default recordings/<time>.m4a)",
    }
    capability = "camera"
    # The listing (Windows), the longest recording and ffmpeg's slack, so the
    # registry's own timeout never cuts a legal recording short.
    timeout_s = _MAX_SECONDS + _AUDIO_SLACK_S + 15.0

    def available(self, ctx: ActionContext) -> bool:
        return ctx.which("ffmpeg") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return _AUDIO_HINT

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        ffmpeg = ctx.which("ffmpeg")
        if ffmpeg is None:
            return ActionResult.unsupported(_AUDIO_HINT)
        seconds = self.int_param(params, "seconds", 0)
        if not 1 <= seconds <= _MAX_SECONDS:
            return ActionResult.failed(f"seconds must be an integer from 1 to {_MAX_SECONDS}")

        raw = self.str_param(params, "path") or f"recordings/rec-{int(time.time())}.m4a"
        if not raw.lower().endswith(".m4a"):
            raw += ".m4a"
        resolved = ctx.scope.resolve(raw)
        if not resolved.allowed or resolved.path is None:
            return ActionResult.failed(f"path rejected: {resolved.reason}")
        destination = resolved.path
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ActionResult.failed(f"could not prepare {resolved.relative}: {exc}")
        # As write_file: creating the parent may have followed a symlink planted
        # after the resolve above.
        if ctx.scope.contains(destination) is None:
            return ActionResult.failed("path escapes the workspace once symlinks are resolved")

        system = ctx.system
        if system == "Linux":
            source = ["-f", "pulse", "-i", "default"]
        elif system == "Darwin":
            source = ["-f", "avfoundation", "-i", ":0"]
        elif system == "Windows":
            _, _, listing = _run(
                [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
            )
            microphones = _dshow_devices(listing, "audio")
            if not microphones:
                return ActionResult.failed("this machine has no microphone ffmpeg can see")
            source = ["-f", "dshow", "-i", f"audio={microphones[0]}"]
        else:
            return ActionResult.unsupported(f"record_audio is not supported on {system}")

        argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *source,
                "-t", str(seconds), "-c:a", "aac", str(destination)]
        code, _, err = _run(argv, timeout=seconds + _AUDIO_SLACK_S)
        if code != 0 or not destination.exists():
            return ActionResult.failed(f"the microphone did not record: {err.strip() or code}")
        return ActionResult.success(
            path=resolved.relative,
            absolute_path=str(destination),
            bytes=destination.stat().st_size,
            seconds=seconds,
        )
