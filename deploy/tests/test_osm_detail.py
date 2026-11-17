"""scripts/osm_detail.py and osm_buildings.py: what of the street the car's ground is told (T-1066).

The .pbf pass needs pyosmium and a real extract (scripts/map-region.sh runs it); this pins the
decisions in it: which tagged things are a tree, a lamp, a parking bay and so on, and which
building tags are kept. Does not cover a lake or a park mapped as a multipolygon relation.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


osm_detail = _load("osm_detail")


def test_a_tagged_node_is_a_tree_a_lamp_a_signal_or_a_crossing():
    kind = lambda **tags: osm_detail.detail(tags, "node")  # noqa: E731
    assert kind(natural="tree") == "tree"
    assert kind(highway="street_lamp") == "lamp"
    assert kind(highway="traffic_signals") == "signal"
    assert kind(highway="crossing") == "crossing"
    assert kind(highway="bus_stop") is None and kind(amenity="parking") is None


def test_signs_and_cameras_are_named_by_what_they_are():
    # T-1073 (the owner, 2026-11-11: "also street signs, lights, speed/traffic cameras, flock cameras").
    kind = lambda **tags: osm_detail.detail(tags, "node")  # noqa: E731
    assert kind(highway="stop") == "stop" and kind(traffic_sign="US:R1-1") == "stop" and kind(traffic_sign="stop") == "stop"
    assert kind(highway="give_way") == "give_way" and kind(traffic_sign="give_way") == "give_way"
    assert kind(traffic_sign="maxspeed") == "speed_sign" and kind(traffic_sign="maxspeed;minspeed") == "speed_sign"
    assert kind(traffic_sign="US:W12-2") == "sign" and kind(traffic_sign="yes") == "sign"
    assert kind(highway="speed_camera") == "speed_camera"
    assert kind(man_made="surveillance") == "camera"
    assert kind(man_made="surveillance", **{"surveillance:type": "camera;ALPR"}) == "alpr"
    assert kind(man_made="surveillance", manufacturer="Flock Safety") == "alpr"
    assert kind(man_made="surveillance", **{"surveillance:type": "gunshot_detector"}) is None
    # Indoors is not on the street.
    assert kind(man_made="surveillance", surveillance="indoor") is None


def test_a_sign_keeps_what_it_says_and_barriers_are_named():
    # T-1104 (the owner, 2026-11-12: "the street signs need text ... construction, road barriers").
    says = lambda kind, **tags: osm_detail.text(tags, kind)  # noqa: E731
    assert says("speed_sign", maxspeed="35 mph") == "35" and says("speed_sign", maxspeed="50") == "50"
    assert says("speed_sign", traffic_sign="US:R2-1[25]") == "25" and says("speed_sign", traffic_sign="maxspeed=30") == "30"
    # A catalogue number is not a limit, and a sign with no number says nothing.
    assert says("speed_sign", traffic_sign="DE:274-56") == "" and says("speed_sign", traffic_sign="maxspeed") == ""
    assert says("sign", inscription="NO  OUTLET") == "NO OUTLET" and says("sign", destination="Quarry") == "Quarry"
    assert says("sign", inscription="x" * 25) == "" and says("stop", inscription="STOP") == "" and says("tree", name="Old oak") == ""
    node = lambda **tags: osm_detail.detail(tags, "node")  # noqa: E731
    line = lambda **tags: osm_detail.detail(tags, "line")  # noqa: E731
    assert node(barrier="bollard") == "bollard" and node(barrier="block") == "bollard"
    assert node(barrier="gate") == "gate" and node(barrier="lift_gate") == "gate"
    assert line(barrier="guard_rail") == "barrier" and line(barrier="jersey_barrier") == "barrier"
    assert line(barrier="fence") is None and node(barrier="kerb") is None
    # Roadworks and a building site were already taken (T-1066): still are.
    assert line(highway="construction") == "roadworks" and osm_detail.detail({"landuse": "construction"}, "area") == "construction"


def test_a_line_is_a_tree_row_or_roadworks_and_an_area_is_what_it_is_tagged():
    line = lambda **tags: osm_detail.detail(tags, "line")  # noqa: E731
    area = lambda **tags: osm_detail.detail(tags, "area")  # noqa: E731
    assert line(natural="tree_row") == "tree_row"
    assert line(highway="construction") == "roadworks"
    assert line(highway="residential") is None
    assert area(amenity="parking") == "parking"
    assert area(amenity="parking_space") == "bay"
    assert area(landuse="construction") == "construction" and area(building="construction") == "construction"
    assert area(natural="water") == "water" and area(landuse="reservoir") == "water"
    assert area(leisure="park") == "green" and area(landuse="grass") == "green"
    assert area(natural="wood") == "wood" and area(landuse="forest") == "wood"
    # T-1188: the land's cover, each its own kind; a tagged park inside one still reads as a park.
    assert area(landuse="farmland") == "field"
    assert area(landuse="residential") == "yard"
    assert {area(landuse=u) for u in ("commercial", "retail", "industrial")} == {"paved"}
    assert area(landuse="residential", leisure="park") == "green" and area(landuse="retail", amenity="parking") == "parking"
    assert area(landuse="quarry") is None and area(landuse="cemetery") is None
    # Underground parking is not on the street, and a tree row is no area.
    assert area(amenity="parking", parking="underground") is None
    assert area(natural="tree_row") is None and area(building="yes") is None


def test_buildings_keep_their_colour_roof_material_and_parts(monkeypatch):
    # osm_buildings imports osmium at the top; only its tag list is read here.
    fake = types.ModuleType("osmium")
    fake.filter = types.ModuleType("osmium.filter")
    fake.filter.KeyFilter = object
    monkeypatch.setitem(sys.modules, "osmium", fake)
    monkeypatch.setitem(sys.modules, "osmium.filter", fake.filter)
    keep = set(_load("osm_buildings").KEEP)
    assert {"height", "building:levels", "building", "building:colour", "building:material", "roof:colour",
            "roof:material", "roof:shape", "roof:height", "roof:levels", "roof:direction", "roof:orientation", "min_height", "building:min_level", "building:part",
            "name", "brand", "shop", "amenity"} <= keep


def test_a_road_keeps_its_lanes():
    props = _load("osm_roads").road({"highway": "primary", "lanes": "4"}, [[0, 0], [0, 1]])["properties"]
    assert props == {"highway": "primary", "lanes": "4"}


def test_the_region_script_keeps_its_libraries_per_python_version():
    # pyosmium's wheel is built for one Python: a shared tools/py installed under 3.13
    # failed every later run under 3.11 and 3.12 with "No module named 'osmium._osmium'".
    line = next(ln for ln in (SCRIPTS / "map-region.sh").read_text().splitlines() if ln.startswith("PYLIB="))
    assert "sys.version_info" in line and '"$PY"' in line
