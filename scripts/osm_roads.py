#!/usr/bin/env python3
"""The region's roads and paths, from a .pbf, as one GeoJSON line per line (T-0980).

    osm_roads.py REGION.osm.pbf OUT.geojsonl WEST SOUTH EAST NORTH

What jarvis-core's ``/api/maps/buildings`` hands the car's ground beside the
footprints: every street, driveway, parking aisle, footpath, cycle path and track,
with its class (``highway``) and name, which the ground draws by. Needs ``pyosmium``
(scripts/map-region.sh installs it for the home region, scripts/map_worker.py for the rest). Written to OUT.part and renamed, like
osm_buildings.py.

Does not cover: a pedestrian square drawn as an area, and roads that are only
planned or being built.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # osm_nodes, beside this file however it was loaded
import osm_nodes  # noqa: E402

#: Ways tagged `highway` that are no road anyone travels: planned, gone, indoor or a stop.
NOT_ROADS = {"proposed", "construction", "abandoned", "disused", "razed", "platform", "corridor",
             "elevator", "bus_stop", "rest_area", "services"}


#: `tunnel=` values that are a way through or under a building, not under the ground (T-1184).
PASSAGES = ("building_passage", "covered")
REF_MAX = 40 # as pack.py: "A 4;E 15" fits, a pasted paragraph does not
#: T-1183: what a guide sign says, by its OSM key, with the longest the pack keeps
#: (tests/contracts/map-pack.json `roadTags`; deploy/tests/test_osm_roads.py reads both).
ROAD_TAGS = {"destination": 120, "destination:ref": 40, "junction:ref": 12, "traffic_sign": 60}
#: T-1235: the keys that say what each lane may do (tests/contracts/map-pack.json `turnLanes`).
TURN_LANE_KEYS = ("turn:lanes", "turn:lanes:forward", "turn:lanes:backward")


#: T-1049: `access` values that shut a car out (tests/contracts/map-pack.json `access`), and the tags
#: that say it, the narrowest first: `motorcar=no` beats `access=yes`, `motor_vehicle=yes` beats `access=no`.
ACCESS_CLOSED = ("private", "no")
ACCESS_TAGS = ("motorcar", "motor_vehicle", "vehicle", "access")


def closed_to_cars(tags) -> str | None:
    """"private" or "no" when the first of ACCESS_TAGS the way has says a car may not pass, else None."""
    for key in ACCESS_TAGS:
        said = str(tags.get(key) or "").strip().lower()
        if said:
            return said if said in ACCESS_CLOSED else None
    return None


def road(tags, line: list, junction_ref: str | None = None) -> dict | None:
    """The feature for one tagged way along [line] (`[lon, lat]` points), or None when it is no road."""
    kind = tags.get("highway")
    if not kind or kind in NOT_ROADS or tags.get("area") == "yes" or len(line) < 2:
        return None
    props = {"highway": kind}
    if tags.get("name"):
        props["name"] = tags["name"]
    # What a router needs beside the line (T-0979): which way it may be driven, and how fast.
    way = tags.get("oneway")
    if way in ("-1", "reverse"):
        props["oneway"] = -1
    elif way in ("yes", "1", "true") or (way is None and (kind == "motorway" or tags.get("junction") == "roundabout")):
        props["oneway"] = 1
    if tags.get("maxspeed"):
        props["maxspeed"] = tags["maxspeed"]
    if tags.get("lanes"):  # T-1066: the ground draws the lines between them
        props["lanes"] = tags["lanes"]
    # T-1167: the route number on a shield, and the tags that raise a deck over what it crosses.
    if ref := (tags.get("ref") or "").strip():
        props["ref"] = ref[:REF_MAX]
    if tags.get("bridge") not in (None, "", "no"):
        props["bridge"] = True
    # T-1184: the car draws a tunnel under what it passes beneath. A way through a building or a
    # covered one has nothing to go under: it stays a road on the surface, so it is not marked.
    if str(tags.get("tunnel") or "").strip().lower() not in ("", "no", *PASSAGES):
        props["tunnel"] = True
    if closed := closed_to_cars(tags):  # T-1049: kept in the pack, so the phone's router can leave it out
        props["access"] = closed
    try:
        # T-1181: "1;2" is a way on two levels; the first is the one it is drawn at.
        props["layer"] = max(-5, min(5, int((tags.get("layer") or "x").split(";")[0])))
    except ValueError:  # "x": no level to draw
        pass
    # T-1183: what stands on the sign where a link leaves. The exit's number is mostly mapped on the
    # motorway_junction node (main() hands it here for a link); a number on the way itself wins.
    for key, most in ROAD_TAGS.items():
        if said := (tags.get(key) or "").strip():
            props[key] = said[:most]
    # T-1235: the lanes' turns go on as mapped; pack.py's turn_lanes() decides what it keeps.
    for key in TURN_LANE_KEYS:
        if said := (tags.get(key) or "").strip():
            props[key] = said
    if junction_ref and kind.endswith("_link") and "junction:ref" not in props:
        props["junction:ref"] = junction_ref.strip()[:ROAD_TAGS["junction:ref"]]
    return {"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": line}}


def main(pbf: str, out: str, w: float, s: float, e: float, n: float) -> int:
    import osmium
    from osmium.filter import KeyFilter

    count = 0
    exits: dict[int, str] = {}  # node id -> its exit number; nodes come before ways in a .pbf
    with osm_nodes.node_index(pbf) as where, open(out + ".part", "w", encoding="utf-8") as fh:
        for obj in osm_nodes.with_index(osmium.FileProcessor(pbf).with_filter(KeyFilter("highway")), where):
            if obj.is_node() and obj.tags.get("highway") == "motorway_junction" and obj.tags.get("ref"):
                exits[obj.id] = obj.tags["ref"]
            if not obj.is_way():
                continue
            try:
                line = [[round(nd.lon, 7), round(nd.lat, 7)] for nd in obj.nodes]
            except osmium.InvalidLocationError:  # a node the extract cut off: skip the way
                continue
            xs, ys = [p[0] for p in line], [p[1] for p in line]
            if not line or max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n:
                continue
            ids = [nd.ref for nd in obj.nodes]
            feature = road(obj.tags, line, exits.get(ids[0]) or exits.get(ids[-1]) if ids else None)
            if feature:
                fh.write(json.dumps(feature) + "\n")
                count += 1
    os.replace(out + ".part", out)
    print(f"{count} roads and paths -> {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2], *(float(v) for v in sys.argv[3:])))
