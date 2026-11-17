"""Synthetic keyboard and mouse, via pyautogui when it is installed.

This is the most sensitive capability the agent has after ``run_command``:
whatever is focused receives the keystrokes, and the agent cannot see what that
is. A "type the summary into my notes" that lands in a banking tab types into a
banking tab. So:

* ``type_text``, ``click`` and ``move_mouse`` are Tier 3 — a fresh human
  approval every single time, showing the verbatim text and coordinates.
* The whole module is **off by default** (``input_automation.enabled``) on top
  of that, so a machine that never needs it cannot be talked into it.
* ``pyautogui`` is never a hard dependency. Without it every action here reports
  ``unsupported`` with an install hint.

``screenshot`` is Tier 2, matching the shared tier table (the phone rates it
Tier 2 as well): it captures rather than acts. Its output is untrusted data —
screen text is exactly the kind of content an injected page controls — and it is
written into the workspace so the model gets a path rather than a megabyte of
base64. With ``inline: true`` it ALSO carries the picture, as a JPEG no wider
than ``max_side``: that is computer use (the owner, 2026-09-10) — the model is
on another machine and the only way it sees this screen is if the bytes go
back over the channel. ``scale`` says how many screen pixels one image pixel
is, so a point read off the picture can be clicked.

``key_press`` (a key or a chord: ``enter``, ``ctrl+l``, ``alt+tab``) and
``scroll`` are Tier 3 with ``type_text`` and ``click``, for the same reason:
whatever is focused receives them.
"""

from __future__ import annotations

import base64
import io
import re
import time
from typing import Any

from ..policy import ActionTier
from ..visible import TakenOver
from .base import Action, ActionContext, ActionResult
from .paths import ScopeError, safe_join
from ..theme import worn_name

__all__ = ["TypeText", "Click", "MoveMouse", "Screenshot", "KeyPress", "Scroll", "ForgetScreenshots"]

_INSTALL_HINT = (
    "input automation needs pyautogui: pip install 'jarvis-desktop[input]' "
    "(or pip install pyautogui). On Linux it also needs an X11/XWayland session."
)

MAX_TYPE_CHARS = 4000

PAUSED_REASON = (
    "paused: the owner took over this machine; input automation waits until they resume "
    f"it from the {worn_name()} app"
)

_BOX_PARAM = (
    "array [x, y, width, height] (optional): the target's bounds from the accessibility tree "
    "or the screenshot, outlined before the input"
)


def _paused(ctx: ActionContext) -> bool:
    visible = ctx.visible
    return visible is not None and visible.takeover.paused


def _taken_over(exc: TakenOver) -> ActionResult:
    return ActionResult.denied(str(exc))


def _pyautogui() -> Any | None:
    try:
        import pyautogui

        return pyautogui
    except Exception:  # noqa: BLE001 - it raises on a headless display too
        return None


class _InputAction(Action):
    capability = "ui_automation"
    timeout_s = 60.0

    def available(self, ctx: ActionContext) -> bool:
        return (
            bool(ctx.config.input_automation.enabled)
            and not _paused(ctx)
            and _pyautogui() is not None
        )

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if _paused(ctx):
            # Before the consent prompt, not after: an owner who has just taken the machine
            # back must not be asked to approve the next click on it.
            return PAUSED_REASON
        if not ctx.config.input_automation.enabled:
            return (
                "input automation is disabled on this machine; set "
                '"input_automation": {"enabled": true} in the jarvis-desktop config'
            )
        return _INSTALL_HINT

    def _gui(self, ctx: ActionContext) -> Any | None:
        if not ctx.config.input_automation.enabled:
            return None
        gui = _pyautogui()
        if gui is not None:
            # A runaway loop can be stopped by slamming the pointer into a
            # corner. Costs nothing and has saved people before.
            try:
                gui.FAILSAFE = True
                gui.PAUSE = 0.02
            except Exception:  # noqa: BLE001
                pass
        return gui


class TypeText(_InputAction):
    id = "type_text"
    tier = ActionTier.CONFIRM
    description = "Type text on this machine's keyboard, into whatever is focused."
    params_schema = {
        "text": "string: the exact text to type",
        "interval_s": "number (optional): delay between keystrokes, default 0.01",
        "press_enter": "bool (optional): press Enter afterwards",
    }
    capability = "ui_automation"
    timeout_s = 120.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)
        text = params.get("text")
        if not isinstance(text, str) or not text:
            return ActionResult.failed("text is required")
        if len(text) > MAX_TYPE_CHARS:
            return ActionResult.failed(
                f"text is {len(text)} characters; the limit is {MAX_TYPE_CHARS}"
            )
        interval = max(0.0, min(self.float_param(params, "interval_s", 0.01), 1.0))
        # The dispatcher's timeout cancels the *wrapper*, not the worker thread:
        # a run that outlives its budget carries on typing into whatever is
        # focused long after the server has been told the action timed out, and
        # after the command slot has been freed for a second type_text. So the
        # keystroke interval is squeezed to whatever finishes inside the budget
        # rather than trusted from the parameters (4000 characters at the
        # maximum interval would be an hour of typing under a 120s cap).
        budget = max(1.0, self.timeout_s - 5.0)
        interval = min(interval, budget / max(1, len(text)))
        try:
            if ctx.visible is not None:
                ctx.visible.type(
                    gui, text, interval=interval, press_enter=self.bool_param(params, "press_enter")
                )
            else:
                gui.write(text, interval=interval)
                if self.bool_param(params, "press_enter"):
                    gui.press("enter")
        except TakenOver as exc:
            return _taken_over(exc)
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"typing failed: {exc}")
        return ActionResult.success(
            characters=len(text), pressed_enter=self.bool_param(params, "press_enter")
        )


class Click(_InputAction):
    id = "click"
    tier = ActionTier.CONFIRM
    description = "Click the mouse at a screen position on this machine."
    params_schema = {
        "x": "int: screen x, or omit to click where the pointer already is",
        "y": "int: screen y",
        "button": "string (optional): left | right | middle (default left)",
        "clicks": "int (optional): 1 or 2 (default 1)",
        "box": _BOX_PARAM,
    }
    capability = "ui_automation"
    timeout_s = 60.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)
        button = (self.str_param(params, "button") or "left").lower()
        if button not in ("left", "right", "middle"):
            return ActionResult.failed("button must be left, right or middle")
        clicks = max(1, min(self.int_param(params, "clicks", 1), 2))
        x = params.get("x")
        y = params.get("y")
        try:
            width, height = gui.size()
            if x is None or y is None:
                if ctx.visible is not None:
                    ctx.visible.click(gui, None, None, button=button, clicks=clicks)
                else:
                    gui.click(button=button, clicks=clicks)
                position = None
            else:
                px, py = int(x), int(y)
                if not (0 <= px < width and 0 <= py < height):
                    return ActionResult.failed(
                        f"({px}, {py}) is off screen; this display is {width}x{height}"
                    )
                if ctx.visible is not None:
                    ctx.visible.click(
                        gui, px, py, button=button, clicks=clicks, box=params.get("box")
                    )
                else:
                    gui.click(x=px, y=py, button=button, clicks=clicks)
                position = [px, py]
        except (TypeError, ValueError):
            return ActionResult.failed("x and y must be integers")
        except TakenOver as exc:
            return _taken_over(exc)
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"click failed: {exc}")
        return ActionResult.success(position=position, button=button, clicks=clicks)


class MoveMouse(_InputAction):
    id = "move_mouse"
    # Tier 3 with the rest: moving the pointer is the setup move for a click,
    # and a pointer that jumps under someone's hand is how a click lands
    # somewhere other than where they were looking.
    tier = ActionTier.CONFIRM
    description = "Move the mouse pointer to a screen position on this machine."
    params_schema = {
        "x": "int: screen x",
        "y": "int: screen y",
        "duration_s": "number (optional): glide time, default 0",
        "box": _BOX_PARAM,
    }
    capability = "ui_automation"
    timeout_s = 60.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)
        try:
            x, y = int(params["x"]), int(params["y"])
        except (KeyError, TypeError, ValueError):
            return ActionResult.failed("x and y are required integers")
        duration = max(0.0, min(self.float_param(params, "duration_s", 0.0), 5.0))
        try:
            width, height = gui.size()
            if not (0 <= x < width and 0 <= y < height):
                return ActionResult.failed(
                    f"({x}, {y}) is off screen; this display is {width}x{height}"
                )
            if ctx.visible is not None:
                # The visible glide has its own pace; duration_s is for the bare backend.
                ctx.visible.move(gui, x, y, box=params.get("box"))
            else:
                gui.moveTo(x, y, duration=duration)
        except TakenOver as exc:
            return _taken_over(exc)
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"pointer move failed: {exc}")
        return ActionResult.success(position=[x, y])


class Screenshot(_InputAction):
    id = "screenshot"
    # Tier 2 per the shared tier table: it captures, it does not act. The image
    # still lands only in the workspace and its contents are untrusted.
    tier = ActionTier.NOTIFY
    description = "Capture the screen of this machine and save it in the workspace."
    params_schema = {
        "filename": "string (optional): name for the PNG, default a timestamp",
        "region": "array [x, y, width, height] (optional): capture part of the screen",
        "inline": "bool (optional): also return the picture as a JPEG, base64, for a model elsewhere",
        "max_side": "int (optional): longest side of the inline picture, default 1600, at most 2560",
    }
    capability = "ui_automation"
    timeout_s = 60.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)

        name = self.str_param(params, "filename") or f"screenshot-{int(time.time())}.png"
        if not name.lower().endswith(".png"):
            name += ".png"
        target_dir = ctx.config.input_automation.screenshot_dir or "screenshots"
        try:
            base = ctx.scope.resolve(target_dir, allow_root=True)
            if not base.allowed or base.path is None:
                return ActionResult.failed(f"screenshot directory rejected: {base.reason}")
            base.path.mkdir(parents=True, exist_ok=True)
            destination = safe_join(base.path, name)
        except ScopeError as exc:
            return ActionResult.failed(f"filename rejected: {exc}")
        except OSError as exc:
            return ActionResult.failed(f"could not prepare the screenshot directory: {exc}")

        region = params.get("region")
        kwargs: dict[str, Any] = {}
        if isinstance(region, (list, tuple)) and len(region) == 4:
            try:
                kwargs["region"] = tuple(int(v) for v in region)
            except (TypeError, ValueError):
                return ActionResult.failed("region must be four integers [x, y, width, height]")

        try:
            image = _grab(gui, kwargs.get("region"))
            image.save(str(destination))
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"screenshot failed: {exc}")

        relative = ctx.scope.resolve(str(destination))
        # Whatever is on screen was drawn by other programs, so the capture is
        # untrusted data — including any text an OCR step later pulls out of it.
        payload: dict[str, Any] = {
            "path": relative.relative if relative.allowed else str(destination),
            "absolute_path": str(destination),
            "bytes": destination.stat().st_size if destination.exists() else 0,
            "screen": [int(image.width), int(image.height)],
        }
        if self.bool_param(params, "inline"):
            payload.update(_inline(image, max(320, min(self.int_param(params, "max_side", 1600), 2560))))
        return ActionResult.untrusted(payload)


# `screenshot` names its file `screenshot-<epoch seconds>.png`; older builds and the Hermes
# side used epoch milliseconds, so both widths are read as a date.
_STAMPED = re.compile(r"^screenshot-(\d{10}|\d{13})\.png$")
MAX_FORGET_MINUTES = 1440


class ForgetScreenshots(Action):
    """Delete the screenshots this agent took in the last N minutes ("forget what I just showed").

    What it does not cover: a picture saved under a name of its own (`filename` on `screenshot`)
    carries no date, so it is left and counted as `undated`; and it never looks outside the
    screenshot folder. The date is the one in the file name, read against this machine's clock.
    Not an input action: it needs neither pyautogui nor `input_automation.enabled`, since
    deleting must stay possible on a machine that has switched capturing off.
    """

    id = "forget_screenshots"
    # Tier 3 like delete_file: the person at the machine sees it before pictures go.
    tier = ActionTier.CONFIRM
    description = "Delete the screenshots this machine saved in the last N minutes."
    params_schema = {"minutes": "int: 1 to 1440, how far back to erase"}
    capability = "files"
    timeout_s = 30.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        minutes = params.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not (
            1 <= minutes <= MAX_FORGET_MINUTES
        ):
            return ActionResult.failed(f"minutes is a whole number from 1 to {MAX_FORGET_MINUTES}")
        base = ctx.scope.resolve(
            ctx.config.input_automation.screenshot_dir or "screenshots", allow_root=True
        )
        if not base.allowed or base.path is None:
            return ActionResult.failed("the screenshot folder is outside the allowed roots")
        since = time.time() - minutes * 60
        erased = failed = undated = 0
        try:
            names = sorted(p for p in base.path.iterdir() if p.suffix.lower() == ".png")
        except FileNotFoundError:
            names = []  # nothing was ever captured here
        except OSError:
            return ActionResult.failed("the screenshot folder could not be read")
        for path in names:
            found = _STAMPED.match(path.name)
            # A link could point anywhere; only a plain file in the folder is ours to delete.
            if not found or path.is_symlink() or not path.is_file():
                undated += 1
                continue
            stamp = float(found.group(1))
            if len(found.group(1)) == 13:
                stamp /= 1000
            if stamp < since:
                continue
            try:
                path.unlink()
                erased += 1
            except OSError:
                failed += 1
        if failed:
            # Fixed text: an OS error would name a path on this machine.
            return ActionResult.failed(f"{failed} of {erased + failed} screenshots could not be deleted")
        return ActionResult.success({"minutes": minutes, "erased": erased, "undated": undated})


def _grab(gui: Any, region: tuple[int, ...] | None) -> Any:
    """The screen as a PIL image: pyautogui's grab, else Pillow's own.

    pyautogui (pyscreeze) refuses to grab on Linux unless `scrot` or
    `gnome-screenshot` is installed, even though Pillow's ImageGrab reads an
    X11 display directly -- so a stock desktop with neither binary answered
    "install gnome-screenshot" to every look. Pillow is what pyautogui's own
    grab returns anyway, so the fallback hands back the same kind of thing.
    """
    try:
        return gui.screenshot(**({"region": region} if region else {}))
    except Exception as first:  # noqa: BLE001 - pyscreeze raises its own exception type
        try:
            from PIL import ImageGrab
        except Exception:  # noqa: BLE001
            raise first
        try:
            image = ImageGrab.grab()
        except Exception:  # noqa: BLE001
            raise first
        if region:
            x, y, w, h = region
            image = image.crop((x, y, x + w, y + h))
        return image


def _inline(image: Any, max_side: int) -> dict[str, Any]:
    """The picture as the model will see it: a JPEG no longer than `max_side`, and its scale.

    JPEG at 70, not the PNG on disk: a 1080p desktop is 200-400 kB this way
    and two megabytes as PNG, and it crosses the device channel, the house and
    the tool result as base64. `scale` is screen pixels per image pixel, so
    `(x * scale, y * scale)` is where a point in the picture is on the screen.
    """
    width, height = int(image.width), int(image.height)
    longest = max(width, height)
    scale = 1.0
    shown = image
    if longest > max_side:
        scale = longest / max_side
        shown = image.resize((max(1, round(width / scale)), max(1, round(height / scale))))
    buffer = io.BytesIO()
    shown.convert("RGB").save(buffer, format="JPEG", quality=70, optimize=True)
    return {
        "image_jpeg_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "image_size": [int(shown.width), int(shown.height)],
        "scale": round(scale, 4),
    }


#: Keys a chord may name, beyond a single character: pyautogui's own names,
#: the ones a person would say. Anything else is refused rather than guessed.
KEY_NAMES = frozenset(
    "enter return tab space backspace delete escape esc home end pageup pagedown up down left right "
    "insert printscreen ctrl alt shift win super cmd command option "
    "f1 f2 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12 volumeup volumedown volumemute".split()
)
MAX_CHORD_KEYS = 4


def parse_chord(raw: Any) -> list[str] | str:
    """``"ctrl+shift+t"`` -> ``["ctrl", "shift", "t"]``, or the reason it cannot be one."""
    if not isinstance(raw, str) or not raw.strip():
        return "keys is required: a key or a chord like ctrl+l"
    parts = [p.strip().lower() for p in raw.split("+")]
    if not parts or any(not p for p in parts):
        return "a chord is keys joined by +, like ctrl+shift+t"
    if len(parts) > MAX_CHORD_KEYS:
        return f"a chord is at most {MAX_CHORD_KEYS} keys"
    for part in parts:
        if len(part) == 1 and part.isprintable():
            continue
        if part not in KEY_NAMES:
            return f"{part!r} is not a key this agent presses"
    return parts


class KeyPress(_InputAction):
    id = "key_press"
    tier = ActionTier.CONFIRM
    description = "Press a key or a chord (enter, ctrl+l, alt+tab) on this machine's keyboard, into whatever is focused."
    params_schema = {
        "keys": "string: a key, or keys joined by + for a chord",
        "times": "int (optional): repeat count, 1 to 10, default 1",
    }
    capability = "ui_automation"
    timeout_s = 60.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)
        chord = parse_chord(params.get("keys"))
        if isinstance(chord, str):
            return ActionResult.failed(chord)
        times = max(1, min(self.int_param(params, "times", 1), 10))
        try:
            if ctx.visible is not None:
                ctx.visible.press(gui, chord, times)
            else:
                for _ in range(times):
                    if len(chord) == 1:
                        gui.press(chord[0])
                    else:
                        gui.hotkey(*chord)
        except TakenOver as exc:
            return _taken_over(exc)
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"key press failed: {exc}")
        return ActionResult.success(keys="+".join(chord), times=times)


class Scroll(_InputAction):
    id = "scroll"
    tier = ActionTier.CONFIRM
    description = "Scroll the mouse wheel on this machine, at a screen position or where the pointer is."
    params_schema = {
        "clicks": "int: wheel clicks, positive up, negative down, -50 to 50",
        "x": "int (optional): screen x to scroll at",
        "y": "int (optional): screen y",
        "box": _BOX_PARAM,
    }
    capability = "ui_automation"
    timeout_s = 60.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)
        try:
            clicks = int(params["clicks"])
        except (KeyError, TypeError, ValueError):
            return ActionResult.failed("clicks is required: an integer, positive up, negative down")
        clicks = max(-50, min(clicks, 50))
        if clicks == 0:
            return ActionResult.failed("clicks must not be 0")
        x, y = params.get("x"), params.get("y")
        try:
            if x is None or y is None:
                if ctx.visible is not None:
                    ctx.visible.scroll(gui, clicks)
                else:
                    gui.scroll(clicks)
                position = None
            else:
                px, py = int(x), int(y)
                width, height = gui.size()
                if not (0 <= px < width and 0 <= py < height):
                    return ActionResult.failed(f"({px}, {py}) is off screen; this display is {width}x{height}")
                if ctx.visible is not None:
                    ctx.visible.scroll(gui, clicks, px, py, box=params.get("box"))
                else:
                    gui.scroll(clicks, x=px, y=py)
                position = [px, py]
        except (TypeError, ValueError):
            return ActionResult.failed("x and y must be integers")
        except TakenOver as exc:
            return _taken_over(exc)
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"scroll failed: {exc}")
        return ActionResult.success(clicks=clicks, position=position)
