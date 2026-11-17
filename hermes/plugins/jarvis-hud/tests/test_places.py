"""place_search and car_navigate (jarvis-hud/places.py): the car's places and routes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("jarvis-hud")
places = plugin.places
phone = plugin.phone

CAR = (41.8781, -87.6298)


def feature(name: str, lat: float, lon: float, **props) -> dict:
    return {"geometry": {"coordinates": [lon, lat]}, "properties": {"name": name, **props}}


def test_nearest_first_and_the_point_is_rounded_before_it_leaves():
    asked = {}

    def fetch(base, params):
        asked.update(base=base, **params)
        return {"features": [
            feature("Far Hardware", 41.99, -87.70, city="Chicago"),
            feature("Near Hardware", 41.88, -87.63, street="State St", housenumber="12", city="Chicago"),
        ]}

    out = places.search("hardware", f"{CAR[0]},{CAR[1]}", 5, fetch=fetch)
    assert out["ok"] and [p["name"] for p in out["places"]] == ["Near Hardware", "Far Hardware"]
    assert out["places"][0]["address"] == "12 State St, Chicago"
    assert out["places"][0]["distance_km"] < 1
    assert (asked["lat"], asked["lon"]) == (41.878, -87.63)
    assert (asked["zoom"], asked["location_bias_scale"]) == (14, 0.1)


def test_the_house_first_then_the_public_photon_and_it_says_so(monkeypatch):
    monkeypatch.delenv("PHOTON_URL", raising=False)
    monkeypatch.setenv("PHOTON_PUBLIC", "1")
    tried = []

    def fetch(base, params):
        tried.append(base)
        if base == "http://127.0.0.1:2322":
            raise ConnectionError("refused")
        return {"features": [feature("Cafe", 41.88, -87.63)]}

    out = places.search("coffee", fetch=fetch, house=lambda params: None)
    assert tried == ["http://127.0.0.1:2322", places.PUBLIC]
    assert out["source"] == places.PUBLIC and "public" in out["note"]


def test_no_public_fallback_when_turned_off(monkeypatch):
    monkeypatch.setenv("PHOTON_PUBLIC", "0")

    def fetch(base, params):
        raise ConnectionError("refused")

    out = places.search("coffee", fetch=fetch, house=lambda params: None)
    assert out["ok"] is False and "127.0.0.1:2322" in out["error"]


def _answering(answer: dict | None, sent: list):
    """An emit whose surface answers the route the way the phone and the car page do
    (`/hud @sid phone-result <id> <json>`), or never when ``answer`` is None."""
    def emit(event, payload):
        sent.append((event, payload))
        if answer is not None:
            phone.deliver(payload["id"], json.dumps(answer))
        return True
    return emit


def test_navigate_sends_the_event_the_phone_reads_and_refuses_nonsense():
    sent = []
    ok = json.loads(places.handle_car_navigate(
        {"lat": 41.88, "lon": -87.63, "label": "Near Hardware", "app": "google"},
        _answering({"status": "ok", "opened": "car", "app": "google"}, sent)))
    assert ok["ok"] and ok["opened"] == "car" and ok["app"] == "google"
    (event, payload), = sent
    assert event == "hud.car.navigate" and len(payload.pop("id")) == 16
    assert payload == {"lat": 41.88, "lon": -87.63, "label": "Near Hardware", "app": "google"}
    for bad in ({"lat": "north", "lon": 1}, {"lat": 91, "lon": 0}, {"lat": 1, "lon": 1, "app": "waze"}):
        assert json.loads(places.handle_car_navigate(bad, lambda e, p: True))["ok"] is False


def test_a_route_no_screen_opened_is_never_reported_open():
    """The owner, 2026-11-09: "route me to ..." from the phone's popup, off the car: Jarvis said
    "your phone has the destination open" and nothing opened. The tool said `delivered` when
    the event reached the session, whether or not any screen acted on it. Now only a screen's
    answer, or the phone's own maps app over the house, makes a route open."""
    to = {"lat": 41.6, "lon": -40.6, "label": "Burger place"}
    asked = []

    def house(action, params, reason, wake_s=0.0):
        asked.append((action, params))
        return {"ok": True, "via": "house", "status": "ok"}

    # No screen answers: the phone's maps app, over the house's channel.
    out = json.loads(places.handle_car_navigate(to, _answering(None, []), wait_s=0.01, house=house))
    assert out["ok"] and out["opened"] == "phone" and out["via"] == "house"
    assert asked == [("start_navigation", {"latitude": 41.6, "longitude": -40.6, "destination": "Burger place"})]

    # Neither: said so, and nothing is learned from it (no `to`).
    out = json.loads(places.handle_car_navigate(to, _answering(None, []), wait_s=0.01, house=lambda *a, **k: None))
    assert out["ok"] is False and "to" not in out
    # The event reached the conversation and no screen answered in time: sent, not confirmed.
    # The owner's drive, 2026-11-11: the car's page answered late on a slow link, Jarvis said
    # the route "didn't take" and to try again, and the page opened it anyway.
    assert "was sent" in out["error"] and "not confirmed" in out["error"] and "do not say it is open" in out["error"]
    # No screen in the conversation at all: nowhere.
    out = json.loads(places.handle_car_navigate(to, lambda e, p: False, wait_s=0.01, house=lambda *a, **k: None))
    assert out["ok"] is False and "not open anywhere" in out["error"] and "to" not in out

    # A screen that tried and could not (no maps app on the phone): its words, not "open".
    out = json.loads(places.handle_car_navigate(
        to, _answering({"status": "error", "error": "No maps app is installed"}, []), house=house))
    assert out["ok"] is False and "No maps app" in out["error"] and "to" not in out
    assert len(asked) == 1  # a screen that answered is not second-guessed over the house


def test_both_tools_are_registered():
    names = {plugin.PLACE_SEARCH["name"], plugin.CAR_NAVIGATE["name"]}
    assert names == {"place_search", "car_navigate"}


def test_a_kind_of_place_is_every_one_around_the_car_nearest_first(monkeypatch):
    monkeypatch.setenv("OVERPASS_URL", "https://overpass.example/api/interpreter")
    sent = []

    def fetch(query):
        sent.append(query)
        return {"elements": [
            {"lat": 41.885, "lon": -87.62, "tags": {"name": "Intelligentsia", "addr:street": "Randolph St"}},
            {"center": {"lat": 41.8795, "lon": -87.6297}, "tags": {"brand": "Corner Bakery"}},
            {"tags": {"name": "no position"}},
        ]}

    out = places.nearby("amenity=cafe", "41.8781,-87.6298", 5, fetch=fetch, house=lambda params: None)
    assert [p["name"] for p in out["places"]] == ["Corner Bakery", "Intelligentsia"]
    assert out["places"][0]["distance_km"] < 0.3
    assert sent == ['[out:json][timeout:10];nwr["amenity"="cafe"](around:5000,41.8781,-87.6298);out center 60;']


def test_only_a_plain_tag_reaches_the_overpass_query():
    for bad in ('amenity"="cafe"];out;', "cafe", "AMENITY=cafe", "amenity=ca fe"):
        assert places.nearby(bad, "41.8,-87.6", fetch=lambda q: {"elements": []})["ok"] is False
    assert places.nearby("amenity=cafe", None)["ok"] is False
    assert json.loads(plugin.places.handle_place_search({"kind": "amenity=cafe"}))["ok"] is False


def test_an_address_still_resolves_when_every_photon_times_out(monkeypatch):
    # 2026-10-07: "route me to 425 SW 11th Street" got "no Photon answered (... timed
    # out)" twice and the model scraped Nominatim through a browser instead.
    monkeypatch.setenv("PHOTON_PUBLIC", "1")
    asked = {}

    def photon(base, params):
        raise TimeoutError("The read operation timed out")

    def nominatim(path, params):
        asked.update(path=path, **params)
        return [{"lat": "41.5778215", "lon": "-40.6272994", "name": "The Meridian", "type": "apartments",
                 "address": {"house_number": "425", "road": "Southwest 11th Street", "city": "Port Larch",
                             "state": "Vale"}}]

    out = places.search("425 SW 11th Street", "41.5911,-40.6037", fetch=photon, nominatim=nominatim, house=lambda params: None)
    assert out["ok"] and out["source"] == places.NOMINATIM and "Nominatim" in out["note"]
    assert out["places"][0] == {"name": "The Meridian", "address": "425 Southwest 11th Street, Port Larch, Vale",
                                "kind": "apartments", "lat": 41.577821, "lon": -40.627299,
                                "distance_km": out["places"][0]["distance_km"]}
    assert asked["path"] == "search" and asked["q"] == "425 SW 11th Street"
    # The point only biases the search and leaves rounded, as it does for Photon.
    assert asked["viewbox"] == "-41.104,42.091,-40.104,41.091" and "bounded" not in asked


def test_nominatim_is_public_too_and_stays_off_with_the_public_switch(monkeypatch):
    monkeypatch.setenv("PHOTON_PUBLIC", "0")
    called = []
    out = places.search("x", fetch=lambda b, p: (_ for _ in ()).throw(ConnectionError("refused")),
                        nominatim=lambda path, p: called.append(path) or [])
    assert out["ok"] is False and called == []


def test_where_am_i_is_a_reverse_lookup_of_the_point(monkeypatch):
    monkeypatch.setenv("PHOTON_PUBLIC", "1")
    asked = {}

    def nominatim(path, params):
        asked.update(path=path, **params)
        return {"lat": "41.59112", "lon": "-40.60371", "address": {
            "house_number": "1007", "road": "East Quarry Road", "city": "Port Larch", "state": "Vale"}}

    out = json.loads(places.handle_place_search({"near": "41.59112345,-40.60371234"}, nominatim=nominatim, house=lambda params: None))
    assert out["ok"] and out["places"][0]["address"] == "1007 East Quarry Road, Port Larch, Vale"
    assert asked["path"] == "reverse" and (asked["lat"], asked["lon"]) == (41.5911, -40.6037)


def _never(*args):
    raise AssertionError(f"asked a public service: {args!r}")


def test_place_search_is_the_house_s_index_and_nothing_public_by_default(monkeypatch):
    """T-0765: by default an address went to the public Photon, then Nominatim, and a kind of place
    to the public Overpass, each with a point round the car. The house's index answers them now,
    and with it absent nothing public is asked unless the owner turns a public service on."""
    for name in ("PHOTON_PUBLIC", "OVERPASS_URL", "PHOTON_URL"):
        monkeypatch.delenv(name, raising=False)
    asked = []
    cafe = {"name": "Corner Bakery", "address": "", "kind": "amenity=cafe", "lat": 41.8795, "lon": -87.6297,
            "distance_km": 0.07}

    def house(params):
        asked.append(params)
        return [cafe]

    out = places.search("corner bakery", "41.8781,-87.6298", fetch=_never, nominatim=_never, house=house)
    assert out == {"ok": True, "places": [cafe], "source": "house"}
    assert asked[-1] == {"q": "corner bakery", "limit": 5, "lat": 41.8781, "lon": -87.6298}
    out = places.nearby("amenity=cafe", "41.8781,-87.6298", 3, fetch=_never, house=house)
    assert out["ok"] and out["places"] == [cafe] and out["source"] == "house"
    assert asked[-1] == {"kind": "amenity=cafe", "limit": 3, "lat": 41.8781, "lon": -87.6298}
    assert places.reverse("41.8781,-87.6298", _never, house=house)["places"] == [cafe]
    assert asked[-1] == {"limit": 1, "lat": 41.8781, "lon": -87.6298}

    tried = []
    gone = places.search("corner bakery", fetch=lambda b, p: tried.append(b) or _never(b),
                         nominatim=_never, house=lambda p: None)
    assert gone["ok"] is False and tried == ["http://127.0.0.1:2322"]
    assert places.nearby("amenity=cafe", "41.8,-87.6", fetch=_never, house=lambda p: None)["ok"] is False
    assert places.reverse("41.8,-87.6", _never, house=lambda p: None)["ok"] is False


def test_the_house_index_is_asked_with_the_house_token(monkeypatch):
    import httpx
    from _shared import _house

    seen = []

    def core(request):
        seen.append(request)
        if request.url.params.get("q") == "nowhere":
            return httpx.Response(404, json={"code": "no_places"})
        return httpx.Response(200, json={"results": [{"name": "Corner Bakery"}]})

    monkeypatch.setattr(_house, "configured", lambda: True)
    monkeypatch.setattr(_house, "client", lambda timeout=20.0: httpx.Client(
        base_url="http://house.test", transport=httpx.MockTransport(core)))
    assert places._house_places({"q": "corner"}) == [{"name": "Corner Bakery"}]
    assert (seen[0].method, seen[0].url.path) == ("GET", "/api/maps/places")
    assert places._house_places({"q": "nowhere"}) is None
    monkeypatch.setattr(_house, "configured", lambda: False)
    assert places._house_places({"q": "corner"}) is None and len(seen) == 2


def test_navigate_carries_stops_in_order_and_refuses_bad_ones():
    sent = []
    via = [{"lat": 41.9, "lon": -87.6, "label": "Pharmacy"}, {"lat": 41.95, "lon": -87.65}]
    car = {"status": "ok", "opened": "car"}
    ok = json.loads(places.handle_car_navigate({"lat": 42.0, "lon": -87.7, "label": "Home", "via": via},
                                               _answering(car, sent)))
    assert ok["ok"]
    assert sent[0][1]["via"] == [{"lat": 41.9, "lon": -87.6, "label": "Pharmacy"}, {"lat": 41.95, "lon": -87.65, "label": ""}]
    assert "via" not in json.loads(places.handle_car_navigate({"lat": 1, "lon": 1}, _answering(car, [])))["to"]
    for bad in ([{"lat": "x", "lon": 1}], [{"lat": 1, "lon": 1}] * 6, "home"):
        assert json.loads(places.handle_car_navigate({"lat": 1, "lon": 1, "via": bad}, lambda e, p: True))["ok"] is False


def test_route_eta_answers_minutes_km_and_the_arrival_time():
    asked = []

    def fetch(a, b):
        asked.append((a, b))
        return {"distance_m": 18400, "duration_s": 1260}

    out = json.loads(places.handle_route_eta({"from_lat": 41.88, "from_lon": -87.63, "lat": 42.0, "lon": -87.7},
                                             fetch, now=lambda: 0.0))
    assert out["ok"] and out["minutes"] == 21 and out["km"] == 18.4
    assert "about 21 minutes" in out["said"]
    assert asked[0] == ((41.88, -87.63), (42.0, -87.7))
    assert json.loads(places.handle_route_eta({"lat": 1, "lon": 1}, fetch))["ok"] is False
    assert json.loads(places.handle_route_eta({"from_lat": 1, "from_lon": 1, "lat": 1, "lon": 1},
                                              lambda a, b: {}))["ok"] is False


def test_route_eta_asks_the_house_router_not_a_public_one(monkeypatch):
    """T-0765: the drive's two ends went to the public OSRM; the house's Valhalla answers it."""
    import httpx
    from _shared import _house

    seen = []

    def house(request):
        seen.append(request)
        return httpx.Response(200, json={"distance_m": 1000, "duration_s": 120})

    monkeypatch.setattr(_house, "client", lambda timeout=20.0: httpx.Client(
        base_url="http://house.test", transport=httpx.MockTransport(house)))
    out = json.loads(places.handle_route_eta({"from_lat": 41.88, "from_lon": -87.63, "lat": 42.0, "lon": -87.7},
                                             now=lambda: 0.0))
    assert out["ok"] and out["minutes"] == 2
    assert (seen[0].method, str(seen[0].url)) == ("POST", "http://house.test/api/maps/route")
    assert json.loads(seen[0].content) == {"from": [41.88, -87.63], "to": [42.0, -87.7]}


def test_cancel_ends_the_route_and_needs_no_place():
    sent = []
    ok = json.loads(places.handle_car_navigate({"cancel": True}, lambda e, p: sent.append(p) or True))
    assert ok["ok"] and sent == [{"cancel": True}]
    assert json.loads(places.handle_car_navigate({}, lambda e, p: True))["ok"] is False


def test_a_long_drive_is_said_in_hours():
    assert places._duration_words(21) == "21 minutes"
    assert places._duration_words(60) == "1 hour"
    assert places._duration_words(131) == "2 hours 11 minutes"
    assert places._duration_words(1) == "1 minute"
