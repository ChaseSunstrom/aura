#!/usr/bin/env python3
"""The region's places, from a .pbf, as one GeoJSON point per line (T-0765).

    osm_places.py REGION.osm.pbf OUT.geojsonl WEST SOUTH EAST NORTH

What jarvis-core's ``/api/maps/places`` searches, so "the nearest coffee" and an
address are answered in the house instead of by the public Overpass, Photon or
Nominatim. A place is anything with a kind (``amenity``, ``shop``, ...) or a
street address; an area is its outline's middle. Needs ``pyosmium``
(scripts/map-region.sh installs it). Written to OUT.part and renamed, like
osm_buildings.py.

Does not cover: a place drawn only as an unclosed way (a few car parks), and
anything the extract has no tag for.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # osm_nodes, beside this file however it was loaded
import osm_nodes  # noqa: E402

#: The keys a kind of place is named by ("amenity=cafe"), in the order one is chosen.
KINDS = ("amenity", "shop", "tourism", "leisure", "office", "craft", "healthcare")
ADDRESS = {"addr:housenumber": "housenumber", "addr:street": "street", "addr:city": "city", "addr:state": "state"}


def place(tags, lon: float, lat: float) -> dict | None:
    """The feature for one tagged object at (lon, lat), or None when it is no place."""
    props = {v: tags[k] for k, v in ADDRESS.items() if k in tags}
    kind = next((f"{k}={tags[k]}" for k in KINDS if k in tags), "")
    if kind:
        props["kind"] = kind
    name = tags.get("name") or tags.get("brand")
    if name:
        props["name"] = name
    if not kind and not ("housenumber" in props and "street" in props):
        return None
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}}


def main(pbf: str, out: str, w: float, s: float, e: float, n: float) -> int:
    import osmium
    from osmium.filter import KeyFilter

    factory = osmium.geom.GeoJSONFactory()
    count = 0
    with osm_nodes.node_index(pbf) as where, open(out + ".part", "w", encoding="utf-8") as fh:
        proc = osmium.FileProcessor(pbf).with_filter(KeyFilter(*KINDS, "addr:housenumber"))
        for obj in osm_nodes.with_index(proc, where, always=False).with_areas():
            if obj.is_node():
                lon, lat = obj.location.lon, obj.location.lat
            elif obj.is_area():
                try:
                    ring = json.loads(factory.create_multipolygon(obj))["coordinates"][0][0]
                except RuntimeError:  # a broken relation: skip it, the rest is fine
                    continue
                xs, ys = [p[0] for p in ring], [p[1] for p in ring]
                lon, lat = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            else:
                continue  # a closed way comes again as its area
            if not (w <= lon <= e and s <= lat <= n):
                continue
            feature = place(obj.tags, lon, lat)
            if feature:
                fh.write(json.dumps(feature) + "\n")
                count += 1
    os.replace(out + ".part", out)
    print(f"{count} places -> {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2], *(float(v) for v in sys.argv[3:])))
