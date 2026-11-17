"""Live work zones on the car's map (T-1108; the owner, 2026-11-12: "traffic data,
construction, road barriers"): what the house reads from an open WZDx feed and answers at
/api/maps/live. The feed is faked with ``httpx.MockTransport``; nothing here goes out.

Does not cover a real publisher's feed (its shape is the spec's, the fixtures are made up)
or how the car draws a zone (HoloWorldTest).
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
from aura.auth import DATA_AUTH, AuthManager  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import maps  # noqa: E402
from aura.integrations.maps import workzones as wz  # noqa: E402

BOX = (0.0, 0.0, 1.0, 1.0)
NOW = 1_800_000_000.0  # 2027-01-15


def zone(line=((0.1, 0.1), (0.2, 0.2)), start="2027-01-01T00:00:00Z", end="2027-02-01T00:00:00Z", impact="some-lanes-closed", kind="work-zone", gtype="LineString", **props):
    return {"type": "Feature", "geometry": {"type": gtype, "coordinates": [list(p) for p in line]},
            "properties": {"core_details": {"event_type": kind}, "start_date": start, "end_date": end, "vehicle_impact": impact, **props}}


def feed(*features):
    return {"type": "FeatureCollection", "feed_info": {"version": "4.2"}, "features": list(features)}


def test_roadworks_and_a_closure_under_way_are_read():
    zones = wz.parse(feed(zone(), zone(impact="all-lanes-closed", gtype="MultiPoint")), BOX)
    assert [z["kind"] for z in wz.live(zones, BOX, NOW)] == ["roadworks", "closure"]
    assert wz.live(zones, BOX, NOW)[0]["line"] == [[0.1, 0.1], [0.2, 0.2]]


def test_a_zone_not_begun_one_over_and_one_only_planned_are_not_live():
    zones = wz.parse(feed(
        zone(start="2027-03-01T00:00:00Z", end="2027-04-01T00:00:00Z"),
        zone(start="2026-01-01T00:00:00Z", end="2026-02-01T00:00:00Z"),
        zone(event_status="planned"), zone(event_status="completed"),
        zone(end=None),  # no end: under way until the feed says otherwise
    ), BOX)
    assert len(wz.live(zones, BOX, NOW)) == 1
    # A zone that ends between two fetches is dropped when asked, not at the next fetch.
    assert len(wz.live(wz.parse(feed(zone()), BOX), BOX, NOW + 400 * 86400)) == 0
    # ... while one with no end date is still under way then.
    assert len(wz.live(zones, BOX, NOW + 400 * 86400)) == 1


def test_a_zone_outside_the_region_or_the_asked_box_is_left_out():
    zones = wz.parse(feed(zone(), zone(line=((5.0, 5.0), (5.1, 5.1)))), BOX)
    assert len(zones) == 1
    assert wz.live(zones, (0.5, 0.5, 0.6, 0.6), NOW) == []


@pytest.mark.parametrize("bad", [
    zone(line=((0.1, 0.1),)), zone(line=((0.1, 0.1), (999.0, 0.2))), zone(line=((0.1, 0.1), (float("nan"), 0.2))),
    zone(gtype="Polygon"), zone(start="soon"), zone(kind="incident"), zone(kind="detour"), zone(kind="restriction"), zone(line=((0.1, 0.1), ("x", 0.2))),
    {"type": "Feature"}, "text", None, {"properties": {"core_details": "x"}},
])
def test_a_feature_that_does_not_read_is_skipped_and_the_rest_kept(bad):
    assert len(wz.parse(feed(bad, zone()), BOX)) == 1


def test_a_document_that_is_not_a_feed_is_empty_and_an_oversize_one_is_refused():
    assert wz.parse([], BOX) == [] and wz.parse({"features": 7}, BOX) == []
    assert wz.read(json.dumps(feed(zone())).encode(), BOX)
    with pytest.raises(ValueError):
        wz.read(b" " * (wz.MAX_BYTES + 1), BOX)
    with pytest.raises(ValueError):
        wz.read(b"<html>", BOX)


def test_a_feed_longer_than_the_cap_keeps_the_cap():
    assert len(wz.parse(feed(*[zone()] * (wz.MAX_ZONES + 5)), BOX)) == wz.MAX_ZONES


def test_a_document_nested_too_deep_is_refused_not_a_crash():
    with pytest.raises(ValueError):
        wz.read(b"[" * 200_000, BOX)


# --- the house's answer --------------------------------------------------------


OPEN = {"start": "2020-01-01T00:00:00Z", "end": None}  # under way whatever the day the suite runs


@pytest.fixture()
def house(tmp_path, monkeypatch):
    """(client, asks, options): a house whose feed is [asks]' transport; set options to turn it on."""
    monkeypatch.delenv("AURA_QUIET_HOUSE", raising=False)
    monkeypatch.delenv("JARVIS_QUIET_HOUSE", raising=False)
    asks: list[str] = []
    answer = {"status": 200, "body": json.dumps(feed(zone(**OPEN), zone(impact="all-lanes-closed", **OPEN))).encode()}

    def fake(request: httpx.Request) -> httpx.Response:
        asks.append(str(request.url))
        return httpx.Response(answer["status"], content=answer["body"], headers=answer.get("headers") or {})

    jarvis = Jarvis(tmp_path)
    auth = AuthManager()
    jarvis.data[DATA_AUTH] = auth
    _info, secret = asyncio.run(auth.create_token("t"))
    jarvis.config = {"jarvis": {"latitude": 0.15, "longitude": 0.15}}
    jarvis.data[maps.DOMAIN] = {"transport": httpx.MockTransport(fake)}
    asyncio.run(maps.async_setup(jarvis, {"data_dir": str(tmp_path)}))
    with TestClient(create_app(jarvis, static_dir=tmp_path / "no-www")) as c:
        c.headers["Authorization"] = f"Bearer {secret}"
        yield c, asks, answer, jarvis.data[maps.DOMAIN]["options"]


LIVE = "/api/maps/live?bbox=0.05,0.05,0.15,0.15"


def test_live_with_no_feed_set_asks_nothing_and_claims_nothing(house):
    client, asks, _answer, options = house
    assert client.get(LIVE).json() == {"zones": [], "source": "off", "as_of": None}
    options["workzones_url"] = "http://feed.example/wzdx"  # not https: as good as unset
    assert client.get(LIVE).json()["source"] == "off"
    assert asks == []
    assert client.get(LIVE, headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/maps/live?bbox=1,2,3").status_code == 400


def test_live_answers_the_feeds_zones_in_the_box_and_reads_it_once_a_ttl(house):
    client, asks, _answer, options = house
    options["workzones_url"] = "https://feed.example/wzdx"
    got = client.get(LIVE).json()
    assert got["source"] == "on" and [z["kind"] for z in got["zones"]] == ["roadworks", "closure"]
    assert set(got["zones"][0]) == {"kind", "line"} and got["zones"][0]["line"] == [[0.1, 0.1], [0.2, 0.2]]
    assert client.get("/api/maps/live?bbox=0.5,0.5,0.6,0.6").json()["zones"] == []
    assert asks == ["https://feed.example/wzdx"]  # three asks of the house, one of the feed


def test_live_under_the_privacy_switch_asks_nothing(house, monkeypatch):
    client, asks, _answer, options = house
    options["workzones_url"] = "https://feed.example/wzdx"
    monkeypatch.setenv("AURA_QUIET_HOUSE", "true")
    assert client.get(LIVE).json()["source"] == "off" and asks == []


@pytest.mark.parametrize("broken", [
    {"status": 500, "body": b"no"}, {"status": 200, "body": b"<html>"},
    {"status": 302, "body": b"", "headers": {"Location": "https://elsewhere.example/feed"}},
])
def test_live_with_a_feed_that_fails_is_down_and_is_not_asked_again_inside_the_ttl(house, broken):
    client, asks, answer, options = house
    options["workzones_url"] = "https://feed.example/wzdx"
    answer.update(broken)
    assert client.get(LIVE).json() == {"zones": [], "source": "down", "as_of": None}
    assert client.get(LIVE).json()["source"] == "down"
    assert asks == ["https://feed.example/wzdx"]  # the redirect was not followed either


def test_live_keeps_the_last_good_read_through_a_failure_but_not_for_long(house):
    client, _asks, answer, options = house
    jarvis = client.app.state.jarvis
    options["workzones_url"] = "https://feed.example/wzdx"
    box = (0.0, 0.0, 1.0, 1.0)
    assert len(asyncio.run(maps.live(jarvis, box, NOW))["zones"]) == 2
    answer.update(status=500)
    later = asyncio.run(maps.live(jarvis, box, NOW + 700))  # past the TTL: read again, and it fails
    assert later["source"] == "on" and later["as_of"] == NOW and len(later["zones"]) == 2
    stale = asyncio.run(maps.live(jarvis, box, NOW + 700 + 3 * 600))
    assert stale == {"zones": [], "source": "down", "as_of": NOW}


def test_live_gives_up_on_a_feed_that_drips(house, monkeypatch):
    client, _asks, _answer, options = house
    options["workzones_url"] = "https://feed.example/wzdx"

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, content=b"{}")

    client.app.state.jarvis.data[maps.DOMAIN]["client"] = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    monkeypatch.setattr(maps, "WORKZONES_DEADLINE_S", 0.05)
    assert client.get(LIVE).json()["source"] == "down"


def test_live_refuses_a_feed_past_the_size_cap(house, monkeypatch):
    client, _asks, answer, options = house
    options["workzones_url"] = "https://feed.example/wzdx"
    monkeypatch.setattr(wz, "MAX_BYTES", 64)
    assert client.get(LIVE).json()["source"] == "down"
