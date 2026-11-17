"""System state, volume, desktop notifications, lock and sleep.

``psutil`` is used when it is importable and the stdlib is used when it is not,
because a self-hosted agent should install and run on a bare Python. Every
per-OS helper degrades to ``unsupported`` with an actionable sentence rather
than raising.
"""

from __future__ import annotations

import getpass
import json
import mimetypes
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import url2pathname

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult
from ..theme import worn_name

try:  # optional
    import psutil
except Exception:  # noqa: BLE001
    psutil = None

__all__ = [
    "GetSystemState", "SetVolume", "MediaControl", "MediaNowPlaying", "PlayMedia",
    "Notify", "LockScreen", "LockIfIdle", "Sleep", "SetBrightness", "ToggleDnd", "SetWallpaper",
    "SetScreenTimeout", "SetBluetooth",
]


def _run(argv: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    """Small helper for the per-OS shell-outs in this module.

    Unlike :class:`~aura_desktop.actions.shell.RunCommand` these are fixed
    argv built from validated parameters — never a string from the model — so
    they are not routed through the denylist.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]}: timed out"
    except OSError as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class GetSystemState(Action):
    id = "get_system_state"
    tier = ActionTier.AUTO
    description = "Report OS, hostname, uptime, CPU, memory, disk and battery for this machine."
    params_schema = {"path": "string (optional): which filesystem to report disk usage for"}
    capability = "system"
    timeout_s = 10.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        data: dict[str, Any] = {
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "device_name": ctx.config.device_name,
            "psutil": psutil is not None,
        }
        try:
            data["user"] = getpass.getuser()
        except Exception:  # noqa: BLE001 - no passwd entry in some containers
            data["user"] = None

        data["cpu"] = self._cpu()
        data["memory"] = self._memory()
        data["disk"] = self._disk(ctx, params)
        battery = self._battery()
        if battery is not None:
            data["battery"] = battery
        uptime = self._uptime()
        if uptime is not None:
            data["uptime_s"] = uptime
        return ActionResult.success(data)

    # --- per-metric, psutil first, stdlib second ---------------------------

    @staticmethod
    def _cpu() -> dict[str, Any]:
        out: dict[str, Any] = {"count": os.cpu_count()}
        if psutil is not None:
            try:
                out["percent"] = psutil.cpu_percent(interval=0.2)
            except Exception:  # noqa: BLE001
                pass
        if hasattr(os, "getloadavg"):
            try:
                one, five, fifteen = os.getloadavg()
                out["load_avg"] = [round(one, 2), round(five, 2), round(fifteen, 2)]
                if "percent" not in out and out["count"]:
                    out["percent"] = round(min(100.0, one / out["count"] * 100), 1)
            except OSError:
                pass
        return out

    @staticmethod
    def _memory() -> dict[str, Any]:
        if psutil is not None:
            try:
                vm = psutil.virtual_memory()
                return {
                    "total_bytes": vm.total,
                    "available_bytes": vm.available,
                    "percent_used": vm.percent,
                }
            except Exception:  # noqa: BLE001
                pass
        # Linux: /proc/meminfo. Everything else: sysconf, when it has the keys.
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                fields = {}
                for line in fh:
                    key, _, rest = line.partition(":")
                    value = rest.strip().split(" ")[0]
                    if value.isdigit():
                        fields[key] = int(value) * 1024
            total = fields.get("MemTotal")
            available = fields.get("MemAvailable", fields.get("MemFree"))
            if total:
                return {
                    "total_bytes": total,
                    "available_bytes": available,
                    "percent_used": (
                        round((total - available) / total * 100, 1) if available else None
                    ),
                }
        except OSError:
            pass
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            return {"total_bytes": total, "available_bytes": None, "percent_used": None}
        except (ValueError, OSError, AttributeError):
            return {"total_bytes": None, "available_bytes": None, "percent_used": None}

    @staticmethod
    def _disk(ctx: ActionContext, params: dict[str, Any]) -> dict[str, Any]:
        raw = Action.str_param(params, "path")
        target = ctx.scope.default_root
        if raw:
            resolved = ctx.scope.resolve(raw, allow_root=True)
            # Disk usage of a path outside the roots leaks nothing dangerous,
            # but keeping every file-shaped parameter inside the scope means
            # there is one rule to remember rather than two.
            if resolved.allowed and resolved.path is not None:
                target = resolved.path
        try:
            usage = shutil.disk_usage(str(target))
        except OSError as exc:
            return {"path": str(target), "error": str(exc)}
        return {
            "path": str(target),
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "percent_used": round((usage.total - usage.free) / usage.total * 100, 1)
            if usage.total
            else None,
        }

    @staticmethod
    def _battery() -> dict[str, Any] | None:
        if psutil is not None and hasattr(psutil, "sensors_battery"):
            try:
                battery = psutil.sensors_battery()
            except Exception:  # noqa: BLE001
                battery = None
            if battery is not None:
                return {
                    "percent": round(battery.percent, 1),
                    "charging": bool(battery.power_plugged),
                    "seconds_left": (
                        battery.secsleft if battery.secsleft and battery.secsleft > 0 else None
                    ),
                }
        # Linux without psutil: sysfs.
        base = "/sys/class/power_supply"
        try:
            names = sorted(n for n in os.listdir(base) if n.startswith("BAT"))
        except OSError:
            return None
        for name in names:
            try:
                with open(f"{base}/{name}/capacity", encoding="utf-8") as fh:
                    percent = int(fh.read().strip())
                with open(f"{base}/{name}/status", encoding="utf-8") as fh:
                    status = fh.read().strip()
            except (OSError, ValueError):
                continue
            return {"percent": percent, "charging": status.lower() == "charging", "status": status}
        return None

    @staticmethod
    def _uptime() -> float | None:
        if psutil is not None:
            try:
                return round(time.time() - psutil.boot_time(), 1)
            except Exception:  # noqa: BLE001
                pass
        try:
            with open("/proc/uptime", encoding="utf-8") as fh:
                return round(float(fh.read().split()[0]), 1)
        except (OSError, ValueError, IndexError):
            return None


class SetVolume(Action):
    id = "set_volume"
    tier = ActionTier.AUTO  # trivially reversible; the user can hear it happen
    description = "Set this machine's output volume (0-100), or mute/unmute it."
    params_schema = {
        "level": "int 0-100: the new output volume",
        "mute": "bool (optional): mute or unmute instead of setting a level",
    }
    capability = "system"
    # Above the longest per-OS deadline below (the Windows mixer nudge waits up
    # to 20s). An action whose own subprocess outlives the dispatcher's cap gets
    # reported as a timeout while the work carries on in an orphaned thread.
    timeout_s = 25.0

    def available(self, ctx: ActionContext) -> bool:
        system = ctx.system
        if system == "Darwin" or system == "Windows":
            return True
        return ctx.which("pactl", "wpctl", "amixer") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return (
            "no supported mixer found; install pulseaudio-utils (pactl), "
            "wireplumber (wpctl) or alsa-utils (amixer)"
        )

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        mute = params.get("mute")
        if mute is not None and "level" not in params:
            return self._set_mute(ctx, self.bool_param(params, "mute"))
        if "level" not in params:
            return ActionResult.failed("level (0-100) or mute is required")
        level = max(0, min(100, self.int_param(params, "level", -1)))
        if self.int_param(params, "level", -1) < 0:
            return ActionResult.failed("level must be an integer between 0 and 100")

        system = ctx.system
        if system == "Darwin":
            code, _, err = _run(["osascript", "-e", f"set volume output volume {level}"])
        elif system == "Windows":
            code, _, err = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    # No third-party module: nudge the volume with media keys.
                    "$w = New-Object -ComObject WScript.Shell;"
                    "1..50 | ForEach-Object { $w.SendKeys([char]174) };"
                    f"1..{max(0, level) // 2} | ForEach-Object {{ $w.SendKeys([char]175) }}",
                ],
                timeout=20.0,
            )
        elif ctx.which("pactl"):
            code, _, err = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
        elif ctx.which("wpctl"):
            code, _, err = _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level / 100:.2f}"])
        elif ctx.which("amixer"):
            code, _, err = _run(["amixer", "-q", "sset", "Master", f"{level}%"])
        else:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "no mixer")

        if code != 0:
            return ActionResult.failed(f"could not set the volume: {err.strip() or code}")
        return ActionResult.success(level=level)

    def _set_mute(self, ctx: ActionContext, mute: bool) -> ActionResult:
        system = ctx.system
        if system == "Darwin":
            flag = "true" if mute else "false"
            code, _, err = _run(["osascript", "-e", f"set volume output muted {flag}"])
        elif ctx.which("pactl"):
            code, _, err = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
        elif ctx.which("wpctl"):
            code, _, err = _run(
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"]
            )
        elif ctx.which("amixer"):
            code, _, err = _run(["amixer", "-q", "sset", "Master", "mute" if mute else "unmute"])
        else:
            return ActionResult.unsupported("muting is not supported on this machine")
        if code != 0:
            return ActionResult.failed(f"could not change mute: {err.strip() or code}")
        return ActionResult.success(muted=mute)


#: command -> playerctl verb (MPRIS), and the Windows virtual key it maps to.
#: Windows has one play/pause toggle key and no play or pause of its own, so a
#: "pause" there is refused rather than sent as a toggle that could start music.
_MEDIA = {
    "play_pause": ("play-pause", 179),
    "play": ("play", None),
    "pause": ("pause", None),
    "next": ("next", 176),
    "previous": ("previous", 177),
    "stop": ("stop", 178),
}


class MediaControl(Action):
    id = "media_control"
    tier = ActionTier.AUTO  # as set_volume: reversible, and the user hears it happen
    description = "Play, pause or skip whatever is playing on this machine."
    params_schema = {"command": "string: " + " | ".join(_MEDIA)}
    capability = "system"
    timeout_s = 12.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system == "Windows":
            return ctx.which("powershell") is not None
        return ctx.which("playerctl") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if ctx.system == "Linux":
            return "install playerctl to control media players over MPRIS"
        return "media control is not supported on this machine"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        command = self.str_param(params, "command") or ""
        if command not in _MEDIA:
            return ActionResult.failed("command must be one of " + ", ".join(_MEDIA))
        verb, vk = _MEDIA[command]
        if ctx.system == "Windows":
            if vk is None:
                return ActionResult.unsupported("Windows has only a play/pause toggle; use play_pause")
            argv = ["powershell", "-NoProfile", "-Command",
                    f"(New-Object -ComObject WScript.Shell).SendKeys([char]{vk})"]
        elif ctx.which("playerctl"):
            argv = ["playerctl", verb]
        else:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")
        code, _, err = _run(argv)
        if code != 0:
            # playerctl exits 1 with "No players found" when nothing is playing.
            return ActionResult.failed(f"could not {verb}: {err.strip() or code}")
        return ActionResult.success(command=command)


#: Fixed scripts, no parameters interpolated: each prints status, app, title,
#: artist, album (tab-separated, or JSON on Windows) or nothing when idle.
_PLAYERCTL_FORMAT = "{{status}}\t{{playerName}}\t{{title}}\t{{artist}}\t{{album}}"
_SMTC_SCRIPT = (
    "Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
    "$as = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {"
    " $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and"
    " $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1;"
    "function Await($op, $t) { $k = $as.MakeGenericMethod($t).Invoke($null, @($op));"
    " $k.Wait(-1) | Out-Null; $k.Result };"
    "$m = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,"
    " Windows.Media.Control, ContentType = WindowsRuntime];"
    "$s = (Await ($m::RequestAsync()) $m).GetCurrentSession();"
    "if ($s -eq $null) { exit 0 };"
    "$p = Await ($s.TryGetMediaPropertiesAsync())"
    " ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,"
    " Windows.Media.Control, ContentType = WindowsRuntime]);"
    "[pscustomobject]@{status = \"$($s.GetPlaybackInfo().PlaybackStatus)\";"
    " app = $s.SourceAppUserModelId; title = $p.Title; artist = $p.Artist;"
    " album = $p.AlbumTitle} | ConvertTo-Json -Compress"
)
#: Spotify first: it is the one people name. A missing app makes osascript exit
#: non-zero at compile time, which just moves on to the next.
_MAC_PLAYERS = ("Spotify", "Music")


def _mac_now_playing_script(app: str) -> str:
    return (
        f'if application "{app}" is running then tell application "{app}" to return '
        "(player state as text) & tab & (name of current track) & tab & "
        "(artist of current track) & tab & (album of current track)"
    )


def describe_now_playing(title: str | None, artist: str | None, app: str | None, playing: bool) -> str:
    """The answer as one sentence; the phone's ``MediaNowPlaying.describe``."""
    what = " — ".join(x for x in (title, artist) if x)
    who = f" in {app}" if app else ""
    if not what and not app:
        return "nothing is playing"
    if not what:
        return ("something is playing" if playing else "something is paused") + who
    return ("playing " if playing else "paused: ") + what + who


class MediaNowPlaying(Action):
    id = "media_now_playing"
    tier = ActionTier.AUTO  # read-only, as on the phone
    description = (
        "What is playing on this machine right now: title, artist, album, the app, "
        "and whether it is playing or paused."
    )
    params_schema: dict[str, str] = {}
    capability = "system"
    timeout_s = 25.0  # above the 15s PowerShell deadline below

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system == "Windows":
            return ctx.which("powershell") is not None
        if ctx.system == "Darwin":
            return ctx.which("osascript") is not None
        return ctx.which("playerctl") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if ctx.system == "Linux":
            return "install playerctl to read media players over MPRIS"
        return "now-playing is not supported on this machine"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        status = app = title = artist = album = None
        if ctx.system == "Windows":
            code, out, err = _run(["powershell", "-NoProfile", "-Command", _SMTC_SCRIPT], timeout=15.0)
            if code != 0:
                return ActionResult.failed(f"could not read the media session: {err.strip() or code}")
            if out.strip():
                try:
                    info = json.loads(out)
                except ValueError:
                    return ActionResult.failed("unreadable media session output")
                status, app, title, artist, album = (
                    info.get(k) for k in ("status", "app", "title", "artist", "album")
                )
        elif ctx.system == "Darwin":
            for player in _MAC_PLAYERS:
                code, out, _ = _run(["osascript", "-e", _mac_now_playing_script(player)])
                fields = out.rstrip("\n").split("\t")
                if code == 0 and len(fields) == 4:
                    status, title, artist, album = fields
                    app = player
                    break
        elif ctx.which("playerctl"):
            code, out, err = _run(["playerctl", "metadata", "--format", _PLAYERCTL_FORMAT])
            fields = out.rstrip("\n").split("\t")
            if code == 0 and len(fields) == 5:
                status, app, title, artist, album = fields
            elif "no player" not in err.lower():
                return ActionResult.failed(f"could not read the player: {err.strip() or code}")
        else:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")

        state = (status or "stopped").strip().lower()
        playing = state == "playing"
        title, artist, album, app = (x.strip() or None if isinstance(x, str) else None
                                     for x in (title, artist, album, app))
        if status is None:
            return ActionResult.success(playing=False, spoken=describe_now_playing(None, None, None, False))
        return ActionResult.success(
            title=title, artist=artist, album=album, app=app, playing=playing, state=state,
            spoken=describe_now_playing(title, artist, app, playing),
        )


class PlayMedia(Action):
    id = "play_media"
    tier = ActionTier.AUTO  # as on the phone; audible, and media_control stops it
    description = (
        "Play something in this machine's default player: an http(s) or spotify: URI, "
        "an audio/video file in the workspace, or a Spotify search."
    )
    params_schema = {
        "source": "string (optional): https://…, spotify:…, file:// or a workspace path",
        "query": "string (optional): search Spotify for this instead",
    }
    capability = "system"
    timeout_s = 15.0

    #: Each other scheme (javascript:, smb:, vscode:, ms-settings:, …) reaches
    #: another program's URL handler with no consent prompt; this is Tier 1.
    ALLOWED_SCHEMES = ("http", "https", "spotify", "file")

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        query = (self.str_param(params, "query") or "").strip()
        if query:
            target = "spotify:search:" + quote(query[:200], safe="")
        else:
            resolved = self._target(ctx, (self.str_param(params, "source") or "").strip())
            if isinstance(resolved, ActionResult):
                return resolved
            target = resolved
        return self._open(ctx, target)

    def _target(self, ctx: ActionContext, source: str) -> str | ActionResult:
        if not source:
            return ActionResult.failed("source or query is required")
        if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in source):
            return ActionResult.failed("source contains whitespace or control characters")
        scheme = urlsplit(source).scheme.lower() if ":" in source else ""
        if len(scheme) == 1:  # a Windows drive letter, not a scheme
            scheme = ""
        if scheme and scheme not in self.ALLOWED_SCHEMES:
            return ActionResult.failed(
                f"only {', '.join(self.ALLOWED_SCHEMES)} sources can be played (got {scheme})"
            )
        if scheme in ("http", "https"):
            return source if urlsplit(source).netloc else ActionResult.failed("url has no host")
        if scheme == "spotify":
            return source
        path = url2pathname(urlsplit(source).path) if scheme == "file" else source
        found = ctx.scope.resolve(path, must_be_file=True)
        if not found.allowed or found.path is None:
            return ActionResult.failed(str(found.reason))
        # A default handler for a script or a binary may run it: media files only. By name AND
        # by content: xdg-open picks the handler by sniffing the bytes, so a script named
        # "song.mp3" would pass a name check and still be run by whatever handles scripts.
        mime = _guess_type(found.path)
        if not mime.startswith(("audio/", "video/")):
            return ActionResult.failed("only audio or video files can be played")
        # The opener reads the path later, after the check: a writer in the workspace could
        # swap the file in between. So the bytes are copied once into a directory only this
        # process can write, checked there, and the copy is what is opened.
        copy = _private_copy(found.path)
        if copy is None or not _looks_like_media(copy):
            return ActionResult.failed("only audio or video files can be played")
        return str(copy)

    def _open(self, ctx: ActionContext, target: str) -> ActionResult:
        if ctx.system == "Windows":
            # ShellExecute with the target as one argument: no cmd.exe, no parsing.
            try:
                os.startfile(target)  # type: ignore[attr-defined]
            except OSError as exc:
                return ActionResult.failed(f"could not open {target}: {exc}")
            return ActionResult.success(source=target)
        opener = "open" if ctx.system == "Darwin" else ctx.which("xdg-open") and "xdg-open"
        if not opener:
            return ActionResult.unsupported("install xdg-utils (xdg-open) to open media")
        code, _, err = _run([opener, target])
        if code != 0:
            return ActionResult.failed(f"could not open {target}: {err.strip() or code}")
        return ActionResult.success(source=target)


#: Python's own table lacks these without /etc/mime.types (slim images, some Windows installs),
#: and a real song or a WebP wallpaper was refused there; the byte checks still run after.
KNOWN_TYPES = {".flac": "audio/flac", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
               ".m4a": "audio/mp4", ".mkv": "video/x-matroska", ".mka": "audio/x-matroska",
               ".webp": "image/webp"}


def _guess_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or KNOWN_TYPES.get(path.suffix.lower(), "")
#: A larger file is refused rather than copied (ponytail: a whole-film copy per play; stream
#: through a pinned fd if films become the use).
MAX_MEDIA_COPY = 1 << 30
COPY_PREFIX = "jarvis-play-"
#: Copies kept for players that may still be reading them; older ones are removed.
KEEP_COPIES = 3


def _private_copy(path: Any) -> Any:
    """[path]'s bytes in a fresh 0700 directory under the temp dir, or None (too big, unreadable).
    The copy keeps the name, so the opener still sees the media extension."""
    import shutil
    import tempfile
    from pathlib import Path

    try:
        src = open(path, "rb")
    except OSError:
        return None
    _prune_copies(tempfile.gettempdir())
    with src:
        if os.fstat(src.fileno()).st_size > MAX_MEDIA_COPY:
            return None
        home = Path(tempfile.mkdtemp(prefix=COPY_PREFIX))
        dest = home / Path(path).name
        # Bounded while copying, not only before: the file can grow under us.
        left = MAX_MEDIA_COPY + 1
        with open(dest, "xb") as out:
            while left > 0 and (chunk := src.read(min(1 << 20, left))):
                out.write(chunk)
                left -= len(chunk)
        if left <= 0:
            shutil.rmtree(home, ignore_errors=True)
            return None
    return dest


def _prune_copies(tmp: str, keep: int = KEEP_COPIES) -> None:
    """Remove all but the newest [keep] of our copy directories: a player may still be reading
    the last few, the older ones are what would otherwise fill the disk play by play."""
    import shutil
    from pathlib import Path

    uid = getattr(os, "getuid", lambda: None)()  # None on Windows, whose temp dir is per-user
    dirs = sorted((d for d in Path(tmp).glob(COPY_PREFIX + "*")
                   if d.is_dir() and (uid is None or d.stat().st_uid == uid)),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    for old in dirs[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def _looks_like_media(path: Any) -> bool:
    """The file's first bytes are a known audio/video container (MP3, FLAC, Ogg, WAV/AVI,
    MP4/M4A/MOV, Matroska/WebM, AAC ADTS). Not covered: rarer containers, refused as not media."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    if head[:3] == b"ID3" or head[:4] in (b"fLaC", b"OggS", b"\x1aE\xdf\xa3"):
        return True
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"AVI "):
        return True
    if head[4:8] == b"ftyp":
        return True
    # MPEG audio frame sync (MP3 without a tag) or AAC ADTS: 11/12 set bits.
    return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0


class SetBrightness(Action):
    id = "set_brightness"
    tier = ActionTier.NOTIFY  # as on the phone
    description = "Set this machine's screen brightness (0-100%)."
    params_schema = {"level": "int 0-100: percentage of maximum brightness"}
    capability = "system"
    timeout_s = 12.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system == "Windows":
            return ctx.which("powershell") is not None
        if ctx.system == "Darwin":
            return ctx.which("brightness") is not None
        return ctx.which("brightnessctl", "ddcutil") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if ctx.system == "Darwin":
            return "install the brightness command line tool (brew install brightness)"
        return "install brightnessctl (laptop panels) or ddcutil (external monitors)"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        level = self.int_param(params, "level", -1)
        if not 0 <= level <= 100:
            return ActionResult.failed("level must be an integer between 0 and 100")
        if ctx.system == "Windows" and ctx.which("powershell"):
            # Laptop panels only: WMI has no brightness for an external monitor.
            argv = ["powershell", "-NoProfile", "-Command",
                    "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                    f".WmiSetBrightness(1,{level})"]
        elif ctx.system == "Darwin" and ctx.which("brightness"):
            argv = ["brightness", f"{level / 100:.2f}"]
        elif ctx.system == "Linux" and ctx.which("brightnessctl"):
            argv = ["brightnessctl", "set", f"{level}%"]
        elif ctx.system == "Linux" and ctx.which("ddcutil"):
            argv = ["ddcutil", "setvcp", "10", str(level)]  # VCP 0x10 is luminance
        else:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")
        code, _, err = _run(argv)
        if code != 0:
            return ActionResult.failed(f"could not set the brightness: {err.strip() or code}")
        return ActionResult.success(level=level)


#: notification daemon -> (pause argv, resume argv). Windows Focus Assist and
#: macOS Focus have no supported command line, so they answer unsupported.
_DND = (
    ("dunstctl", ["dunstctl", "set-paused", "true"], ["dunstctl", "set-paused", "false"]),
    ("swaync-client", ["swaync-client", "--dnd-on"], ["swaync-client", "--dnd-off"]),
    ("gsettings",
     ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "false"],
     ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "true"]),
)


class ToggleDnd(Action):
    id = "toggle_dnd"
    tier = ActionTier.NOTIFY  # as on the phone
    description = "Turn Do Not Disturb (paused notification banners) on or off on this machine."
    params_schema = {"enabled": "bool: true pauses notifications, false resumes them"}
    capability = "system"
    timeout_s = 12.0

    def available(self, ctx: ActionContext) -> bool:
        return ctx.system == "Linux" and ctx.which(*(d[0] for d in _DND)) is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if ctx.system == "Linux":
            return "no scriptable notification daemon found (dunst, swaync or GNOME)"
        return "Do Not Disturb has no supported command line on this OS"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        if not isinstance(params.get("enabled"), bool):
            return ActionResult.failed("enabled (true or false) is required")
        enabled = params["enabled"]
        tool = next((d for d in _DND if ctx.which(d[0])), None) if ctx.system == "Linux" else None
        if tool is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")
        code, _, err = _run(tool[1] if enabled else tool[2])
        if code != 0:
            return ActionResult.failed(f"could not change Do Not Disturb: {err.strip() or code}")
        return ActionResult.success(enabled=enabled)


#: Windows PowerShell 5.1's WinRT Radio API: the one non-admin route to the Bluetooth radio
#: (PowerShell 7 cannot load WinRT types). ``{state}`` is only ever 'On' or 'Off', chosen by
#: the action from a bool, never text from the model.
_WINRT_BLUETOOTH = (
    "Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
    "$m=([System.WindowsRuntimeSystemExtensions].GetMethods()|?{{$_.Name -eq 'AsTask' -and "
    "$_.GetParameters().Count -eq 1 -and "
    "$_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'}})[0];"
    "function Await($t,$r){{$x=$m.MakeGenericMethod($r).Invoke($null,@($t));$x.Wait(-1)|Out-Null;$x.Result}};"
    "[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]|Out-Null;"
    "Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) "
    "([Windows.Devices.Radios.RadioAccessStatus])|Out-Null;"
    "$all=Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) "
    "([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]);"
    "$bt=$all|?{{$_.Kind -eq 'Bluetooth'}}|Select-Object -First 1;"
    "if(-not $bt){{[Console]::Error.WriteLine('no Bluetooth radio');exit 2}};"
    "$s=Await ($bt.SetStateAsync('{state}')) ([Windows.Devices.Radios.RadioAccessStatus]);"
    "if(\"$s\" -ne 'Allowed'){{[Console]::Error.WriteLine(\"radio access: $s\");exit 1}}"
)


class SetBluetooth(Action):
    """Bluetooth radio on/off: bluetoothctl (BlueZ), blueutil (macOS), WinRT radios (Windows).

    Not covered: pairing or connecting a device, or a radio rfkill has hard-blocked (a
    hardware switch bluetoothctl cannot override).
    """

    id = "set_bluetooth"
    tier = ActionTier.NOTIFY  # as on the phone
    description = "Turn this machine's Bluetooth radio on or off."
    params_schema = {"on": "bool: true turns Bluetooth on, false turns it off"}
    capability = "system"
    timeout_s = 20.0

    def _argv(self, ctx: ActionContext, on: bool) -> list[str] | None:
        if ctx.system == "Linux" and ctx.which("bluetoothctl"):
            return ["bluetoothctl", "power", "on" if on else "off"]
        if ctx.system == "Darwin" and ctx.which("blueutil"):
            return ["blueutil", "--power", "1" if on else "0"]
        if ctx.system == "Windows" and ctx.which("powershell"):
            return ["powershell", "-NoProfile", "-Command",
                    _WINRT_BLUETOOTH.format(state="On" if on else "Off")]
        return None

    def available(self, ctx: ActionContext) -> bool:
        return self._argv(ctx, True) is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if ctx.system == "Darwin":
            return "install blueutil (brew install blueutil)"
        if ctx.system == "Windows":
            return "set_bluetooth needs Windows PowerShell (powershell.exe) on PATH"
        return "install bluez (bluetoothctl): apt install bluez, dnf install bluez"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        on = params.get("on")
        if not isinstance(on, bool):
            return ActionResult.failed("on (true or false) is required")
        argv = self._argv(ctx, on)
        if argv is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")
        code, _, err = _run(argv, timeout=15.0)
        if code != 0:
            return ActionResult.failed(f"could not change Bluetooth: {err.strip() or code}")
        return ActionResult.success(on=on)


#: macOS: the path arrives as argv, never as AppleScript text.
_MAC_WALLPAPER = [
    "osascript", "-e", "on run argv", "-e",
    'tell application "System Events" to tell every desktop to set picture to (item 1 of argv)',
    "-e", "end run",
]


class SetWallpaper(Action):
    id = "set_wallpaper"
    tier = ActionTier.NOTIFY  # as on the phone
    description = "Set this machine's desktop wallpaper from a PNG, JPEG or WebP in the workspace."
    params_schema = {"path": "string: an image file in the workspace"}
    capability = "system"
    timeout_s = 15.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system == "Windows":
            return True
        if ctx.system == "Darwin":
            return ctx.which("osascript") is not None
        return ctx.which("plasma-apply-wallpaperimage", "gsettings") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return "no wallpaper command found (KDE plasma-apply-wallpaperimage or GNOME gsettings)"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        path = (self.str_param(params, "path") or "").strip()
        if not path:
            return ActionResult.failed("path is required")
        found = ctx.scope.resolve(path, must_be_file=True)
        if not found.allowed or found.path is None:
            return ActionResult.failed(str(found.reason))
        # By name AND by content, as play_media: the desktop hands the file to an image decoder.
        mime = _guess_type(found.path)
        if mime not in ("image/png", "image/jpeg", "image/webp") or not _looks_like_image(found.path):
            return ActionResult.failed("only PNG, JPEG or WebP images can be a wallpaper")
        target = str(found.path)
        if ctx.system == "Windows":
            if not _windows_set_wallpaper(target):
                return ActionResult.failed("Windows refused the wallpaper")
            return ActionResult.success(path=target)
        if ctx.system == "Darwin" and ctx.which("osascript"):
            code, _, err = _run([*_MAC_WALLPAPER, target])
        elif ctx.system == "Linux" and ctx.which("plasma-apply-wallpaperimage"):
            code, _, err = _run(["plasma-apply-wallpaperimage", target])  # absolute: no "-" lead
        elif ctx.system == "Linux" and ctx.which("gsettings"):
            # as_uri percent-encodes quotes and spaces, so the quoted GVariant literal is exact.
            uri = f"'{found.path.as_uri()}'"
            schema = "org.gnome.desktop.background"
            code, _, err = _run(["gsettings", "set", schema, "picture-uri", uri])
            if code == 0:
                # GNOME 42+ shows this one in dark mode; older GNOME lacks the key, so
                # its failure is not the wallpaper's.
                _run(["gsettings", "set", schema, "picture-uri-dark", uri])
        else:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")
        if code != 0:
            return ActionResult.failed(f"could not set the wallpaper: {err.strip() or code}")
        return ActionResult.success(path=target)


def _looks_like_image(path: Any) -> bool:
    """The first bytes are PNG, JPEG or WebP. Not covered: whether the rest decodes."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    return (head[:8] == b"\x89PNG\r\n\x1a\n" or head[:3] == b"\xff\xd8\xff"
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def _windows_set_wallpaper(path: str) -> bool:
    """SystemParametersInfoW(SPI_SETDESKWALLPAPER) in-process: no shell sees the path."""
    import ctypes

    # 20 = SPI_SETDESKWALLPAPER; 3 = SPIF_UPDATEINIFILE | SPIF_SENDCHANGE (persist + broadcast).
    return bool(ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 3))  # type: ignore[attr-defined]


class SetScreenTimeout(Action):
    id = "set_screen_timeout"
    tier = ActionTier.AUTO  # as on the phone
    description = "Set how many seconds the screen stays on after the last input."
    params_schema = {"seconds": "int 15-1800: the timeout (Windows rounds up to whole minutes)"}
    capability = "system"
    timeout_s = 15.0

    #: The phone's range, so one sentence means the same on both.
    MIN_SECONDS = 15
    MAX_SECONDS = 1800

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system == "Windows":
            return ctx.which("powercfg") is not None
        return ctx.system == "Linux" and ctx.which("gsettings") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if ctx.system == "Darwin":
            return "macOS needs administrator rights (pmset) to change the display sleep"
        return "no screen timeout command found (GNOME gsettings or Windows powercfg)"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        seconds = self.int_param(params, "seconds", -1)
        if not self.MIN_SECONDS <= seconds <= self.MAX_SECONDS:
            return ActionResult.failed(
                f"seconds must be between {self.MIN_SECONDS} and {self.MAX_SECONDS}"
            )
        if ctx.system == "Windows" and ctx.which("powercfg"):
            minutes = -(-seconds // 60)  # powercfg counts whole minutes; round up, never to 0 (never)
            for source in ("ac", "dc"):
                code, _, err = _run(["powercfg", "/change", f"monitor-timeout-{source}", str(minutes)])
                if code != 0:
                    return ActionResult.failed(f"could not set the screen timeout: {err.strip() or code}")
            return ActionResult.success(seconds=minutes * 60)
        if ctx.system == "Linux" and ctx.which("gsettings"):
            code, _, err = _run(
                ["gsettings", "set", "org.gnome.desktop.session", "idle-delay", f"uint32 {seconds}"]
            )
            if code != 0:
                return ActionResult.failed(f"could not set the screen timeout: {err.strip() or code}")
            return ActionResult.success(seconds=seconds)
        return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")


class Notify(Action):
    id = "notify"
    tier = ActionTier.AUTO
    description = "Show a desktop notification on this machine."
    params_schema = {
        "title": "string: the notification title",
        "message": "string: the body text",
        "urgency": "string (optional): low | normal | critical",
    }
    capability = "notify"
    #: Above the 15s PowerShell toast deadline, for the same reason as SetVolume.
    timeout_s = 20.0

    def available(self, ctx: ActionContext) -> bool:
        # Always available: the last resort is a log line, which is honest and
        # never fails. The agent should not lose a notification because the
        # desktop toolkit is missing.
        return bool(ctx.config.notifications_enabled)

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return "notifications are disabled in the jarvis-desktop config"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        title = (self.str_param(params, "title") or f"{worn_name()}").strip()[:200]
        message = (self.str_param(params, "message") or "").strip()[:2000]
        if not message:
            return ActionResult.failed("message is required")
        urgency = (self.str_param(params, "urgency") or "normal").strip().lower()
        if urgency not in ("low", "normal", "critical"):
            urgency = "normal"

        system = ctx.system
        if system == "Linux" and ctx.which("notify-send"):
            code, _, err = _run(["notify-send", "-a", worn_name(), "-u", urgency, "--", title, message])
            if code == 0:
                return ActionResult.success(delivered="notify-send", title=title)
        elif system == "Darwin" and ctx.which("osascript"):
            script = (
                f"display notification {_applescript_str(message)} "
                f"with title {_applescript_str(title)}"
            )
            code, _, err = _run(["osascript", "-e", script])
            if code == 0:
                return ActionResult.success(delivered="osascript", title=title)
        elif system == "Windows":
            delivered = _windows_toast(title, message)
            if delivered:
                return ActionResult.success(delivered=delivered, title=title)

        # Degrade gracefully: a log line is still a delivered notification as
        # far as the user's journal is concerned, and it never fails.
        import logging

        logging.getLogger("aura_desktop.notify").info("NOTIFY %s: %s", title, message)
        return ActionResult.success(
            delivered="log",
            title=title,
            note="no desktop notifier found; the notification was written to the agent log",
        )


def _applescript_str(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _ps_single_quote(text: str) -> str:
    """Body of a PowerShell **single-quoted** string literal.

    Inside `'...'` PowerShell treats every character literally except `'`
    itself, which is escaped by doubling it. So doubling quotes is the whole
    escape — provided nothing else is interpolated into the script.

    This matters more than it looks: the callers build a ``-Command`` string
    from parameters the *server* chose, and ``notify`` is Tier 1. Without this,
    a title of ``x'); <anything>; ('`` would close the literal and run arbitrary
    PowerShell with no consent prompt anywhere in the path.

    Control characters are dropped as well: they cannot escape the literal, but
    they can hide the rest of a payload from anyone reading the audit log.
    """
    cleaned = "".join(" " if ch in "\r\n\t" else ch for ch in text if ord(ch) >= 0x20 or ch in "\r\n\t")
    return cleaned.replace("'", "''")


def _windows_toast(title: str, message: str) -> str | None:
    try:
        from win10toast import ToastNotifier

        ToastNotifier().show_toast(title, message, duration=8, threaded=True)
        return "win10toast"
    except Exception:  # noqa: BLE001
        pass
    code, _, _ = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
            " ContentType = WindowsRuntime] > $null;"
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(2);"
            f"$t.GetElementsByTagName('text')[0].AppendChild("
            f"$t.CreateTextNode('{_ps_single_quote(title)}')) > $null;"
            f"$t.GetElementsByTagName('text')[1].AppendChild("
            f"$t.CreateTextNode('{_ps_single_quote(message)}')) > $null;"
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{worn_name()}')"
            ".Show([Windows.UI.Notifications.ToastNotification]::new($t))",
        ],
        timeout=15.0,
    )
    return "powershell" if code == 0 else None


class LockScreen(Action):
    id = "lock_screen"
    # Tier 3: it can lock a user out of a session with unsaved work, and the
    # only way back in is a password the agent does not have.
    tier = ActionTier.CONFIRM
    description = "Lock this machine's screen."
    params_schema: dict[str, str] = {}
    capability = "system"
    timeout_s = 12.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system in ("Darwin", "Windows"):
            return True
        return ctx.which(
            "loginctl", "xdg-screensaver", "swaylock", "i3lock", "gnome-screensaver-command"
        ) is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return "no screen locker found; install one of loginctl, swaylock, i3lock or xdg-screensaver"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        return _lock_now(ctx)


def _lock_now(ctx: ActionContext) -> ActionResult:
    system = ctx.system
    if system == "Darwin":
        candidates = [["pmset", "displaysleepnow"]]
    elif system == "Windows":
        candidates = [["rundll32.exe", "user32.dll,LockWorkStation"]]
    else:
        candidates = [
            ["loginctl", "lock-session"],
            ["swaylock", "-f"],
            ["i3lock"],
            ["gnome-screensaver-command", "--lock"],
            ["xdg-screensaver", "lock"],
        ]
    for argv in candidates:
        if not shutil.which(argv[0]):
            continue
        code, _, err = _run(argv)
        if code == 0:
            return ActionResult.success(locked=True, locked_with=argv[0])
    return ActionResult.failed("every screen locker this machine has refused to run")


class LockIfIdle(Action):
    """The away-lock (T-0332): lock only a PC nobody has touched for ``idle_s``.

    Tier 1 because it cannot interrupt anyone -- locking a PC idle for minutes is what a
    screensaver does -- which is also what makes a false or spoofed "the owner left" harmless. A
    threshold under a minute is refused: it would be ``lock_screen`` without the ask. Does not
    cover a session that reports no idle time (Wayland without an idle protocol): it refuses,
    and says it cannot tell.
    """

    id = "lock_if_idle"
    tier = ActionTier.AUTO
    description = ("Lock this machine's screen only if nobody has used it for idle_s seconds "
                   "(at least 60); otherwise refuse and say why.")
    params_schema = {"idle_s": "int: seconds without input before it may lock (60 or more, default 300)"}
    capability = "system"
    timeout_s = 12.0
    MIN_IDLE_S = 60

    def available(self, ctx: ActionContext) -> bool:
        return LockScreen().available(ctx)

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return LockScreen().unavailable_reason(ctx)

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        raw = params.get("idle_s", 300)
        try:
            need = int(raw)
        except (TypeError, ValueError):
            need = -1
        if isinstance(raw, bool) or need < self.MIN_IDLE_S:
            return ActionResult(False, {"locked": False, "reason": "bad_idle_s"},
                                f"idle_s must be a whole number of seconds, {self.MIN_IDLE_S} or more")
        idle = ctx.idle_seconds()
        if idle is None:
            return ActionResult(False, {"locked": False, "reason": "idle_unknown"},
                                "this PC cannot tell how long it has been idle, so it was not locked")
        if idle < need:
            return ActionResult(False, {"locked": False, "reason": "in_use", "idle_s": int(idle)},
                                f"in use: the last input was {int(idle)} s ago, so it was not locked")
        return _lock_now(ctx)


class Sleep(Action):
    id = "sleep"
    tier = ActionTier.CONFIRM
    description = "Suspend this machine (sleep). The agent goes offline until it wakes."
    params_schema: dict[str, str] = {}
    capability = "system"
    timeout_s = 15.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system in ("Darwin", "Windows"):
            return True
        return ctx.which("systemctl", "loginctl", "pm-suspend") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return "no suspend command found (systemctl / loginctl / pm-suspend)"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        system = ctx.system
        if system == "Darwin":
            candidates = [["pmset", "sleepnow"]]
        elif system == "Windows":
            candidates = [["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]]
        else:
            candidates = [
                ["systemctl", "suspend"],
                ["loginctl", "suspend"],
                ["pm-suspend"],
            ]
        for argv in candidates:
            if not shutil.which(argv[0]):
                continue
            code, _, err = _run(argv, timeout=10.0)
            if code == 0:
                return ActionResult.success(suspended_with=argv[0])
            if code == 124:
                # _run's timeout: a suspend call that has not returned is what a sleeping
                # machine looks like. Trying the next command would suspend it again on wake.
                # Not covered: a tool that hung without suspending also reads as this.
                return ActionResult.success(suspended_with=argv[0], confirmed=False)
        return ActionResult.failed("every suspend command this machine has refused to run")
