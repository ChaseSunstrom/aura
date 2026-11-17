#!/usr/bin/env python3
"""What stands on and beside the region's streets, from a .pbf, one GeoJSON feature per line (T-1066).

    osm_detail.py REGION.osm.pbf OUT.geojsonl WEST SOUTH EAST NORTH

The car's ground drew footprints and street edges and nothing else of the place (the owner,
2026-11-11: "trees, road lines, parking spots, light posts, trees, construction"). Each
feature has one property, ``kind``: a point (tree, lamp, signal, crossing, and since T-1073
stop, give_way, speed_sign, sign, speed_camera, camera, alpr), a line
(tree_row, roadworks) or an area (parking, bay, construction, water, green, wood, and since
T-1188 the land cover: field, yard, paved), which
pack.py writes as the pack's kind ``d``. Needs ``pyosmium``, like osm_roads.py; written to
OUT.part and renamed.

Does not cover: an area mapped as a multipolygon relation (a lake with islands, a large
park): only closed ways are read. Nor a tree nobody mapped: OSM has the trees someone
drew, which in most towns is a fraction of them. Nor a sign nobody gave a number or words
(T-1104 keeps a speed limit's number and a sign's inscription where the map has them), and a
camera is where someone mapped one, not where one is. A signal is where it stands: nothing
says what it shows now.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # osm_nodes, beside this file however it was loaded
import osm_nodes  # noqa: E402

_NODES = {("natural", "tree"): "tree", ("highway", "street_lamp"): "lamp",
          ("highway", "traffic_signals"): "signal", ("highway", "crossing"): "crossing",
          ("highway", "stop"): "stop", ("highway", "give_way"): "give_way",
          ("highway", "speed_camera"): "speed_camera",
          # T-1104: what stands in or beside a road to stop a car.
          ("barrier", "bollard"): "bollard", ("barrier", "block"): "bollard",
          ("barrier", "gate"): "gate", ("barrier", "lift_gate"): "gate", ("barrier", "swing_gate"): "gate"}
# US MUTCD codes for the two signs drawn in their own shape.
_STOP = ("stop", "US:R1-1")
_GIVE_WAY = ("give_way", "yield", "US:R1-2")
_LINES = {("natural", "tree_row"): "tree_row", ("highway", "construction"): "roadworks",
          ("barrier", "guard_rail"): "barrier", ("barrier", "jersey_barrier"): "barrier"}
_AREAS = {
    ("amenity", "parking"): "parking", ("amenity", "parking_space"): "bay",
    ("landuse", "construction"): "construction", ("building", "construction"): "construction",
    ("natural", "water"): "water", ("landuse", "reservoir"): "water", ("landuse", "basin"): "water",
    ("leisure", "park"): "green", ("leisure", "garden"): "green", ("leisure", "pitch"): "green",
    ("leisure", "golf_course"): "green", ("landuse", "grass"): "green", ("landuse", "meadow"): "green",
    ("landuse", "recreation_ground"): "green", ("landuse", "village_green"): "green",
    ("natural", "wood"): "wood", ("landuse", "forest"): "wood",
    # T-1188: land cover. Last, so a way tagged both (a park inside a residential block) is the park.
    ("landuse", "farmland"): "field", ("landuse", "residential"): "yard",
    ("landuse", "commercial"): "paved", ("landuse", "retail"): "paved", ("landuse", "industrial"): "paved",
}
_SHAPES = {"node": _NODES, "line": _LINES, "area": _AREAS}
KEYS = sorted({k for table in _SHAPES.values() for k, _v in table} | {"traffic_sign", "man_made"})
TEXT_MAX = 24
_NUMBER = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")


def _sign(value: str) -> str:
    codes = [c.strip() for c in value.replace(",", ";").split(";")]
    if any(c in _STOP for c in codes):
        return "stop"
    if any(c in _GIVE_WAY for c in codes):
        return "give_way"
    return "speed_sign" if any("maxspeed" in c for c in codes) else "sign"


def text(tags, kind: str) -> str:
    """What a sign says (T-1104): a speed limit's number, any other sign's words; else ``""``.

    The number is the node's ``maxspeed``, else the one written into ``traffic_sign``
    (``US:R2-1[35]``, ``maxspeed=30``). Does not cover a country code whose digits are the
    sign's catalogue number and not its limit (``DE:274-56``): only ``[n]`` and ``=n`` are read.
    """
    if kind == "speed_sign":
        found = _NUMBER.search(str(tags.get("maxspeed") or ""))
        if not found:
            found = re.search(r"[\[=](\d{1,3})(?!\d)", str(tags.get("traffic_sign") or ""))
        return found.group(1) if found else ""
    if kind == "sign":
        words = " ".join(str(tags.get("inscription") or tags.get("destination") or tags.get("name") or "").split())
        return words if len(words) <= TEXT_MAX else ""
    return ""


def _camera(tags) -> str | None:
    """A camera watching the street: an ALPR one (Flock's are) apart from the rest; not one indoors."""
    what = (tags.get("surveillance:type") or "camera").lower()
    if tags.get("surveillance") == "indoor" or not ("camera" in what or "alpr" in what):
        return None
    maker = " ".join(tags.get(k) or "" for k in ("manufacturer", "brand", "operator")).lower()
    return "alpr" if "alpr" in what or "flock" in maker else "camera"


def detail(tags, shape: str) -> str | None:
    """The kind of one tagged node, line or area (a closed way), or None when the ground draws none."""
    if shape == "area" and tags.get("parking") in ("underground", "multi-storey"):
        return None  # under or inside a building: not on the street
    if shape == "node" and tags.get("man_made") == "surveillance":
        return _camera(tags)
    if shape == "node" and tags.get("traffic_sign"):
        return _sign(tags.get("traffic_sign"))
    for (key, value), kind in _SHAPES[shape].items():
        if tags.get(key) == value:
            return kind
    return None


def main(pbf: str, out: str, w: float, s: float, e: float, n: float) -> int:
    import osmium
    from osmium.filter import KeyFilter

    count = 0
    with osm_nodes.node_index(pbf) as where, open(out + ".part", "w", encoding="utf-8") as fh:
        def write(kind: str, shape: str, coords: list, says: str = "") -> None:
            nonlocal count
            fh.write(json.dumps({"type": "Feature", "properties": {"kind": kind, **({"text": says} if says else {})},
                                 "geometry": {"type": shape, "coordinates": coords}}) + "\n")
            count += 1

        for obj in osm_nodes.with_index(osmium.FileProcessor(pbf).with_filter(KeyFilter(*KEYS)), where):
            if obj.is_node():
                kind = detail(obj.tags, "node")
                if kind and w <= obj.location.lon <= e and s <= obj.location.lat <= n:
                    write(kind, "Point", [round(obj.location.lon, 7), round(obj.location.lat, 7)], text(obj.tags, kind))
            elif obj.is_way():
                closed = obj.is_closed() and len(obj.nodes) >= 4
                kind = (detail(obj.tags, "area") if closed else None) or detail(obj.tags, "line")
                if not kind:
                    continue
                try:
                    line = [[round(nd.lon, 7), round(nd.lat, 7)] for nd in obj.nodes]
                except osmium.InvalidLocationError:  # a node the extract cut off: skip the way
                    continue
                xs, ys = [p[0] for p in line], [p[1] for p in line]
                if len(line) < 2 or max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n:
                    continue
                if kind in _AREAS.values():
                    write(kind, "Polygon", [line])
                else:
                    write(kind, "LineString", line)
    os.replace(out + ".part", out)
    print(f"{count} trees, lamps, parking and other details -> {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2], *(float(v) for v in sys.argv[3:])))
