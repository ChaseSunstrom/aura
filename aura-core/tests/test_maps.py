"""The map integration (T-0614): tiles, buildings, geocode, route and match.

Photon and Valhalla are faked with ``httpx.MockTransport`` and the data files
are written to ``tmp_path``; nothing here needs a container or a download.
What the fakes cannot prove: that the real services answer these shapes. The
shapes are Photon's GeoJSON ``/api`` and Valhalla's ``/route`` and
``/trace_attributes`` as documented, and ``docs/runbooks/maps.md`` has the live
check.
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.api.server import create_app  # noqa: E402
from aura.auth import DATA_AUTH, ENV_TOKEN, AuthManager  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import maps  # noqa: E402
from aura.integrations.maps import pack  # noqa: E402


def _encode6(points):
    out, plat, plon = "", 0, 0
    for lat, lon in points:
        for cur, prev in ((round(lat * 1e6), plat), (round(lon * 1e6), plon)):
            v = cur - prev
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out += chr((0x20 | (v & 0x1F)) + 63)
                v >>= 5
            out += chr(v + 63)
        plat, plon = round(lat * 1e6), round(lon * 1e6)
    return out


SHAPE = [(51.5, -0.1), (51.501, -0.1), (51.501, -0.099)]
SEEN: list[httpx.Request] = []


def _fake(request: httpx.Request) -> httpx.Response:
    SEEN.append(request)
    path = request.url.path
    if path == "/api":  # photon
        return httpx.Response(200, json={"features": [
            {"geometry": {"coordinates": [-0.1276, 51.5072]},
             "properties": {"name": "Downing Street", "city": "London", "country": "UK",
                            "osm_id": 9, "extent": [1, 2, 3, 4]}},
            {"geometry": {"coordinates": [0, 0]}, "properties": {}},
        ]})
    body = json.loads(request.content)
    if path == "/route":
        return httpx.Response(200, json={"trip": {
            "summary": {"length": 0.2, "time": 40},
            "legs": [{"shape": _encode6(SHAPE), "maneuvers": [
                {"instruction": "Drive north on Mall.", "street_names": ["Mall"],
                 "length": 0.1, "time": 20, "begin_shape_index": 0, "type": 1},
                {"instruction": "Turn right onto Birdcage Walk.",
                 "street_names": ["Birdcage Walk"], "length": 0.1, "time": 20,
                 "begin_shape_index": 1, "type": 10},
                {"instruction": "You have arrived.", "street_names": [], "length": 0,
                 "time": 0, "begin_shape_index": 2, "type": 4},
            ]}]}})
    if path == "/trace_attributes":
        assert body["shape_match"] == "map_snap"
        return httpx.Response(200, json={
            "matched_points": [{"lat": 51.5, "lon": -0.1, "edge_index": 0},
                               {"lat": 51.5009, "lon": -0.1, "edge_index": 1}],
            "edges": [{"names": ["Mall"]}, {"names": ["Horse Guards Road", "A3212"]}]})
    return httpx.Response(404)


def _jsonl(path: Path, features) -> None:
    path.write_text("\n".join(json.dumps(f) for f in features), encoding="utf-8")


def _line(*points, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": list(points)}}


def _poly(w, s, e, n, **props):
    return {"type": "Feature", "properties": props, "geometry": {
        "type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    data = tmp_path / "maps"
    data.mkdir()
    (data / "region.pmtiles").write_bytes(b"PMTiles" + bytes(range(256)) * 4)
    _jsonl(data / "osm-buildings.geojsonl", [
        _poly(-0.1001, 51.5001, -0.0999, 51.5003, height="12 m"),
        _poly(-0.1001, 51.5011, -0.0999, 51.5013, **{"building:levels": "4"}),
        _poly(-0.1001, 51.5021, -0.0999, 51.5023),  # no height: flat footprint
        _poly(5, 5, 5.001, 5.001, height="3"),  # far outside the bbox asked for
    ])
    _jsonl(data / "overture-buildings.geojsonl", [
        _poly(-0.10005, 51.50005, -0.09995, 51.50025, height=99),  # overlaps an OSM one
        _poly(-0.1001, 51.5031, -0.0999, 51.5033, height=7.5),  # fills a gap
    ])
    _jsonl(data / "osm-roads.geojsonl", [
        _line([-0.1005, 51.5], [-0.1005, 51.503], highway="residential", name="Downing Street"),
        _line([-0.1003, 51.5005], [-0.1002, 51.5006], highway="footway"),
        _line([5, 5], [5.01, 5.01], highway="motorway", name="Far Away"),  # outside the bbox
    ])
    jarvis = Jarvis(tmp_path)
    auth = AuthManager()
    jarvis.data[DATA_AUTH] = auth
    _info, secret = asyncio.run(auth.create_token("t"))
    jarvis.config = {"jarvis": {"latitude": 51.5, "longitude": -0.1}}
    SEEN.clear()
    jarvis.data[maps.DOMAIN] = {"transport": httpx.MockTransport(_fake)}
    asyncio.run(maps.async_setup(jarvis, {
        "data_dir": str(data), "photon_url": "http://photon", "valhalla_url": "http://valhalla",
    }))
    with TestClient(create_app(jarvis, static_dir=tmp_path / "no-www")) as c:
        c.headers["Authorization"] = f"Bearer {secret}"
        yield c


def test_map_routes_need_a_token(client):
    assert client.get("/api/maps/buildings?bbox=0,0,1,1", headers={"Authorization": ""}).status_code == 401


def test_map_tiles_serve_ranges(client):
    r = client.get("/api/maps/tiles.pmtiles", headers={"Range": "bytes=0-6"})
    assert r.status_code == 206 and r.content == b"PMTiles"


def test_map_tiles_missing_file_says_how_to_fetch(client, tmp_path):
    (tmp_path / "maps" / "region.pmtiles").unlink()
    r = client.get("/api/maps/tiles.pmtiles")
    assert r.status_code == 404 and "map-region.sh" in r.json()["detail"]["message"]


def test_map_buildings_osm_first_overture_fills_gaps(client):
    r = client.get("/api/maps/buildings?bbox=-0.11,51.499,-0.09,51.504")
    assert r.status_code == 200
    got = [(b["height_m"], b["source"]) for b in r.json()["buildings"]]
    # 12 m, 4 levels x 3 m, no height (None), then the Overture gap; the 99 m
    # Overture footprint over an OSM building and the far one are absent.
    assert got == [(12.0, "osm"), (12.0, "osm"), (None, "osm"), (7.5, "overture")]
    assert r.json()["buildings"][0]["ring"][0] == [-0.1001, 51.5001]


def test_map_buildings_carry_the_roads_and_paths_round_them(client):
    # T-0980: the car's ground drew no roads; streets and paths ride in the one ask.
    r = client.get("/api/maps/buildings?bbox=-0.11,51.499,-0.09,51.504")
    assert r.json()["roads"] == [
        {"line": [[-0.1005, 51.5], [-0.1005, 51.503]], "kind": "residential", "name": "Downing Street"},
        {"line": [[-0.1003, 51.5005], [-0.1002, 51.5006]], "kind": "footway", "name": ""},
    ]


HOME_BOX = "-0.11,51.499,-0.09,51.504"


def _pack(tmp_path, name="home", bbox=(-1.0, 51.0, 1.0, 52.0)):
    data = tmp_path / "maps"
    (data / "packs").mkdir(exist_ok=True)
    return pack.build(data / "packs" / f"{name}.pack", name, bbox, osm=data / maps.OSM_FILE,
                      overture=data / maps.OVERTURE_FILE, roads=data / maps.ROADS_FILE)


def test_a_pack_answers_what_the_lists_did_with_the_lists_gone_t0979(client, tmp_path):
    # T-0979: a state's footprints do not fit in memory (182 MiB per 50 km box), so the house
    # answers from a cell-indexed file; the answer is the lists', row by row (the pack's order is
    # nearest the box's centre first, T-1198, so the lists are compared as sets of rows).
    def rows(answer):
        return {k: sorted(json.dumps(r, sort_keys=True) for r in v) if isinstance(v, list) else v
                for k, v in answer.items()}

    before = client.get(f"/api/maps/buildings?bbox={HOME_BOX}").json()
    assert _pack(tmp_path) == {"buildings": 5, "roads": 3, "places": 0, "details": 0}
    for p in (tmp_path / "maps").glob("*.geojsonl"):
        p.unlink()
    assert before["buildings"] and rows(client.get(f"/api/maps/buildings?bbox={HOME_BOX}").json()) == rows(before)
    assert not list((tmp_path / "maps" / "packs").glob("*.part"))


def test_a_pack_elsewhere_leaves_the_ask_to_the_lists(client, tmp_path):
    before = client.get(f"/api/maps/buildings?bbox={HOME_BOX}").json()
    _pack(tmp_path, "trip", bbox=(4.0, 4.0, 6.0, 6.0))
    assert client.get(f"/api/maps/buildings?bbox={HOME_BOX}").json() == before
    far = client.get("/api/maps/buildings?bbox=4.99,4.99,5.02,5.02").json()
    assert [r["name"] for r in far["roads"]] == ["Far Away"] and len(far["buildings"]) == 1


def test_a_road_through_many_cells_comes_back_once_and_two_packs_do_not_double_it(tmp_path):
    roads = tmp_path / "r.geojsonl"
    _jsonl(roads, [_line([0.005, 0.005], [0.035, 0.035], highway="primary", name="Long")])
    for name in ("a", "b"):  # a state and the country it is in both hold the road
        pack.build(tmp_path / f"{name}.pack", name, (0.0, 0.0, 1.0, 1.0), roads=roads)
    got = pack.read([tmp_path / "a.pack", tmp_path / "b.pack"], (0.0, 0.0, 0.04, 0.04), 10)
    assert got == {"buildings": [], "details": [], "ground": [], "truncated": False,
                   "roads": [{"line": [[0.005, 0.005], [0.035, 0.035]], "kind": "primary", "name": "Long"}]}
    assert pack.read([tmp_path / "a.pack"], (0.5, 0.5, 0.6, 0.6), 10) == {"buildings": [], "roads": [], "details": [], "ground": [], "truncated": False}
    assert pack.read([tmp_path / "a.pack"], (2.0, 2.0, 2.1, 2.1), 10) is None  # no pack covers it


def test_a_pack_keeps_what_a_router_needs_of_a_road(tmp_path):
    import sqlite3
    roads = tmp_path / "r.geojsonl"
    _jsonl(roads, [_line([0, 0], [0, 0.001], highway="primary", oneway=-1, maxspeed="35 mph"),
                   _line([0, 0], [0.001, 0], highway="service", maxspeed="walk")])
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=roads)
    got = [r[0] for r in sqlite3.connect(tmp_path / "a.pack").execute("SELECT extra FROM features ORDER BY id")]
    assert got == ['{"oneway":-1,"kph":56}', None]
    assert pack.info(tmp_path / "a.pack")["roads"] == 2 and pack.info(roads) is None


_ROAD_LIMITS = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())["pack"]["roadLimits"]


def test_the_writers_road_limits_are_the_contracts_t1219():
    """The phone's reader (MapPackTest) asserts the same case, so a limit moved on one side fails the other's suite."""
    assert pack.ROAD_MAX_POINTS == _ROAD_LIMITS["max_points"]
    assert pack.ROAD_MAX_CHARS == _ROAD_LIMITS["max_geom_chars"]
    # numbers per point: a third number is dropped, a short point refuses the road
    n = _ROAD_LIMITS["numbers_per_point"]
    assert pack._road_line([[1.0] * (n + 1), [2.0] * (n + 1)]) == [[1.0] * n, [2.0] * n]
    assert pack._road_line([[1.0] * (n - 1), [2.0] * (n - 1)]) is None
    # range: the edge is kept, past it refused
    lon, lat = _ROAD_LIMITS["max_lon"], _ROAD_LIMITS["max_lat"]
    assert pack._road_line([[-lon, -lat], [lon, lat]]) is not None
    assert pack._road_line([[lon + 1e-9, 0.0], [0.0, 0.0]]) is None
    assert pack._road_line([[0.0, lat + 1e-9], [0.0, 0.0]]) is None


def _phone_reads(geom: str) -> bool:
    """What MapPack.kt `line(geom, 2)` accepts of a road, by the contract's roadLimits."""
    try:
        pts = json.loads(geom)
    except ValueError:
        return False
    n, lim = _ROAD_LIMITS["numbers_per_point"], _ROAD_LIMITS
    return (isinstance(pts, list) and 2 <= len(pts) <= lim["max_points"] and len(geom) <= lim["max_geom_chars"] and all(
        isinstance(p, list) and len(p) == n and all(type(v) in (int, float) for v in p)
        and -lim["max_lon"] <= p[0] <= lim["max_lon"] and -lim["max_lat"] <= p[1] <= lim["max_lat"] for p in pts))


def test_a_pack_never_holds_a_road_the_phone_refuses_and_keeps_its_neighbours_t1087(tmp_path):
    import sqlite3
    nan, inf = float("nan"), float("inf")
    roads = tmp_path / "r.geojsonl"
    _jsonl(roads, [
        _line([0.1, 0.1], [0.2, 0.2], highway="primary", name="Before"),
        _line([0.3, 0.3], highway="primary", name="One"),
        _line([0.3, 0.3], [nan, 0.4], highway="primary", name="Nan"),
        _line([0.3, 0.3], [0.4, inf], highway="primary", name="Inf"),
        _line([0.3, 0.3], [181.0, 0.4], highway="primary", name="Lon"),
        _line([0.3, 0.3], [0.4, -90.5], highway="primary", name="Lat"),
        _line([0.3, 0.3], ["0.4", 0.4], highway="primary", name="Text"),
        _line([0.3, 0.3], [True, 0.4], highway="primary", name="Flag"),
        _line([0.3, 0.3], [0.4], highway="primary", name="Short point"),
        _line([0.3, 0.3], [1e999, 0.4], highway="primary", name="Huge"),
        _line([0.5, 0.5, 120.0], [0.6, 0.6, 121.0], highway="primary", name="Height"),
        _line([0.7, 0.7], [0.8, 0.8], highway="primary", name="After"),
    ])
    counts = pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=roads)
    rows = sqlite3.connect(tmp_path / "a.pack").execute("SELECT name, geom FROM features WHERE kind = 'r' ORDER BY id").fetchall()
    assert [n for n, _ in rows] == ["Before", "Height", "After"]
    assert json.loads(rows[1][1]) == [[0.5, 0.5], [0.6, 0.6]]  # the extra dimension the phone refuses is left off
    assert all(_phone_reads(g) for _, g in rows)
    assert counts["roads"] == 3 and counts["skipped_roads"] == 9


def test_a_road_past_what_the_phone_reads_is_cut_into_pieces_it_reads_t1087(tmp_path):
    import sqlite3
    # 4500 points at nine digits of both: past MAX_POINTS (2000) and, in two pieces of 2000, near MAX_GEOM_CHARS
    way = [[0.1 + i * 1e-5 + 1e-9 * 3, 0.1 + 1e-9 * 7] for i in range(4500)]
    roads = tmp_path / "r.geojsonl"
    _jsonl(roads, [_line(*way, highway="primary", name="Long", maxspeed="50")])
    counts = pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=roads)
    rows = [json.loads(g) for (g,) in sqlite3.connect(tmp_path / "a.pack").execute("SELECT geom FROM features WHERE kind = 'r' ORDER BY id")]
    assert counts["roads"] == len(rows) >= 3 and "skipped_roads" not in counts
    assert all(_phone_reads(json.dumps(r, separators=(",", ":"))) for r in rows)
    assert all(a[-1] == b[0] for a, b in zip(rows, rows[1:]))  # a joint vertex shared, so the road still joins up
    assert rows[0] + [p for r in rows[1:] for p in r[1:]] == way  # and none lost
    # and the text limit alone: few points, each a long number
    fat = [[0.1 + 1.23456789012345678e-4 * i, 0.2 + 1.1111111111111111e-4 * i] for i in range(1990)]
    assert len(json.dumps(fat, separators=(",", ":"))) > _ROAD_LIMITS["max_geom_chars"]  # under 2000 points, over the text limit
    _jsonl(roads, [_line(*fat, highway="primary")])
    pack.build(tmp_path / "b.pack", "b", (0.0, 0.0, 1.0, 1.0), roads=roads)
    got = [g for (g,) in sqlite3.connect(tmp_path / "b.pack").execute("SELECT geom FROM features WHERE kind = 'r'")]
    assert len(got) >= 2 and all(_phone_reads(g) for g in got)


def _ring_of(*pts):
    return {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [list(pts)]}}


def _detail(kind, gtype, coords):
    return {"type": "Feature", "properties": {"kind": kind}, "geometry": {"type": gtype, "coordinates": coords}}


def _rows(path, kind):
    import sqlite3
    return [json.loads(g) for (g,) in sqlite3.connect(path).execute("SELECT geom FROM features WHERE kind = ? ORDER BY id", (kind,))]


def test_a_pack_never_holds_a_building_the_phone_refuses_t1226(tmp_path):
    nan = float("nan")
    ok = [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.1]]
    big = [[0.1 + i * 1e-5, 0.1] for i in range(_ROAD_LIMITS["max_points"])] + [[0.1, 0.1]]  # one past the cap
    osm, over = tmp_path / "o.geojsonl", tmp_path / "v.geojsonl"
    _jsonl(osm, [
        _ring_of(*ok),
        _ring_of([0.3, 0.3, 5.0], [0.4, 0.3, 5.0], [0.4, 0.4, 5.0], [0.3, 0.3, 5.0]),  # altitude: trimmed
        _ring_of([0.3, 0.3], [nan, 0.3], [0.4, 0.4], [0.3, 0.3]),
        _ring_of([0.3, 0.3], [181.0, 0.3], [0.4, 0.4], [0.3, 0.3]),
        _ring_of([0.3, 0.3], [0.4], [0.4, 0.4], [0.3, 0.3]),
        _ring_of(*big),
        _ring_of([0.3, 0.3], [0.4, 0.3], [0.3, 0.3]),  # not a ring at all: not counted
    ])
    _jsonl(over, [_ring_of([0.5, 0.5, 9.0], [0.6, 0.5, 9.0], [0.6, 0.6, 9.0], [0.5, 0.5, 9.0]),
                  _ring_of([0.7, 0.7], [0.8, 0.7], [0.8, nan], [0.7, 0.7])])
    counts = pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), osm=osm, overture=over)
    rows = _rows(tmp_path / "a.pack", "b")
    assert len(rows) == counts["buildings"] == 3 and counts["skipped_buildings"] == 4  # nan, lon, the cap, overture's nan; a short point is no ring, as before
    assert rows[1] == [[0.3, 0.3], [0.4, 0.3], [0.4, 0.4], [0.3, 0.3]] and rows[2][0] == rows[2][-1]
    assert all(_phone_reads(json.dumps(r, separators=(",", ":"))) for r in rows)


def test_a_pack_never_holds_a_detail_the_phone_refuses_t1226(tmp_path):
    nan, inf = float("nan"), float("inf")
    far = [[0.1 + i * 1e-5, 0.1] for i in range(2 * _ROAD_LIMITS["max_points"] + 5)]
    ring = [[0.5, 0.5], [0.6, 0.5], [0.6, 0.6], [0.5, 0.5]]
    detail = tmp_path / "d.geojsonl"
    _jsonl(detail, [
        _detail("tree", "Point", [0.1, 0.1, 30.0]),  # altitude: trimmed
        _detail("tree", "Point", [nan, 0.1]),
        _detail("tree", "Point", [0.1, inf]),
        _detail("tree", "Point", [200.0, 0.1]),
        _detail("tree_row", "LineString", [[0.1, 0.1, 3.0], [0.2, 0.2, 3.0]]),
        _detail("tree_row", "LineString", [[0.1, 0.1], [nan, 0.2]]),
        _detail("tree_row", "LineString", [[0.1, 0.1], [0.2, -91.0]]),
        _detail("barrier", "LineString", far),  # a line past the cap is cut like a road
        _detail("parking", "Polygon", [[[0.5, 0.5, 1.0], [0.6, 0.5, 1.0], [0.6, 0.6, 1.0], [0.5, 0.5, 1.0]]]),
        _detail("parking", "Polygon", [[[0.5, 0.5], [0.6, 0.5], [0.6, nan], [0.5, 0.5]]]),
        _detail("water", "Polygon", [[[0.1 + i * 1e-5, 0.1] for i in range(_ROAD_LIMITS["max_points"])] + [[0.1, 0.1]]]),
        _detail("water", "Polygon", [ring]),
        _detail("unknown_kind", "Point", [0.1, 0.1]),  # not a kind of this pack: not counted
    ])
    counts = pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), detail=detail)
    rows = _rows(tmp_path / "a.pack", "d")
    assert rows[0] == [[0.1, 0.1]] and rows[1] == [[0.1, 0.1], [0.2, 0.2]]
    assert rows[-2] == ring and rows[-1] == ring  # the parking with altitude, trimmed, and the water
    assert len(rows) == counts["details"] and counts["skipped_details"] == 7  # 3 points, 2 lines, 2 areas
    assert all(_phone_reads(json.dumps(r, separators=(",", ":"))) for r in rows if len(r) > 1)


def _coverage_of(path):
    import sqlite3
    row = sqlite3.connect(path).execute("SELECT value FROM meta WHERE key = 'coverage'").fetchone()
    return row[0] if row else None


def test_a_pack_writes_the_coverage_row_the_contract_says_and_reads_it_back_t1048(tmp_path):
    """The house writes the row for the contract's roads, and the same row says covered/hole per ask
    when decoded by the contract's own rule (the phone's reader repeats that rule in Kotlin)."""
    import base64
    import math
    case = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())["pack"]["coverage"]["case"]
    roads = tmp_path / "r.geojsonl"
    _jsonl(roads, [_line(*r, highway="residential") for r in case["roads"]])
    pack.build(tmp_path / "a.pack", "a", tuple(case["bbox"]), roads=roads)
    row = _coverage_of(tmp_path / "a.pack")
    assert row == case["row"]
    bits = base64.b64decode(row)
    assert len(bits) == math.ceil(case["cols"] * case["rows"] / 8)
    x0, y0 = math.floor(case["bbox"][0] * 100), math.floor(case["bbox"][1] * 100)

    def lit(lon, lat):
        gx, gy = math.floor(lon * 100) - x0, math.floor(lat * 100) - y0
        if not (0 <= gx < case["cols"] and 0 <= gy < case["rows"]):
            return False
        i = gy * case["cols"] + gx
        return bool(bits[i // 8] >> (i % 8) & 1)

    for ask in case["points"]:
        if pack.read([tmp_path / "a.pack"], (*ask["at"], *ask["at"]), 10) is not None:  # inside the bbox
            assert lit(*ask["at"]) == ask["covered"], ask["why"]


def test_a_pack_with_no_road_or_too_wide_a_grid_writes_no_coverage_row_t1048(tmp_path):
    only = tmp_path / "b.geojsonl"
    _jsonl(only, [_poly(0.001, 0.001, 0.002, 0.002, building="yes")])
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), osm=only)
    assert _coverage_of(tmp_path / "a.pack") is None  # buildings say nothing of where roads are
    roads = tmp_path / "r.geojsonl"
    _jsonl(roads, [_line([0.005, 0.005], [0.015, 0.005], highway="residential")])
    pack.build(tmp_path / "w.pack", "w", (0.0, 0.0, 30.0, 30.0), roads=roads)  # 9 million cells
    assert _coverage_of(tmp_path / "w.pack") is None
    # a road with a point that is not a number leaves the pack built and the row off that road
    bad = tmp_path / "x.geojsonl"
    _jsonl(bad, [_line([0.005, 0.005], [float("nan"), 0.005], highway="residential"),
                 _line([0.005, 0.005], [0.015, 0.005], highway="residential")])
    pack.build(tmp_path / "n.pack", "n", (0.0, 0.0, 1.0, 1.0), roads=bad)
    assert _coverage_of(tmp_path / "n.pack") is not None


def test_a_pack_serves_a_road_s_ref_bridge_tunnel_and_layer_t1167(tmp_path):
    import sqlite3
    roads = tmp_path / "r.geojsonl"
    _jsonl(roads, [_line([0, 0], [0, 0.001], highway="motorway", name="Spur", ref="X 9;Y 12", bridge=True, layer=1,
                         oneway=1, lanes="2"),
                   _line([0, 0], [0.001, 0], highway="primary", tunnel=True, layer=-1),
                   # Wrong types are dropped, never served; a layer out of range is clamped.
                   _line([0, 0.002], [0.001, 0.002], highway="primary", ref=5, bridge="yes", tunnel=1, layer="2"),
                   _line([0, 0.003], [0.001, 0.003], highway="primary", ref="  ", layer=True),
                   _line([0, 0.004], [0.001, 0.004], highway="primary", layer=99, ref="R" * 500)])
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=roads)
    rows = {r["name"] or r["line"][1][1]: r for r in pack.read([tmp_path / "a.pack"], (0, 0, 0.01, 0.01), 10)["roads"]}
    assert rows["Spur"] == {"line": [[0, 0], [0, 0.001]], "kind": "motorway", "name": "Spur", "lanes": 2, "oneway": 1,
                            "ref": "X 9;Y 12", "bridge": True, "layer": 1}
    assert rows[0.0]["tunnel"] is True and rows[0.0]["layer"] == -1 and "bridge" not in rows[0.0]
    for y in (0.002, 0.003):
        assert set(rows[y]) == {"line", "kind", "name"}
    assert len(rows[0.004]["ref"]) == pack.REF_MAX and rows[0.004]["layer"] == 5  # clamped, not dropped
    # A pack built before these fields (the extra holds only oneway/kph/lanes) serves roads as it did.
    db = sqlite3.connect(tmp_path / "a.pack")
    db.execute("UPDATE features SET extra = '{\"lanes\":2}' WHERE name = 'Spur'")
    db.commit()
    db.close()
    old = pack.read([tmp_path / "a.pack"], (0, 0, 0.01, 0.01), 10)["roads"]
    assert {"line": [[0, 0], [0, 0.001]], "kind": "motorway", "name": "Spur", "lanes": 2} in old


def test_a_pack_says_when_it_cut_the_answer(tmp_path):
    roads = tmp_path / "r.geojsonl"
    _jsonl(roads, [_line([0.001 * i, 0.0], [0.001 * i, 0.001], highway="service") for i in range(4)])
    pack.build(tmp_path / "a.pack", "a", (0.0, 0.0, 1.0, 1.0), roads=roads)
    got = pack.read([tmp_path / "a.pack"], (0.0, 0.0, 0.01, 0.01), 3)
    assert len(got["roads"]) == 3 and got["truncated"]


@pytest.mark.parametrize("bbox", ["x", "1,2,3", "0,0,0.5,0.5,9", "1,1,0,0", "0,0,90,90", "nan,0,1,1"])
def test_map_buildings_refuses_a_bad_or_huge_bbox(client, bbox):
    assert client.get(f"/api/maps/buildings?bbox={bbox}").status_code == 400


def test_map_buildings_without_data_is_empty_not_an_error(client, tmp_path):
    for p in (tmp_path / "maps").glob("*.geojsonl"):
        p.unlink()
    r = client.get("/api/maps/buildings?bbox=-0.11,51.499,-0.09,51.504")
    # T-1074: and it says nothing here holds the place, so a phone does not keep the emptiness.
    assert r.json() == {"buildings": [], "roads": [], "details": [], "ground": [], "truncated": False, "covered": False}


def test_geocode_shapes_photon_and_biases_to_the_house(client):
    r = client.get("/api/maps/geocode", params={"q": "downing street", "limit": 3})
    assert r.json()["results"] == [{
        "name": "Downing Street", "label": "Downing Street, London, UK",
        "lat": 51.5072, "lon": -0.1276,
    }]
    q = SEEN[-1].url.params
    assert q["q"] == "downing street" and q["limit"] == "3"
    assert q["lat"] == "51.5" and q["lon"] == "-0.1"
    assert client.get("/api/maps/geocode?q=").status_code == 400


def test_route_names_the_current_street_and_the_next_turn(client):
    r = client.post("/api/maps/route", json={"from": [51.5, -0.1], "to": [51.501, -0.099]})
    assert r.status_code == 200
    d = r.json()
    assert d["distance_m"] == 200 and d["duration_s"] == 40
    assert d["current_street"] == "Mall"
    assert d["next_turn"] == {
        "instruction": "Turn right onto Birdcage Walk.", "street": "Birdcage Walk",
        "distance_m": 100, "at": [51.501, -0.1],
    }
    assert d["shape"] == [list(p) for p in SHAPE]
    sent = json.loads(SEEN[-1].content)
    assert sent["costing"] == "auto" and sent["locations"][0] == {"lat": 51.5, "lon": -0.1}


def test_route_refuses_bad_points_and_costing(client):
    assert client.post("/api/maps/route", json={"from": [51.5], "to": [1, 2]}).status_code == 400
    assert client.post("/api/maps/route", json={"from": [95, 0], "to": [1, 2]}).status_code == 400
    bad = {"from": [1, 2], "to": [1, 3], "costing": "../x"}
    assert client.post("/api/maps/route", json=bad).status_code == 400


def test_directions_is_the_house_s_route_in_osrm_s_shape_through_the_stops(client):
    """T-0765: the car's route came from the public OSRM, which saw where every drive began and
    ended. The house's Valhalla answers in OSRM's shape, so the car's reader is unchanged."""
    asked = []
    osrm = {"code": "Ok", "routes": [{"distance": 200.0, "duration": 40.0, "legs": [{"steps": []}, {"steps": []}],
                                      "geometry": {"type": "LineString", "coordinates": [[-0.1, 51.5], [-0.099, 51.501]]}}]}

    def valhalla(request):
        asked.append(json.loads(request.content))
        return httpx.Response(200, json=osrm)

    client.app.state.jarvis.data[maps.DOMAIN]["client"] = httpx.AsyncClient(transport=httpx.MockTransport(valhalla))
    trip = {"from": [51.5, -0.1], "via": [[51.5005, -0.1]], "to": [51.501, -0.099]}
    r = client.post("/api/maps/directions", json=trip)
    assert r.status_code == 200 and r.json() == osrm
    sent = asked[-1]
    assert (sent["format"], sent["shape_format"], sent["costing"]) == ("osrm", "geojson", "auto")
    assert [(p["lat"], p["lon"]) for p in sent["locations"]] == [(51.5, -0.1), (51.5005, -0.1), (51.501, -0.099)]
    assert client.post("/api/maps/directions", json={**trip, "via": [[1, 2]] * 6}).status_code == 400
    assert client.post("/api/maps/directions", json={**trip, "via": "home"}).status_code == 400
    osrm["routes"] = []
    assert client.post("/api/maps/directions", json=trip).status_code == 404


def test_match_snaps_to_the_road_and_names_the_street(client):
    r = client.post("/api/maps/match", json={"points": [[51.5, -0.1], [51.5009, -0.1001]]})
    assert r.json() == {"lat": 51.5009, "lon": -0.1, "street": "Horse Guards Road",
                        "limit_mph": None, "limit_source": "none", "road": ""}
    assert client.post("/api/maps/match", json={"points": [[1, 2]] * 101}).status_code == 400


def _matched(client, *edges, points=((40.0, -90.0), (40.0005, -90.0))):
    """Valhalla answering a match whose last fix is on the last of [edges]; returns the body asked."""
    asked = []

    def valhalla(request):
        asked.append(json.loads(request.content))
        return httpx.Response(200, json={
            "matched_points": [{"lat": 40.0, "lon": -90.0, "edge_index": len(edges) - 1}],
            "edges": list(edges)})

    client.app.state.jarvis.data[maps.DOMAIN]["client"] = httpx.AsyncClient(transport=httpx.MockTransport(valhalla))
    got = client.post("/api/maps/match", json={"points": [list(p) for p in points]}).json()
    return got, asked[-1]


def test_a_drive_s_first_fix_gets_a_limit_too_t0755(client):
    """The car's first ask of a drive (and every ask while it has moved under 15 m) carries one
    fix, and Valhalla refuses a shape of one ("Insufficient shape provided", a 404), so the car
    said "No limit known here" until the next ask, 30 s on. One fix is matched as itself twice."""
    got, asked = _matched(client, {"road_class": "residential"}, points=((40.0, -90.0),))
    assert got["limit_source"] == "typical"
    assert [(p["lat"], p["lon"]) for p in asked["shape"]] == [(40.0, -90.0)] * 2


def test_match_gives_the_road_s_posted_limit_from_the_house_t0755(client):
    """T-0755: the car's limit asked Overpass round one point, which named the cross street at a
    junction and is unreachable from here; the house's Valhalla knows the road the fixes are on
    and keeps OSM's maxspeed on it, in km/h (35 mph is stored as 56)."""
    got, asked = _matched(client, {"names": ["Side St"], "speed_limit": 40, "road_class": "residential"},
                          {"names": ["Main St"], "speed_limit": 56, "road_class": "secondary"})
    assert {"edge.speed_limit", "edge.road_class", "edge.use"} <= set(asked["filters"]["attributes"])
    assert (got["limit_mph"], got["limit_source"], got["road"], got["street"]) == (35, "posted", "secondary", "Main St")
    for kph, mph in ((40, 25), (72, 45), (105, 65), (113, 70)):
        assert _matched(client, {"speed_limit": kph, "road_class": "primary"})[0]["limit_mph"] == mph


def test_match_falls_back_to_the_usual_limit_for_the_road_s_kind(client):
    usual = lambda edge: _matched(client, edge)[0]  # noqa: E731
    assert (usual({"road_class": "residential"})["limit_mph"], usual({"road_class": "residential"})["limit_source"]) == (25, "typical")
    assert usual({"road_class": "motorway", "use": "ramp"})["limit_mph"] == 45
    assert usual({"road_class": "motorway", "speed_limit": "unlimited"})["limit_source"] == "typical"
    assert usual({"road_class": "service_other", "use": "parking_aisle"})["limit_mph"] == 15
    assert usual({"names": ["Track"]})["limit_source"] == "none"


def _pt(lat, lon, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def test_places_come_from_the_house_s_own_index_t0765(client, tmp_path):
    """T-0765: "the nearest coffee" asked the public Overpass, and an address the public Photon or
    Nominatim, each with a point round the car. The region's extract is the house's own index."""
    _jsonl(tmp_path / "maps" / "osm-places.geojsonl", [
        _pt(51.501, -0.1, name="Intelligentsia", kind="amenity=cafe", housenumber="1", street="Mall"),
        _pt(51.5002, -0.1, name="Corner Bakery", kind="amenity=cafe"),
        _pt(51.6, -0.1, name="Far Cafe", kind="amenity=cafe"),  # 11 km out: not "near"
        _pt(51.50005, -0.1001, housenumber="10", street="Downing Street", city="London"),
        _pt(51.5003, -0.1, name="Hardware Hut", kind="shop=hardware"),
    ])
    near = "&lat=51.5&lon=-0.1"
    cafes = client.get("/api/maps/places?kind=amenity=cafe" + near).json()["results"]
    assert [p["name"] for p in cafes] == ["Corner Bakery", "Intelligentsia"]
    assert cafes[0]["distance_km"] < 0.1 and cafes[1]["address"] == "1 Mall"
    found = client.get("/api/maps/places?q=10 downing st" + near).json()["results"]
    assert [(p["name"], p["address"]) for p in found] == [("10 Downing Street", "10 Downing Street, London")]
    assert [p["name"] for p in client.get("/api/maps/places?q=intelli").json()["results"]] == ["Intelligentsia"]
    here = client.get("/api/maps/places?limit=1" + near).json()["results"]
    assert [p["address"] for p in here] == ["10 Downing Street, London"]
    assert client.get("/api/maps/places?kind=amenity=cafe").status_code == 400  # a kind needs a point


def test_places_without_an_index_say_how_to_build_it(client, tmp_path):
    r = client.get("/api/maps/places?q=coffee")
    assert r.status_code == 404 and "map-region.sh" in r.text


def test_upstream_down_is_a_502_naming_the_service(tmp_path, client):
    client.app.state.jarvis.data[maps.DOMAIN]["client"] = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("no"))))
    r = client.get("/api/maps/geocode?q=x")
    assert r.status_code == 502 and "photon" in r.json()["detail"]["message"]


def test_region_comes_from_a_setting_else_the_house_zone(client):
    jarvis = client.app.state.jarvis
    assert client.get("/api/maps/region").json()["source"] == "house"
    jarvis.data[maps.DOMAIN]["options"]["bbox"] = [-1, 50, 0, 51]
    assert client.get("/api/maps/region").json() == {"bbox": [-1, 50, 0, 51], "source": "setting"}
    # From the environment they arrive as strings, empty when unset.
    jarvis.data[maps.DOMAIN]["options"]["bbox"] = "-2,50,-1,51"
    assert client.get("/api/maps/region").json()["bbox"] == [-2, 50, -1, 51]
    jarvis.data[maps.DOMAIN]["options"].update(bbox="", center="40,-90", radius_km="10")
    got = client.get("/api/maps/region").json()
    assert got["source"] == "setting" and got["bbox"][1] < 40 < got["bbox"][3]
    assert abs((got["bbox"][3] - got["bbox"][1]) - 20 / 111) < 0.001


def test_the_region_is_the_car_s_own_drives_kept_in_the_house_t0765(client, tmp_path):
    """T-0765: no setting says where the house is (the zone is the shipped placeholder, GAPS G17)
    and the owner wants that kept in. The car already sends its fixes to the house's map-match, so
    the house counts them per 0.1 degree cell and takes the region round the one driven most."""
    jarvis = client.app.state.jarvis
    assert client.get("/api/maps/region").json()["source"] == "house"
    for _ in range(maps.DRIVE_FIXES - 1):
        client.post("/api/maps/match", json={"points": [[44.6312, -71.2788]]})
    client.post("/api/maps/match", json={"points": [[10.01, 10.01]]})  # one fix far off moves nothing
    assert client.get("/api/maps/region").json()["source"] == "house"  # not enough driven yet
    client.post("/api/maps/match", json={"points": [[44.6312, -71.2788]]})
    got = client.get("/api/maps/region").json()
    assert got["source"] == "drives"
    w, s, e, n = got["bbox"]
    assert w < -71.25 < e and s < 44.65 < n and abs((n - s) - 50 / 111) < 0.01  # the cell's middle, 25 km
    # Kept in the house's own storage, at the cell's grain: a region, not where the car stopped.
    kept = (tmp_path / ".storage" / f"{maps.DRIVES_KEY}.json").read_text()
    assert "44.6312" not in kept and "-71.2788" not in kept
    # A second start of the house still knows it.
    jarvis.data[maps.DOMAIN].pop("drives")
    asyncio.run(maps.async_setup(jarvis, dict(jarvis.data[maps.DOMAIN]["options"])))
    assert client.get("/api/maps/region").json()["source"] == "drives"
    # What scripts/map-region.sh fetched is what the data covers, ahead of the drives.
    (tmp_path / "maps" / "region.bbox").write_text("-71.5,44.4,-71.0,44.9\n")
    assert client.get("/api/maps/region").json() == {"bbox": [-71.5, 44.4, -71.0, 44.9], "source": "fetched"}


# --- regions (T-0979): the catalogue, the asks the worker carries out, the pack download -------
def _square(w, s, e, n):
    return {"type": "MultiPolygon", "coordinates": [[[[w, s], [e, s], [e, n], [w, n], [w, s]]]]}


INDEX = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": _square(0, 0, 10, 10),
     "properties": {"id": "ruritania", "name": "Ruritania", "urls": {"pbf": "https://download.geofabrik.de/ruritania-latest.osm.pbf"}}},
    {"type": "Feature", "geometry": _square(4, 4, 6, 6),
     "properties": {"id": "ruritania/strelsau", "parent": "ruritania", "name": "Strelsau",
                    "urls": {"pbf": "https://download.geofabrik.de/ruritania/strelsau-latest.osm.pbf"}}},
    {"type": "Feature", "geometry": _square(20, 20, 21, 21),
     "properties": {"id": "elsewhere", "name": "Else Where", "urls": {"pbf": "http://plain.example/x.pbf"}}},
]}


@pytest.fixture
def regions(client, tmp_path):
    (tmp_path / "maps" / maps.regions.INDEX_FILE).write_text(json.dumps(INDEX))
    return client


def test_the_catalogue_is_searched_by_name_and_says_when_it_has_not_arrived(regions, tmp_path):
    got = regions.get("/api/maps/catalogue?q=strel").json()
    assert got == {"ready": True, "results": [
        {"id": "ruritania-strelsau", "name": "Strelsau", "parent": "ruritania", "bbox": [4.0, 4.0, 6.0, 6.0]}]}
    assert [r["id"] for r in regions.get("/api/maps/catalogue?q=ruri").json()["results"]] == ["ruritania", "ruritania-strelsau"]
    # A region whose extract is not Geofabrik's own https download is not offered.
    assert regions.get("/api/maps/catalogue?q=else").json()["results"] == []
    (tmp_path / "maps" / maps.regions.INDEX_FILE).unlink()
    assert regions.get("/api/maps/catalogue?q=strel").json() == {"ready": False, "results": []}


def test_asking_for_a_region_leaves_the_ask_for_the_worker_and_lists_it(regions, tmp_path):
    r = regions.post("/api/maps/regions", json={"id": "ruritania-strelsau"})
    assert r.status_code == 200 and r.json()["state"] == "queued"
    assert (tmp_path / "maps" / "jobs" / "ruritania-strelsau.want").read_text() == "present"
    assert regions.post("/api/maps/regions", json={"id": "atlantis"}).status_code == 404
    assert regions.post("/api/maps/regions", json={"id": "../etc"}).status_code == 404
    # The worker says how far it is; the list repeats it.
    (tmp_path / "maps" / "regions").mkdir()
    (tmp_path / "maps" / "regions" / "ruritania-strelsau.json").write_text(json.dumps({"state": "working", "note": "packing"}))
    row, = regions.get("/api/maps/regions").json()["regions"]
    assert (row["id"], row["name"], row["state"], row["note"]) == ("ruritania-strelsau", "Strelsau", "working", "packing")
    # T-1270: a quiet house holds the ask; the list says so, with the worker's reason.
    (tmp_path / "maps" / "regions" / "ruritania-strelsau.json").write_text(json.dumps({"state": "held", "note": "the quiet house is on"}))
    row, = regions.get("/api/maps/regions").json()["regions"]
    assert (row["state"], row["note"]) == ("held", "the quiet house is on")


def test_a_packed_region_is_ready_with_its_size_and_can_be_downloaded_in_ranges(regions, tmp_path):
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    row, = regions.get("/api/maps/regions").json()["regions"]
    size = (tmp_path / "maps" / "packs" / "ruritania-strelsau.pack").stat().st_size
    assert row["state"] == "ready" and row["bytes"] == size and row["roads"] == 3 and row["built"] > 0
    r = regions.get("/api/maps/packs/ruritania-strelsau", headers={"Range": "bytes=0-15"})
    assert r.status_code == 206 and r.content == b"SQLite format 3\x00"
    assert regions.get("/api/maps/packs/nowhere").status_code == 404
    assert regions.get("/api/maps/packs/..%2Fregion").status_code == 404


def test_a_pack_download_answers_an_etag_and_a_range_resumes_only_while_it_matches_t1019(regions, tmp_path):
    """The ETag is the pack's sha256: starlette's own is the file's mtime and size, which a rebuild
    to the same size under a restored mtime would leave standing, and which says nothing to the
    phone's own check of its copy (T-1024). A cut download resumes on If-Range; a pack rebuilt
    since gets the whole file, never a splice of two."""
    import hashlib
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    path = tmp_path / "maps" / "packs" / "ruritania-strelsau.pack"
    whole = path.read_bytes()
    tag = f'"{hashlib.sha256(whole).hexdigest()}"'
    url = "/api/maps/packs/ruritania-strelsau"
    r = regions.get(url)
    assert r.status_code == 200 and r.headers["etag"] == tag and r.content == whole
    # A phone that holds this pack is told so, with no body.
    for held in (tag, f"W/{tag}", f'"other", {tag}', "*"):
        r = regions.get(url, headers={"If-None-Match": held})
        assert r.status_code == 304 and r.content == b"" and r.headers["etag"] == tag, held
    r = regions.get(url, headers={"If-None-Match": '"stale"'})
    assert r.status_code == 200 and r.content == whole
    # Range with the matching tag resumes; a stale tag gets the whole pack, 200, not a 206.
    r = regions.get(url, headers={"Range": "bytes=16-", "If-Range": tag})
    assert r.status_code == 206 and r.content == whole[16:] and r.headers["content-range"] == f"bytes 16-{len(whole) - 1}/{len(whole)}"
    r = regions.get(url, headers={"Range": "bytes=16-", "If-Range": '"stale"'})
    assert r.status_code == 200 and r.content == whole
    assert regions.get("/api/maps/packs/nowhere", headers={"If-None-Match": "*"}).status_code == 404


def test_a_ready_region_lists_the_sha256_of_its_pack_and_follows_a_rebuild(regions, tmp_path):
    """T-1024: the phone checks its copy against this before it replaces a good pack."""
    import hashlib
    import os
    import re
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())["sha256"]
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    path = tmp_path / "maps" / "packs" / "ruritania-strelsau.pack"
    row, = regions.get("/api/maps/regions").json()["regions"]
    assert re.fullmatch(contract["pattern"], row[contract["field"]])
    assert row[contract["field"]] == hashlib.sha256(path.read_bytes()).hexdigest()
    # Same size, other bytes, later mtime (a rebuild, or a flipped block): the list follows it.
    data = bytearray(path.read_bytes())
    # One bit, not the byte: the file's last bytes can be the coverage row's text (T-1048), and 0xFF
    # there is not UTF-8, which would make the pack unreadable rather than rebuilt.
    data[-1] ^= 0x01
    path.write_bytes(bytes(data))
    os.utime(path, ns=(1, path.stat().st_mtime_ns + 10**9))
    row, = regions.get("/api/maps/regions").json()["regions"]
    assert row["sha256"] == hashlib.sha256(bytes(data)).hexdigest()


def test_a_region_with_no_pack_lists_no_sha256(regions, tmp_path):
    regions.post("/api/maps/regions", json={"id": "ruritania"})
    row, = regions.get("/api/maps/regions").json()["regions"]
    assert "sha256" not in row


def test_removing_a_region_is_an_ask_too_and_the_home_pack_is_not_removable(regions, tmp_path):
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    _pack(tmp_path, "home")
    assert regions.delete("/api/maps/regions/ruritania-strelsau").json()["state"] == "removing"
    assert (tmp_path / "maps" / "jobs" / "ruritania-strelsau.want").read_text() == "absent"
    assert regions.delete("/api/maps/regions/home").status_code == 400
    assert regions.delete("/api/maps/regions/atlantis").status_code == 404


def test_the_region_holding_a_point_is_the_smallest_one_round_it(regions, tmp_path):
    jarvis = regions.app.state.jarvis
    at = lambda lat, lon: asyncio.run(maps.regions.region_at(jarvis, lat, lon))  # noqa: E731
    assert at(5.0, 5.0)["id"] == "ruritania-strelsau" and at(1.0, 1.0)["id"] == "ruritania"
    assert at(50.0, 50.0) is None
    # ...and whether a pack on disk already covers the point.
    assert not asyncio.run(maps.regions.covered(jarvis, 5.0, 5.0))
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    assert asyncio.run(maps.regions.covered(jarvis, 5.0, 5.0))


def test_the_pack_and_the_region_calls_are_the_contract_s(regions, tmp_path):
    """tests/contracts/map-pack.json: what the adapter relays and the phone's reader repeats."""
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())
    about = contract["pack"]
    assert (about["format"], about["cells_per_degree"], about["columns"]) == (pack.FORMAT, pack._PER_DEG, pack._COLS)
    for lon, lat, cell in about["cells"]:
        assert pack.cell(lon, lat) == cell, (lon, lat)
    assert contract["id"] == maps.regions._ID.pattern
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    regions.post("/api/maps/regions", json={"id": "ruritania"})
    for verb, path in contract["calls"].values():
        r = regions.request(verb, path.replace("{id}", "ruritania-strelsau") + ("?q=ruri" if "catalogue" in path else ""),
                            **({"json": {"id": "ruritania"}} if verb == "POST" else {}))
        assert r.status_code == 200, (verb, path)
    assert {r["state"] for r in regions.get("/api/maps/regions").json()["regions"]} <= set(contract["states"])


def test_an_ask_the_house_cannot_write_says_so(regions, tmp_path):
    (tmp_path / "maps" / "jobs").write_text("a file where the directory should be")
    r = regions.post("/api/maps/regions", json={"id": "ruritania"})
    assert r.status_code == 503 and "runbooks/maps.md" in r.json()["detail"]["message"]


def test_with_a_home_pack_the_lists_are_never_read_into_memory(client, tmp_path):
    _pack(tmp_path)
    jarvis = client.app.state.jarvis
    jarvis.data[maps.DOMAIN].pop("cache", None)
    asyncio.run(maps._warm(jarvis))
    client.get(f"/api/maps/buildings?bbox={HOME_BOX}")
    assert not jarvis.data[maps.DOMAIN].get("cache")


def test_a_trip_s_region_is_asked_for_once_when_no_pack_covers_where_it_ends(regions, tmp_path):
    want = tmp_path / "maps" / "jobs" / "ruritania-strelsau.want"
    got = regions.post("/api/maps/cover", json={"lat": 5.0, "lon": 5.0}).json()
    assert got == {"covered": False, "asked": {"id": "ruritania-strelsau", "name": "Strelsau"}} and want.read_text() == "present"
    # Asked already: said so, and not asked again (a removal in progress is not undone by a drive).
    want.write_text("absent")
    assert regions.post("/api/maps/cover", json={"lat": 5.0, "lon": 5.0}).json() == {
        "covered": False, "asked": None, "pending": "ruritania-strelsau"}
    assert want.read_text() == "absent"
    assert regions.post("/api/maps/cover", json={"lat": 50.0, "lon": 50.0}).json() == {"covered": False, "asked": None, "pending": None}
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    assert regions.post("/api/maps/cover", json={"lat": 5.0, "lon": 5.0}).json() == {"covered": True, "asked": None}
    for bad in ({"lat": "x", "lon": 1}, {"lat": 91, "lon": 0}, {}):
        assert regions.post("/api/maps/cover", json=bad).status_code == 400


def test_a_removed_region_leaves_a_tombstone_a_trip_does_not_undo_until_asked_for_by_name_t1019(regions, tmp_path):
    """The owner removes a region; the worker deletes its ask and pack; the next drive ends inside
    it and `cover` fetched it again, a download the owner had just refused. The tombstone is the
    owner's refusal, kept after the worker has taken the ask away."""
    jobs = tmp_path / "maps" / "jobs"
    trip = {"lat": 5.0, "lon": 5.0}
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    assert regions.delete("/api/maps/regions/ruritania-strelsau").json()["state"] == "removing"
    # What the worker does with an `absent` ask: the pack, the extract and the ask go.
    (tmp_path / "maps" / "packs" / "ruritania-strelsau.pack").unlink()
    (jobs / "ruritania-strelsau.want").unlink()
    got = regions.post("/api/maps/cover", json=trip).json()
    assert got["asked"] is None and got["covered"] is False and got.get("removed") is True
    assert not (jobs / "ruritania-strelsau.want").exists()  # nothing was left for the worker
    assert regions.post("/api/maps/cover", json=trip).json() == got  # and a second drive is the same
    # The buildings call asks through the same cover (T-1074): it leaves no ask either.
    regions.get("/api/maps/buildings?bbox=4.99,4.99,5.0,5.0")
    assert not (jobs / "ruritania-strelsau.want").exists()
    # The tombstone is no region's row in the list, and no other region is held back by it.
    assert regions.get("/api/maps/regions").json()["regions"] == []
    assert regions.post("/api/maps/cover", json={"lat": 8.0, "lon": 8.0}).json()["asked"]["id"] == "ruritania"
    # Asking for it by name lifts it: the ask is left, and once it is built a trip is covered.
    assert regions.post("/api/maps/regions", json={"id": "ruritania-strelsau"}).json()["state"] == "queued"
    assert (jobs / "ruritania-strelsau.want").read_text() == "present"
    _pack(tmp_path, "ruritania-strelsau", bbox=(4.0, 4.0, 6.0, 6.0))
    assert regions.post("/api/maps/cover", json=trip).json() == {"covered": True, "asked": None}
    # Removing it again is a new tombstone, even for a region the house fetched on its own.
    (jobs / "ruritania-strelsau.auto").touch()
    regions.delete("/api/maps/regions/ruritania-strelsau")
    (tmp_path / "maps" / "packs" / "ruritania-strelsau.pack").unlink()
    (jobs / "ruritania-strelsau.want").unlink()
    (jobs / "ruritania-strelsau.auto").unlink()
    assert regions.post("/api/maps/cover", json=trip).json().get("removed") is True


def test_a_pack_covers_its_region_s_outline_not_its_bounding_box(regions, tmp_path):
    """A state's box holds the city just over its border: a trip there must still fetch the neighbour."""
    tri = {"type": "Polygon", "coordinates": [[[30, 30], [40, 30], [30, 40], [30, 30]]]}
    index = {"type": "FeatureCollection", "features": INDEX["features"] + [
        {"type": "Feature", "geometry": tri, "properties": {"id": "zenda", "name": "Zenda", "urls": {"pbf": "https://download.geofabrik.de/zenda-latest.osm.pbf"}}},
        {"type": "Feature", "geometry": _square(36, 36, 40, 40),
         "properties": {"id": "hentzau", "name": "Hentzau", "urls": {"pbf": "https://download.geofabrik.de/hentzau-latest.osm.pbf"}}}]}
    (tmp_path / "maps" / maps.regions.INDEX_FILE).write_text(json.dumps(index))
    _pack(tmp_path, "zenda", bbox=(30.0, 30.0, 40.0, 40.0))
    assert regions.post("/api/maps/cover", json={"lat": 32.0, "lon": 32.0}).json() == {"covered": True, "asked": None}
    assert regions.post("/api/maps/cover", json={"lat": 38.0, "lon": 38.0}).json() == {
        "covered": False, "asked": {"id": "hentzau", "name": "Hentzau"}}
    # A held country covers its states: nothing smaller is fetched inside it.
    _pack(tmp_path, "ruritania", bbox=(0.0, 0.0, 10.0, 10.0))
    assert regions.post("/api/maps/cover", json={"lat": 5.0, "lon": 5.0}).json() == {"covered": True, "asked": None}
    # The home pack is no catalogue region: its box is what it holds.
    _pack(tmp_path, "home", bbox=(60.0, 60.0, 61.0, 61.0))
    assert regions.post("/api/maps/cover", json={"lat": 60.5, "lon": 60.5}).json() == {"covered": True, "asked": None}


def test_the_privacy_switch_stops_a_trip_s_region_but_not_one_asked_for(regions, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_QUIET_HOUSE", "true")
    assert regions.post("/api/maps/cover", json={"lat": 5.0, "lon": 5.0}).json() == {
        "covered": False, "asked": None, "pending": None, "quiet": True}
    assert not (tmp_path / "maps" / "jobs" / "ruritania-strelsau.want").exists()
    assert regions.post("/api/maps/regions", json={"id": "ruritania-strelsau"}).status_code == 200


def test_an_empty_answer_for_a_place_no_pack_covers_says_so_and_asks_for_its_region_t1074(regions, tmp_path):
    jobs = tmp_path / "maps" / "jobs"
    got = regions.get("/api/maps/buildings?bbox=4.99,6.99,5.0,7.0").json()  # in Ruritania, outside Strelsau
    assert got["covered"] is False and got["buildings"] == [] and (jobs / "ruritania.want").read_text() == "present"
    # Fetched on the house's own: listed as such, until the owner asks for it by name.
    assert (jobs / "ruritania.auto").is_file()
    assert [r["auto"] for r in regions.get("/api/maps/regions").json()["regions"] if r["id"] == "ruritania"] == [True]
    assert regions.post("/api/maps/regions", json={"id": "ruritania"}).json()["auto"] is False
    # Packed: an empty box inside it is the map's own emptiness, kept by the phone like any tile.
    _pack(tmp_path, "ruritania", bbox=(0.0, 0.0, 10.0, 10.0))
    assert "covered" not in regions.get("/api/maps/buildings?bbox=4.99,6.99,5.0,7.0").json()
    # An answer with something in it is never marked, covered by a pack or by the lists.
    assert "covered" not in regions.get(f"/api/maps/buildings?bbox={HOME_BOX}").json()


def test_no_region_is_asked_for_with_auto_cover_off_or_the_privacy_switch_on_t1074(regions, tmp_path, monkeypatch):
    jobs = tmp_path / "maps" / "jobs"
    maps._options(regions.app.state.jarvis)["auto_cover"] = False
    assert regions.get("/api/maps/buildings?bbox=4.99,6.99,5.0,7.0").json()["covered"] is False
    maps._options(regions.app.state.jarvis)["auto_cover"] = True
    monkeypatch.setenv("AURA_QUIET_HOUSE", "true")
    assert regions.get("/api/maps/buildings?bbox=4.99,6.99,5.0,7.0").json()["covered"] is False
    assert not list(jobs.glob("*.want"))


def test_only_a_few_self_fetched_regions_wait_at_once_t1074(regions, tmp_path):
    """A client with the key could name a box in every country; three asks wait, the fourth does not."""
    jobs = tmp_path / "maps" / "jobs"
    jobs.mkdir()
    for name in ("a", "b", "c"):
        (jobs / f"{name}.want").write_text("present")
        (jobs / f"{name}.auto").touch()
    got = regions.post("/api/maps/cover", json={"lat": 5.0, "lon": 5.0}).json()
    assert got == {"covered": False, "asked": None, "pending": None, "busy": ["a", "b", "c"]}
    assert not (jobs / "ruritania-strelsau.want").exists()
    # One of them built: there is room for the ask again.
    _pack(tmp_path, "a", bbox=(70.0, 70.0, 71.0, 71.0))
    assert regions.post("/api/maps/cover", json={"lat": 5.0, "lon": 5.0}).json()["asked"]["id"] == "ruritania-strelsau"


def test_a_road_that_runs_far_outside_the_box_lights_only_cells_of_the_grid_and_its_ring_t1048():
    """One segment across 100 degrees is ten thousand cells a side: unclamped it is 10^8 set entries."""
    from aura.integrations.maps import pack

    lit: set[tuple[int, int]] = set()
    pack._touched([(-50.0, -50.0), (50.0, 50.0)], lit, (10.0, 10.0, 10.05, 10.05))
    assert 0 < len(lit) <= 8 * 8  # the 6 x 6 grid and one cell round it
    wide: set[tuple[int, int]] = set()
    pack._touched([(0.0, 0.0), (0.01, 0.01)], wide, (-90.0, -90.0, 90.0, 90.0))
    assert not wide  # a grid too wide to be written is not walked at all
