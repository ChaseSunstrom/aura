"""scripts/osm_places.py: what the house's place index keeps from the region's extract (T-0765).

The .pbf pass needs pyosmium and a real extract (scripts/map-region.sh runs it); this pins
the one decision in it, which tagged objects are places.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "osm_places", Path(__file__).resolve().parents[2] / "scripts" / "osm_places.py")
osm_places = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(osm_places)


def test_a_kind_or_a_street_address_makes_a_place_and_nothing_else_does():
    cafe = osm_places.place({"amenity": "cafe", "shop": "coffee", "name": "Corner", "addr:street": "Mall"}, -0.1, 51.5)
    assert cafe["properties"] == {"kind": "amenity=cafe", "name": "Corner", "street": "Mall"}
    assert cafe["geometry"]["coordinates"] == [-0.1, 51.5]
    house = osm_places.place({"addr:housenumber": "10", "addr:street": "Downing Street", "addr:city": "London"}, 0, 0)
    assert house["properties"] == {"housenumber": "10", "street": "Downing Street", "city": "London"}
    assert osm_places.place({"brand": "Shell", "amenity": "fuel"}, 0, 0)["properties"]["name"] == "Shell"
    assert osm_places.place({"addr:housenumber": "10"}, 0, 0) is None  # no street: not findable
    assert osm_places.place({"highway": "residential", "name": "Mall"}, 0, 0) is None
