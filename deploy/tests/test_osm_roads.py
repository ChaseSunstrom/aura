"""scripts/osm_roads.py: which ways the car's ground draws from the region's extract (T-0980).

The .pbf pass needs pyosmium and a real extract (scripts/map-region.sh runs it); this pins
the one decision in it, which tagged ways are roads or paths.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "osm_roads", Path(__file__).resolve().parents[2] / "scripts" / "osm_roads.py")
osm_roads = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(osm_roads)

LINE = [[-0.1, 51.5], [-0.1, 51.501]]


def test_streets_driveways_and_paths_are_kept_with_their_class_and_name():
    street = osm_roads.road({"highway": "residential", "name": "Mall", "surface": "asphalt"}, LINE)
    assert street == {"type": "Feature", "properties": {"highway": "residential", "name": "Mall"},
                      "geometry": {"type": "LineString", "coordinates": LINE}}
    # The owner, 2026-11-10: "why cant you also have driveways/paths".
    for kind in ("service", "footway", "path", "cycleway", "track", "steps"):
        assert osm_roads.road({"highway": kind}, LINE)["properties"] == {"highway": kind}


def test_a_road_keeps_its_route_number_bridge_tunnel_and_layer_t1167():
    props = lambda **tags: osm_roads.road({"highway": "primary", **tags}, LINE)["properties"]  # noqa: E731
    # OSM joins several numbers with ';': the raw string stays, trimmed and capped.
    assert props(ref=" X 9;Y 12 ")["ref"] == "X 9;Y 12"
    assert len(props(ref="Z" * 500)["ref"]) == osm_roads.REF_MAX and "ref" not in props(ref="  ")
    for yes in ("yes", "viaduct", "movable"):
        assert props(bridge=yes)["bridge"] is True and props(tunnel=yes)["tunnel"] is True
    plain = props(bridge="no", tunnel="no", layer="x")
    assert "bridge" not in plain and "tunnel" not in plain and "layer" not in plain
    assert props(layer="1")["layer"] == 1 and props(layer=" -1 ")["layer"] == -1
    assert props(layer="9")["layer"] == 5 and props(layer="-40")["layer"] == -5
    assert "layer" not in props(layer="") and "layer" not in props(layer="x;1")
    # T-1181: a way tagged for two levels ("1;2") is drawn at its first, not left on the ground.
    assert props(layer="1;2")["layer"] == 1 and props(layer=" 2 ; 1")["layer"] == 2
    assert props(layer="-1;0")["layer"] == -1 and props(layer="9;1")["layer"] == 5


def test_a_passage_through_a_building_is_a_road_not_a_tunnel_t1184():
    props = lambda **tags: osm_roads.road({"highway": "service", **tags}, LINE)["properties"]  # noqa: E731
    # The car's map draws a tunnel under what it passes beneath; a way through a building, or
    # a covered one, has nothing over it to go under and stays on the surface.
    for passage in ("building_passage", "covered", "Building_Passage"):
        assert "tunnel" not in props(tunnel=passage)
    assert props(tunnel="culvert")["tunnel"] is True and props(tunnel="yes")["tunnel"] is True


def test_a_road_keeps_what_a_guide_sign_says_t1183():
    props = lambda **tags: osm_roads.road({"highway": "motorway_link", **tags}, LINE)["properties"]  # noqa: E731
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())
    assert osm_roads.ROAD_TAGS == contract["pack"]["roadTags"]
    got = props(**{"destination": " Northtown;Eastville ", "destination:ref": "I 99;US 4", "junction:ref": "12B",
                   "traffic_sign": "city_limit", "surface": "asphalt"})
    assert got["destination"] == "Northtown;Eastville" and "surface" not in got
    assert got["destination:ref"] == "I 99;US 4" and got["junction:ref"] == "12B" and got["traffic_sign"] == "city_limit"
    # Capped like the pack's own caps (tests/contracts/map-pack.json roadTags), blank ones left out.
    caps = props(**{"destination": "N" * 500, "destination:ref": "R" * 500, "junction:ref": "9" * 50,
                    "traffic_sign": "t" * 500, "destination:backward": "Westburg"})
    assert [len(caps[k]) for k in ("destination", "destination:ref", "junction:ref", "traffic_sign")] == [120, 40, 12, 60]
    assert "destination:backward" not in caps
    assert not {"destination", "destination:ref", "junction:ref", "traffic_sign"} & set(props(destination="  ", traffic_sign=""))
    # The exit's number is mapped on the motorway_junction node: a link takes it from there, a way's own tag wins,
    # and a road that is no link does not.
    assert "junction:ref" not in props()
    link = lambda tags, node: osm_roads.road({"highway": "motorway_link", **tags}, LINE, node)["properties"]  # noqa: E731
    assert link({}, "7")["junction:ref"] == "7" and link({"junction:ref": "12B"}, "7")["junction:ref"] == "12B"
    assert "junction:ref" not in osm_roads.road({"highway": "motorway"}, LINE, "7")["properties"]


def test_a_road_keeps_the_turns_of_its_lanes_t1235():
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())
    assert list(osm_roads.TURN_LANE_KEYS) == contract["pack"]["turnLanes"]["keys"]
    props = lambda **tags: osm_roads.road({"highway": "primary", **tags}, LINE)["properties"]  # noqa: E731
    got = props(**{"turn:lanes": " left|through ", "turn:lanes:forward": "through|right", "turn:lanes:backward": "  "})
    assert got["turn:lanes"] == "left|through" and got["turn:lanes:forward"] == "through|right"
    assert "turn:lanes:backward" not in got and "turn:lanes" not in props()


def test_planned_indoor_and_area_ways_and_single_points_are_not_roads():
    for kind in ("proposed", "construction", "abandoned", "platform", "corridor"):
        assert osm_roads.road({"highway": kind}, LINE) is None
    assert osm_roads.road({"highway": "pedestrian", "area": "yes"}, LINE) is None
    assert osm_roads.road({"highway": "residential"}, LINE[:1]) is None
    assert osm_roads.road({"building": "yes"}, LINE) is None


def test_a_road_says_which_way_it_runs_and_how_fast_for_the_phone_s_router_t0979():
    props = lambda **tags: osm_roads.road({"highway": "primary", **tags}, LINE)["properties"]  # noqa: E731
    assert props(oneway="yes", maxspeed="35 mph") == {"highway": "primary", "oneway": 1, "maxspeed": "35 mph"}
    assert props(oneway="-1")["oneway"] == -1 and "oneway" not in props(oneway="no")
    # A motorway and a roundabout are one-way unless they say otherwise.
    assert osm_roads.road({"highway": "motorway"}, LINE)["properties"]["oneway"] == 1
    assert props(junction="roundabout")["oneway"] == 1
    assert "oneway" not in osm_roads.road({"highway": "motorway", "oneway": "no"}, LINE)["properties"]


def test_a_road_a_car_may_not_pass_is_marked_by_the_contract_s_cases_t1049():
    access = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())["pack"]["access"]
    assert osm_roads.ACCESS_CLOSED == tuple(access["closed"]) and access["key"] == "access"
    for case in access["tags"]:
        props = osm_roads.road({"highway": "service", **case["tags"]}, LINE)["properties"]
        # Only the verdict is kept, as the contract's value: the raw tag may be any text.
        assert (props.get("access") in access["closed"]) is case["closed"], case
        assert set(props) <= {"highway", "access"}
