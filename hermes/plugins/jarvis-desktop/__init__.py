"""jarvis-desktop — computer use on the owner's desktop, through the paired agent."""
from __future__ import annotations

from aura_schema import from_env

from . import schemas, tools

TOOLSET = "desktop"


def _available() -> bool:
    return bool(from_env().house_token)


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped HASS_* value names itself at load
    for name, schema, handler, emoji in (
        ("desktop_devices", schemas.DESKTOP_DEVICES, tools.handle_devices, "🖥️"),
        ("desktop_screen", schemas.DESKTOP_SCREEN, tools.handle_screen, "🖥️"),
        ("desktop_windows", schemas.DESKTOP_WINDOWS, tools.handle_windows, "🖥️"),
        ("desktop_click", schemas.DESKTOP_CLICK, tools.handle_click, "🖱️"),
        ("desktop_type", schemas.DESKTOP_TYPE, tools.handle_type, "⌨️"),
        ("desktop_key", schemas.DESKTOP_KEY, tools.handle_key, "⌨️"),
        ("desktop_scroll", schemas.DESKTOP_SCROLL, tools.handle_scroll, "🖱️"),
        ("desktop_focus", schemas.DESKTOP_FOCUS, tools.handle_focus, "🖥️"),
        ("desktop_open", schemas.DESKTOP_OPEN, tools.handle_open, "🖥️"),
        ("desktop_run", schemas.DESKTOP_RUN, tools.handle_run, "🖥️"),
        ("desktop_clipboard", schemas.DESKTOP_CLIPBOARD, tools.handle_clipboard, "📋"),
        ("desktop_files", schemas.DESKTOP_FILES, tools.handle_files, "📁"),
        ("desktop_notify", schemas.DESKTOP_NOTIFY, tools.handle_notify, "🔔"),
        ("desktop_lock", schemas.DESKTOP_LOCK, tools.handle_lock, "🔒"),
        ("desktop_sleep", schemas.DESKTOP_SLEEP, tools.handle_sleep, "🖥️"),
        ("desktop_notes", schemas.DESKTOP_NOTES, tools.handle_notes, "📝"),
        ("desktop_media", schemas.DESKTOP_MEDIA, tools.handle_media, "🎵"),
        ("desktop_window", schemas.DESKTOP_WINDOW, tools.handle_window, "🖥️"),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, check_fn=_available, emoji=emoji)
    # No pre_tool_call hook of its own: the approval matrix (jarvis-guard) has
    # a row for every desktop_* act, and the agent's tier policy asks the
    # person at the machine besides.
