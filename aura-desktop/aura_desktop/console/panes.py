"""From a layout document to cells: where every pane goes, per mode.

The document is the settings schema's ``LayoutDocument`` — ``{mode, tree,
panels}`` — exactly as a ``hud.layout.apply`` frame carries it and as
``tests/contracts/hud-layout.json`` pins it. This module never changes a
document (the plugin is the authority on every operation, and the web follows
it); it only reads one and answers "which cells". ``tests/test_console_panes.py``
replays the contract's op-case table through :func:`arrange`, so every tree
the plugin can produce has been laid out here at least once.

The walk is the web's (``rects.ts``): a ``row`` split divides the columns and a
``column`` split the rows, the first child takes ``ratio`` of the axis, and
the split position is what is rounded — never the two children on their own,
which is how a one-cell seam appears between neighbours. One cell of gap sits
between siblings so two hairline edges never fuse into one thick bar.

What degrades, and how (GLASS_HUD §9.7): Floating tiles in the reading order
of the rects, because cells cannot overlap without a compositor; Ambient hides
every panel and centres the reactor; a pane that would fall below
:data:`MIN_W` × :data:`MIN_H` is hidden and counted, not drawn as a sliver a
border would not fit in. Nothing here knows the §9.1 envelopes: the plugin has
none either, and a terminal of 80 columns cannot honour a 4-unit minimum
anyway, so the floor is the one a border needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

__all__ = [
    "Rect",
    "Placed",
    "Rail",
    "Arrangement",
    "tile",
    "arrange",
    "ordinal",
    "neighbour",
    "MIN_W",
    "MIN_H",
    "GAP",
    "STRIP_H",
    "RAIL_W",
    "RAIL_MIN_COLS",
    "MODES",
]

#: The smallest box a border fits round with one cell of content inside.
MIN_W = 4
MIN_H = 3
#: Cells between two tiled neighbours.
GAP = 1
#: The status strip at the bottom: two rows, because the reactor's ring is two rows.
STRIP_H = 2
#: The Focus rail's width, and the narrowest terminal that still gets one.
RAIL_W = 24
RAIL_MIN_COLS = 60
#: The four presets, in the order ``alt-m`` cycles them on the web. They were
#: window-manager modes until v3 retired the tiler (ADR-0036); the console keeps
#: drawing panes, because a terminal has no third dimension to put them in, but
#: the names are the map's.
MODES: tuple[str, ...] = ("overview", "task", "focus", "ambient")

Tree = Any  # str | {"split", "ratio", "children"} | None — the schema's shape


@dataclass(frozen=True)
class Rect:
    """A box in cells, from the terminal's top-left."""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def fits(self) -> bool:
        return self.w >= MIN_W and self.h >= MIN_H


@dataclass(frozen=True)
class Placed:
    """One drawn panel: its id and type, its box, whether it has the keyboard."""

    pid: str
    type: str
    rect: Rect
    focused: bool = False
    pinned: bool = False


@dataclass(frozen=True)
class Rail:
    """Focus mode's rail: the other panels as one single-line pane of titles."""

    rect: Rect
    pids: tuple[str, ...]


@dataclass(frozen=True)
class Arrangement:
    """Everything the renderer needs for one frame's geometry."""

    mode: str
    room: Rect
    #: In reading order — the tree's leaf order, the phone's stack order.
    placed: tuple[Placed, ...]
    #: Panels the document holds that this frame does not draw, and why not
    #: is the mode: hidden in Ambient, orphaned in Tiled, or too small.
    hidden: tuple[str, ...]
    #: The bottom strip the reactor and the state line live in.
    strip: Rect
    #: The reactor's own cells (two rows of three): in the strip, or the centre.
    reactor: Rect
    rail: Rail | None = None

    @property
    def focused(self) -> str | None:
        for p in self.placed:
            if p.focused:
                return p.pid
        return None

    def rects(self) -> dict[str, Rect]:
        return {p.pid: p.rect for p in self.placed}


# --- the tree ---------------------------------------------------------------------


def _leaves(tree: Tree) -> list[str]:
    """Leaf ids in reading order. The plugin's ``leaves`` has the same walk; the
    console cannot import the plugin (it ships to a workstation without it),
    and the contract's ``leaves`` cases are replayed against this one to hold
    the two to the same order."""
    if tree is None:
        return []
    if isinstance(tree, str):
        return [tree]
    return [leaf for child in tree["children"] for leaf in _leaves(child)]


def tile(tree: Tree, area: Rect, gap: int = GAP) -> dict[str, Rect]:
    """Every leaf's box inside ``area``, keyed by id, in reading order.

    Boxes may come out smaller than :data:`MIN_W` × :data:`MIN_H` — or empty
    — when the area is; the caller decides what to hide. They never overlap
    and never leave ``area``.
    """
    out: dict[str, Rect] = {}

    def walk(node: Tree, box: Rect) -> None:
        if node is None:
            return
        if isinstance(node, str):
            out[node] = box
            return
        horizontal = node["split"] == "row"
        extent = box.w if horizontal else box.h
        usable = max(extent - gap, 0)
        # The split position is rounded once; both children are cut from it.
        first = int(usable * float(node["ratio"]) + 0.5)
        first = max(0, min(usable, first))
        second = usable - first
        a, b = node["children"]
        # The second child starts after the gap; an area too small for a gap
        # gives it zero cells at the far edge rather than a cell outside.
        start = min(first + gap, extent)
        if horizontal:
            walk(a, Rect(box.x, box.y, first, box.h))
            walk(b, Rect(box.x + start, box.y, second, box.h))
        else:
            walk(a, Rect(box.x, box.y, box.w, first))
            walk(b, Rect(box.x, box.y + start, box.w, second))

    walk(tree, area)
    return out


def _balanced(ids: list[str], depth: int = 0) -> Tree:
    """A tree of even splits over ``ids`` in order, rows and columns alternating
    by depth — what Floating degrades to, since its rects cannot overlap here."""
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]
    half = (len(ids) + 1) // 2
    return {
        "split": "row" if depth % 2 == 0 else "column",
        "ratio": 0.5,
        "children": [_balanced(ids[:half], depth + 1), _balanced(ids[half:], depth + 1)],
    }


# --- the modes --------------------------------------------------------------------


def _panels(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in doc.get("panels", []) if isinstance(p, dict) and p.get("id")]


def _reading_order(panels: Iterable[dict[str, Any]]) -> list[str]:
    """Floating panels by where their rect puts them: top to bottom, then left
    to right. A panel with no rect sorts first, where the plugin would have
    staggered it."""
    def key(p: dict[str, Any]) -> tuple[float, float]:
        rect = p.get("rect") or {}
        return (float(rect.get("y", 0.0)), float(rect.get("x", 0.0)))

    return [p["id"] for p in sorted(panels, key=key)]


def arrange(doc: dict[str, Any], cols: int, rows: int, focused: str | None = None) -> Arrangement:
    """The frame's geometry for ``doc`` on a ``cols`` × ``rows`` terminal.

    ``focused`` is the pane holding the keyboard; when it names nothing drawn,
    the first drawn pane takes it, so there is always somewhere for a key to
    go while anything is on screen. An unknown mode is drawn as Tiled rather
    than refused: the schema already refused it upstream, and a console that
    blanked on a frame it did not expect would be worse than one that tiled.
    """
    cols = max(cols, 0)
    rows = max(rows, 0)
    room = Rect(0, 0, cols, rows)
    strip = Rect(0, max(rows - STRIP_H, 0), cols, min(STRIP_H, rows))
    region = Rect(0, 0, cols, max(rows - STRIP_H, 0))
    panels = _panels(doc)
    types = {p["id"]: str(p.get("type", "")) for p in panels}
    pinned = {p["id"] for p in panels if p.get("pinned")}
    mode = str(doc.get("mode") or "task")
    tree: Tree = doc.get("tree")
    rail: Rail | None = None

    if mode == "ambient":
        # §9.5: the reactor at the centre of the room, every panel hidden (not
        # closed), one pane strip at the bottom. Three rows for the strip so it
        # can carry its own single-line border.
        strip = Rect(0, max(rows - 3, 0), cols, min(3, rows))
        reactor = Rect(max((cols - 3) // 2, 0), max(strip.y // 2 - 1, 0), 3, 2)
        return Arrangement(mode, room, (), tuple(types), strip, reactor)

    reactor = Rect(strip.x + 1, strip.y, 3, strip.h)
    if mode == "focus":
        order = _leaves(tree)
        main = order[0] if order and order[0] in types else (next(iter(types)) if types else None)
        others = [pid for pid in types if pid != main]
        boxes: dict[str, Rect] = {}
        if main is not None:
            if others and cols >= RAIL_MIN_COLS:
                rail = Rail(Rect(cols - RAIL_W, 0, RAIL_W, region.h), tuple(others))
                boxes[main] = Rect(0, 0, cols - RAIL_W - GAP, region.h)
            else:
                boxes[main] = region
    elif mode == "overview":
        boxes = tile(_balanced(_reading_order(panels)), region)
    else:
        boxes = tile(tree, region)

    drawn = [(pid, rect) for pid, rect in boxes.items() if pid in types and rect.fits()]
    shown = {pid for pid, _ in drawn}
    if focused not in shown:
        focused = drawn[0][0] if drawn else None
    placed = tuple(Placed(pid, types[pid], rect, pid == focused, pid in pinned) for pid, rect in drawn)
    hidden = tuple(pid for pid in types if pid not in shown and not (rail and pid in rail.pids))
    return Arrangement(mode, room, placed, hidden, strip, reactor, rail)


# --- moving focus -----------------------------------------------------------------


def ordinal(arrangement: Arrangement, n: int) -> str | None:
    """``alt-1…9``: the n-th drawn pane in reading order, or nothing."""
    if n < 1 or n > len(arrangement.placed):
        return None
    return arrangement.placed[n - 1].pid


def _overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    """More than a shared edge, or every tiled neighbour would qualify everywhere."""
    return min(a1, b1) - max(a0, b0) > 0


def neighbour(arrangement: Arrangement, pid: str, direction: str) -> str | None:
    """The pane ``alt-shift-h/j/k/l`` moves to: the web's rule (``rects.ts``).

    A candidate must straddle the origin on the other axis; among those, one
    level with the origin's centre wins, then the nearest along the direction,
    then the nearest across. The web's last tier — anything at all in that
    direction, for diagonal floating layouts — is not needed here, because
    Floating tiles.
    """
    rects = arrangement.rects()
    origin = rects.get(pid)
    if origin is None:
        return None
    horizontal = direction in ("left", "right")
    cx, cy = origin.centre
    best: tuple[int, float, float, str] | None = None
    for other, box in rects.items():
        if other == pid:
            continue
        ox, oy = box.centre
        dx, dy = ox - cx, oy - cy
        along = {"left": -dx, "right": dx, "up": -dy, "down": dy}[direction]
        if along <= 0:
            continue
        straddles = (_overlaps(origin.y, origin.bottom, box.y, box.bottom) if horizontal
                     else _overlaps(origin.x, origin.right, box.x, box.right))
        if not straddles:
            continue
        level = (box.y <= cy <= box.bottom) if horizontal else (box.x <= cx <= box.right)
        across = abs(dy) if horizontal else abs(dx)
        rank = (0 if level else 1, along, across, other)
        if best is None or rank < best:
            best = rank
    return best[3] if best else None
