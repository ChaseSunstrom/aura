"""A one-time code from the owner's phone, shown on this desktop (T-0135).

The house pushes ``show_code`` (tests/contracts/code-relay.json) when a text on the phone
carries a code. This shows it in a small popup that slides in at the screen's top-right corner:
the code, who sent it and from which phone, Copy, Autofill and a close. Copy puts it on the
clipboard and clears it again a minute later if it is still there. Autofill types it into the
window that had focus when the popup appeared, never whatever has focus at the click (clicking
the popup can move focus), and is offered only where that window can be found again. Copy,
Autofill or close sends ``code_done``, so the house takes the popup off the owner's other
desktops; the popup also leaves by itself when the code's lifetime ends.

The code is never logged. Not covered: whether the text's sender is who they say. The popup
names them so the owner can judge; nothing here can.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import theme
from .tokens import TOKENS
from .theme import worn_name

_LOGGER = logging.getLogger(__name__)

CODE_RE = re.compile(r"[0-9]{4,8}")
TTL_MIN, TTL_MAX, TTL_DEFAULT = 30, 300, 180
CLIPBOARD_CLEAR_S = 60.0
RING_PX = 28


def _ms(token: str) -> int:
    return int(TOKENS[token].removesuffix("ms"))


SLIDE_IN_MS = _ms("--jv-dur-float-in")
SLIDE_OUT_MS = _ms("--jv-dur-float-out")
REDUCED_MS = _ms("--jv-dur-panel-reduced")


@dataclass(frozen=True)
class Code:
    code: str
    sender: str
    device: str
    msg_id: str
    expires_at: float
    ttl_s: float = TTL_DEFAULT

    def ring(self, now: float) -> float:
        """The timer ring's arc in degrees: full when it arrives, gone when it expires."""
        return 360.0 * min(max((self.expires_at - now) / self.ttl_s, 0.0), 1.0)


class CodeBoard:
    """Which codes are on screen. Pure: the view and the tests drive it."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.codes: dict[str, Code] = {}

    def show(self, frame: dict[str, Any]) -> Code | None:
        """The code a ``show_code`` frame carries, or None when it is malformed or a repeat."""
        code, msg_id = frame.get("code"), frame.get("msg_id")
        if not isinstance(code, str) or not CODE_RE.fullmatch(code):
            return None
        if not isinstance(msg_id, str) or not msg_id or len(msg_id) > 64:
            return None
        self.expire()
        if msg_id in self.codes:
            return None
        try:
            ttl = min(max(int(frame.get("ttl_s") or TTL_DEFAULT), TTL_MIN), TTL_MAX)
        except (TypeError, ValueError):
            ttl = TTL_DEFAULT
        item = Code(code, str(frame.get("from") or "")[:64], str(frame.get("device") or "")[:64],
                    msg_id, self._clock() + ttl, ttl)
        self.codes[msg_id] = item
        return item

    def dismiss(self, msg_id: Any) -> Code | None:
        return self.codes.pop(str(msg_id or ""), None)

    def expire(self) -> list[Code]:
        now = self._clock()
        gone = [c for c in self.codes.values() if c.expires_at <= now]
        for c in gone:
            del self.codes[c.msg_id]
        return gone


# --- the window that had focus ---------------------------------------------------


def focused_window() -> Any | None:
    """A handle on the window with focus now, or None where this platform cannot say."""
    if sys.platform == "win32":
        import ctypes

        return ctypes.windll.user32.GetForegroundWindow() or None  # type: ignore[attr-defined]
    if sys.platform == "darwin" or not os.environ.get("DISPLAY"):
        return None  # macOS and Wayland: no portable way back to a window; Copy only
    try:
        out = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def refocus(handle: Any) -> bool:
    """Give focus back to [handle]; False when the window is gone."""
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return bool(user32.IsWindow(handle) and user32.SetForegroundWindow(handle))
    try:
        return subprocess.run(["xdotool", "windowactivate", "--sync", str(handle)],
                              capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def reduced_motion() -> bool:
    """The desktop asked for less motion (GNOME's setting; other desktops get the full slide)."""
    if sys.platform.startswith("linux"):
        try:
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
                                 capture_output=True, text=True, timeout=2)
            return out.returncode == 0 and out.stdout.strip() == "false"
        except (OSError, subprocess.TimeoutExpired):
            return False
    return False


# --- copy and autofill ----------------------------------------------------------------


def copy(code: str, clip: Any, timer: Callable[[float, Callable[[], None]], Any] | None = None) -> bool:
    """Put [code] on the clipboard; take it off again after a minute if it is still there."""
    try:
        clip.copy(code)
    except Exception:  # noqa: BLE001 - no clipboard backend is "could not copy", not a crash
        _LOGGER.warning("could not write the clipboard")
        return False

    def clear() -> None:
        try:
            if clip.paste() == code:
                clip.copy("")
        except Exception:  # noqa: BLE001
            pass

    (timer or _timer)(CLIPBOARD_CLEAR_S, clear)
    return True


def autofill(code: str, target: Any, gui: Any, focus: Callable[[Any], bool] = refocus) -> bool:
    """Type [code] into [target], the window focused when the popup appeared; nowhere else."""
    if target is None or gui is None or not focus(target):
        return False
    gui.write(code, interval=0.02)
    return True


def _timer(seconds: float, fn: Callable[[], None]) -> Any:
    t = threading.Timer(seconds, fn)
    t.daemon = True
    t.start()
    return t


# --- the view ---------------------------------------------------------------------------


class CodePopups:
    """``show_code`` / ``show_code_dismiss`` from the channel, onto the screen.

    [done] sends ``code_done`` for a msg_id. [clip] and [gui] return pyperclip and pyautogui
    (or None) when first needed, so the agent does not import them at start. Each popup runs its
    own Tk root on its own thread, as the companion's dialogs do: the agent's main thread is an
    asyncio loop.
    """

    def __init__(self, done: Callable[[str], None], clip: Callable[[], Any] = lambda: None,
                 gui: Callable[[], Any] = lambda: None) -> None:
        self.board = CodeBoard()
        self._done = done
        self._clip = clip
        self._gui = gui
        self._close: dict[str, Callable[[], Any]] = {}

    def handle(self, frame: dict[str, Any]) -> None:
        if frame.get("type") == "show_code_dismiss":
            closer = self._close.pop(str(frame.get("msg_id") or ""), None)
            self.board.dismiss(frame.get("msg_id"))
            if closer:
                closer()
            return
        item = self.board.show(frame)
        if item is None:
            return
        target = focused_window()
        threading.Thread(target=self._show, args=(item, target), daemon=True,
                         name="jarvis-code-popup").start()

    def _finish(self, item: Code) -> None:
        self.board.dismiss(item.msg_id)
        self._close.pop(item.msg_id, None)
        self._done(item.msg_id)

    def _show(self, item: Code, target: Any) -> None:  # pragma: no cover - drawn on a real screen
        try:
            import tkinter as tk
        except Exception:  # noqa: BLE001
            return
        root = tk.Tk()
        theme.style_window(root, f"{worn_name()} code")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        settled: list[bool] = []

        def leave(after: Callable[[], Any] | None = None) -> None:
            if settled:
                return
            settled.append(True)

            def gone() -> None:
                if after:
                    after()
                root.quit()

            slide(out=True, then=gone)

        self._close[item.msg_id] = lambda: root.after(0, leave)

        theme.label(root, f"Code from {item.sender or 'a text'}" + (f" · {item.device}" if item.device else ""),
                    colour=theme.TEXT_DIM, size=theme.FS_SMALL).pack(fill="x", padx=16, pady=(12, 0))
        top = theme.row(root)
        top.pack(fill="x", padx=16, pady=(2, 6))
        theme.label(top, item.code, colour=theme.TEXT_BRIGHT, size=theme.FS_HEADER * 2).pack(side="left")
        # The time left, as a ring that dissolves: the code is no use after it, and a number
        # counting down would compete with the code for the eye.
        ring = tk.Canvas(top, width=RING_PX, height=RING_PX, bg=theme.BG, highlightthickness=0)
        ring.pack(side="right")
        ring.create_oval(2, 2, RING_PX - 2, RING_PX - 2, outline=theme.PANEL, width=3)
        arc = ring.create_arc(2, 2, RING_PX - 2, RING_PX - 2, start=90, extent=359.9,
                              style="arc", outline=theme.ACCENT, width=3)
        row = theme.row(root)
        row.pack(fill="x", padx=12, pady=(0, 12))

        def on_copy() -> None:
            clip = self._clip()
            if clip is not None and copy(item.code, clip):
                leave(lambda: self._finish(item))

        def on_autofill() -> None:
            # Typed first, closed after: a window that has gone keeps the popup (and Copy) up
            # rather than take the code off every screen with nothing typed.
            if autofill(item.code, target, self._gui()):
                leave(lambda: self._finish(item))

        theme.button(row, "Copy", on_copy, kind="accent", width=8).pack(side="left", padx=4)
        if target is not None:
            theme.button(row, "Autofill", on_autofill, kind="accent", width=9).pack(side="left", padx=4)
        theme.button(row, "×", lambda: leave(lambda: self._finish(item)), kind="quiet", width=3).pack(
            side="right", padx=4)

        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        x_end = root.winfo_screenwidth() - w - 24
        reduced = reduced_motion()

        def slide(out: bool = False, then: Callable[[], Any] | None = None) -> None:
            ms = REDUCED_MS if reduced else (SLIDE_OUT_MS if out else SLIDE_IN_MS)
            steps = max(1, ms // 16)
            start = time.monotonic()

            def frame() -> None:
                t = min((time.monotonic() - start) * 1000 / ms, 1.0)
                eased = 1 - (1 - t) ** 3  # ease-out: fast in, settles
                offset = (eased if out else 1 - eased) * (0 if reduced else w + 24)
                root.geometry(f"{w}x{h}+{int(x_end + offset)}+24")
                try:
                    root.attributes("-alpha", (1 - eased) if out else eased)
                except tk.TclError:
                    pass
                if t < 1.0:
                    root.after(max(1, ms // steps), frame)
                elif then:
                    then()

            frame()

        def tick() -> None:
            if settled:
                return
            now = time.monotonic()
            if now >= item.expires_at:
                leave(lambda: self.board.dismiss(item.msg_id))
                return
            ring.itemconfigure(arc, extent=max(item.ring(now), 0.1))
            root.after(1000 if reduced else 100, tick)

        slide()
        tick()
        try:
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
