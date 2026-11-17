"""What a map pack says of the street beyond a footprint and a line (T-1066).

The owner, 2026-11-11: "instead of buildings being blocks, can we have the actual shape and
color of them ... trees, road lines, parking spots, light posts, trees, construction". The
phone can draw only what the pack carries. Does not cover the extract itself (pyosmium and a
real .pbf; deploy/tests/test_osm_detail.py pins its decisions) or how anything is drawn.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.integrations.maps import pack  # noqa: E402

RING = [[0.001, 0.001], [0.002, 0.001], [0.002, 0.002], [0.001, 0.002], [0.001, 0.001]]
BOX = (0.0, 0.0, 0.01, 0.01)


def _lines(path: Path, features: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(f) for f in features), encoding="utf-8")
    return path


def _area(props: dict, ring: list = RING) -> dict:
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def test_a_colour_is_six_hex_digits_from_a_name_a_short_hex_or_a_material():
    assert pack.colour("#ABC") == "#aabbcc" and pack.colour("#12Ab9f") == "#12ab9f"
    assert pack.colour("red") == "#ff0000" and pack.colour(" White ") == "#ffffff"
    assert pack.colour("brick") is None and pack.colour(None, "brick") == pack.MATERIALS["brick"]
    assert pack.colour("nonsense", "nonsense") is None and pack.colour("#12345") is None


def test_a_building_keeps_its_colour_roof_kind_part_and_where_it_starts(tmp_path):
    osm = _lines(tmp_path / "osm.geojsonl", [
        _area({"building": "house", "building:colour": "#F00", "roof:colour": "grey", "roof:shape": "gabled",
               "building:levels": "2"}),
        _area({"building": "yes", "building:material": "brick"}, [[p[0] + 0.003, p[1]] for p in RING]),
        _area({"building:part": "yes", "height": "30", "min_height": "12", "building:min_level": "9"},
              [[p[0], p[1] + 0.003] for p in RING]),
    ])
    overture = _lines(tmp_path / "ov.geojsonl", [
        _area({"height": 8, "facade_color": "#00ff00", "roof_color": "#111111", "roof_shape": "hipped", "class": "retail"},
              [[p[0] + 0.006, p[1]] for p in RING]),
    ])
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), osm=osm, overture=overture)
    got = pack.read([tmp_path / "a.pack"], BOX, 10)["buildings"]
    # Nearest the box's centre first (T-1198), not in the order they were written.
    assert [{k: v for k, v in b.items() if k != "ring"} for b in got] == [
        {"height_m": None, "source": "osm", "colour": pack.MATERIALS["brick"]},
        {"height_m": 30.0, "source": "osm", "min_height_m": 12.0, "part": True},
        {"height_m": 8.0, "source": "overture", "colour": "#00ff00", "roof_colour": "#111111", "roof_shape": "hipped",
         "kind": "retail"},
        {"height_m": 6.0, "source": "osm", "colour": "#ff0000", "roof_colour": "#808080", "roof_shape": "gabled",
         "kind": "house"},
    ]


def test_a_roof_keeps_its_height_ridge_direction_and_orientation(tmp_path):
    # T-1185: the pack dropped roof:height, roof:levels, roof:direction and roof:orientation.
    def look(**tags):
        return pack.look({"building": "house", **tags})

    assert look(**{"roof:height": "2.5"})["roof_height_m"] == 2.5
    assert look(**{"roof:levels": "2"})["roof_height_m"] == 2 * pack.M_PER_LEVEL
    assert look(**{"roof:height": "4", "roof:levels": "1"})["roof_height_m"] == 4.0
    assert look(**{"roof:direction": "90"})["roof_dir"] == 90.0
    assert look(**{"roof:direction": "-90"})["roof_dir"] == 270.0
    assert look(**{"roof:orientation": "Across"})["roof_orient"] == "across"
    # Free text a mapper typed is not a number or a word the map reads: left out, not passed on.
    junk = look(**{"roof:height": "tall", "roof:direction": "up", "roof:orientation": "sideways"})
    assert not {"roof_height_m", "roof_dir", "roof_orient"} & junk.keys()
    assert not {"roof_height_m", "roof_dir", "roof_orient"} & pack.look({"building": "yes"}).keys()


def test_a_road_keeps_its_lanes_and_the_details_come_with_the_ask(tmp_path):
    roads = _lines(tmp_path / "roads.geojsonl", [
        {"type": "Feature", "properties": {"highway": "primary", "lanes": "4", "name": "Mall"},
         "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 0.005]]}},
        {"type": "Feature", "properties": {"highway": "service", "lanes": "many"},
         "geometry": {"type": "LineString", "coordinates": [[0.001, 0.0], [0.001, 0.005]]}},
    ])
    detail = _lines(tmp_path / "detail.geojsonl", [
        {"type": "Feature", "properties": {"kind": "tree"}, "geometry": {"type": "Point", "coordinates": [0.004, 0.004]}},
        {"type": "Feature", "properties": {"kind": "tree_row"},
         "geometry": {"type": "LineString", "coordinates": [[0.004, 0.004], [0.005, 0.005]]}},
        _area({"kind": "parking"}),
        {"type": "Feature", "properties": {"kind": "tree"}, "geometry": {"type": "Point", "coordinates": [0.5, 0.5]}},
        {"type": "Feature", "properties": {"kind": "ufo"}, "geometry": {"type": "Point", "coordinates": [0.004, 0.004]}},
        {"type": "Feature", "properties": {"kind": "parking"}, "geometry": {"type": "Point", "coordinates": [0.004, 0.004]}},
    ])
    counts = pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=roads, detail=detail)
    assert counts == {"buildings": 0, "roads": 2, "places": 0, "details": 4}
    assert pack.info(tmp_path / "a.pack")["details"] == 4
    got = pack.read([tmp_path / "a.pack"], BOX, 10)
    # Nearest the box's centre first (T-1198).
    assert got["roads"] == [
        {"line": [[0.001, 0.0], [0.001, 0.005]], "kind": "service", "name": ""},
        {"line": [[0.0, 0.0], [0.0, 0.005]], "kind": "primary", "name": "Mall", "lanes": 4},
    ]
    assert got["details"] == [
        {"kind": "tree_row", "geom": [[0.004, 0.004], [0.005, 0.005]]},
        {"kind": "tree", "geom": [[0.004, 0.004]]},
        {"kind": "parking", "geom": RING},
    ]
    # Still format 1: a phone that read yesterday's pack reads this one, and skips kind d.
    db = sqlite3.connect(tmp_path / "a.pack")
    assert dict(db.execute("SELECT key, value FROM meta"))["format"] == "1"
    assert [r[0] for r in db.execute("SELECT DISTINCT kind FROM features ORDER BY kind")] == ["d", "r"]


def test_a_sign_s_text_a_barrier_and_a_one_way_road_come_with_the_ask(tmp_path):
    # T-1104: the number on a speed sign, the new kinds, and which way a one-way street runs.
    roads = _lines(tmp_path / "roads.geojsonl", [
        {"type": "Feature", "properties": {"highway": "residential", "oneway": -1, "name": "Mill Lane"},
         "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 0.005]]}},
    ])
    point = lambda props: {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [0.004, 0.004]}}  # noqa: E731
    detail = _lines(tmp_path / "detail.geojsonl", [
        point({"kind": "speed_sign", "text": "35"}),
        point({"kind": "sign", "text": "x" * 25}),
        point({"kind": "stop", "text": 7}),
        point({"kind": "bollard"}),
        {"type": "Feature", "properties": {"kind": "barrier"},
         "geometry": {"type": "LineString", "coordinates": [[0.004, 0.004], [0.005, 0.005]]}},
    ])
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=roads, detail=detail)
    got = pack.read([tmp_path / "a.pack"], BOX, 10)
    assert got["roads"] == [{"line": [[0.0, 0.0], [0.0, 0.005]], "kind": "residential", "name": "Mill Lane", "oneway": -1}]
    assert got["details"] == [
        {"kind": "barrier", "geom": [[0.004, 0.004], [0.005, 0.005]]},  # covers the centre (T-1198)
        {"kind": "speed_sign", "geom": [[0.004, 0.004]], "text": "35"},
        # Too long to be a sign's words, and not words at all: the sign stands, saying nothing.
        {"kind": "sign", "geom": [[0.004, 0.004]]},
        {"kind": "stop", "geom": [[0.004, 0.004]]},
        {"kind": "bollard", "geom": [[0.004, 0.004]]},
    ]


def test_the_ground_s_heights_come_with_the_ask_cell_by_cell(tmp_path):
    # T-1105: one row per cell, found only by a box that reaches its cell.
    n = pack.GROUND_GRID
    ground = _lines(tmp_path / "ground.jsonl", [
        {"cell": [0, 0], "h": list(range(n * n))},
        {"cell": [1, 0], "h": [40] * (n * n)},
        {"cell": [50, 50], "h": [9] * (n * n)},
        {"cell": [2, 0], "h": [1, 2, 3]},          # not a grid
        {"cell": ["x", 0], "h": [0] * (n * n)},    # not a cell
        {"h": [0] * (n * n)},
    ])
    counts = pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), ground=ground)
    assert counts == {"buildings": 0, "roads": 0, "places": 0, "details": 0}
    got = pack.read([tmp_path / "a.pack"], (0.002, 0.002, 0.008, 0.008), 10)
    assert got["ground"] == [{"cell": [0, 0], "n": n, "h": list(range(n * n))}]
    both = pack.read([tmp_path / "a.pack"], (0.002, 0.002, 0.012, 0.008), 10)["ground"]
    assert [g["cell"] for g in both] == [[0, 0], [1, 0]]
    db = sqlite3.connect(tmp_path / "a.pack")
    assert dict(db.execute("SELECT key, value FROM meta"))["format"] == "1"
    assert db.execute("SELECT count(*) FROM cells").fetchone()[0] == 3


def test_a_pack_with_no_ground_answers_an_empty_one(tmp_path):
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0))
    assert pack.read([tmp_path / "a.pack"], BOX, 10)["ground"] == []


def test_the_detail_kinds_are_the_contract_s():
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())
    assert contract["pack"]["kinds"]["detail"] == "d"
    assert contract["pack"]["kinds"]["ground"] == "g" and contract["pack"]["ground"] == {"grid": pack.GROUND_GRID}
    assert contract["pack"]["sign"] == {"max": pack.SIGN_MAX}
    assert contract["pack"]["details"] == {"point": sorted(pack.POINTS), "line": sorted(pack.LINES), "area": sorted(pack.AREAS)}


# --- T-1106: the business in a building is its sign -------------------------------------------


def _place(lon: float, lat: float, kind: str, name: str | None = None) -> dict:
    props = {"kind": kind, **({"name": name} if name else {})}
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def _signs(tmp_path, buildings: list[dict], places: list[dict]) -> list[tuple]:
    osm = _lines(tmp_path / "osm.geojsonl", buildings)
    pts = _lines(tmp_path / "places.geojsonl", places)
    pack.build(tmp_path / "s.pack", "s", (0.0, 0.0, 1.0, 1.0), osm=osm, places=pts)
    # Sorted as text: the answer is nearest first (T-1198), and these tests are about the signs.
    return sorted(((b.get("sign"), b.get("trade")) for b in pack.read([tmp_path / "s.pack"], BOX, 10)["buildings"]),
                  key=str)


def test_a_sign_is_one_short_printable_line():
    assert pack.sign("  Corner \n Bakery ") == "Corner Bakery"
    assert pack.sign("") is None and pack.sign(None) is None
    assert pack.sign("x" * (pack.SIGN_MAX + 1)) is None and pack.sign("a\x00b") is None
    assert pack.sign("Cafe\u202eoof") is None and pack.sign("Caf\u00e9 Z\u00fcrich") == "Caf\u00e9 Z\u00fcrich"


def test_a_building_tagged_as_a_business_carries_its_name_and_trade(tmp_path):
    east = [[p[0] + 0.003, p[1]] for p in RING]
    far = [[p[0] + 0.006, p[1]] for p in RING]
    assert _signs(tmp_path, [
        _area({"building": "retail", "shop": "bakery", "name": "Corner Bakery"}),
        _area({"building": "yes", "amenity": "fuel", "brand": "Made Up Fuel"}, east),
        _area({"building": "house", "name": "Rose Cottage"}, far),  # a name with no trade is not a sign
    ], []) == [("Corner Bakery", "bakery"), ("Made Up Fuel", "fuel"), (None, None)]


def test_a_named_place_inside_a_building_is_its_sign_and_the_buildings_own_tags_win(tmp_path):
    east = [[p[0] + 0.003, p[1]] for p in RING]
    assert _signs(tmp_path, [
        _area({"building": "yes"}),
        _area({"building": "yes", "shop": "books", "name": "Own Name"}, east),
    ], [
        _place(0.0015, 0.0015, "amenity=cafe", "Bean There"),
        _place(0.0045, 0.0015, "amenity=cafe", "Not This One"),
        _place(0.0015, 0.0016, "amenity=bench", "A Bench"),
        _place(0.0015, 0.0014, "amenity=cafe"),  # no name: nothing to write on a sign
    ]) == [("Bean There", "cafe"), ("Own Name", "books")]


def test_a_place_just_outside_goes_to_the_nearest_wall_within_reach_and_no_further(tmp_path):
    # RING's east wall is at 0.002; 0.00005 degrees is about 5.5 m, 0.0003 about 33 m.
    assert _signs(tmp_path, [_area({"building": "yes"})],
                  [_place(0.00205, 0.0015, "shop=florist", "Petal")]) == [("Petal", "florist")]
    assert _signs(tmp_path, [_area({"building": "yes"})],
                  [_place(0.0023, 0.0015, "shop=florist", "Petal")]) == [(None, None)]


def test_a_place_far_north_still_reaches_its_wall(tmp_path):
    # At 78 degrees a degree of longitude is a fifth of one at the equator: 0.0002 is about 4.6 m.
    ring = [[10.001, 78.001], [10.002, 78.001], [10.002, 78.002], [10.001, 78.002], [10.001, 78.001]]
    osm = _lines(tmp_path / "osm.geojsonl", [_area({"building": "yes"}, ring)])
    pts = _lines(tmp_path / "places.geojsonl", [_place(10.0022, 78.0015, "shop=florist", "Petal")])
    pack.build(tmp_path / "n.pack", "n", (10.0, 78.0, 11.0, 79.0), osm=osm, places=pts)
    got = pack.read([tmp_path / "n.pack"], (10.0, 78.0, 10.01, 78.01), 10)["buildings"]
    assert [b.get("sign") for b in got] == ["Petal"]


def test_one_sign_a_building_a_shop_before_an_office_then_the_nearest(tmp_path):
    assert _signs(tmp_path, [_area({"building": "yes"})], [
        _place(0.0015, 0.0015, "office=lawyer", "Upstairs LLP"),
        _place(0.00203, 0.0015, "shop=shoes", "Outside Shoes"),
        _place(0.0015, 0.0015, "shop=bakery", "Inside Bakery"),
    ]) == [("Inside Bakery", "bakery")]


def test_a_part_never_takes_the_sign_its_building_does(tmp_path):
    assert _signs(tmp_path, [
        _area({"building:part": "yes", "height": "9"},
              [[0.0012, 0.0012], [0.0018, 0.0012], [0.0018, 0.0018], [0.0012, 0.0018], [0.0012, 0.0012]]),
        _area({"building": "yes"}),
    ], [_place(0.0015, 0.0015, "shop=bakery", "Inside Bakery")]) == [("Inside Bakery", "bakery"), (None, None)]


def _road(line: list, **props) -> dict:
    return {"type": "Feature", "properties": {"highway": "residential", **props},
            "geometry": {"type": "LineString", "coordinates": line}}


def _plates(tmp_path, roads: list[dict], detail: list[dict] | None = None) -> list[tuple[str, float, float]]:
    """(number, metres east, metres north) of each speed sign the pack answers, in the order kept."""
    made = pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=_lines(tmp_path / "r.geojsonl", roads),
                      detail=_lines(tmp_path / "d.geojsonl", detail) if detail else None)
    got = [d for d in pack.read([tmp_path / "a.pack"], (-0.01, -0.01, 0.02, 0.02), 50)["details"]
           if d["kind"] == "speed_sign"]
    assert made["details"] >= len(got)
    return [(d.get("text", ""), round(d["geom"][0][0] * 111_320, 1), round(d["geom"][0][1] * 111_320, 1)) for d in got]


def test_a_plate_stands_where_the_posted_limit_changes_one_for_each_way_in(tmp_path):
    # T-1122: OSM maps few limit signs, so one is derived where roads that continue into each
    # other post different limits. The join is 222.6 m north; each plate is 8 m into its road
    # and 5 m to the right of someone driving into it.
    got = _plates(tmp_path, [
        _road([[0.0, 0.0], [0.0, 0.002]], maxspeed="30 mph"),
        _road([[0.0, 0.002], [0.0, 0.004]], maxspeed="45 mph"),
        _road([[0.0, 0.004], [0.0, 0.006]], maxspeed="45 mph"),  # keeps its limit: no plate at 445 m
        _road([[0.0, 0.006], [0.0, 0.008]]),  # posts none: no plate at 668 m
    ])
    assert sorted(got) == [("30", -5.0, 214.6), ("45", 5.0, 230.6)]


def test_a_one_way_street_gets_a_plate_only_where_it_can_be_driven_into(tmp_path):
    got = _plates(tmp_path, [
        _road([[0.0, 0.0], [0.0, 0.002]], maxspeed="25", oneway=1),
        _road([[0.0, 0.002], [0.0, 0.004]], maxspeed="40", oneway=1),
        # Drawn north to south and driven south to north: entered at its last point.
        _road([[0.0, 0.006], [0.0, 0.004]], maxspeed="60", oneway=-1),
    ])
    assert sorted(got) == [("40", 5.0, 230.6), ("60", 5.0, 453.3)]


def test_a_repeated_first_point_and_a_broken_one_do_not_lose_the_plate_or_the_pack(tmp_path):
    got = _plates(tmp_path, [
        _road([[0.0, 0.0], [0.0, 0.002]], maxspeed="30", oneway=1),
        _road([[0.0, 0.002], [0.0, 0.002], [0.0, 0.004]], maxspeed="45", oneway=1),
        _road([["east", 0.0], [0.001, 0.002]], maxspeed="45"),  # not a road: left out, where it used to stop the build
    ])
    assert got == [("45", 5.0, 230.6)]


def test_where_three_posted_roads_meet_and_one_differs_each_way_in_has_its_plate(tmp_path):
    got = _plates(tmp_path, [
        _road([[0.0, 0.0], [0.0, 0.002]], maxspeed="30", oneway=1),
        _road([[0.0, 0.002], [0.0, 0.004]], maxspeed="30", oneway=1),
        _road([[0.002, 0.002], [0.0, 0.002]], maxspeed="50", oneway=1),
    ])
    assert got == [("30", 5.0, 230.6)]


def test_a_limit_in_other_units_is_compared_by_speed_and_a_wide_road_s_plate_stands_further_out(tmp_path):
    got = _plates(tmp_path, [
        _road([[0.0, 0.0], [0.0, 0.002]], maxspeed="50", oneway=1),
        _road([[0.0, 0.002], [0.0, 0.004]], maxspeed="31 mph", oneway=1),  # 50 km/h: no change
        _road([[0.0, 0.004], [0.0, 0.006]], maxspeed="55 mph", lanes="4", oneway=1),
        _road([[0.0, 0.006], [0.0, 0.008]], maxspeed="walk", oneway=1),  # not a number: no plate
    ])
    assert got == [("55", 8.5, 453.3)]


def test_a_pack_carries_where_a_car_may_not_pass_and_serves_only_the_contract_s_values_t1049(tmp_path):
    import sqlite3
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())
    assert contract["pack"]["access"]["key"] == pack.ACCESS_KEY == "access"
    assert tuple(contract["pack"]["access"]["closed"]) == pack.ACCESS_CLOSED
    roads = [
        _road([[0.0, 0.0], [0.002, 0.0]], access="private"),
        _road([[0.0, 0.001], [0.002, 0.001]], access="no"),
        # Anything else (or a wrong type) is a road like any other, never served.
        _road([[0.0, 0.002], [0.002, 0.002]], access="destination"),
        _road([[0.0, 0.003], [0.002, 0.003]], access=True),
        _road([[0.0, 0.004], [0.002, 0.004]], access=["private"]),
        _road([[0.0, 0.005], [0.002, 0.005]]),
    ]
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=_lines(tmp_path / "r.geojsonl", roads))
    got = {r["line"][0][1]: r.get("access") for r in pack.read([tmp_path / "a.pack"], BOX, 50)["roads"]}
    assert got == {0.0: "private", 0.001: "no", 0.002: None, 0.003: None, 0.004: None, 0.005: None}
    # A pack built by another writer is read through the same cap: a hand-edited value is not served.
    db = sqlite3.connect(tmp_path / "a.pack")
    db.execute("UPDATE features SET extra = '{\"access\":\"yes\"}' WHERE kind = 'r' AND s = 0.0")
    db.commit()
    db.close()
    assert "access" not in pack.read([tmp_path / "a.pack"], (0, 0, 0.0005, 0.0005), 50)["roads"][0]


def test_a_road_in_the_pack_carries_what_its_guide_sign_says_and_no_more_t1183(tmp_path):
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())
    assert contract["pack"]["roadTags"] == pack.ROAD_TAGS == {
        "destination": 120, "destination:ref": 40, "junction:ref": 12, "traffic_sign": 60}
    said = {"destination": "Northtown;Eastville", "destination:ref": "I 99;US 4", "junction:ref": "12B",
            "traffic_sign": "city_limit"}
    roads = [
        _road([[0.0, 0.0], [0.0, 0.002]], highway="motorway_link", name="", **said),
        _road([[0.002, 0.0], [0.002, 0.002]], highway="motorway_link", destination="N" * 500,
              **{"destination:ref": 7, "junction:ref": "9" * 50}),  # too long is cut, a number is no text
        _road([[0.004, 0.0], [0.004, 0.002]], destination="Bad‮Name", traffic_sign="  ",
              **{"junction:ref": "1\x07"}),
        _road([[0.006, 0.0], [0.006, 0.002]]),
    ]
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=_lines(tmp_path / "r.geojsonl", roads))
    got = sorted(pack.read([tmp_path / "a.pack"], BOX, 50)["roads"], key=lambda r: r["line"][0][0])
    tags = [{k: v for k, v in r.items() if k in said} for r in got]
    assert tags[0] == said
    assert tags[1] == {"destination": "N" * 120, "junction:ref": "9" * 12}
    assert tags[2] == {} and tags[3] == {}  # a name that reorders or hides characters is no sign's words


def test_a_road_in_the_pack_carries_its_lanes_turns_whole_or_not_at_all_t1235(tmp_path):
    spec = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())["pack"]["turnLanes"]
    assert (tuple(spec["keys"]), spec["max"], spec["max_lanes"]) == (
        pack.TURN_LANE_KEYS, pack.TURN_LANES_MAX, pack.TURN_LANES_MAX_LANES)
    for case in spec["cases"]:
        assert pack.turn_lanes(case["in"]) == case["out"], case
    roads = [
        _road([[0.0, 0.0], [0.0, 0.002]], **{"turn:lanes": "left|through;right"}),
        _road([[0.002, 0.0], [0.002, 0.002]], **{"turn:lanes:forward": "through|right", "turn:lanes:backward": "left"}),
        _road([[0.004, 0.0], [0.004, 0.002]], **{"turn:lanes": "left|<b>", "turn:lanes:forward": 5}),
        _road([[0.006, 0.0], [0.006, 0.002]]),
    ]
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=_lines(tmp_path / "r.geojsonl", roads))
    got = sorted(pack.read([tmp_path / "a.pack"], BOX, 50)["roads"], key=lambda r: r["line"][0][0])
    turns = [{k: v for k, v in r.items() if k in pack.TURN_LANE_KEYS} for r in got]
    assert turns == [{"turn:lanes": "left|through;right"},
                     {"turn:lanes:forward": "through|right", "turn:lanes:backward": "left"}, {}, {}]
    # A pack built by another writer is read through the same rule.
    db = sqlite3.connect(tmp_path / "a.pack")
    db.execute("UPDATE features SET extra = '{\"turn:lanes\":\"Left|right\"}' WHERE kind = 'r' AND s = 0.0")
    db.commit()
    db.close()
    assert "turn:lanes" not in pack.read([tmp_path / "a.pack"], (0, 0, 0.0005, 0.0005), 50)["roads"][0]


def test_a_capped_pack_read_keeps_the_nearest_features_whatever_their_ids_t1198(tmp_path):
    case = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json")
                      .read_text())["pack"]["capped"]
    out = tmp_path / "a.pack"
    db = sqlite3.connect(out)
    db.executescript(pack._SCHEMA)
    box = tuple(case["box"])
    db.executemany("INSERT INTO meta VALUES (?, ?)", [("format", str(pack.FORMAT)), ("name", "a"),
                                                      ("bbox", ",".join(map(str, box))), ("built", "1")])
    for fid, (w, s, e, n) in enumerate(case["features"], 1):
        ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
        db.execute("INSERT INTO features VALUES (?, 'b', ?, ?, ?, ?, 9, 'osm', '', NULL, ?)",
                   (fid, w, s, e, n, json.dumps(ring)))
        for a, b in pack.cell_rows((w, s, e, n)):
            db.executemany("INSERT OR IGNORE INTO cells VALUES (?, ?)", [(c, fid) for c in range(a, b + 1)])
    db.commit()
    db.close()
    got = pack.read([out], box, case["limit"])["buildings"]
    firsts = [tuple(f[:2]) for f in case["features"]]
    assert [firsts.index(tuple(b["ring"][0])) + 1 for b in got] == case["want"]


def test_a_mapped_sign_saying_the_same_within_thirty_metres_is_not_doubled(tmp_path):
    roads = [_road([[0.0, 0.0], [0.0, 0.002]], maxspeed="30", oneway=1),
             _road([[0.0, 0.002], [0.0, 0.004]], maxspeed="45", oneway=1)]
    sign = lambda text, north: {"type": "Feature", "properties": {"kind": "speed_sign", **text},  # noqa: E731
                                "geometry": {"type": "Point", "coordinates": [0.0, north / 111_320]}}
    assert _plates(tmp_path, roads, [sign({"text": "45"}, 250.0)]) == [("45", 0.0, 250.0)]
    assert _plates(tmp_path, roads, [sign({}, 250.0)]) == [("", 0.0, 250.0)]  # a sign with no number read: believed
    # Another number, or the same one too far off, is another sign.
    assert sorted(_plates(tmp_path, roads, [sign({"text": "30"}, 250.0)])) == [("30", 0.0, 250.0), ("45", 5.0, 230.6)]
    assert sorted(_plates(tmp_path, roads, [sign({"text": "45"}, 270.0)])) == [("45", 0.0, 270.0), ("45", 5.0, 230.6)]
