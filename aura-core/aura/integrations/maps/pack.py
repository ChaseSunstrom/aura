"""A region pack: one SQLite file of a region's footprints, roads and places (T-0979).

    python3 pack.py OUT.pack NAME WEST,SOUTH,EAST,NORTH [--osm F] [--overture F] [--roads F] [--places F]
                    [--detail F] [--ground F]

The lists the house used to hold in memory do not fit a state (182 MiB of Python
per 50 km box), so a region is a file indexed by cell: the house answers
``/api/maps/buildings`` from it per ask, and the phone downloads the same file and
reads it with its own SQLite when the house is out of reach.

The file (``FORMAT`` 1; a reader refuses another number):

* ``meta(key, value)``: ``format``, ``name``, ``bbox`` ("w,s,e,n"), ``built`` (unix
  seconds), the four counts and, when the pack holds a road (T-1048), ``coverage``: a bit per
  cell of the bbox, set near a road, so a router can tell an end in a hole from one on the map
  (``coverage()``; the contract's ``coverage`` has the layout). A pack from before it has none.
* ``features(id, kind, w, s, e, n, h, tag, name, extra, geom)``: ``kind`` is ``b``
  (footprint: ``h`` metres or NULL, ``tag`` its source, ``geom`` the outer ring, ``extra``
  what the map knows of its look: ``colour``, ``roof_colour``, ``roof_shape``,
  ``roof_height_m``, ``roof_dir`` (degrees, the way the roof slopes), ``roof_orient``
  (``along``/``across`` the longest wall; T-1185), ``min_height_m``, ``kind``, ``part`` for a ``building:part``, and ``sign`` with ``trade``
  where a named business is in it: its own tags, else a named place inside it or within
  SIGN_REACH_M of its wall, T-1106),
  ``r`` (road: ``tag`` the highway class, ``extra`` its ``oneway`` (1 along the line, -1
  against it), ``kph`` where posted, ``lanes`` and, T-1167, ``ref``, ``bridge``,
  ``tunnel`` and ``layer`` and, T-1183, ``destination``, ``destination:ref``,
  ``junction:ref`` and ``traffic_sign`` (``ROAD_TAGS``) and, T-1235, ``turn:lanes`` and its
  ``:forward`` / ``:backward`` forms (``TURN_LANE_KEYS``, through ``turn_lanes()``) and, T-1049, ``access`` (``"private"`` or
  ``"no"`` where a car may not pass: kept, not dropped, so a trip to a door on it still ends there), ``geom`` the line: 2 to ``ROAD_MAX_POINTS``
  points of ``[lon, lat]``, finite and on the globe, under ``ROAD_MAX_CHARS`` of JSON, T-1087), ``p`` (place: ``tag``
  its kind, ``extra`` its properties, ``geom`` the point) or ``d`` (a detail of the street,
  T-1066: ``tag`` one of ``POINTS``, ``LINES`` or ``AREAS``, ``geom`` its point as a
  one-point list, its line or its ring; a ring or detail line is held to the road's point rule: a third
  number dropped, one the phone would refuse left out and counted in ``skipped_buildings`` /
  ``skipped_details``, a line past the caps cut, T-1226) or ``g`` (the ground's height over one cell, T-1105:
  ``name`` the cell as ``"cx,cy"`` (its south-west corner in hundredths of a degree), ``tag``
  the samples a side, ``geom`` that many squared whole metres above sea level, rows south to
  north, each west to east, first and last on the cell's edges; it is indexed under its own
  cell only). ``d``, ``g`` and the looks came after the first packs and
  the format is still 1: a reader asks for the kinds it knows. ``geom`` is JSON,
  ``[lon, lat]`` points. Ids are in the order written: OSM footprints, then the
  Overture ones no OSM footprint overlaps.
* ``cells(cell, fid)``: every ``CELL_DEG`` cell a feature's box touches; ``cell()``
  is the number, and ``tests/contracts/map-pack.json`` binds it to the phone's.

Stdlib only, and no import from the package, so it also runs as a script where
the house is not installed (``scripts/map-region.sh``).

Not covered: a footprint with holes or several parts is its first outer ring; a building
drawn in parts is in the pack as its outline and as each part, and which outline a part
belongs to is not said; a colour is the mapper's word or a usual one for the material,
never a measured one; the
inputs are trusted to be the region's (nothing is cut at the bbox, which is only
what the pack says it covers); two packs holding the same feature are told apart
by its geometry alone.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterator

FORMAT = 1
CELL_DEG = 0.01  # ~1 km: a drive-view ask (at most 0.1 degrees) reads about a hundred cells
_PER_DEG = 100  # 1 / CELL_DEG, as the integer both readers multiply by
_COLS = 36000
#: T-1048: a bbox of more cells than this writes no coverage row (the contract's ``coverage.max_cells``;
#: 4 million cells is 500 KB of bits, a 20 degree square): the readers answer the bbox for such a pack.
COVERAGE_MAX_CELLS = 4_000_000
M_PER_LEVEL = 3.0
_FEET = 0.3048
_HEIGHT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(m|ft|')?\s*$")

Box = tuple[float, float, float, float]

#: The details a pack holds, by the shape of their ``geom`` (tests/contracts/map-pack.json).
POINTS = frozenset({
    "tree", "lamp", "signal", "crossing",
    "stop", "give_way", "speed_sign", "sign", "speed_camera", "camera", "alpr",  # T-1073
    "bollard", "gate",  # T-1104
})
LINES = frozenset({"tree_row", "roadworks", "barrier"})
#: A sign's words are a few characters on a plate; longer is not a sign's text.
TEXT_MAX = 24
REF_MAX = 40  # a route number is "I 95" or "A 4;E 15": longer is a tagging mistake
#: T-1183: what a guide sign says, by OSM key, and the most the pack keeps of each; the contract
#: (tests/contracts/map-pack.json `roadTags`) and both readers repeat it.
ROAD_TAGS = {"destination": 120, "destination:ref": 40, "junction:ref": 12, "traffic_sign": 60}
#: T-1235: what each lane of a road may do at its end, by OSM key (the contract's `turnLanes`):
#: `turn:lanes` for a one-way road, `:forward` and `:backward` for a two-way one. Kept whole or not
#: at all: a cut list would put an arrow in the wrong lane.
TURN_LANE_KEYS = ("turn:lanes", "turn:lanes:forward", "turn:lanes:backward")
TURN_LANES_MAX = 80
TURN_LANES_MAX_LANES = 8
_TURN_LANES = re.compile(r"[a-z_;|]+")
#: T-1049: a road a car may not pass carries `access` with one of these (the contract's `access`);
#: any other value is dropped on the way in and on the way out, so the phone sees only these two.
ACCESS_KEY = "access"
ACCESS_CLOSED = ("private", "no")
AREAS = frozenset({"parking", "bay", "construction", "water", "green", "wood", "field", "yard", "paved"})  # T-1188: land cover
#: A shop sign's words: longer is a description, not a fascia.
SIGN_MAX = 32
#: A named place gives its name to the footprint it is in, or to the nearest within this.
SIGN_REACH_M = 15.0
#: A derived limit plate (T-1122): how far into its road it stands, and how near a mapped sign
#: saying the same must be for the plate to be left out.
PLATE_IN_M = 8.0
PLATE_NEAR_M = 30.0
#: The place kinds that hang a sign, best first; a named car park or bench is not a business.
_TRADES = ("shop", "amenity", "tourism", "office", "craft", "healthcare")
_NO_SIGN = frozenset({"parking", "parking_space", "parking_entrance", "bicycle_parking", "bench", "shelter",
                      "toilets", "waste_basket", "recycling", "post_box", "drinking_water", "fountain"})
#: Samples a side of a cell's ground heights (scripts/osm_ground.py writes them).
GROUND_GRID = 17

#: The colour names mappers write most (CSS's), and a usual colour for a material.
_NAMES = {
    "white": "#ffffff", "black": "#000000", "grey": "#808080", "gray": "#808080", "red": "#ff0000",
    "brown": "#a52a2a", "yellow": "#ffff00", "beige": "#f5f5dc", "green": "#008000", "blue": "#0000ff",
    "orange": "#ffa500", "silver": "#c0c0c0", "tan": "#d2b48c", "maroon": "#800000", "pink": "#ffc0cb",
    "lightgrey": "#d3d3d3", "lightgray": "#d3d3d3", "darkgrey": "#a9a9a9", "darkgray": "#a9a9a9",
    "ivory": "#fffff0", "cream": "#fffdd0", "darkred": "#8b0000", "darkgreen": "#006400", "navy": "#000080",
}
MATERIALS = {
    "brick": "#a5503c", "concrete": "#b4b4ae", "glass": "#8fb4c8", "wood": "#9c6b3f", "stone": "#9a9588",
    "sandstone": "#c9b28a", "metal": "#9aa3a8", "steel": "#9aa3a8", "plaster": "#e2dccd", "stucco": "#e2dccd",
    "limestone": "#d6d0bd", "roof_tiles": "#a3523a", "tile": "#a3523a", "slate": "#5b6470", "copper": "#5fa08c",
    "tar_paper": "#3c3c3e", "asphalt": "#3c3c3e", "gravel": "#8e8a82", "thatch": "#b59a5a", "grass": "#6f8f4f",
}
_HEX = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6})$")

_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE features(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, w REAL NOT NULL, s REAL NOT NULL,
  e REAL NOT NULL, n REAL NOT NULL, h REAL, tag TEXT NOT NULL, name TEXT NOT NULL, extra TEXT, geom TEXT NOT NULL);
CREATE TABLE cells(cell INTEGER NOT NULL, fid INTEGER NOT NULL, PRIMARY KEY(cell, fid)) WITHOUT ROWID;
"""


def cell(lon: float, lat: float) -> int:
    """The cell holding a point: rows of ``_COLS`` cells, south to north."""
    return (math.floor(lat * _PER_DEG) + 9000) * _COLS + math.floor(lon * _PER_DEG) + 18000


def cell_rows(box: Box) -> Iterator[tuple[int, int]]:
    """(first, last) cell of each row of cells [box] touches."""
    w, s, e, n = box
    for y in range(math.floor(s * _PER_DEG), math.floor(n * _PER_DEG) + 1):
        yield cell(w, y / _PER_DEG + CELL_DEG / 2), cell(e, y / _PER_DEG + CELL_DEG / 2)


def _grid(bbox: Box) -> tuple[int, int, int, int] | None:
    """The first cell and the size of [bbox]'s coverage grid, or None when it is over COVERAGE_MAX_CELLS."""
    w, s, e, n = bbox
    x0, y0 = math.floor(w * _PER_DEG), math.floor(s * _PER_DEG)
    cols, rows = math.floor(e * _PER_DEG) - x0 + 1, math.floor(n * _PER_DEG) - y0 + 1
    return None if cols < 1 or rows < 1 or cols * rows > COVERAGE_MAX_CELLS else (x0, y0, cols, rows)


def _touched(pts: list, lit: set[tuple[int, int]], bbox: Box) -> None:
    """Add to [lit] every cell the box of each segment of [pts] touches; a point that is not finite adds none.

    Only cells of [bbox]'s grid and the ring round it are walked, and none when the grid is too wide to
    be written: a way that runs far outside the box (or one bad far-off point) would otherwise fill
    memory with cells nobody reads."""
    grid = _grid(bbox)
    if grid is None:
        return
    x0, y0, cols, rows = grid
    try:
        xy = [(float(p[0]), float(p[1])) for p in pts]
    except (TypeError, ValueError, IndexError):
        return
    if not all(math.isfinite(v) for p in xy for v in p):
        return
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        for cy in range(max(y0 - 1, math.floor(min(y1, y2) * _PER_DEG)), min(y0 + rows, math.floor(max(y1, y2) * _PER_DEG)) + 1):
            for cx in range(max(x0 - 1, math.floor(min(x1, x2) * _PER_DEG)), min(x0 + cols, math.floor(max(x1, x2) * _PER_DEG)) + 1):
                lit.add((cx, cy))


def coverage(bbox: Box, lit: set[tuple[int, int]]) -> str | None:
    """The coverage row (T-1048, ``tests/contracts/map-pack.json`` ``coverage``): base64 of a bit per cell
    of [bbox], set when a cell of [lit], or one of the eight round it, is that cell. None when [lit] is
    empty (a pack with no road says nothing of where roads are) or the grid is over COVERAGE_MAX_CELLS.

    It says where the extract has roads, not that a road there may be driven: a cell of open water
    beside a shore is set by the shore's neighbours, and a pond in the middle of a roadless area is not.
    """
    grid = _grid(bbox)
    if not lit or grid is None:
        return None
    x0, y0, cols, rows = grid
    bits = bytearray((cols * rows + 7) // 8)
    for cx, cy in lit:
        for gy in range(cy - 1 - y0, cy + 2 - y0):
            for gx in range(cx - 1 - x0, cx + 2 - x0):
                if 0 <= gx < cols and 0 <= gy < rows:
                    i = gy * cols + gx
                    bits[i >> 3] |= 1 << (i & 7)
    return base64.b64encode(bytes(bits)).decode()


def metres(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    m = _HEIGHT.match(str(value or ""))
    if not m:
        return None
    v = float(m.group(1)) * (_FEET if m.group(2) in ("ft", "'") else 1.0)
    return v if v > 0 else None


def height(props: dict[str, Any]) -> float | None:
    h = metres(props.get("height")) or metres(props.get("building:height"))
    if h:
        return h
    levels = metres(props.get("building:levels") or props.get("num_floors"))
    return levels * M_PER_LEVEL if levels else None


def roof_height(props: dict[str, Any]) -> float | None:
    """The roof's own height in metres: ``roof:height``, else ``roof:levels`` of M_PER_LEVEL (T-1185)."""
    h = metres(props.get("roof:height"))
    if h:
        return h
    levels = metres(props.get("roof:levels"))
    return levels * M_PER_LEVEL if levels else None


def roof_dir(said: Any) -> float | None:
    """``roof:direction`` as degrees clockwise from north in [0, 360), else None (a compass word is not read)."""
    try:
        d = float(str(said).strip())
    except ValueError:
        return None
    return d % 360 if d == d and abs(d) < 1e6 else None


def colour(said: Any, material: Any = None) -> str | None:
    """``#rrggbb`` for a colour tag (a hex or a name), else the usual colour of [material], else None."""
    c = str(said or "").strip().lower().replace(" ", "")
    c = _NAMES.get(c, c)
    if _HEX.match(c):
        return c if len(c) == 7 else "#" + "".join(ch * 2 for ch in c[1:])
    return MATERIALS.get(str(material or "").strip().lower())


def look(p: dict[str, Any]) -> dict[str, Any]:
    """What OSM's or Overture's properties say of a building beyond its height; only what is known."""
    kind = str(p.get("building") or p.get("class") or p.get("subtype") or "")
    low = metres(p.get("min_height"))
    if low is None and (levels := metres(p.get("building:min_level"))):
        low = levels * M_PER_LEVEL
    out = {
        "colour": colour(p.get("building:colour") or p.get("facade_color"), p.get("building:material") or p.get("facade_material")),
        "roof_colour": colour(p.get("roof:colour") or p.get("roof_color"), p.get("roof:material") or p.get("roof_material")),
        "roof_shape": str(p.get("roof:shape") or p.get("roof_shape") or "")[:24] or None,
        "min_height_m": low,
        "roof_height_m": roof_height(p),
        "roof_dir": roof_dir(p.get("roof:direction")),
        "roof_orient": orient if (orient := str(p.get("roof:orientation") or "").strip().lower()) in ("along", "across") else None,
        "kind": kind[:24] if kind not in ("", "yes") else None,
        "part": bool(p.get("building:part")) and p.get("building:part") != "no" or None,
    }
    trade = str(p.get("shop") or p.get("amenity") or "").strip().lower()
    name = sign(p.get("name") or p.get("brand"))
    if name and trade and trade not in _NO_SIGN and trade not in ("yes", "no", "vacant"):
        out["sign"], out["trade"] = name, trade[:24]
    return {k: v for k, v in out.items() if v}


def sign(said: Any) -> str | None:
    """A name as a sign holds it: one line of printable characters, at most SIGN_MAX; else None.

    A format character (a direction override, a zero-width joiner) is refused: a name is a
    mapper's free text, and one that reorders what the driver reads is not a sign.
    """
    s = " ".join(str(said or "").split())
    ok = 0 < len(s) <= SIGN_MAX and s.isprintable() and not any(unicodedata.category(c) == "Cf" for c in s)
    return s if ok else None


def words(said: Any, most: int) -> str | None:
    """A guide sign's words as the pack keeps them: one trimmed printable line, cut at `most`.

    Cut rather than refused (a long list of destinations still starts right), but a format
    character or a control one refuses it, as in sign(). A non-string is no text.
    """
    if not isinstance(said, str):
        return None
    s = " ".join(said.split())[:most]
    ok = bool(s) and s.isprintable() and not any(unicodedata.category(c) == "Cf" for c in s)
    return s if ok else None


def turn_lanes(said: Any) -> str | None:
    """A road's lane-by-lane turns as the pack keeps them: ``left|through;right|right``, lanes left to right.

    Only lowercase words, ``_``, ``;`` and ``|`` and at most TURN_LANES_MAX characters in at
    most TURN_LANES_MAX_LANES lanes; anything else is no list (not cut: a cut list moves every
    arrow). Does not check the words against OSM's values or the list against the road's lanes.
    """
    if not isinstance(said, str):
        return None
    s = said.strip()
    ok = len(s) <= TURN_LANES_MAX and _TURN_LANES.fullmatch(s) and s.count("|") < TURN_LANES_MAX_LANES
    return s if ok else None


def _inside(ring: list, lon: float, lat: float) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _reach_m(ring: list, lon: float, lat: float) -> float:
    """Metres from the point to the ring: 0 inside it, else to its nearest wall."""
    if _inside(ring, lon, lat):
        return 0.0
    kx, ky = 111_320.0 * math.cos(math.radians(lat)), 110_540.0
    best = math.inf
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        ax, ay, bx, by = (x1 - lon) * kx, (y1 - lat) * ky, (x2 - lon) * kx, (y2 - lat) * ky
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def kph(posted: Any) -> int | None:
    """A posted limit ("35 mph", "50", "30 knots") in km/h, or None for one that is no number."""
    m = re.match(r"^\s*(\d{1,3})\s*(mph|km/h|kph)?\s*$", str(posted or ""), re.I)
    if not m or not int(m.group(1)):
        return None
    return round(int(m.group(1)) * 1.609344) if (m.group(2) or "").lower() == "mph" else int(m.group(1))


def _plates(ends: dict[tuple[float, float], list[tuple]], mapped: list[tuple[float, float, str]]) -> Iterator[tuple[float, float, str]]:
    """(lon, lat, number) of a limit plate for each road end where the posted limit changes (T-1122).

    [ends] is, by the point roads meet at, each posted road ending there: (km/h, the number as
    posted, the next point along the road, lanes, whether it may be driven into from here).
    A plate stands PLATE_IN_M into its road, right of someone driving in, unless a mapped
    sign within PLATE_NEAR_M says the same or was read with no number. Does not cover
    left-hand traffic (the plate is on the right there too), a side street met in the middle
    of another road (only ends meet), a road with no posted limit beside one with, or repeats
    along a long road. Where three posted roads meet and one differs, each gets its plate:
    someone going straight on between the two alike passes a plate that repeats their limit.
    """
    for (lon, lat), meet in ends.items():
        if len({m[0] for m in meet}) < 2:
            continue
        per_lon = 111_320.0 * max(0.05, math.cos(math.radians(lat)))
        for _kph, number, (nlon, nlat), lanes, enters in meet:
            dx, dy = (nlon - lon) * per_lon, (nlat - lat) * 111_320.0
            reach = math.hypot(dx, dy)
            if not enters or reach <= 0:
                continue
            ux, uy, t, side = dx / reach, dy / reach, min(PLATE_IN_M, reach), max(lanes, 2) * 1.75 + 1.5
            x, y = ux * t + uy * side, uy * t - ux * side  # right of travel: the heading turned clockwise
            # ponytail: every mapped sign is walked for every plate; a grid if a pack maps thousands
            if any(said in ("", number) and math.hypot((mlon - lon) * per_lon - x, (mlat - lat) * 111_320.0 - y) <= PLATE_NEAR_M
                   for mlon, mlat, said in mapped):
                continue
            yield lon + x / per_lon, lat + y / 111_320.0, number


def _features(path: Path | None, shaped: bool = True) -> Iterator[dict[str, Any]]:
    if path is None or not Path(path).is_file():
        return
    with Path(path).open("rb") as fh:
        for line in fh:
            line = line.strip().lstrip(b"\x1e")  # osmium writes RFC 8142 record separators
            try:
                f = json.loads(line) if line else None
            except ValueError:
                continue
            if isinstance(f, dict) and (not shaped or isinstance(f.get("geometry"), dict)):
                yield f


def _ring(f: dict[str, Any]) -> list | None:
    g = f["geometry"]
    try:
        ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
        ok = g["type"] in ("Polygon", "MultiPolygon") and len(ring) >= 4
        return ring if ok and all(len(p) >= 2 for p in ring) else None
    except (KeyError, IndexError, TypeError):
        return None  # a point, a line or a broken row is not a footprint


def _box(points: list) -> Box:
    xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


#: What the phone's reader keeps of one road (MapPack.kt MAX_POINTS, MAX_GEOM_CHARS). It refuses a road
#: outside them and then routes nowhere in the box, so the writer never emits one (T-1087). The contract's
#: `roadLimits` holds both numbers and test_maps.py and MapPackTest.kt each assert theirs equal it (T-1219).
ROAD_MAX_POINTS = 2000
ROAD_MAX_CHARS = 64 * 1024


def _point(p: Any) -> list[float] | None:
    """[lon, lat] of a point of two real finite numbers inside the globe (a third, the height, dropped: the
    phone refuses a point with three), else None."""
    if not (isinstance(p, list) and len(p) >= 2 and all(type(v) in (int, float) for v in p[:2])):
        return None
    return [p[0], p[1]] if -180.0 <= p[0] <= 180.0 and -90.0 <= p[1] <= 90.0 else None  # False for NaN and inf too


def _road_line(pts: Any, least: int = 2) -> list[list[float]] | None:
    """The line as [lon, lat] pairs, or None when it has under [least] points or one that cannot be trusted.

    Roads, detail lines and building rings all pass through it (T-1226): the phone reads each with one rule.
    Does not check the line is near the pack's box or inside the point and text caps (``_cut`` and ``_fits``).
    """
    if not isinstance(pts, list) or len(pts) < least:
        return None
    line = [_point(p) for p in pts]
    return None if None in line else line  # type: ignore[return-value]


def _fits(line: list[list[float]]) -> bool:
    """Whether the phone reads [line] whole: a ring cannot be cut, so one past either cap is left out."""
    return len(line) <= ROAD_MAX_POINTS and len(json.dumps(line, separators=(",", ":"))) <= ROAD_MAX_CHARS


def _footprint(f: dict[str, Any], counts: dict[str, int], key: str) -> list[list[float]] | None:
    """The ring of [f] the phone reads, or None for no ring (not counted) or one it would drop (counted in [key])."""
    ring = _ring(f)
    if not ring:
        return None
    fit = _road_line(ring, 4)
    if fit is None or not _fits(fit):
        counts[key] = counts.get(key, 0) + 1
        return None
    return fit


def _cut(line: list[list[float]]) -> list[list[list[float]]]:
    """[line] in pieces of at most ROAD_MAX_POINTS points and ROAD_MAX_CHARS of JSON, each two or more
    points, neighbours sharing their joint vertex so the road still joins up and none of it is lost."""
    pieces: list[list[list[float]]] = []
    start, chars = 0, 2
    for i, p in enumerate(line):
        chars += len(json.dumps(p, separators=(",", ":"))) + 1
        if i - start + 1 > ROAD_MAX_POINTS or chars > ROAD_MAX_CHARS:
            pieces.append(line[start:i])
            start, chars = i - 1, 2 + sum(len(json.dumps(q, separators=(",", ":"))) + 1 for q in line[i - 1:i + 1])
    pieces.append(line[start:])
    return pieces


class _Writer:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def add(self, kind: str, box: Box, geom: Any, tag: str = "", name: str = "",
            h: float | None = None, extra: Any = None) -> None:
        cur = self.db.execute(
            "INSERT INTO features(kind, w, s, e, n, h, tag, name, extra, geom) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kind, *box, h, tag, name, json.dumps(extra, separators=(",", ":")) if extra else None,
             json.dumps(geom, separators=(",", ":"))))
        fid = cur.lastrowid
        self.db.executemany("INSERT OR IGNORE INTO cells VALUES (?, ?)", (
            (c, fid) for a, b in cell_rows(box) for c in range(a, b + 1)))

    def near(self, lon: float, lat: float) -> tuple[int, float] | None:
        """(id, metres) of the whole footprint the point is in, or the nearest within SIGN_REACH_M."""
        d = SIGN_REACH_M / 100_000.0  # a little more than the reach, in degrees of latitude
        dx = d / max(0.05, math.cos(math.radians(lat)))  # a degree of longitude narrows toward a pole
        box, best = (lon - dx, lat - d, lon + dx, lat + d), None
        for a, b in cell_rows(box):
            for fid, geom, extra in self.db.execute(
                    "SELECT id, geom, extra FROM features WHERE kind = 'b' AND w <= ? AND e >= ? AND s <= ?"
                    " AND n >= ? AND id IN (SELECT fid FROM cells WHERE cell BETWEEN ? AND ?)",
                    (box[2], box[0], box[3], box[1], a, b)):
                if extra and '"part"' in extra:
                    continue  # a part stands on its building, which takes the sign
                m = _reach_m(json.loads(geom), lon, lat)
                if m <= SIGN_REACH_M and (best is None or (m, fid) < (best[1], best[0])):
                    best = (fid, m)
        return best

    def overlapping(self, box: Box) -> list[tuple[int, float | None]]:
        """(id, height) of the OSM footprints whose box touches [box]."""
        seen: dict[int, float | None] = {}
        for a, b in cell_rows(box):
            for fid, h in self.db.execute(
                    "SELECT id, h FROM features WHERE kind = 'b' AND tag LIKE 'osm%' AND w <= ? AND e >= ?"
                    " AND s <= ? AND n >= ? AND id IN (SELECT fid FROM cells WHERE cell BETWEEN ? AND ?)",
                    (box[2], box[0], box[3], box[1], a, b)):
                seen[fid] = h
        return list(seen.items())


def build(out: Path, name: str, bbox: Box, *, osm: Path | None = None, overture: Path | None = None,
          roads: Path | None = None, places: Path | None = None, detail: Path | None = None,
          ground: Path | None = None) -> dict[str, int]:
    """Write the pack to [out] (``.part`` first, renamed when whole) and return its counts.

    OSM footprints first; an Overture footprint only where no OSM one overlaps it, and an
    Overture height where OSM has the footprint and no height. Blocking, and minutes for a state.
    The ground's heights are not counted: a pack with none is a flat one, not a short one.
    """
    out = Path(out)
    part = out.with_name(out.name + ".part")
    if part.exists():
        part.unlink()
    db = sqlite3.connect(part)
    try:
        db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + _SCHEMA)
        w = _Writer(db)
        counts = {"buildings": 0, "roads": 0, "places": 0, "details": 0}
        for f in _features(osm):
            ring = _footprint(f, counts, "skipped_buildings")
            if ring:
                p = f.get("properties") or {}
                w.add("b", _box(ring), ring, "osm", h=height(p), extra=look(p))
                counts["buildings"] += 1
        for f in _features(overture):
            ring = _footprint(f, counts, "skipped_buildings")
            if not ring:
                continue
            box, h = _box(ring), height(f.get("properties") or {})
            under = w.overlapping(box)
            if not under:
                w.add("b", box, ring, "overture", h=h, extra=look(f.get("properties") or {}))
                counts["buildings"] += 1
            elif h:
                db.executemany("UPDATE features SET h = ?, tag = 'osm+overture' WHERE id = ?",
                               [(h, fid) for fid, had in under if had is None])
        ends: dict[tuple[float, float], list[tuple]] = {}
        lit: set[tuple[int, int]] = set()
        for f in _features(roads):
            pts, p = f["geometry"].get("coordinates"), f.get("properties") or {}
            if f["geometry"].get("type") != "LineString":
                continue
            pts = _road_line(pts)
            if pts is None:  # T-1087: one the phone would refuse, and refuse routing for the whole box with it
                counts["skipped_roads"] = counts.get("skipped_roads", 0) + 1
                continue
            lanes = re.match(r"^\s*(\d{1,2})\s*$", str(p.get("lanes") or ""))
            ref, layer = p.get("ref"), p.get("layer")
            extra = {"oneway": p.get("oneway") if p.get("oneway") in (1, -1) else None, "kph": kph(p.get("maxspeed")),
                     "lanes": int(lanes.group(1)) if lanes else None,
                     # T-1167: the number on the shield and the raised deck; a wrong type is dropped, never served.
                     "ref": ref.strip()[:REF_MAX] if isinstance(ref, str) else None,
                     "bridge": True if p.get("bridge") is True else None,
                     "tunnel": True if p.get("tunnel") is True else None,
                     "layer": max(-5, min(5, layer)) if type(layer) is int else None,
                     ACCESS_KEY: p.get(ACCESS_KEY) if p.get(ACCESS_KEY) in ACCESS_CLOSED else None,  # T-1049
                     **{k: words(p.get(k), most) for k, most in ROAD_TAGS.items()},  # T-1183
                     **{k: turn_lanes(p.get(k)) for k in TURN_LANE_KEYS}}  # T-1235
            if extra["kph"]:
                number = re.match(r"\s*(\d+)", str(p["maxspeed"])).group(1)  # type: ignore[union-attr]
                for way, enters in ((pts, extra["oneway"] != -1), (pts[::-1], extra["oneway"] != 1)):
                    # The first point that is somewhere else: extracts repeat a vertex now and then.
                    here, onward = way[0], next((q for q in way[1:] if q[:2] != way[0][:2]), way[0])
                    ends.setdefault((round(float(here[0]), 7), round(float(here[1]), 7)), []).append(
                        (extra["kph"], number, (float(onward[0]), float(onward[1])), extra["lanes"] or 0, enters))
            for piece in _cut(pts):
                w.add("r", _box(piece), piece, str(p.get("highway") or ""), str(p.get("name") or ""),
                      extra={k: v for k, v in extra.items() if v})
                counts["roads"] += 1
            _touched(pts, lit, bbox)
        signs: dict[int, tuple[int, float, str, str]] = {}
        for f in _features(places):
            try:
                lon, lat = (float(v) for v in f["geometry"]["coordinates"][:2])
            except (KeyError, TypeError, ValueError):
                continue
            p = f.get("properties") or {}
            w.add("p", (lon, lat, lon, lat), [lon, lat], str(p.get("kind") or ""), str(p.get("name") or ""), extra=p)
            counts["places"] += 1
            key, _, trade = str(p.get("kind") or "").partition("=")
            said = sign(p.get("name"))
            if said and key in _TRADES and trade and trade not in _NO_SIGN:
                at = w.near(lon, lat)
                if at:
                    # One sign a building: a shop before an office, then the place nearest its wall.
                    signs[at[0]] = min(signs.get(at[0], (9, 0.0, "", "")), (_TRADES.index(key), at[1], said, trade[:24]))
        for fid, (_rank, _m, said, trade) in signs.items():
            (extra,) = db.execute("SELECT extra FROM features WHERE id = ?", (fid,)).fetchone()
            had = json.loads(extra) if extra else {}
            if "sign" not in had:  # what the building is tagged as itself wins
                db.execute("UPDATE features SET extra = ? WHERE id = ?",
                           (json.dumps({**had, "sign": said, "trade": trade}, separators=(",", ":")), fid))
        mapped: list[tuple[float, float, str]] = []
        for f in _features(detail):
            kind, g = str((f.get("properties") or {}).get("kind") or ""), f["geometry"]
            try:
                if kind in POINTS and g.get("type") == "Point":
                    pts = _road_line([g["coordinates"]], 1)  # T-1226: what the phone refuses is left out, counted
                    pieces = [pts] if pts else []
                elif kind in LINES and g.get("type") == "LineString" and len(g["coordinates"]) >= 2:
                    pts = _road_line(g["coordinates"])
                    pieces = _cut(pts) if pts else []  # a line is cut like a road
                elif kind in AREAS and _ring(f):
                    pts = _footprint(f, counts, "skipped_details")  # counts its own refusal
                    pieces = [pts] if pts else []
                    if not pts:
                        continue
                else:
                    continue  # a kind this pack does not know, or one in the wrong shape
                if not pieces:
                    counts["skipped_details"] = counts.get("skipped_details", 0) + 1
                    continue
                says = (f.get("properties") or {}).get("text")
                says = says.strip() if isinstance(says, str) else ""
                for piece in pieces:
                    w.add("d", _box(piece), piece, kind, extra={"text": says} if 0 < len(says) <= TEXT_MAX else None)
                    counts["details"] += 1
                if kind == "speed_sign":
                    mapped.append((pts[0][0], pts[0][1], says))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        for lon, lat, number in _plates(ends, mapped):
            w.add("d", (lon, lat, lon, lat), [[lon, lat]], "speed_sign", extra={"text": number})
            counts["details"] += 1
        for f in _features(ground, shaped=False):
            try:
                cx, cy = (int(v) for v in f["cell"])
                hs = [int(v) for v in f["h"]]
            except (KeyError, TypeError, ValueError):
                continue
            if len(hs) != GROUND_GRID * GROUND_GRID or not (-18000 <= cx < 18000 and -9000 <= cy < 9000):
                continue
            # Its own cell only: the box touches the eight round it, which hold their own heights.
            cur = db.execute(
                "INSERT INTO features(kind, w, s, e, n, h, tag, name, extra, geom) VALUES ('g',?,?,?,?,NULL,?,?,NULL,?)",
                (cx / _PER_DEG, cy / _PER_DEG, (cx + 1) / _PER_DEG, (cy + 1) / _PER_DEG, str(GROUND_GRID),
                 f"{cx},{cy}", json.dumps(hs, separators=(",", ":"))))
            db.execute("INSERT OR IGNORE INTO cells VALUES (?, ?)", ((cy + 9000) * _COLS + cx + 18000, cur.lastrowid))
        meta = {"format": FORMAT, "name": name, "bbox": ",".join(repr(float(v)) for v in bbox),
                "built": int(time.time()), **counts}
        if (row := coverage(bbox, lit)) is not None:
            meta["coverage"] = row
        db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, str(v)) for k, v in meta.items()])
        db.commit()
    finally:
        db.close()
    os.replace(part, out)
    return counts


def _open(path: Path) -> tuple[sqlite3.Connection, Box] | None:
    """The pack read-only and the box it covers, or None for a file that is not a pack this reads."""
    try:
        db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        meta = dict(db.execute("SELECT key, value FROM meta"))
        w, s, e, n = (float(v) for v in meta["bbox"].split(","))
        if int(meta["format"]) != FORMAT:
            raise ValueError(f"format {meta['format']}")
        return db, (w, s, e, n)
    except (sqlite3.Error, KeyError, ValueError):
        return None


def info(path: Path) -> dict[str, Any] | None:
    """What a pack says of itself (its meta, and its size on disk), or None when it is not one."""
    opened = _open(path)
    if opened is None:
        return None
    db, box = opened
    try:
        meta = dict(db.execute("SELECT key, value FROM meta"))
    finally:
        db.close()
    return {"name": meta.get("name", ""), "bbox": list(box), "built": int(meta.get("built") or 0),
            "bytes": Path(path).stat().st_size,
            **{k: int(meta.get(k) or 0) for k in ("buildings", "roads", "places", "details")}}


def _hits(a: Box, b: Box) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _rows(db: sqlite3.Connection, kind: str, box: Box, limit: int) -> list[tuple]:
    """Nearest first (T-1198): by the distance from the box's centre to the feature's own box (0
    when it covers the centre), ties by id, so a cap drops the far features, not the newest.
    The phone's ``MapPack.rows`` orders the same; ``tests/contracts/map-pack.json`` ``capped``."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    seen: dict[int, tuple] = {}
    for a, b in cell_rows(box):
        for d, *row in db.execute(
                "SELECT max(w - ?1, ?1 - e, 0) + max(s - ?2, ?2 - n, 0) AS d, id, h, tag, name, geom, extra"
                " FROM features WHERE kind = ?3 AND w <= ?4 AND e >= ?5 AND s <= ?6 AND n >= ?7"
                " AND id IN (SELECT fid FROM cells WHERE cell BETWEEN ?8 AND ?9) ORDER BY d, id LIMIT ?10",
                (cx, cy, kind, box[2], box[0], box[3], box[1], a, b, limit + 1)):
            seen[row[0]] = (d, *row)
    return [r[1:] for r in sorted(seen.values(), key=lambda r: (r[0], r[1]))]


def read(packs: list[Path], box: Box, limit: int) -> dict[str, Any] | None:
    """``/api/maps/buildings`` for [box] from the packs that cover it, at most [limit] of each
    kind; None when no pack covers the box, so the caller can look elsewhere. Blocking."""
    covered = False
    buildings: dict[str, dict[str, Any]] = {}
    roads: dict[str, dict[str, Any]] = {}
    details: dict[tuple[str, str], dict[str, Any]] = {}
    ground: dict[str, dict[str, Any]] = {}
    for path in packs:
        opened = _open(path)
        if opened is None:
            continue
        db, covers = opened
        try:
            if not _hits(covers, box):
                continue
            covered = True
            for _id, h, tag, _name, geom, extra in _rows(db, "b", box, limit):
                buildings.setdefault(geom, {"ring": json.loads(geom), "height_m": h, "source": tag,
                                            **(json.loads(extra) if extra else {})})
            for _id, _h, tag, name, geom, extra in _rows(db, "r", box, limit):
                x = json.loads(extra) if extra else {}
                lanes, oneway, ref, layer = x.get("lanes"), x.get("oneway"), x.get("ref"), x.get("layer")
                roads.setdefault(geom, {"line": json.loads(geom), "kind": tag, "name": name,
                                        **({"lanes": lanes} if lanes else {}),
                                        **({"oneway": oneway} if type(oneway) is int and oneway in (1, -1) else {}),
                                        **({"ref": ref} if isinstance(ref, str) and ref else {}),
                                        **({"bridge": True} if x.get("bridge") is True else {}),
                                        **({"tunnel": True} if x.get("tunnel") is True else {}),
                                        **({ACCESS_KEY: x[ACCESS_KEY]} if x.get(ACCESS_KEY) in ACCESS_CLOSED else {}),
                                        **({"layer": layer} if type(layer) is int and layer and -5 <= layer <= 5 else {}),
                                        # T-1183: re-read through the cap, so a pack built by another writer is no wider
                                        **{k: s for k, most in ROAD_TAGS.items() if (s := words(x.get(k), most))},
                                        **{k: s for k in TURN_LANE_KEYS if (s := turn_lanes(x.get(k)))}})  # T-1235
            for _id, _h, tag, _name, geom, extra in _rows(db, "d", box, limit):
                says = (json.loads(extra) if extra else {}).get("text")
                details.setdefault((tag, geom), {"kind": tag, "geom": json.loads(geom),
                                                 **({"text": says} if says else {})})
            for _id, _h, tag, name, geom, _extra in _rows(db, "g", box, limit):
                try:
                    ground.setdefault(name, {"cell": [int(v) for v in name.split(",")], "n": int(tag),
                                             "h": json.loads(geom)})
                except ValueError:
                    continue  # one cell that does not read is level ground there, not a lost answer
        except sqlite3.Error:
            continue  # a pack cut short by a full disk answers nothing; the others still do
        finally:
            db.close()
    if not covered:
        return None
    return {"buildings": list(buildings.values())[:limit], "roads": list(roads.values())[:limit],
            "details": list(details.values())[:limit], "ground": list(ground.values()),
            "truncated": len(buildings) > limit or len(roads) > limit}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    files: dict[str, Path] = {}
    rest = argv[3:]
    while rest:
        flag = rest.pop(0)
        if flag not in ("--osm", "--overture", "--roads", "--places", "--detail", "--ground") or not rest:
            print(f"pack: bad argument {flag}", file=sys.stderr)
            return 2
        files[flag[2:]] = Path(rest.pop(0))
    try:
        w, s, e, n = (float(v) for v in argv[2].split(","))
    except ValueError:
        print("pack: the bbox is WEST,SOUTH,EAST,NORTH", file=sys.stderr)
        return 2
    counts = build(Path(argv[0]), argv[1], (w, s, e, n), **files)
    print(f"{argv[0]}: {counts} in {Path(argv[0]).stat().st_size / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
