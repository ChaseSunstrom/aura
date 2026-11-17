"""Places near the car, and the route the car's phone should follow.

The owner, 2026-09-25: "auto navigation from the location using photon so
jarvis can find addresses nearest to me based on my location (I should also be
able to ask it to change routes, etc.)". A car turn tells Jarvis where the car
is (the phone adds it to the prompt, ``HermesCarJarvis``); ``place_search``
asks Photon for places matching the words, biased to that point and sorted by
distance; ``car_navigate`` sends ``hud.car.navigate`` to the session, and the
phone hands the destination to Organic Maps, Google Maps or HERE WeGo in the
car's app pane (``CarHud``). A new destination is a changed route.

Photon is the house's own (setting ``places.photon_url``, ``PHOTON_URL``, ``jarvis-core`` compose ``photon``,
``--profile geocode``) when one runs. Until then it falls back to the public
Photon at komoot, which then sees the words and a rounded point -- said in the
tool's result, and turned off with ``PHOTON_PUBLIC=0``.

Does not plan a route, read traffic or pick between the apps: the navigation
app does the routing, and which app is the person's choice on the phone.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Callable, Optional

import httpx
from aura_schema import from_env

from . import phone

from _shared import _house

_LOGGER = logging.getLogger(__name__)

#: Photon itself, hosted by its authors: asked only when the owner turns on
#: `places.photon_public` (off by default, T-0765). The house's own index
#: (jarvis-core `/api/maps/places`) and Photon (`places.photon_url`) come first.
PUBLIC = "https://photon.komoot.io"
# OpenStreetMap's Overpass answers "the nearest coffee": every place of a kind
# within a radius. Photon matches names -- "coffee" found places *called* coffee
# 2 km out, past a dozen cafes (measured 2026-09-25).
#: An OSM tag, key=value, the only shape spliced into the Overpass query.
KIND_RE = re.compile(r"[a-z][a-z_:]{0,30}=[a-z0-9][a-z0-9_;-]{0,40}")
RADIUS_M = 5000
NAVIGATE_EVENT = "hud.car.navigate"
#: The navigation apps the phone knows (``CarHud.NAV_APPS``); empty is "the person's first".
APPS = ("organic", "google", "here")
TIMEOUT_S = 6.0
#: Photon's public server refuses a client's default agent (403 to
#: "python-httpx"); it asks to be told who is calling.
USER_AGENT = "Jarvis-home-assistant/1.0 (self-hosted place_search)"

Fetch = Callable[[str, dict], dict]
#: The house's place index: the params of one ask -> its places, or None when it has no index
#: (or is not reachable), which is the one case a fallback is tried.
HouseFetch = Callable[[dict], Optional[list]]


def _house_places(params: dict) -> Optional[list]:
    if not _house.configured():
        return None
    try:
        with _house.client(timeout=TIMEOUT_S) as c:
            response = c.get("/api/maps/places", params=params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return list(response.json().get("results") or [])
    except Exception as exc:  # noqa: BLE001 - a house that cannot answer is "no index"
        _LOGGER.debug("the house's place index did not answer: %s", exc)
        return None


def _point(near: Optional[tuple[float, float]]) -> dict:
    # In the house, so the point goes as it is: rounding is for what leaves.
    return {"lat": near[0], "lon": near[1]} if near else {}
#: OpenStreetMap's own geocoder: the last resort when no Photon answers. The public
#: Photon took 4.8 s and then timed out twice on 2026-10-07 while Nominatim answered the
#: same address in 0.26 s. It is public, so it obeys the same `places.photon_public` switch.
NOMINATIM = "https://nominatim.openstreetmap.org"
NominatimFetch = Callable[[str, dict], Any]


def _nominatim(path: str, params: dict) -> Any:
    response = httpx.get(f"{NOMINATIM}/{path}", params={**params, "format": "jsonv2", "addressdetails": 1},
                         timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def _fetch(base: str, params: dict) -> dict:
    response = httpx.get(f"{base.rstrip('/')}/api", params=params, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def _endpoints() -> list[str]:
    settings = from_env()
    out = [str(settings.places_photon_url).rstrip("/")]
    if settings.places_photon_public and PUBLIC not in out:
        out.append(PUBLIC)
    return out


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance: close enough to rank the shops down the road."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _near(raw: Any) -> Optional[tuple[float, float]]:
    try:
        lat, lon = (float(x) for x in str(raw).split(","))
    except (TypeError, ValueError):
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _place(feature: dict, near: Optional[tuple[float, float]]) -> Optional[dict]:
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])
    street = " ".join(str(props[k]) for k in ("housenumber", "street") if props.get(k))
    where = ", ".join(str(props[k]) for k in ("city", "state") if props.get(k))
    place = {
        "name": props.get("name") or street or where,
        "address": ", ".join(x for x in (street, where) if x),
        "kind": props.get("osm_value") or props.get("type") or "",
        "lat": round(lat, 6),
        "lon": round(lon, 6),
    }
    if near:
        place["distance_km"] = round(distance_km(near[0], near[1], lat, lon), 2)
    return place


def _osm_place(row: dict, near: Optional[tuple[float, float]]) -> Optional[dict]:
    """A Nominatim row in the shape a Photon feature becomes."""
    try:
        lat, lon = float(row["lat"]), float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    addr = row.get("address") or {}
    street = " ".join(str(addr[k]) for k in ("house_number", "road") if addr.get(k))
    town = next((addr[k] for k in ("city", "town", "village", "hamlet", "suburb") if addr.get(k)), "")
    where = ", ".join(str(x) for x in (town, addr.get("state")) if x)
    place = {
        "name": row.get("name") or street or where,
        "address": ", ".join(x for x in (street, where) if x),
        "kind": row.get("type") or "",
        "lat": round(lat, 6),
        "lon": round(lon, 6),
    }
    if near:
        place["distance_km"] = round(distance_km(near[0], near[1], lat, lon), 2)
    return place


NOMINATIM_NOTE = "asked OpenStreetMap's Nominatim: the words and a rounded point left the house"


def reverse(near_raw: Any, nominatim: NominatimFetch = _nominatim, house: HouseFetch = _house_places) -> dict:
    """The address at [near_raw] ("where am I"): the house's index, else (if allowed) Nominatim,
    rounded to ~10 m because a street number needs it."""
    near = _near(near_raw)
    if near is None:
        return {"ok": False, "error": "near is \"lat,lon\""}
    found = house({"limit": 1, **_point(near)})
    if found is not None:
        if not found:
            return {"ok": False, "error": "the house's map has no address within 150 m"}
        return {"ok": True, "places": found, "source": "house"}
    if not from_env().places_photon_public:
        return {"ok": False, "error": "the house has no place index yet (scripts/map-region.sh) and public lookups are off"}
    try:
        row = nominatim("reverse", {"lat": round(near[0], 4), "lon": round(near[1], 4), "zoom": 18})
    except Exception as exc:
        return {"ok": False, "error": f"Nominatim did not answer ({exc})"}
    place = _osm_place(row, None) if isinstance(row, dict) else None
    if place is None:
        return {"ok": False, "error": "no address is known there"}
    return {"ok": True, "places": [place], "source": NOMINATIM, "note": NOMINATIM_NOTE.replace("words and a ", "")}


def search(query: str, near_raw: Any = None, limit: int = 5, fetch: Fetch = _fetch,
           nominatim: NominatimFetch = _nominatim, house: HouseFetch = _house_places) -> dict:
    """The places for [query], nearest first when [near_raw] is "lat,lon": the house's index,
    then Photon (the house's, then the public one if allowed), then Nominatim if allowed."""
    near = _near(near_raw)
    want = max(1, min(int(limit or 5), 10))
    found = house({"q": query, "limit": want, **_point(near)})
    if found:
        return {"ok": True, "places": found, "source": "house"}
    # Twice as many asked for as returned: Photon ranks by text first, so the
    # nearest few are not always its top few.
    params: dict[str, Any] = {"q": query, "limit": want * 2}
    if near:
        # Photon weighs by this point; rounded, because a shop down the road
        # does not need the car's position to the metre.
        params.update(lat=round(near[0], 3), lon=round(near[1], 3))
        # How hard the point pulls: Photon's default favours the words, and
        # "hardware" from Chicago's Loop came back 11 km out and in Rockford;
        # this brings the shops 1.5 km away first (measured 2026-09-25).
        params.update(zoom=14, location_bias_scale=0.1)
    last = ""
    for base in _endpoints():
        try:
            body = fetch(base, params)
        except Exception as exc:  # an absent local Photon is the ordinary case
            last = f"{base}: {exc}"
            _LOGGER.debug("photon at %s did not answer: %s", base, exc)
            continue
        places = [p for p in (_place(f, near) for f in body.get("features") or []) if p]
        if near:
            places.sort(key=lambda p: p["distance_km"])
        result: dict[str, Any] = {"ok": True, "places": places[:want], "source": base}
        if base == PUBLIC:
            result["note"] = "asked the public Photon (komoot): the words and a rounded point left the house"
        return result
    if from_env().places_photon_public:
        params = {"q": query, "limit": want}
        if near:
            # A preference, not a fence: an address across town still comes back.
            lat, lon = round(near[0], 3), round(near[1], 3)
            params["viewbox"] = f"{lon - 0.5:.3f},{lat + 0.5:.3f},{lon + 0.5:.3f},{lat - 0.5:.3f}"
        try:
            rows = nominatim("search", params)
        except Exception as exc:
            last += f"; {NOMINATIM}: {exc}"
        else:
            found = [p for p in (_osm_place(r, near) for r in rows or []) if p]
            if near:
                found.sort(key=lambda p: p["distance_km"])
            return {"ok": True, "places": found[:want], "source": NOMINATIM, "note": NOMINATIM_NOTE}
    return {"ok": False, "error": f"no geocoder answered ({last})"}


def _overpass(query: str) -> dict:
    response = httpx.post(str(from_env().places_overpass_url), data={"data": query},
                          timeout=TIMEOUT_S * 2, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def nearby(kind: str, near_raw: Any, limit: int = 5, fetch: Callable[[str], dict] = _overpass,
           house: HouseFetch = _house_places) -> dict:
    """Every place tagged [kind] ("amenity=cafe") within RADIUS_M of [near_raw], nearest first:
    the house's index, else Overpass when the owner has set `places.overpass_url`."""
    near = _near(near_raw)
    if near is None:
        return {"ok": False, "error": "a kind of place needs `near` (\"lat,lon\") to look around"}
    if not KIND_RE.fullmatch(kind):
        return {"ok": False, "error": "kind is an OpenStreetMap tag like amenity=cafe or shop=hardware"}
    key, value = kind.split("=", 1)
    want = max(1, min(int(limit or 5), 10))
    found = house({"kind": kind, "limit": want, **_point(near)})
    if found is not None:
        return {"ok": True, "places": found, "source": "house", "note": f"within {RADIUS_M // 1000} km, from the house's map"}
    if not str(from_env().places_overpass_url):
        return {"ok": False, "error": "the house has no place index yet (scripts/map-region.sh) and no Overpass is set"}
    lat, lon = round(near[0], 4), round(near[1], 4)
    query = f'[out:json][timeout:10];nwr["{key}"="{value}"](around:{RADIUS_M},{lat},{lon});out center 60;'
    try:
        body = fetch(query)
    except Exception as exc:
        return {"ok": False, "error": f"Overpass did not answer ({exc})"}
    out = []
    for element in body.get("elements") or []:
        tags = element.get("tags") or {}
        plat = element.get("lat", (element.get("center") or {}).get("lat"))
        plon = element.get("lon", (element.get("center") or {}).get("lon"))
        if plat is None or plon is None:
            continue
        street = " ".join(str(tags[k]) for k in ("addr:housenumber", "addr:street") if tags.get(k))
        out.append({
            "name": tags.get("name") or tags.get("brand") or value.replace("_", " "),
            "address": ", ".join(x for x in (street, tags.get("addr:city", "")) if x),
            "kind": value,
            "lat": round(float(plat), 6),
            "lon": round(float(plon), 6),
            "distance_km": round(distance_km(near[0], near[1], float(plat), float(plon)), 2),
        })
    out.sort(key=lambda p: p["distance_km"])
    return {"ok": True, "places": out[:want], "source": "overpass",
            "note": f"within {RADIUS_M // 1000} km; the kind and a rounded point went to OpenStreetMap's Overpass"}


def handle_place_search(args: dict, nominatim: NominatimFetch = _nominatim, house: HouseFetch = _house_places,
                        **kwargs: Any) -> str:
    del kwargs
    query = str(args.get("query") or "").strip()
    kind = str(args.get("kind") or "").strip().lower()
    limit = int(args.get("limit") or 5)
    if kind:
        return json.dumps(nearby(kind, args.get("near"), limit, house=house))
    if not query:
        if args.get("near"):
            # "Where am I": only the point, so the answer is the address there.
            return json.dumps(reverse(args.get("near"), nominatim, house=house))
        return json.dumps({"ok": False, "error": "place_search needs what to look for, or `near` alone for the address there"})
    return json.dumps(search(query, args.get("near"), limit, nominatim=nominatim, house=house))


def navigate_payload(args: dict) -> tuple[Optional[dict], str]:
    """The event's body, or why there is none. `cancel` ends the route on Jarvis's map."""
    if args.get("cancel") is True:
        return {"cancel": True}, ""
    try:
        lat, lon = float(args["lat"]), float(args["lon"])
    except (KeyError, TypeError, ValueError):
        return None, "car_navigate needs lat and lon (from place_search)"
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, "lat and lon are out of range"
    app = str(args.get("app") or "").strip().lower()
    if app and app not in APPS:
        return None, f"app is one of {', '.join(APPS)}, or empty for the person's own"
    label = str(args.get("label") or "").strip()[:80]
    via, problem = _stops(args.get("via"))
    if problem:
        return None, problem
    payload: dict = {"lat": lat, "lon": lon, "label": label, "app": app}
    if via:
        # Only Jarvis's own map routes through stops; a phone app gets the destination.
        payload["via"] = via
    return payload, ""


#: The router routes through any number; a drive's errands are a handful.
MAX_STOPS = 5


def _stops(raw: object) -> tuple[list[dict], str]:
    """Stops on the way, in order (the owner's "multi-stop errand chaining"), or why not."""
    if raw in (None, "", []):
        return [], ""
    if not isinstance(raw, list) or len(raw) > MAX_STOPS:
        return [], f"via is a list of up to {MAX_STOPS} stops, each {{lat, lon, label}}"
    out = []
    for stop in raw:
        try:
            lat, lon = float(stop["lat"]), float(stop["lon"])
        except (KeyError, TypeError, ValueError):
            return [], "each stop in via needs lat and lon (from place_search)"
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return [], "a stop's lat and lon are out of range"
        out.append({"lat": lat, "lon": lon, "label": str(stop.get("label") or "").strip()[:80]})
    return out, ""


#: How long a route waits for the screen that took it to say so. The car page and the phone
#: answer within a second of the event; past this, no screen in the conversation took it.
NAVIGATE_WAIT_S = 8.0


def handle_car_navigate(args: dict, emit: Callable[[str, dict], bool], wait_s: float = NAVIGATE_WAIT_S,
                        house: Optional[Callable[..., Optional[dict]]] = None) -> str:
    """Open the route where the person is, and say only where a screen opened it.

    The screen in the conversation that drives (the car page, the phone's popup or home screen)
    answers with ``phone-result <id>`` where it opened the route; with no answer, the phone's own
    maps app over the house's device channel (``start_navigation``). A route nobody opened is said
    to be not open, and has no ``to`` for learned.py to learn from: the tool used to report
    ``delivered`` when the event merely reached the session, and Jarvis told the owner the phone
    had the destination open when nothing had (2026-11-09). Does not cover a screen that opened
    the route and lost its answer on the way (the house is then asked too: a second open of the
    same destination)."""
    payload, problem = navigate_payload(args)
    if payload is None:
        return json.dumps({"ok": False, "error": problem})
    if payload.get("cancel"):
        return json.dumps({"ok": True, "delivered": emit(NAVIGATE_EVENT, payload), "to": payload})
    sent = False
    with phone.awaiting() as (rid, slot):
        sent = emit(NAVIGATE_EVENT, {**payload, "id": rid})
        if sent and slot["done"].wait(wait_s):
            answer = slot["answer"] or {}
            if not answer.get("ok"):
                return json.dumps({"ok": False, "error": str(answer.get("error") or "the screen could not open the route")})
            return json.dumps({"ok": True, "opened": str(answer.get("opened") or "phone"),
                               "app": str(answer.get("app") or ""), "to": payload})
    if house is not None:
        params = {"latitude": payload["lat"], "longitude": payload["lon"], "destination": payload["label"]}
        answered = house("start_navigation", params, "a route Jarvis was asked for")
        if answered and answered.get("ok"):
            return json.dumps({"ok": True, "opened": "phone", "via": "house", "to": payload})
    if sent:
        # Reached the conversation, unanswered: on a slow link the car's page answers late and
        # opens the route all the same (the owner's drive, 2026-11-11: "it didn't take, try
        # again" was said of a route the car then opened).
        return json.dumps({"ok": False, "error": f"the route was sent to this conversation's screen and is not confirmed: "
                                                 f"it did not answer in {wait_s:g} s, and the phone is not on the house's "
                                                 "channel. A car on a slow link may still open it when the event arrives. "
                                                 "Say it was sent and is not confirmed yet; do not say it is open, and do "
                                                 "not send it again unless asked"})
    return json.dumps({"ok": False, "error": "the route is not open anywhere: no car screen or phone in this "
                                             "conversation took it, and the phone is not on the house's channel. "
                                             "Say so, and do not say it is open"})


#: The house's router (jarvis-core ``/api/maps/route``, its Valhalla): the public OSRM this
#: used saw both ends of every drive (T-0765). (origin, destination) -> the house's answer.
RouteFetch = Callable[[tuple, tuple], dict]


def _route(a: tuple, b: tuple) -> dict:
    with _house.client(timeout=TIMEOUT_S) as c:
        response = c.post("/api/maps/route", json={"from": list(a), "to": list(b)})
    response.raise_for_status()
    return response.json()


def _duration_words(minutes: int) -> str:
    """"21 minutes", "1 hour", "2 hours 11 minutes": "131 minutes" is not how a drive is said."""
    hours, rest = divmod(minutes, 60)
    parts = ([f"{hours} hour{'s' if hours != 1 else ''}"] if hours else []) + \
        ([f"{rest} minute{'s' if rest != 1 else ''}"] if rest or not hours else [])
    return " ".join(parts)


def handle_route_eta(args: dict, fetch: RouteFetch = _route, now: Optional[Callable[[], float]] = None) -> str:
    """How long from the car to a place, by road: minutes, km and the arrival time.

    For "text Isa my ETA" and "how long to the store" (the 2026-10-08 wants): one
    lookup the model can quote or send. The car's position is the one its turn
    carries ("In the car, at lat,lon"). Not covered: live traffic -- the house router's
    time is the road network's, so the answer says "about".
    """
    import time as _time

    try:
        a = (float(args["from_lat"]), float(args["from_lon"]))
        b = (float(args["lat"]), float(args["lon"]))
    except (KeyError, TypeError, ValueError):
        return json.dumps({"ok": False, "error": "route_eta needs from_lat/from_lon (the car's position) and lat/lon"})
    if not all(-90 <= p[0] <= 90 and -180 <= p[1] <= 180 for p in (a, b)):
        return json.dumps({"ok": False, "error": "a position is out of range"})
    try:
        body = fetch(a, b)
        seconds = float(body["duration_s"])
        metres = float(body["distance_m"])
        # Inside the try: localtime/round raise OverflowError on an Infinity the router's JSON carried.
        arrive = _time.localtime((now or _time.time)() + seconds)
        minutes = max(1, round(seconds / 60))
    except Exception as exc:  # noqa: BLE001 - any router failure is one answer
        return json.dumps({"ok": False, "error": f"no route: {exc.__class__.__name__}"})
    return json.dumps({
        "ok": True, "minutes": minutes, "km": round(metres / 1000, 1),
        "arrive_at": _time.strftime("%-I:%M %p", arrive),
        "said": f"about {_duration_words(minutes)}, arriving around {_time.strftime('%-I:%M %p', arrive)}",
    })


MEDIA_EVENT = "hud.phone.media"
MEDIA_ACTIONS = ("play", "queue", "pause", "next", "previous")
MEDIA_KINDS = ("song", "album", "artist", "playlist", "genre", "any")
#: The structured fields a music app's play-from-search reads (MediaStore.EXTRA_MEDIA_*).
MEDIA_FIELDS = ("title", "artist", "album", "playlist", "genre")


def media_payload(args: dict) -> tuple[Optional[dict], str]:
    """The phone's media request (`phone_media`), or why there is none.

    Symfonium answered "No results" to a whole sentence as the query (the owner,
    2026-09-25): a music app matches an artist, an album or a title field by field,
    so each is sent alone beside the kind, and the query is the one field that names it.
    """
    action = str(args.get("action") or "play").strip().lower()
    if action not in MEDIA_ACTIONS:
        return None, f"action is one of {', '.join(MEDIA_ACTIONS)}"
    fields = {f: str(args.get(f) or "").strip()[:120] for f in MEDIA_FIELDS}
    fields = {f: v for f, v in fields.items() if v}
    kind = str(args.get("kind") or "").strip().lower()
    if kind and kind not in MEDIA_KINDS:
        return None, f"kind is one of {', '.join(MEDIA_KINDS)}"
    if not kind:
        # The most specific field named decides it.
        kind = next((k for k, f in (("song", "title"), ("album", "album"), ("playlist", "playlist"),
                                    ("artist", "artist"), ("genre", "genre")) if f in fields), "any")
    query = str(args.get("query") or "").strip()[:120]
    if not query:
        query = fields.get({"song": "title", "album": "album", "playlist": "playlist",
                            "artist": "artist", "genre": "genre"}.get(kind, ""), "")
    shuffle = args.get("shuffle")
    extra = {"shuffle": bool(shuffle)} if shuffle is not None else {}
    if action not in ("play", "queue"):
        return {"action": action, "query": "", "kind": "any", **extra}, ""
    if action == "queue" and not (query or fields):
        return None, "queue needs what to add: a title, an album, an artist or a query"
    return {"action": action, "query": query, "kind": kind, **fields, **extra}, ""


#: The phone's device-channel actions a media request becomes when it travels the house's
#: channel (android-app MediaActions.kt, ParityActions.kt): the media keys, and the search.
HOUSE_MEDIA_KEYS = "media_control"
HOUSE_MEDIA_SEARCH = "media_search"


def house_media(payload: dict) -> tuple[str, dict]:
    """The phone action and its parameters for a media request sent over the house's channel.
    A bare play, pause, next or previous is the media keys, which every installed app has;
    anything named, queued or shuffled is the search action, handed the request as it is."""
    named = payload.get("query") or any(payload.get(f) for f in MEDIA_FIELDS)
    if not named and "shuffle" not in payload and payload["action"] != "queue":
        return HOUSE_MEDIA_KEYS, {"command": payload["action"]}
    return HOUSE_MEDIA_SEARCH, payload


def handle_phone_media(args: dict, emit: Callable[[str, dict], bool],
                       house: Optional[Callable[..., Optional[dict]]] = None,
                       here: Optional[Callable[[], Optional[bool]]] = None) -> str:
    """Music on the person's phone -- the phone's own apps, not the house's speakers
    (the owner, 2026-09-25: "I asked it to play music and it didn't ... it kept doing
    HA calls"). The phone plays a query through Android's play-from-search, which the
    installed music app answers, and play/pause/next/previous through its active
    media session.

    Two routes (T-1474). In the phone's own conversation it is the session's frame. From a
    conversation that is no phone's (`here()` is False: the desk console, a scheduled job)
    a frame reaches nothing that plays music, and the session taking it used to read as
    "delivered": the request goes over the house's device channel instead (`house`, None for
    a guest, whose request must not open the owner's phone), and with no phone there the
    reply says so. When nobody can say whose conversation it is, the session is tried
    first and the house only if the session took nothing.

    Does not cover: two phones on the house's channel (it answers none, and the reply says
    no phone is here); a phone app older than the search action (its own refusal comes
    back); whether the music app found anything (the phone does not report it)."""
    payload, problem = media_payload(args)
    if payload is None:
        return json.dumps({"ok": False, "error": problem})
    away = here is not None and here() is False
    if not away and emit(MEDIA_EVENT, payload):
        return json.dumps({"ok": True, "delivered": True, "request": payload})
    if house is not None:
        action, params = house_media(payload)
        answered = house(action, params, "music on the phone")
        if answered is not None:
            ok = bool(answered.get("ok"))
            note = {}
            unknown = "unknown action" in str(answered.get("error") or "")
            if not ok and action == HOUSE_MEDIA_SEARCH and unknown:
                note = {"note": "the phone's app is older than this request and needs its update; "
                                "pause, next, previous and a bare play work without it"}
            return json.dumps({**answered, "ok": ok, "delivered": ok, "request": payload, **note})
    if away:
        return json.dumps({"ok": False, "delivered": False, "request": payload,
                           "error": "no phone is in this conversation, and none was reached over the "
                                    "house's channel. Say so; nothing was played or changed."})
    return json.dumps({"ok": False, "delivered": False, "request": payload,
                       "note": "no phone is connected to this conversation"})
