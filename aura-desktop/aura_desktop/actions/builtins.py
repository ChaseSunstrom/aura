"""The LOCAL action table — the authority on what this machine can do and how
dangerous each of those things is.

The server's ``tier`` field can raise these numbers for a single command;
nothing can lower them. Adding an action is a two-line change here plus a row in
the README, so the table is deliberately in one place where it can be reviewed
as a whole.

The tiers here match the phone's table action for action where the two overlap
(``read_file`` AUTO, ``write_file`` NOTIFY, ``delete_file`` CONFIRM,
``read_clipboard``/``write_clipboard`` NOTIFY, ``http_request`` NOTIFY raising
to CONFIRM for non-GET, shell CONFIRM). That is the point: a user who learns
what Jarvis will and will not do without asking should not have to learn it
twice.
"""

from __future__ import annotations

from ..audit import AuditLog
from ..config import Config
from ..consent import ConsentGateway
from ..policy import ActionTier, PolicyProvider
from .apps import FocusWindow, LaunchApp, ListWindows, OpenUrl, WindowControl
from .base import Action, ActionContext
from .camera import RecordAudio, TakePhoto
from .clipboard import ReadClipboard, WriteClipboard
from .notes import FindNote, SaveNote
from .files import DeleteFile, ListDir, ReadFile, WriteFile
from .inputauto import Click, ForgetScreenshots, KeyPress, MoveMouse, Screenshot, Scroll, TypeText
from .net import HttpRequest
from .netinfo import GetNetworkInfo
from .paths import PathScope
from .registry import ActionRegistry
from .shell import RunCommand
from .system import (
    GetSystemState, LockIfIdle, LockScreen, MediaControl, MediaNowPlaying, Notify, PlayMedia, SetBrightness,
    SetBluetooth, SetScreenTimeout, SetVolume, SetWallpaper, Sleep, ToggleDnd,
)

__all__ = ["all_actions", "build_context", "build_registry", "TIER_TABLE"]


def all_actions() -> list[Action]:
    """Every built-in action, in the order the manifest will list them."""
    return [
        # System
        GetSystemState(),
        SetVolume(),
        MediaControl(),
        MediaNowPlaying(),
        PlayMedia(),
        SetBrightness(),
        ToggleDnd(),
        SetWallpaper(),
        SetScreenTimeout(),
        SetBluetooth(),
        Notify(),
        LockScreen(),
        LockIfIdle(),
        Sleep(),
        # Apps and windows
        LaunchApp(),
        OpenUrl(),
        ListWindows(),
        FocusWindow(),
        WindowControl(),
        # Files
        ReadFile(),
        WriteFile(),
        ListDir(),
        DeleteFile(),
        # Clipboard
        ReadClipboard(),
        WriteClipboard(),
        # Notes, which live on the hub rather than on this machine
        SaveNote(),
        FindNote(),
        # Network
        HttpRequest(),
        GetNetworkInfo(),
        # Shell
        RunCommand(),
        # Input automation
        TypeText(),
        Click(),
        MoveMouse(),
        Screenshot(),
        ForgetScreenshots(),
        KeyPress(),
        Scroll(),
        # Camera
        TakePhoto(),
        RecordAudio(),
    ]


#: The table again, flat, as a review artefact and a test fixture. If this and
#: :func:`all_actions` ever disagree, ``tests/test_actions.py`` fails.
TIER_TABLE: dict[str, ActionTier] = {
    # Notes are the user's own text on their own hub: writing one costs
    # nothing and is visible, reading one returns what they already wrote.
    "save_note": ActionTier.NOTIFY,
    "find_note": ActionTier.NOTIFY,
    "get_system_state": ActionTier.AUTO,
    "set_volume": ActionTier.AUTO,
    "media_control": ActionTier.AUTO,
    "media_now_playing": ActionTier.AUTO,
    "play_media": ActionTier.AUTO,
    # The phone's tiers for the same four settings.
    "set_brightness": ActionTier.NOTIFY,
    "toggle_dnd": ActionTier.NOTIFY,
    "set_wallpaper": ActionTier.NOTIFY,
    "set_screen_timeout": ActionTier.AUTO,
    "notify": ActionTier.AUTO,
    "lock_screen": ActionTier.CONFIRM,
    # Locks only a PC idle for idle_s (>= 60 s): it cannot interrupt anyone (T-0332).
    "lock_if_idle": ActionTier.AUTO,
    "sleep": ActionTier.CONFIRM,
    "launch_app": ActionTier.AUTO,
    "open_url": ActionTier.AUTO,
    "list_windows": ActionTier.AUTO,
    "focus_window": ActionTier.NOTIFY,
    "window_control": ActionTier.NOTIFY,
    "read_file": ActionTier.AUTO,
    "write_file": ActionTier.NOTIFY,
    "list_dir": ActionTier.AUTO,
    "delete_file": ActionTier.CONFIRM,
    "read_clipboard": ActionTier.NOTIFY,
    "write_clipboard": ActionTier.NOTIFY,
    "http_request": ActionTier.NOTIFY,
    "get_network_info": ActionTier.AUTO,
    "run_command": ActionTier.CONFIRM,
    "type_text": ActionTier.CONFIRM,
    "click": ActionTier.CONFIRM,
    "key_press": ActionTier.CONFIRM,
    "scroll": ActionTier.CONFIRM,
    "move_mouse": ActionTier.CONFIRM,
    "screenshot": ActionTier.NOTIFY,
    # Deleting pictures is asked at the machine, like delete_file (T-1399).
    "forget_screenshots": ActionTier.CONFIRM,
    # A camera sees the room and whoever is in it: asked every time, like the phone.
    "take_photo": ActionTier.CONFIRM,
    # The microphone, the same way.
    "record_audio": ActionTier.CONFIRM,
    # The phone's tier: a radio toggle is visible and undone by the next call.
    "set_bluetooth": ActionTier.NOTIFY,
}


def build_context(config: Config) -> ActionContext:
    scope = PathScope(config.file_roots or (config.workspace,))
    scope.ensure_roots()
    allowed_hosts = tuple(h for h in (config.server_host,) if h)
    return ActionContext(config=config, scope=scope, allowed_hosts=allowed_hosts)


def build_registry(
    config: Config,
    policy: PolicyProvider,
    audit: AuditLog,
    consent: ConsentGateway | None = None,
    ctx: ActionContext | None = None,
) -> ActionRegistry:
    """Wire the whole action layer together."""
    registry = ActionRegistry(
        ctx or build_context(config), policy=policy, audit=audit, consent=consent
    )
    registry.register_all(all_actions())
    return registry
