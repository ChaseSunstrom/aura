"""The console theme, read from the generated file and painted per terminal.

``console_theme.json`` beside this module is ``design/generated/console-theme.json``
written a second time by ``design/build.py`` — the palette with its 256- and
16-colour fallbacks, the type roles as SGR attributes, the border sets, the
reactor's glyphs. Nothing in this module is a colour: every escape is built
from a palette entry looked up by name, so a retuned token reaches the
terminal at the next ``design/build.py`` and the token lint has nothing to find.

The two ladders of GLASS_HUD §9.7 are decided by :class:`Caps`, which is the
reactor's (there is one detector in the package, not two that could disagree
about the same ``TERM``):

* colour — truecolor paints the hex; 256 the ``xterm256`` slot; 16 the
  ``ansi16`` slot; ``none`` paints nothing at all, so a pipe or ``NO_COLOR``
  gets clean text;
* glyphs — a UTF-8 locale gets the double and single box sets; anything else
  the ASCII set, and the reactor's ASCII glyphs through :mod:`reactor`.

Not covered: a terminal that re-themes its sixteen system colours shows the
16-colour tier a shade off, which is the most any fixed table can promise, and
no attempt is made to read the terminal's background — the palette assumes the
dark room of §2, as every surface does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Iterable

from .. import reactor
from ..reactor import Caps, detect

__all__ = [
    "Caps",
    "detect",
    "Colour",
    "Box",
    "Theme",
    "load",
    "RESET",
    "visible_width",
]

#: The packaged copy of the generated theme.
THEME_FILE = "console_theme.json"

#: The escape that ends any run this module starts.
RESET = "\x1b[0m"

#: SGR attribute names the theme uses -> their codes. ``reverse`` is what the
#: control grade is made of on a terminal (``material.control.sgr``): a filled
#: control with no fill colour is the foreground and background swapped.
ATTRS: dict[str, str] = {"bold": "1", "dim": "2", "italic": "3", "underline": "4", "reverse": "7"}

Grade = str  # "panel" | "pane"


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    """The theme as a dict, read once. Raises if the package shipped without it —
    a console that silently ran uncoloured would hide exactly the packaging
    mistake ``pyproject.toml``'s package-data line exists to prevent."""
    text = resources.files(__package__).joinpath(THEME_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


@dataclass(frozen=True)
class Colour:
    """One palette entry: the hex the tokens hold and its two fallback slots."""

    name: str
    hex: str
    rgb: tuple[int, int, int]
    xterm256: int
    ansi16: int


@dataclass(frozen=True)
class Box:
    """A border set: the six glyphs a rectangle is drawn with."""

    h: str
    v: str
    tl: str
    tr: str
    bl: str
    br: str


def _code(slot: int, *, background: bool) -> str:
    """The 16-colour SGR for an ``ansi16`` slot: 30–37 / 90–97, or 40–47 / 100–107."""
    base = (40 if background else 30) if slot < 8 else (100 if background else 90) - 8
    return str(base + slot)


class Theme:
    """The theme with a way to paint on a given :class:`Caps`.

    Constructed from :func:`load` by default; a test can hand in a dict to
    prove a rule without the real file, but nothing in the package does.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = load() if data is None else data
        self._palette: dict[str, Colour] = {
            name: Colour(name, entry["hex"], tuple(entry["rgb"]), entry["xterm256"], entry["ansi16"])
            for name, entry in self.data["palette"].items()
        }

    # --- the palette ------------------------------------------------------------

    @property
    def palette(self) -> dict[str, Colour]:
        return self._palette

    def colour(self, name: str) -> Colour:
        """A palette entry by its token name (``accent``, ``text-dim``, ``reactor-alert``).

        A name the theme lacks is a KeyError on purpose: the caller typed a
        colour that no token defines, which is the one thing this module
        refuses to paper over with a default.
        """
        return self._palette[name]

    def reactor_colour(self, state: reactor.State | str) -> str:
        """The palette name of a reactor state's C (``reactor.states.<state>.color``)."""
        key = state.value if isinstance(state, reactor.State) else str(state)
        return str(self.data["reactor"]["states"][key]["color"])

    def state_attrs(self, state: reactor.State | str) -> tuple[str, ...]:
        """The SGR attributes the theme gives a state (``dim`` for warming and offline)."""
        key = state.value if isinstance(state, reactor.State) else str(state)
        return tuple(self.data["reactor"]["states"][key]["sgr"])

    # --- painting ---------------------------------------------------------------

    def sgr(self, fg: str | None, caps: Caps, *, attrs: Iterable[str] = (),
            bg: str | None = None) -> str:
        """The escape that sets ``fg``/``bg`` (palette names) and ``attrs`` on this terminal.

        Empty on the ``none`` tier, attributes included: a terminal that gets
        no colour gets no bold either, because the only such terminals are
        pipes, ``TERM=dumb`` and a person who set ``NO_COLOR`` to say "plain".
        """
        if caps.color == "none":
            return ""
        parts: list[str] = []
        for name in attrs:
            parts.append(ATTRS[name])
        for palette_name, background in ((fg, False), (bg, True)):
            if palette_name is None:
                continue
            colour = self.colour(palette_name)
            if caps.color == "truecolor":
                r, g, b = colour.rgb
                parts.append(f"{48 if background else 38};2;{r};{g};{b}")
            elif caps.color == "256":
                parts.append(f"{48 if background else 38};5;{colour.xterm256}")
            else:
                parts.append(_code(colour.ansi16, background=background))
        return "\x1b[" + ";".join(parts) + "m" if parts else ""

    def paint(self, text: str, fg: str | None, caps: Caps, *, attrs: Iterable[str] = (),
              bg: str | None = None) -> str:
        """``text`` painted, and the paint ended, or ``text`` untouched."""
        esc = self.sgr(fg, caps, attrs=attrs, bg=bg)
        return f"{esc}{text}{RESET}" if esc else text

    def role(self, name: str) -> tuple[str, tuple[str, ...]]:
        """A type role (``title``, ``body``, ``secondary``, ``figure`` …) as its
        palette colour and SGR attributes — the console's whole type scale,
        since a terminal has one size (``type.$rule``)."""
        entry = self.data["type"][name]
        return str(entry["color"]), tuple(entry["sgr"])

    # --- glyphs -----------------------------------------------------------------

    def box(self, grade: Grade, caps: Caps) -> Box:
        """The border set for a grade: a single hairline for ``panel`` and
        ``pane`` alike (ADR-0031), and the ASCII set for both where the
        locale is not UTF-8."""
        key = grade if caps.unicode else "ascii"
        entry = self.data["borders"][key]
        return Box(entry["h"], entry["v"], entry["tl"], entry["tr"], entry["bl"], entry["br"])

    def divider(self, caps: Caps) -> str:
        """The rule inside a panel (``borders.divider``), or its ASCII stand-in."""
        return str(self.data["borders"]["divider"]) if caps.unicode else str(self.data["borders"]["ascii"]["h"])

    def live(self, caps: Caps) -> str:
        """The live-row mark (``borders.live``), or its ASCII stand-in."""
        return str(self.data["borders"]["live"]) if caps.unicode else str(self.data["borders"]["ascii"]["v"])

    @property
    def focused_attrs(self) -> tuple[str, ...]:
        """What the facing edge of the focused panel adds (``borders.focused.sgr``)."""
        return tuple(self.data["borders"]["focused"]["sgr"])

    @property
    def fps(self) -> int:
        """``motion.fps``: how often the strip's reactor is resampled (§8.1)."""
        return int(self.data["motion"]["fps"])


def visible_width(text: str) -> int:
    """Cells a painted string occupies: the text without its escapes.

    Every glyph the console draws is one cell wide — the box sets, the arcs,
    the palette of marks — and the content it prints is clipped by this rule,
    so the frame's columns add up. A wide glyph from a reply (CJK, an emoji)
    counts as one here and overruns by one on screen; the console does not
    carry a width table for the sake of a character the model rarely emits.
    """
    out = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b" and i + 1 < n and text[i + 1] == "[":
            j = i + 2
            while j < n and text[j] not in "m":
                j += 1
            i = j + 1
            continue
        out += 1
        i += 1
    return out
