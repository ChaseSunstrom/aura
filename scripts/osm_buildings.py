#!/usr/bin/env python3
"""OSM buildings inside a bbox, from a .pbf, as one GeoJSON feature per line.

    osm_buildings.py REGION.osm.pbf OUT.geojsonl WEST SOUTH EAST NORTH

Needs ``pyosmium`` (scripts/map-region.sh installs it under the data folder).
Keeps only the tags the map reads: height, levels, the building's kind, its wall and roof
colour, material and roof shape, where a part starts (T-1066) and the business it is
tagged as (T-1106: name, brand, shop, amenity); a ``building:part``
is written beside its building, which pack.py marks as a part. Written
to OUT.part and renamed, so a killed run never leaves a half file that the
next run mistakes for done.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # osm_nodes, beside this file however it was loaded
import osm_nodes  # noqa: E402

import osmium
from osmium.filter import KeyFilter

KEEP = ("height", "building:height", "building:levels", "building", "building:colour", "building:material",
        "roof:colour", "roof:material", "roof:shape", "roof:height", "roof:levels", "roof:direction",
        "roof:orientation", "min_height", "building:min_level", "building:part",
        "name", "brand", "shop", "amenity")  # the last four: the sign on its front (T-1106)


def main(pbf: str, out: str, w: float, s: float, e: float, n: float) -> int:
    factory = osmium.geom.GeoJSONFactory()
    count = 0
    with osm_nodes.node_index(pbf) as where, open(out + ".part", "w", encoding="utf-8") as fh:
        proc = osmium.FileProcessor(pbf).with_filter(KeyFilter("building", "building:part"))
        for obj in osm_nodes.with_index(proc, where, always=False).with_areas():
            if not obj.is_area():
                continue
            try:
                geom = json.loads(factory.create_multipolygon(obj))
            except RuntimeError:  # a broken relation: skip it, the rest is fine
                continue
            ring = geom["coordinates"][0][0]
            xs, ys = [p[0] for p in ring], [p[1] for p in ring]
            if max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n:
                continue
            props = {k: obj.tags[k] for k in KEEP if k in obj.tags}
            fh.write(json.dumps({"type": "Feature", "properties": props, "geometry": geom}) + "\n")
            count += 1
    os.replace(out + ".part", out)
    print(f"{count} OSM buildings -> {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2], *(float(v) for v in sys.argv[3:])))
