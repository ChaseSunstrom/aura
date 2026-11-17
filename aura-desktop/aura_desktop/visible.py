"""Visible computer use: the owner sees every move before it lands, and can take the machine back.

Before any synthetic click, keystroke or scroll, the real pointer glides to the target along the
console's own ease curve, the target is outlined, a click ripples, and only then does the input
fire. The drawing is the desktop app's overlay window (a transparent, click-through, always-on-top
Tauri window, fed ``overlay`` frames over the shell socket); this module only says what to draw
and when, so a bare pip agent with no app gets the same timing and the same take-over, undrawn.

The take-over is a latch (:class:`Takeover`). It trips when, during an action:

* the pointer is somewhere this agent did not put it (the owner moved the mouse), or
* the machine saw input after this agent's last synthetic event (the owner typed; read from the
  idle probe, so only between this agent's own events, never while it is mid-keystroke), or
* the owner pressed Esc (the app's global shortcut, sent as a ``takeover`` frame).

The run stops within one tick (a 60 Hz frame) of the trip being seen, the overlay hides, and every
input action reports itself unavailable until the owner resumes from the app. Nothing here
resumes on its own: a latch that clears itself is a pause that ends while the owner is still
working.

What this does **not** cover: keyboard input during this agent's own typing (both reset the same
idle counter, so they cannot be told apart; the pointer and Esc still work), and Wayland
sessions without XWayland, where pyautogui cannot read the pointer at all.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .tokens import TOKENS
from .theme import worn_name

_LOGGER = logging.getLogger(__name__)

__all__ = ["GLIDE_S", "TakenOver", "Takeover", "Visible", "cursor_path", "ease"]


def _seconds(token: str) -> float:
    value = TOKENS[token].strip()
    return float(value[:-2]) / 1000.0 if value.endswith("ms") else float(value.rstrip("s"))


#: The console's motion tokens, so the agent's pointer moves the way the console's panels do.
GLIDE_S = _seconds("--jv-dur-glide")
DWELL_S = _seconds("--jv-dur-fast")
RIPPLE_S = _seconds("--jv-dur-instant")
_CURVE = tuple(float(v) for v in TOKENS["--jv-ease-in-out"].split("(", 1)[1].rstrip(")").split(","))
#: The highlight drawn round a point when the caller has no box for its target.
TOUCH_PX = int(TOKENS["--jv-touch"].rstrip("px"))

#: One display frame: how often the run looks for the owner's hand. The 100 ms budget is six.
TICK_S = 1 / 60
#: Pixels the pointer may sit off where this agent put it before it counts as the owner's.
#: Not zero: a fractional-scale desktop rounds a warp by a pixel or two.
SLOP_PX = 3
#: How often the idle probe runs (it can be a subprocess) and how much newer than this
#: agent's last event an input must be to count as somebody else's.
IDLE_EVERY_S = 0.05
IDLE_SLACK_S = 0.03
#: How long after pressing Escape itself the agent ignores an Esc from the shell's shortcut.
OWN_ESCAPE_S = 0.5
#: A box bigger than this on either side is not a target, it is a screen.
MAX_BOX_PX = 16384


def ease(t: float) -> float:
    """The console's ``--jv-ease-in-out`` cubic-bezier, evaluated at ``t`` in [0, 1]."""
    x1, y1, x2, y2 = _CURVE
    t = min(max(t, 0.0), 1.0)
    lo, hi = 0.0, 1.0
    for _ in range(32):  # bisection on x(s) = t; x is monotonic for a CSS curve
        s = (lo + hi) / 2
        x = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s**2 * x2 + s**3
        lo, hi = (s, hi) if x < t else (lo, s)
    s = (lo + hi) / 2
    return 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s**2 * y2 + s**3


def cursor_path(start: tuple[int, int], end: tuple[int, int],
                duration: float = GLIDE_S, tick: float = TICK_S) -> list[tuple[int, int]]:
    """One pointer position per frame from ``start`` to ``end``, eased; the last is ``end``."""
    steps = max(1, round(duration / tick))
    (sx, sy), (ex, ey) = start, end
    points = []
    for i in range(1, steps + 1):
        e = ease(i / steps)
        points.append((round(sx + (ex - sx) * e), round(sy + (ey - sy) * e)))
    points[-1] = (ex, ey)
    return points


class TakenOver(Exception):
    """The owner took the machine back; the action stops where it is."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"the owner took over ({reason}); resume from the {worn_name()} app")
        self.reason = reason


class Takeover:
    """The owner's latch: tripped by their hand, cleared only by their word.

    Thread-safe: actions run in worker threads, the shell's Esc arrives on the event loop.
    Listeners are called ``(paused, reason)`` on whichever thread tripped or resumed it.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self.reason = ""
        self._listeners: list[Callable[[bool, str], None]] = []

    @property
    def paused(self) -> bool:
        return self._event.is_set()

    def listen(self, listener: Callable[[bool, str], None]) -> None:
        self._listeners.append(listener)

    def trip(self, reason: str) -> bool:
        """Pause. False when it was already paused, so a second trip tells nobody twice."""
        with self._lock:
            if self._event.is_set():
                return False
            self.reason = reason
            self._event.set()
        _LOGGER.info("the owner took over (%s): input automation paused", reason)
        self._tell(True)
        return True

    def resume(self) -> bool:
        with self._lock:
            if not self._event.is_set():
                return False
            self.reason = ""
            self._event.clear()
        _LOGGER.info("the owner handed the machine back: input automation resumed")
        self._tell(False)
        return True

    def _tell(self, paused: bool) -> None:
        for listener in list(self._listeners):
            try:
                listener(paused, self.reason)
            except Exception:  # noqa: BLE001 - a broken listener must not un-pause anything
                _LOGGER.warning("a take-over listener failed", exc_info=True)


def _position(gui: Any) -> tuple[int, int] | None:
    try:
        x, y = gui.position()
        return int(x), int(y)
    except Exception:  # noqa: BLE001 - a backend that cannot say where the pointer is
        return None


def _box(raw: Any, x: int, y: int) -> list[int]:
    """The caller's ``[x, y, w, h]`` for the target, or a touch-sized square round the point.

    The box is the model's (from the accessibility tree or a screenshot) and is only drawn, never
    clicked; it is still bounded so a hostile one cannot paint the whole screen.
    """
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            bx, by, bw, bh = (int(v) for v in raw)
        except (TypeError, ValueError):
            bx = bw = 0
        else:
            if 0 < bw <= MAX_BOX_PX and 0 < bh <= MAX_BOX_PX:
                return [bx, by, bw, bh]
    half = TOUCH_PX // 2
    return [x - half, y - half, TOUCH_PX, TOUCH_PX]


class Visible:
    """Synthetic input the owner can watch and interrupt.

    ``overlay`` and ``report`` are called from the action's worker thread and must be safe to
    call from any thread (``__main__`` hops them onto the event loop).
    """

    def __init__(
        self,
        overlay: Callable[[dict[str, Any]], Any] | None = None,
        takeover: Takeover | None = None,
        idle: Callable[[], float | None] | None = None,
        report: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.overlay = overlay or (lambda frame: None)
        self.report = report or (lambda activity: None)
        self.takeover = takeover or Takeover()
        self.idle = idle
        self.sleep = sleep
        self.clock = clock
        self._expect: tuple[int, int] | None = None
        self._ours_at = 0.0
        self._idle_at = 0.0
        self._own_escape_until = 0.0
        self.takeover.listen(self._on_takeover)

    # --- the owner's side -------------------------------------------------------------------
    def owner_escape(self) -> bool:
        """The app's global Esc. Ignored just after this agent pressed Escape itself."""
        if self.clock() < self._own_escape_until:
            _LOGGER.debug("ignoring an Esc this agent pressed")
            return False
        return self.takeover.trip("escape")

    def _on_takeover(self, paused: bool, _reason: str) -> None:
        if paused:
            self.overlay({"op": "hide"})
        self.report("paused" if paused else "idle")

    # --- the actions ------------------------------------------------------------------------
    def click(self, gui: Any, x: int | None, y: int | None, *, button: str, clicks: int,
              box: Any = None) -> None:
        with self._action(gui, "clicking"):
            if x is None or y is None:
                x, y = _position(gui) or (0, 0)
                aim = False
            else:
                aim = True
                self._glide(gui, x, y)
            self._highlight(gui, x, y, box)
            self.overlay({"op": "ripple", "at": [x, y]})
            self._hold(gui, RIPPLE_S)
            self._check(gui)
            if aim:
                gui.click(x=x, y=y, button=button, clicks=clicks)
            else:
                gui.click(button=button, clicks=clicks)
            self._ours((x, y))

    def move(self, gui: Any, x: int, y: int, box: Any = None) -> None:
        with self._action(gui, "moving"):
            self._glide(gui, x, y)
            self._highlight(gui, x, y, box)

    def scroll(self, gui: Any, clicks: int, x: int | None = None, y: int | None = None,
               box: Any = None) -> None:
        with self._action(gui, "scrolling"):
            if x is not None and y is not None:
                self._glide(gui, x, y)
                self._highlight(gui, x, y, box)
            at = _position(gui)
            self.overlay({"op": "scroll", "at": list(at) if at else None,
                          "direction": "up" if clicks > 0 else "down"})
            self._hold(gui, DWELL_S)
            self._check(gui)
            if x is not None and y is not None:
                gui.scroll(clicks, x=x, y=y)
            else:
                gui.scroll(clicks)
            self._ours((x, y) if x is not None and y is not None else None)

    def type(self, gui: Any, text: str, *, interval: float, press_enter: bool) -> None:
        with self._action(gui, "typing"):
            at = _position(gui)
            self.overlay({"op": "caret", "at": list(at) if at else None, "chars": len(text)})
            self._hold(gui, DWELL_S)
            # One character at a time, looking for the owner between each: a 4000-character
            # write() is a minute nobody can interrupt.
            for char in text:
                self._check(gui)
                gui.write(char, _pause=False)
                self._ours()
                if interval > 0:
                    self._hold(gui, interval)
            if press_enter:
                self._check(gui)
                gui.press("enter", _pause=False)
                self._ours()

    def press(self, gui: Any, chord: list[str], times: int) -> None:
        with self._action(gui, "pressing"):
            self.overlay({"op": "key", "keys": "+".join(chord)})
            self._hold(gui, DWELL_S)
            for _ in range(times):
                self._check(gui)
                if any(k in ("esc", "escape") for k in chord):
                    # The app's global Esc may see this agent's own Escape; that is not the owner.
                    self._own_escape_until = self.clock() + OWN_ESCAPE_S
                if len(chord) == 1:
                    gui.press(chord[0], _pause=False)
                else:
                    gui.hotkey(*chord, _pause=False)
                self._ours()

    # --- the machinery ----------------------------------------------------------------------
    def _action(self, gui: Any, activity: str) -> "_Running":
        return _Running(self, gui, activity)

    def _glide(self, gui: Any, x: int, y: int) -> None:
        start = _position(gui) or (x, y)
        self.overlay({"op": "path", "from": list(start), "to": [x, y],
                      "ms": round(GLIDE_S * 1000)})
        for px, py in cursor_path(start, (x, y)):
            self._check(gui)
            gui.moveTo(px, py, _pause=False)
            self._ours((px, py))
            self.sleep(TICK_S)

    def _highlight(self, gui: Any, x: int, y: int, box: Any) -> None:
        self.overlay({"op": "highlight", "box": _box(box, x, y)})
        self._hold(gui, DWELL_S)

    def _ours(self, at: tuple[int, int] | None = None) -> None:
        """This agent just made an input, as of now; ``at`` is where it put the pointer.

        The target, not a read-back: a read-back taken just as the owner's hand moves would
        adopt their position as this agent's and miss the take-over. Targets are on screen
        (the actions check), and :data:`SLOP_PX` absorbs a fractional-scale rounding.
        """
        if at is not None:
            self._expect = at
        self._ours_at = self.clock()

    def _hold(self, gui: Any, seconds: float) -> None:
        end = self.clock() + seconds
        while True:
            self._check(gui)
            left = end - self.clock()
            if left <= 0:
                return
            self.sleep(min(TICK_S, left))

    def _check(self, gui: Any) -> None:
        if not self.takeover.paused:
            here = _position(gui) if self._expect is not None else None
            if here is not None and self._expect is not None and max(
                abs(here[0] - self._expect[0]), abs(here[1] - self._expect[1])
            ) > SLOP_PX:
                self.takeover.trip("pointer")
            elif self.idle is not None and self.clock() - self._idle_at >= IDLE_EVERY_S:
                now = self._idle_at = self.clock()
                idle = _safe_idle(self.idle)
                if idle is not None and idle < (now - self._ours_at) - IDLE_SLACK_S:
                    self.takeover.trip("input")
        if self.takeover.paused:
            raise TakenOver(self.takeover.reason)


def _safe_idle(probe: Callable[[], float | None]) -> float | None:
    try:
        return probe()
    except Exception:  # noqa: BLE001 - no idle reading is no keyboard signal, not a crash
        return None


class _Running:
    """One action's span: refuse to start while paused, set the baseline, always tidy up."""

    def __init__(self, visible: Visible, gui: Any, activity: str) -> None:
        self.visible, self.gui, self.activity = visible, gui, activity

    def __enter__(self) -> None:
        v = self.visible
        if v.takeover.paused:
            raise TakenOver(v.takeover.reason)
        # The baseline is now: the owner's click on the approval prompt a moment ago was theirs
        # to make, and is not a take-over.
        v._expect = _position(self.gui)
        v._ours()
        v._idle_at = 0.0
        v.report(self.activity)

    def __exit__(self, *_exc: Any) -> None:
        v = self.visible
        v._expect = None
        if not v.takeover.paused:  # paused already hid it and said so
            v.overlay({"op": "hide"})
            v.report("idle")
