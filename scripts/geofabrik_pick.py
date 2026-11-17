#!/usr/bin/env python3
"""The smallest Geofabrik extract that covers a region (T-0765).

    geofabrik_pick.py INDEX.json WEST,SOUTH,EAST,NORTH    # prints the extract's .pbf URL

INDEX.json is Geofabrik's index-v1.json: a GeoJSON FeatureCollection of every extract's
outline and its urls. An extract covers the region when the region's middle and four
corners are all inside its outline; the smallest of those by outline area wins, so a
city gets its state and not its continent. Exits 1 with nothing on stdout when none
covers it, and never echoes the region. Run it with ``python3 -I``: the index is a
download, read as data only.

Does not cover: a region whose corners are inside an outline while a sliver between them
is not (a very jagged border); the router then has no roads in that sliver.
"""

from __future__ import annotations

import json
import sys


def _inside(x: float, y: float, ring: list) -> bool:
    """Ray casting: a horizontal ray from (x, y) crosses the ring's edges an odd number of times."""
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _polygons(geometry: dict) -> list:
    coords = geometry.get("coordinates") or []
    return coords if geometry.get("type") == "MultiPolygon" else [coords]


def _covers(geometry: dict, x: float, y: float) -> bool:
    return any(rings and _inside(x, y, rings[0]) and not any(_inside(x, y, hole) for hole in rings[1:])
               for rings in _polygons(geometry))


def _area(geometry: dict) -> float:
    total = 0.0
    for rings in _polygons(geometry):
        ring = rings[0] if rings else []
        total += abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2
    return total


def pick(index: dict, bbox: tuple[float, float, float, float]) -> str | None:
    w, s, e, n = bbox
    points = [((w + e) / 2, (s + n) / 2), (w, s), (e, s), (e, n), (w, n)]
    best: tuple[float, str] | None = None
    for feature in index.get("features") or []:
        url = ((feature.get("properties") or {}).get("urls") or {}).get("pbf")
        geometry = feature.get("geometry") or {}
        if not url or not all(_covers(geometry, x, y) for x, y in points):
            continue
        area = _area(geometry)
        if best is None or area < best[0]:
            best = (area, url)
    return best[1] if best else None


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.exit(__doc__)
    with open(argv[1], encoding="utf-8") as fh:
        index = json.load(fh)
    w, s, e, n = (float(v) for v in argv[2].split(","))
    url = pick(index, (w, s, e, n))
    if url is None:
        print("geofabrik_pick: no extract covers the region", file=sys.stderr)
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
