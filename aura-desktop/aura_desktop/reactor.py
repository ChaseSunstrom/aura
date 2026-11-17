"""The arc reactor on a terminal: two rows of arc glyphs, lit by the state.

GLASS_HUD §10 puts one reactor on every surface. The web and the phone draw
three glass rings in a shader; a terminal has no rings, so here it is the six
arc glyphs of ``design/generated/console-theme.json`` (``◜◠◝`` over ``◟◡◞``)
whose lit cells follow the same eleven states, at the same colours, on the same
clock. The device agent shows it beside a prompt (the attention badge on a
consent question) and a pane host can animate it at :data:`FPS`.

Everything the theme says about the reactor — the six glyphs, each state's
glyph, its colour in truecolor / 256 / 16, its halo, rim and shadow — is typed
here as a constant. This package is installed on machines that have no
``design/`` directory, so it cannot read the theme at runtime; the constants
are held to the file by ``tests/test_reactor.py``, which is the same bargain
``tokens.py`` makes with ``design/tokens.json``. A value typed here and not in
the theme fails that test, which is how a fourth palette is kept from growing.

The state machine (:func:`derive`) is the contract's — ``tests/contracts/
reactor.json`` ``derivation`` — and its truth table runs against this file, the
web's ``reactorState.ts`` and the phone's ``ReactorState.kt``, so an approval
that reads as "attention" in the browser cannot read as "thinking" here.

Nothing here writes to a stream. :func:`frame` returns the rows; the caller
prints them, which is what keeps the renderer testable and the prompt code in
charge of its own output.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Literal

from .tokens import TOKENS

__all__ = [
    "State",
    "Inputs",
    "Derived",
    "derive",
    "PRECEDENCE",
    "WORDS",
    "Caps",
    "detect",
    "frame",
    "badge",
    "Light",
    "light",
    "Animator",
    "ease_standard",
    "FPS",
]


# --- the states ---------------------------------------------------------------


class State(str, Enum):
    """The eleven states of GLASS_HUD §10, in precedence order.

    ``GOAL`` is listed because it has a glyph and a word, but it is an overlay:
    :func:`derive` never returns it as the state, it returns it as the flag.
    """

    KILL = "kill"
    OFFLINE = "offline"
    ALERT = "alert"
    ATTENTION = "attention"
    WARMING = "warming"
    SPEAKING = "speaking"
    LISTENING = "listening"
    TOOL = "tool"
    THINKING = "thinking"
    GOAL = "goal"
    IDLE = "idle"


#: The order several applying states resolve in (contract ``precedence``).
PRECEDENCE: tuple[State, ...] = (
    State.KILL,
    State.OFFLINE,
    State.ALERT,
    State.ATTENTION,
    State.WARMING,
    State.SPEAKING,
    State.LISTENING,
    State.TOOL,
    State.THINKING,
    State.IDLE,
)

#: The state word the tray, the notification and the console strip all carry.
WORDS: dict[State, str] = {
    State.IDLE: "Ready",
    State.LISTENING: "Listening",
    State.THINKING: "Thinking",
    State.SPEAKING: "Speaking",
    State.TOOL: "Working",
    State.GOAL: "On a goal",
    State.ATTENTION: "Needs you",
    State.WARMING: "Warming up",
    State.ALERT: "Alert",
    State.KILL: "Kill switch",
    State.OFFLINE: "Offline",
}


@dataclass(frozen=True)
class Inputs:
    """What every surface knows, with the contract's ``rest`` defaults."""

    kill: bool = False
    linkUp: bool = True
    adapterError: bool = False
    turnRunning: bool = False
    errorEvent: bool = False
    attention: bool = False
    warming: bool = False
    speaking: bool = False
    listening: bool = False
    toolsRunning: int = 0
    goalActive: bool = False


@dataclass(frozen=True)
class Derived:
    state: State
    goal: bool


def derive(inputs: Inputs) -> Derived:
    """The contract's ``derivation.rules``, in order.

    The goal overlay is drawn on every state but kill and offline: nothing
    moves there, and a dot orbiting a dead reactor would say the goal is still
    being worked when the link that works it is gone.
    """
    i = inputs
    if i.kill:
        state = State.KILL
    elif not i.linkUp or (i.adapterError and not i.turnRunning):
        state = State.OFFLINE
    elif i.errorEvent or (i.adapterError and i.turnRunning):
        state = State.ALERT
    elif i.attention:
        state = State.ATTENTION
    elif i.warming:
        state = State.WARMING
    elif i.speaking:
        state = State.SPEAKING
    elif i.listening:
        state = State.LISTENING
    elif i.toolsRunning > 0:
        state = State.TOOL
    elif i.turnRunning:
        state = State.THINKING
    else:
        state = State.IDLE
    goal = bool(i.goalActive) and state not in (State.KILL, State.OFFLINE)
    return Derived(state, goal)


def _hex(state: str) -> str:
    """``color.reactor.<state>`` as ``tokens.py`` generated it.

    A lookup, not a literal: the token lint refuses a ``#rrggbb`` outside a
    generated file, and it is right to — the previous console reactor was a
    fourth hand-copied palette, and it was the one that fell behind.
    """
    return TOKENS[f"--jv-reactor-{state}"]


# --- the theme, typed ---------------------------------------------------------
#
# Every value below is console-theme.json's. ``tests/test_reactor.py`` reads the
# file and diffs it against these; nothing here may be a number of its own.

#: ``reactor.ring``: the six arcs, clockwise from the top-left.
RING: tuple[str, ...] = ("◜", "◠", "◝", "◞", "◡", "◟")

#: ``reactor.spinner``: what a terminal with no Unicode animates instead.
SPINNER: tuple[str, ...] = ("-", "\\", "|", "/")

#: The grid the six arcs make (contract ``console.cells``): ``◜◠◝`` over ``◟◡◞``.
CELLS: tuple[tuple[int, ...], ...] = ((0, 1, 2), (5, 4, 3))

#: ``reactor.states.<state>``: the static glyph, its ASCII stand-in, its SGR.
GLYPHS: dict[State, tuple[str, str, tuple[str, ...]]] = {
    State.IDLE: ("○", "o", ()),
    State.LISTENING: ("●", "*", ()),
    State.THINKING: ("◐", "d", ()),
    State.SPEAKING: ("◎", "@", ()),
    State.TOOL: ("◔", "%", ()),
    State.GOAL: ("⊙", "o.", ()),
    State.ATTENTION: ("◡", "n", ()),
    State.WARMING: ("○", "o", ("dim",)),
    State.ALERT: ("⊖", "!", ()),
    State.KILL: ("◯", "X", ()),
    State.OFFLINE: ("◌", ".", ("dim",)),
}

#: ``palette.reactor-<state>``: ``(hex, xterm256, ansi16)``.
#:
#: The hex is never typed: it is ``tokens.py``'s, the generated mirror of
#: ``design/tokens.json`` that ships inside this package, so a retuned accent
#: reaches the terminal at the next ``design/build.py`` with nothing to edit
#: here. The two indices beside it are not colours and have no token — they are
#: this terminal's nearest xterm-256 and ANSI-16 slots, computed once in
#: ``design/generated/console-theme.json`` and typed here because that file is
#: not installed with the package. ``tests/test_reactor.py`` diffs all three
#: against it, which is what stops the ladder drifting from the palette.
COLOURS: dict[State, tuple[str, int, int]] = {
    State.IDLE: (_hex("idle"), 31, 6),
    State.LISTENING: (_hex("listening"), 80, 6),
    State.THINKING: (_hex("thinking"), 80, 6),
    State.SPEAKING: (_hex("speaking"), 117, 7),
    State.TOOL: (_hex("tool"), 80, 6),
    State.GOAL: (_hex("goal"), 31, 6),
    State.ATTENTION: (_hex("attention"), 215, 3),
    State.WARMING: (_hex("warming"), 214, 3),
    State.ALERT: (_hex("alert"), 204, 8),
    State.KILL: (_hex("kill"), 167, 1),
    State.OFFLINE: (_hex("offline"), 215, 3),
}

#: ``palette.orb-housing``: the unlit cell, and the kill switch's dark rings.
HOUSING: tuple[str, int, int] = (TOKENS["--jv-orb-housing"], 232, 0)

#: ``reactor.states.<state>`` halo: the reactor's own core brightness. Goal has
#: none — it inherits the state beneath it. The rim and shadow that stood beside
#: each halo went with the light layer (ADR-0031): nothing was lit from here.
LIGHT: dict[State, float] = {
    State.IDLE: 0.1,
    State.LISTENING: 0.18,
    State.THINKING: 0.18,
    State.SPEAKING: 0.12,
    State.TOOL: 0.18,
    State.ATTENTION: 0.22,
    State.WARMING: 0.08,
    State.ALERT: 0.24,
    State.KILL: 0.0,
    State.OFFLINE: 0.0,
}

#: Speaking and warming move between their value and a peak (light.halo.*-peak).
HALO_PEAK: dict[State, float] = {State.SPEAKING: 0.24, State.WARMING: 0.2}



def _ms(token: str) -> int:
    """A duration token (``260ms``, ``2.4s``, ``41s``) as whole milliseconds."""
    text = TOKENS[token]
    return round(float(text[:-2]) if text.endswith("ms") else float(text[:-1]) * 1000)


#: ``motion.reactor_ms`` and ``motion.durations_ms``: the clock, in milliseconds.
#:
#: Read from the generated tokens like the colours, never typed: the iris was
#: typed as 41000 here, the rings were retuned to 32 s in ``design/tokens.json``
#: (2026-09-09), and ``test:desktop`` was the only thing that noticed.
PERIOD_MS: dict[str, int] = {
    name: _ms(f"--jv-rx-{name}") for name in ("breathe", "level", "think", "iris-b", "speak")
}
DUR_MS: dict[str, int] = {name: _ms(f"--jv-dur-{name}") for name in ("base", "pulse", "blink")}

#: ``motion.fps``: the console samples the eased curve this often (§8.1).
FPS = 30

#: ``motion.ease.standard``'s control points, read from the token rather than
#: typed: a typed copy stayed on Reactor II's curve when the brief moved it.
EASE_STANDARD = tuple(
    float(v) for v in TOKENS["--jv-ease-standard"].removeprefix("cubic-bezier(").removesuffix(")").split(",")
)

#: The idle breath's luminance range (§10, contract ``states.idle.luminance``).
BREATH = (0.6, 0.8)

#: Below this luminance a colour terminal that cannot dim a colour uses SGR dim;
#: at or above this a peak is drawn bold. The 16-colour path has no other way
#: to show a breath, and a reactor that never changes reads as a status light.
DIM_BELOW = 0.7
BOLD_AT = 0.95

#: Alert: this many fast cycles, then hold (contract ``states.alert.cycles``).
ALERT_CYCLES = 3

#: A cold start with no reported progress ramps over this long — llama-swap
#: takes up to about three minutes, and a ramp that never moves reads as stuck.
WARMING_FALLBACK_S = 180.0

# --- capabilities -------------------------------------------------------------

Colour = Literal["truecolor", "256", "16", "none"]


@dataclass(frozen=True)
class Caps:
    """What the terminal can draw: the theme's ``degrade`` ladder."""

    color: Colour = "none"
    unicode: bool = False
    tty: bool = False


def detect(env: dict[str, str] | None = None, stream: Any = None) -> Caps:
    """Read the terminal's abilities off the environment and the stream.

    ``NO_COLOR`` (any value) wins over everything: it is the one convention a
    person sets to say "plain text", and honouring COLORTERM over it would be
    exactly the failure the convention exists to stop. Not a TTY means no colour
    either — escapes in a log file are noise the next reader has to strip.
    """
    e = os.environ if env is None else env
    stream = sys.stderr if stream is None else stream
    try:
        tty = bool(stream) and bool(stream.isatty())
    except Exception:  # noqa: BLE001 — a closed or fake stream is "not a tty"
        tty = False
    lang = " ".join(e.get(k, "") for k in ("LC_ALL", "LC_CTYPE", "LANG")).lower()
    unicode = "utf-8" in lang or "utf8" in lang
    if not tty or "NO_COLOR" in e:
        return Caps("none", unicode, tty)
    term = e.get("TERM", "").lower()
    colorterm = e.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return Caps("truecolor", unicode, tty)
    if "256color" in term:
        return Caps("256", unicode, tty)
    if term in ("", "dumb"):
        return Caps("none", unicode, tty)
    return Caps("16", unicode, tty)


# --- the curve ----------------------------------------------------------------


def _bezier(p1: float, p2: float, t: float) -> float:
    """One axis of a cubic Bézier from (0,0) through p1, p2 to (1,1)."""
    u = 1.0 - t
    return 3.0 * u * u * t * p1 + 3.0 * u * t * t * p2 + t * t * t


def ease_standard(x: float) -> float:
    """``--jv-ease-standard`` (BUILD_BRIEF §1's cubic-bezier(.2,.8,.2,1)) at progress ``x`` (0–1).

    Solved for the parameter by bisection — twenty halvings is more precision
    than a 30 fps sample can show — so the console's curve is the CSS curve,
    not a lookalike whose ends agree and whose middle does not.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    x1, y1, x2, y2 = EASE_STANDARD
    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2.0
        if _bezier(x1, x2, mid) < x:
            lo = mid
        else:
            hi = mid
    return _bezier(y1, y2, (lo + hi) / 2.0)


def _sample(t: float) -> float:
    """``t`` quantised to the console's frame clock, so two callers a few
    milliseconds apart draw the same frame."""
    return int(t * FPS) / FPS


def _triangle(t: float, period_s: float) -> float:
    """0 → 1 → 0 over one period, eased both ways: a breath, a pulse."""
    if period_s <= 0:
        return 1.0
    phase = (t % period_s) / period_s
    return ease_standard(phase * 2.0) if phase < 0.5 else ease_standard((1.0 - phase) * 2.0)


# --- cells ----------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One of the six arcs: its glyph, whether it is lit, at what luminance,
    and whether it is overridden by a marker (the satellite, the bar)."""

    glyph: str
    lit: bool
    luminance: float = 1.0
    colour: State | None = None
    bold: bool = False


def _lum(state: State, t: float, level: float, envelope: float, progress: float | None) -> float:
    """The core luminance the state's motion gives at time ``t`` (§10)."""
    if state is State.IDLE:
        lo, hi = BREATH
        return lo + (hi - lo) * _triangle(t, PERIOD_MS["breathe"] / 1000.0)
    if state is State.LISTENING:
        return 0.55 + 0.45 * max(0.0, min(1.0, level))
    if state is State.SPEAKING:
        beat = _triangle(t, PERIOD_MS["speak"] / 1000.0)
        return 0.6 + 0.4 * max(beat * 0.5, max(0.0, min(1.0, envelope)))
    if state is State.ATTENTION:
        # Rise over dur.pulse, hold, fall, on a dur.blink cycle: held, not blinking.
        cycle = DUR_MS["blink"] / 1000.0
        rise = DUR_MS["pulse"] / 1000.0
        phase = t % cycle
        if phase < rise:
            k = ease_standard(phase / rise)
        elif phase < cycle - rise:
            k = 1.0
        else:
            k = ease_standard((cycle - phase) / rise)
        return 0.55 + 0.45 * k
    if state is State.WARMING:
        p = progress if progress is not None else min(0.9, t / WARMING_FALLBACK_S)
        return 0.2 + 0.8 * max(0.0, min(1.0, p))
    if state is State.ALERT:
        cycle = DUR_MS["pulse"] / 1000.0
        if t >= cycle * ALERT_CYCLES:
            return 1.0
        return 0.5 + 0.5 * _triangle(t, cycle)
    return 1.0


def _ring(state: State, t: float, *, goal: bool, level: float, envelope: float,
          progress: float | None, segments: int) -> list[Cell]:
    """The six cells for an animated state at time ``t``, clockwise from top-left."""
    lum = _lum(state, t, level, envelope, progress)
    bold = lum >= BOLD_AT and state in (State.SPEAKING, State.WARMING)
    cells = [Cell(RING[i], True, lum, state, bold) for i in range(6)]
    if state is State.LISTENING:
        # Fill from the bottom up with the level: an empty ring is silence.
        # At least one cell stays lit: a fully dark ring while the microphone
        # is open would read as offline, the one state listening must not resemble.
        lit = max(1, round(max(0.0, min(1.0, level)) * 6))
        order = (4, 3, 5, 2, 0, 1)  # bottom, bottom corners, top corners, top
        cells = [replace(c, lit=order.index(i) < lit) for i, c in enumerate(cells)]
    elif state is State.THINKING:
        # One lit arc of two cells sweeps clockwise once per motion.reactor.think.
        head = int((t % (PERIOD_MS["think"] / 1000.0)) / (PERIOD_MS["think"] / 1000.0) * 6)
        cells = [replace(c, lit=(i in (head, (head + 1) % 6))) for i, c in enumerate(cells)]
    elif state is State.TOOL:
        n = max(1, min(6, segments))
        cells = [replace(c, lit=(i < n)) for i, c in enumerate(cells)]
    elif state is State.ATTENTION:
        cells[1] = replace(cells[1], lit=False)  # the gap at the top
    elif state is State.ALERT and t >= DUR_MS["pulse"] / 1000.0 * ALERT_CYCLES:
        cells[4] = replace(cells[4], glyph=GLYPHS[State.ALERT][0])  # the bar, once holding
    elif state is State.KILL:
        cells = [replace(c, lit=(i == 4), luminance=1.0) for i, c in enumerate(cells)]
    elif state is State.OFFLINE:
        cells = [replace(c, lit=True, luminance=DIM_BELOW - 0.1) for c in cells]
    if goal and state not in (State.KILL, State.OFFLINE):
        period = PERIOD_MS["iris-b"] / 1000.0
        at = int((t % period) / period * 6)
        cells[at] = replace(cells[at], glyph=GLYPHS[State.GOAL][0], lit=True, colour=State.GOAL)
    return cells


# --- escapes --------------------------------------------------------------------


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3))  # type: ignore[return-value]


def _sgr(colour: tuple[str, int, int], luminance: float, caps: Caps, *, bold: bool = False,
         dim: bool = False, mix_to: tuple[str, int, int] | None = None, mix_k: float = 0.0) -> str:
    """The escape that paints ``colour`` at ``luminance`` on this terminal.

    Truecolor scales the colour toward the housing (the reactor dims, it does
    not grey); 256 and 16 cannot, so they say it with dim/bold instead.
    """
    if caps.color == "none":
        return ""
    parts: list[str] = []
    if caps.color == "truecolor":
        rgb = _rgb(colour[0])
        if mix_to is not None and mix_k > 0:
            rgb = _mix(rgb, _rgb(mix_to[0]), mix_k)
        rgb = _mix(_rgb(HOUSING[0]), rgb, max(0.0, min(1.0, luminance)))
        parts.append(f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}")
    elif caps.color == "256":
        parts.append(f"38;5;{colour[1]}")
    else:
        n = colour[2]
        parts.append(str(90 + n - 8) if n >= 8 else str(30 + n))
    if dim or (caps.color != "truecolor" and luminance < DIM_BELOW):
        parts.append("2")
    if bold:
        parts.append("1")
    return "\x1b[" + ";".join(parts) + "m"


RESET = "\x1b[0m"


def _paint(text: str, colour: tuple[str, int, int], luminance: float, caps: Caps, **kw: Any) -> str:
    esc = _sgr(colour, luminance, caps, **kw)
    return f"{esc}{text}{RESET}" if esc else text


# --- frames ---------------------------------------------------------------------


def _static_rows(state: State, caps: Caps) -> list[str]:
    """The reduced-motion glyph, centred in the top row of the grid."""
    glyph, ascii_, sgr = GLYPHS[state]
    mark = glyph if caps.unicode else ascii_
    painted = _paint(mark, COLOURS[state], 1.0, caps, dim="dim" in sgr)
    # Three columns wide like the ring, the mark in the middle column; a
    # two-character ASCII mark ("o.") takes the middle and the right.
    return [f" {painted}{' ' * max(0, 2 - len(mark))}", "   "]


def _rows_from_cells(cells: list[Cell], state: State, caps: Caps,
                     crossfade: tuple[State, float] | None = None) -> list[str]:
    rows: list[str] = []
    for row in CELLS:
        out = []
        for index in row:
            c = cells[index]
            if c.lit:
                colour = COLOURS[c.colour or state]
                mix_to = COLOURS[crossfade[0]] if crossfade and c.colour is None else None
                out.append(_paint(c.glyph, colour, c.luminance, caps, bold=c.bold,
                                  mix_to=mix_to, mix_k=crossfade[1] if crossfade else 0.0))
            elif caps.color == "none":
                # No colour means no housing shade: the gap in the ring has to
                # BE a gap, or every state prints the same six arcs.
                out.append(" ")
            else:
                out.append(_paint(c.glyph, HOUSING, 1.0, caps, dim=True))
        rows.append("".join(out))
    return rows


def frame(state: State, t: float, *, goal: bool = False, level: float = 0.0,
          envelope: float = 0.0, progress: float | None = None, segments: int = 1,
          caps: Caps | None = None, reduced: bool = False) -> list[str]:
    """The reactor at time ``t`` seconds, as two rows of three cells.

    ``reduced`` gives the state's static glyph (§8.3 rule 1: the states stay
    learnable without motion). A terminal without Unicode gets the spinner for
    an animated state and the ASCII glyph for a still one — the arcs are the
    one thing this surface cannot fake in ASCII.
    """
    caps = caps or Caps()
    t = _sample(max(0.0, t))
    still = state in (State.KILL, State.OFFLINE)
    if reduced or (still and not caps.unicode):
        return _static_rows(state, caps)
    if not caps.unicode:
        tick = SPINNER[int(t * FPS) % len(SPINNER)]
        return [f" {_paint(tick, COLOURS[state], _lum(state, t, level, envelope, progress), caps)} ", "   "]
    cells = _ring(state, t, goal=goal, level=level, envelope=envelope, progress=progress, segments=segments)
    return _rows_from_cells(cells, state, caps)


def badge(state: State, caps: Caps | None = None) -> str:
    """The state's glyph in its colour and its word: one line for a prompt."""
    caps = caps or Caps()
    glyph, ascii_, sgr = GLYPHS[state]
    mark = glyph if caps.unicode else ascii_
    return f"{_paint(mark, COLOURS[state], 1.0, caps, dim='dim' in sgr)} {WORDS[state]}"


# --- the light ------------------------------------------------------------------


@dataclass(frozen=True)
class Light:
    """The reactor's own light, for the reactor's own drawing. It lit panels once
    (§7, the web's ``light`` store); that layer is gone and this is what is left."""

    state: State
    goal: bool
    rgb: tuple[int, int, int]
    halo: float
    neutral: bool


def light(state: State, goal: bool = False, envelope: float = 0.0,
          progress: float | None = None) -> Light:
    halo = LIGHT[state]
    if state in HALO_PEAK:
        k = max(0.0, min(1.0, envelope if state is State.SPEAKING else (progress or 0.0)))
        halo = halo + (HALO_PEAK[state] - halo) * k
    return Light(
        state=state,
        goal=goal and state not in (State.KILL, State.OFFLINE),
        rgb=_rgb(COLOURS[state][0]),
        halo=halo,
        neutral=state in (State.KILL, State.OFFLINE),
    )


# --- the animator ----------------------------------------------------------------


@dataclass
class Animator:
    """A reactor that keeps its own clock, for a pane host.

    ``tick()`` advances one frame; ``paused`` stops the clock (§8.3 rule 2: the
    console pauses when its pane is not the active window) and the reactor
    resumes at the current state, not where it left off — the state is the
    truth, the clock is only presentation. A state change crossfades over
    ``motion.dur.base`` by mixing the two colours in truecolor and switching at
    the midpoint elsewhere, which is the only crossfade a 16-colour cell can do.
    """

    caps: Caps = field(default_factory=Caps)
    state: State = State.IDLE
    goal: bool = False
    level: float = 0.0
    envelope: float = 0.0
    progress: float | None = None
    segments: int = 1
    reduced: bool = False
    paused: bool = False
    t: float = 0.0
    _previous: State | None = None
    _changed_at: float = 0.0

    def set_state(self, state: State, goal: bool = False) -> None:
        if state is not self.state:
            self._previous = self.state
            self._changed_at = self.t
            self.state = state
        self.goal = goal

    def tick(self) -> None:
        if not self.paused:
            self.t += 1.0 / FPS

    def frame_at(self, t: float | None = None) -> list[str]:
        t = self.t if t is None else t
        fade = DUR_MS["base"] / 1000.0
        since = t - self._changed_at
        if self._previous is not None and 0.0 <= since < fade and not self.reduced and self.caps.unicode:
            k = ease_standard(since / fade)
            if self.caps.color == "truecolor":
                cells = _ring(self.state, t, goal=self.goal, level=self.level, envelope=self.envelope,
                              progress=self.progress, segments=self.segments)
                # Colour runs from the OLD state's C to the new one's.
                return _rows_from_cells(cells, self._previous, self.caps, crossfade=(self.state, k))
            shown = self._previous if k < 0.5 else self.state
            return frame(shown, t, goal=self.goal, level=self.level, envelope=self.envelope,
                         progress=self.progress, segments=self.segments, caps=self.caps)
        return frame(self.state, t, goal=self.goal, level=self.level, envelope=self.envelope,
                     progress=self.progress, segments=self.segments, caps=self.caps, reduced=self.reduced)
