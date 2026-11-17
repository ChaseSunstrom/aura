"""map_regions, map_region_download, map_region_remove and a trip's own region (T-0979).

The house is a fake here (one callable: method, path, body -> status, json); what is pinned
is what each tool asks it, what it tells the model, and that a route to somewhere no pack
covers asks for that region once, for the owner only and only while the setting is on.
Not covered: the house's side (aura-core/tests/test_maps.py) and the worker that downloads.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import regions  # noqa: E402

UTAH = {"id": "us-utah", "name": "Utah", "state": "ready", "note": "", "bytes": 734003200, "roads": 900000}


class House:
    def __init__(self):
        self.asked, self.answers = [], {}

    def __call__(self, method, path, body=None):
        self.asked.append((method, path, body))
        return self.answers.get((method, path.split("?")[0]), (200, {}))


@pytest.fixture
def house():
    return House()


def test_map_regions_lists_what_the_house_holds_with_sizes_in_words(house):
    house.answers[("GET", "/api/maps/regions")] = (200, {"regions": [UTAH, {"id": "home", "name": "Home", "state": "ready", "bytes": 122089472}]})
    out = json.loads(regions.handle_map_regions({}, house=house))
    assert out["ok"] and out["regions"] == [
        {"id": "us-utah", "name": "Utah", "state": "ready", "size": "700 MB"},
        {"id": "home", "name": "Home", "state": "ready", "size": "116 MB"}]
    assert house.asked == [("GET", "/api/maps/regions", None)]


def test_map_regions_with_a_query_searches_the_catalogue_too(house):
    house.answers[("GET", "/api/maps/regions")] = (200, {"regions": []})
    house.answers[("GET", "/api/maps/catalogue")] = (200, {"ready": True, "results": [{"id": "us-utah", "name": "Utah", "parent": "us", "bbox": [1, 2, 3, 4]}]})
    out = json.loads(regions.handle_map_regions({"query": "Utah & co"}, house=house))
    assert out["available"] == [{"id": "us-utah", "name": "Utah", "parent": "us"}]
    assert house.asked[1] == ("GET", "/api/maps/catalogue?q=Utah+%26+co", None)
    house.answers[("GET", "/api/maps/catalogue")] = (200, {"ready": False, "results": []})
    assert "catalogue" in json.loads(regions.handle_map_regions({"query": "utah"}, house=house))["note"]


def test_map_region_download_asks_the_house_and_says_it_takes_a_while(house):
    house.answers[("POST", "/api/maps/regions")] = (200, {"id": "us-utah", "name": "Utah", "state": "queued"})
    out = json.loads(regions.handle_map_region_download({"id": "us-utah"}, house=house))
    assert out["ok"] and out["state"] == "queued" and "map_regions" in out["note"]
    assert house.asked == [("POST", "/api/maps/regions", {"id": "us-utah"})]
    house.answers[("POST", "/api/maps/regions")] = (404, {"detail": {"code": "no_region", "message": "no such region in the catalogue"}})
    out = json.loads(regions.handle_map_region_download({"id": "atlantis"}, house=house))
    assert not out["ok"] and "catalogue" in out["error"]


@pytest.mark.parametrize("bad", ["", "../x", "US-UTAH", "a b", None, 7])
def test_a_region_id_that_is_no_id_never_reaches_the_house(house, bad):
    assert not json.loads(regions.handle_map_region_download({"id": bad}, house=house))["ok"]
    assert not json.loads(regions.handle_map_region_remove({"id": bad}, house=house))["ok"]
    assert house.asked == []


def test_map_region_remove_asks_the_house_to_remove_it(house):
    house.answers[("DELETE", "/api/maps/regions/us-utah")] = (200, {"id": "us-utah", "name": "Utah", "state": "removing"})
    out = json.loads(regions.handle_map_region_remove({"id": "us-utah"}, house=house))
    assert out["ok"] and out["state"] == "removing" and house.asked == [("DELETE", "/api/maps/regions/us-utah", None)]
    house.answers[("DELETE", "/api/maps/regions/home")] = (400, {"detail": {"code": "home_region", "message": "the home region is the house's own"}})
    assert "home region" in json.loads(regions.handle_map_region_remove({"id": "home"}, house=house))["error"]


def test_a_house_that_does_not_answer_is_said_plainly(house):
    def down(method, path, body=None):
        raise OSError("connection refused")
    for handler, args in ((regions.handle_map_regions, {}), (regions.handle_map_region_download, {"id": "us-utah"}),
                          (regions.handle_map_region_remove, {"id": "us-utah"})):
        out = json.loads(handler(args, house=down))
        assert not out["ok"] and "house" in out["error"]


def test_a_trip_s_region_is_asked_for_when_nothing_covers_where_it_ends(house):
    house.answers[("POST", "/api/maps/cover")] = (200, {"covered": False, "asked": {"id": "us-utah", "name": "Utah"}})
    assert regions.cover_trip(40.5, -111.9,house=house, on=True) == "Utah"
    assert house.asked == [("POST", "/api/maps/cover", {"lat": 40.5, "lon": -111.9})]
    house.answers[("POST", "/api/maps/cover")] = (200, {"covered": True, "asked": None})
    assert regions.cover_trip(40.5, -111.9,house=house, on=True) is None


def test_a_trip_s_region_is_not_asked_for_with_the_setting_off_or_the_house_away(house):
    assert regions.cover_trip(40.5, -111.9,house=house, on=False) is None and house.asked == []

    def down(method, path, body=None):
        raise OSError("down")
    assert regions.cover_trip(40.5, -111.9,house=down, on=True) is None  # the route still opens


def test_a_route_tells_the_owner_its_map_is_coming_and_fetches_nothing_for_a_guest(house):
    house.answers[("POST", "/api/maps/cover")] = (200, {"covered": False, "asked": {"id": "us-utah", "name": "Utah"}})
    route = json.dumps({"ok": True, "to": {"lat": 40.5, "lon": -111.9, "label": "Larchfield"}})
    assert regions.after_navigate(route, guest=True, house=house, on=True) == route and house.asked == []
    assert "Utah" in json.loads(regions.after_navigate(route, guest=False, house=house, on=True))["map"]
    refused = json.dumps({"ok": False, "to": {"lat": 40.5, "lon": -111.9}})
    assert regions.after_navigate(refused, guest=False, house=house, on=True) == refused and len(house.asked) == 1
    assert regions.after_navigate("not json", guest=False, house=house, on=True) == "not json"



def test_the_setting_and_the_privacy_switch_each_stop_a_trip_s_download(house, monkeypatch):
    house.answers[("POST", "/api/maps/cover")] = (200, {"covered": False, "asked": {"id": "us-utah", "name": "Utah"}})
    assert regions.cover_trip(40.5, -111.9,house=house) == "Utah"  # on by default
    for name in ("AURA_MAPS_AUTO_FETCH", "AURA_QUIET_HOUSE"):
        house.asked.clear()
        monkeypatch.setenv(name, "false" if name == "AURA_MAPS_AUTO_FETCH" else "true")
        assert regions.cover_trip(40.5, -111.9,house=house) is None and house.asked == []
        monkeypatch.delenv(name)
