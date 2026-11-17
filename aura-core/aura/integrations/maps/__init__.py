"""Maps: the car's and the console's real map data, all from this house (T-0614).

    maps:
      data_dir: /config/maps-data        # what scripts/map-region.sh fetched
      photon_url: http://127.0.0.1:2322  # geocoding
      valhalla_url: http://127.0.0.1:8002  # routing and map matching
      bbox: [west, south, east, north]   # the region, else centre + radius_km,
      center: [lat, lon]                 # else what map-region.sh fetched, else
      radius_km: 25                      # where the car is driven, else the house

What it serves (``/api/maps/*``, bearer token, see ``api/rest.py``):

* ``tiles.pmtiles``: the region's Protomaps file, with HTTP ranges, so a
  PMTiles client reads only the tiles it draws.
* ``buildings``: footprints for a bbox. OSM ``height`` / ``building:levels``
  first; an Overture footprint only where no OSM building overlaps it, and an
  Overture height where OSM has the footprint but no height.
* ``places``: names, addresses and kinds of place ("amenity=cafe") near a point,
  from the region's own index (``osm-places.geojsonl``, out of the extract), so
  no public geocoder or Overpass hears where the car is (T-0765).
* ``geocode`` (Photon), ``route``, ``directions`` and ``match`` (Valhalla).
  ``directions`` is the drive in OSRM's shape, for the car's turn-by-turn
  (``jarvis-web`` ``route.ts``), so no public router sees where a drive goes (T-0765).

Not covered: nothing here knows whether the services are running (a dead one is
a 502 naming it); the data files are read as the script wrote them, so a region
bigger than a city is held in memory; ``match`` snaps a trace, the dead
reckoning between fixes is the renderer's. Live traffic has no source and is
not offered.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import json

from ...api.common import ApiError
from ...store import Store
from .._http import single_client
from . import pack, workzones
from .pack import height as _height

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "maps"
TILES_FILE = "region.pmtiles"
OSM_FILE = "osm-buildings.geojsonl"
OVERTURE_FILE = "overture-buildings.geojsonl"
ROADS_FILE = "osm-roads.geojsonl"
PLACES_FILE = "osm-places.geojsonl"
PACKS_DIR = "packs"  # one <region>.pack each (pack.py), beside the lists; regions.py lists them
NEAR_KM = 5.0  # "the nearest coffee" looks this far round the car
REVERSE_M = 150  # "where am I": the nearest address this close, else none
MAX_SPAN_DEG = 0.1  # ~10 km: a drive-view window, not a download of the region
MAX_BUILDINGS = 5000
MAX_ROADS = 5000
MAX_MATCH_POINTS = 100
MAX_VIA = 5  # car_navigate's stops on the way
COSTINGS = {"auto", "bicycle", "pedestrian", "motorcycle", "truck", "taxi"}
#: The usual US limit for a road that posts none, by Valhalla's road_class (T-0755); a ramp
#: off a motorway or trunk is slower than the road, and a parking aisle is walking pace.
USUAL_MPH = {"motorway": 70, "trunk": 55, "primary": 45, "secondary": 40, "tertiary": 35,
             "unclassified": 30, "residential": 25, "service_other": 15}
RAMP_MPH = {"motorway": 45, "trunk": 40}
_KPH_PER_MPH = 1.609344
#: Where the car is driven, counted in the house (T-0765): fixes per DRIVE_CELL degrees (about
#: 10 km, a region and never a street), and how many in one cell before it is the region.
DRIVES_KEY = "maps_drives"
DRIVE_CELL = 0.1
DRIVE_FIXES = 20
_CELL = 0.001  # overlap grid, ~100 m

_TIMEOUT = httpx.Timeout(10.0)


def _store(jarvis: "Jarvis") -> dict[str, Any]:
    return jarvis.data.setdefault(DOMAIN, {})


def _options(jarvis: "Jarvis") -> dict[str, Any]:
    return _store(jarvis).setdefault("options", {})


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    store = _store(jarvis)
    store["options"] = dict(config) if isinstance(config, dict) else {}
    store["drives_store"] = Store(jarvis.config_dir, DRIVES_KEY)
    cells = ((await store["drives_store"].load()) or {}).get("cells")
    store["drives"] = {k: int(v) for k, v in cells.items()} if isinstance(cells, dict) else {}

    async def close() -> None:
        client = store.get("client")
        if client is not None and store.get("owns_client") and not client.is_closed:
            await client.aclose()

    jarvis.register_shutdown(close)
    # Read the footprints and roads now, in a thread: cold they take ~10 s on the host, and a
    # car's first ask after a restart gave up at the phone's 10 s read timeout (T-0980).
    store["warm"] = asyncio.get_running_loop().create_task(_warm(jarvis))
    return True


async def _warm(jarvis: "Jarvis") -> None:
    if (_data_dir(jarvis) / PACKS_DIR / "home.pack").is_file():
        return  # T-0979: the home region is answered from its pack; the lists are never held
    for name, load in ((OSM_FILE, _load), (OVERTURE_FILE, _load), (ROADS_FILE, _load_roads)):
        await _cached(jarvis, name, load)


# --- region -----------------------------------------------------------------
def _around(lat: float, lon: float, radius: float) -> list[float]:
    dlat = radius / 111.0
    dlon = radius / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return [round(lon - dlon, 5), round(lat - dlat, 5), round(lon + dlon, 5), round(lat + dlat, 5)]


def _drive_cell(lat: float, lon: float) -> str:
    return f"{math.floor(lat / DRIVE_CELL)},{math.floor(lon / DRIVE_CELL)}"


async def _learn(jarvis: "Jarvis", fix: dict[str, float]) -> None:
    """Count the car's newest fix in its cell: one per match, and the car asks one every
    stretch of road, so a count is distance driven. Only the cell is kept."""
    store = _store(jarvis)
    cells = store.setdefault("drives", {})
    key = _drive_cell(fix["lat"], fix["lon"])
    cells[key] = cells.get(key, 0) + 1
    if store.get("drives_store") is not None:
        await store["drives_store"].save({"cells": cells})


def region(jarvis: "Jarvis") -> dict[str, Any]:
    """The bbox the data covers: a setting, else what map-region.sh fetched, else the cell the
    car is driven in most (once it has DRIVE_FIXES), else the house coordinates; +- radius."""
    o = _options(jarvis)

    def numbers(v: Any) -> list[str]:  # "a,b,c,d" from the environment, or a YAML list
        return [str(x).strip() for x in (v.split(",") if isinstance(v, str) else v or [])]

    try:
        bbox = [float(v) for v in numbers(o.get("bbox"))]
    except ValueError:
        bbox = []
    if len(bbox) == 4:
        return {"bbox": bbox, "source": "setting"}
    try:
        radius = float(o.get("radius_km") or 25)
    except ValueError:
        radius = 25.0
    if numbers(o.get("center")):
        try:
            lat, lon = (float(v) for v in numbers(o.get("center"))[:2])
            return {"bbox": _around(lat, lon, radius), "source": "setting"}
        except ValueError:
            pass
    try:
        fetched = [float(v) for v in numbers((_data_dir(jarvis) / "region.bbox").read_text().strip())]
    except (OSError, ValueError):
        fetched = []
    if len(fetched) == 4:
        return {"bbox": fetched, "source": "fetched"}
    cells = _store(jarvis).get("drives") or {}
    if cells:
        key, count = max(cells.items(), key=lambda kv: kv[1])
        if count >= DRIVE_FIXES:
            i, j = (int(v) for v in key.split(","))
            return {"bbox": _around((i + 0.5) * DRIVE_CELL, (j + 0.5) * DRIVE_CELL, radius), "source": "drives"}
    house = (jarvis.config or {}).get("jarvis") or {}
    try:
        lat, lon = float(house["latitude"]), float(house["longitude"])
    except (KeyError, TypeError, ValueError) as err:
        raise ApiError("no_region", "set maps.bbox or the house coordinates, or drive with the car page", 404) from err
    return {"bbox": _around(lat, lon, radius), "source": "house"}


def _data_dir(jarvis: "Jarvis") -> Path:
    return Path(_options(jarvis).get("data_dir") or jarvis.config_dir / "maps-data")


def tiles_path(jarvis: "Jarvis") -> Path:
    path = _data_dir(jarvis) / TILES_FILE
    if not path.is_file():
        raise ApiError("no_tiles", "no map tiles yet: run scripts/map-region.sh (docs/runbooks/maps.md)", 404)
    return path


# --- buildings ----------------------------------------------------------------
def parse_bbox(text: str) -> tuple[float, float, float, float]:
    try:
        w, s, e, n = (float(v) for v in text.split(","))
    except ValueError as err:
        raise ApiError("bad_bbox", "bbox is west,south,east,north in degrees", 400) from err
    ok = all(math.isfinite(v) for v in (w, s, e, n)) and -180 <= w < e <= 180 and -90 <= s < n <= 90
    if not ok or e - w > MAX_SPAN_DEG or n - s > MAX_SPAN_DEG:
        raise ApiError("bad_bbox", f"bbox must be ordered, on Earth and at most {MAX_SPAN_DEG} degrees across", 400)
    return w, s, e, n


def _load(path: Path) -> list[tuple[tuple[float, float, float, float], float | None, list]]:
    """(bbox, height, outer ring) per building. Blocking; call in a thread."""
    out = []
    with path.open("rb") as fh:
        for line in fh:
            line = line.strip().lstrip(b"\x1e")  # osmium writes RFC 8142 record separators
            if not line:
                continue
            try:
                f = json.loads(line)
                g = f["geometry"]
                # ponytail: a MultiPolygon draws as its first part; split it if a campus matters
                ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
                xs, ys = [p[0] for p in ring], [p[1] for p in ring]
            except (ValueError, KeyError, IndexError, TypeError):
                continue  # a point, a line or a broken row is not a footprint
            if g["type"] not in ("Polygon", "MultiPolygon") or len(ring) < 4:
                continue
            out.append(((min(xs), min(ys), max(xs), max(ys)), _height(f.get("properties") or {}), ring))
    return out


def _load_roads(path: Path) -> list[tuple[tuple[float, float, float, float], str, str, list]]:
    """(bbox, class, name, line) per road or path osm_roads.py wrote. Blocking; call in a thread."""
    out = []
    with path.open("rb") as fh:
        for line in fh:
            try:
                f = json.loads(line.strip().lstrip(b"\x1e"))
                pts = f["geometry"]["coordinates"]
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                p = f.get("properties") or {}
            except (ValueError, KeyError, IndexError, TypeError):
                continue
            if f["geometry"].get("type") != "LineString" or len(pts) < 2:
                continue
            out.append(((min(xs), min(ys), max(xs), max(ys)), str(p.get("highway") or ""), str(p.get("name") or ""), pts))
    return out


def _load_places(path: Path) -> list[dict[str, Any]]:
    """One row per place osm_places.py wrote, with its words for search. Blocking; call in a thread."""
    out = []
    with path.open("rb") as fh:
        for line in fh:
            try:
                f = json.loads(line.strip().lstrip(b"\x1e"))
                lon, lat = (float(v) for v in f["geometry"]["coordinates"][:2])
                p = f.get("properties") or {}
            except (ValueError, KeyError, TypeError):
                continue
            street = " ".join(str(p[k]) for k in ("housenumber", "street") if p.get(k))
            where = ", ".join(str(p[k]) for k in ("city", "state") if p.get(k))
            address = ", ".join(x for x in (street, where) if x)
            kind = str(p.get("kind") or "")
            name = str(p.get("name") or street or kind.partition("=")[2].replace("_", " "))
            out.append({"name": name, "address": address, "kind": kind, "lat": round(lat, 6),
                        "lon": round(lon, 6), "words": f"{name} {address}".lower().replace(",", " ").split()})
    return out


async def _cached(jarvis: "Jarvis", name: str, load: Any = _load) -> list:
    path = _data_dir(jarvis) / name
    try:
        st = path.stat()
    except OSError:
        return []
    cache = _store(jarvis).setdefault("cache", {})
    key = (st.st_mtime_ns, st.st_size)
    hit = cache.get(name)
    if hit is None or hit[0] != key:
        hit = cache[name] = (key, await asyncio.to_thread(load, path))
    return hit[1]


def _hits(box: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return b[0] <= box[2] and b[2] >= box[0] and b[1] <= box[3] and b[3] >= box[1]


async def buildings(jarvis: "Jarvis", bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """The footprints in [bbox], and the roads and paths through it (T-0980: the car's ground
    had buildings and no streets; one ask brings both). A road is whole, not cut at the bbox.

    T-1074: an empty answer for a place no pack covers says ``covered: false`` (a phone that
    kept it would draw bare ground there for as long as it keeps a tile), and unless
    ``maps.auto_cover`` is off or the privacy switch is on, asks the worker for the region
    round it (`regions.cover`), so the next ask a few minutes on has the place. Not covered:
    a place in no catalogue region stays empty, and is asked about again each time.
    """
    got = await _held(jarvis, bbox)
    if got["buildings"] or got["roads"]:
        return got
    from . import regions  # it imports this module

    lat, lon = (bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2
    # ponytail: opens every pack's meta per empty ask; cache `covered` by cell if a house holds hundreds
    if await regions.covered(jarvis, lat, lon):
        return got
    if _options(jarvis).get("auto_cover", True):
        try:
            await regions.cover(jarvis, lat, lon)
        except ApiError as err:  # the jobs directory not writable: the answer is still the answer
            _LOGGER.warning("maps: no region asked for an uncovered place: %s", err)
    return {**got, "covered": False}


async def _held(jarvis: "Jarvis", bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    packs = sorted((_data_dir(jarvis) / PACKS_DIR).glob("*.pack"))
    if packs:  # T-0979: a region too big to hold is a file; the lists answer what no pack covers
        packed = await asyncio.to_thread(pack.read, packs, bbox, MAX_BUILDINGS)
        if packed is not None:
            return packed
    roads = [r for r in await _cached(jarvis, ROADS_FILE, _load_roads) if _hits(bbox, r[0])]
    osm = [r for r in await _cached(jarvis, OSM_FILE) if _hits(bbox, r[0])]
    overture = [r for r in await _cached(jarvis, OVERTURE_FILE) if _hits(bbox, r[0])]
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (b, _h, _r) in enumerate(osm):
        for cx in range(int(b[0] // _CELL), int(b[2] // _CELL) + 1):
            for cy in range(int(b[1] // _CELL), int(b[3] // _CELL) + 1):
                grid.setdefault((cx, cy), []).append(i)
    heights = [r[1] for r in osm]
    sources = ["osm"] * len(osm)
    extra = []
    for b, h, ring in overture:
        near = {
            i
            for cx in range(int(b[0] // _CELL), int(b[2] // _CELL) + 1)
            for cy in range(int(b[1] // _CELL), int(b[3] // _CELL) + 1)
            for i in grid.get((cx, cy), ())
            if _hits(b, osm[i][0])
        }
        if not near:
            extra.append((h, ring, "overture"))
        elif h:
            for i in near:
                if heights[i] is None:
                    heights[i], sources[i] = h, "osm+overture"
    rows = [(heights[i], osm[i][2], sources[i]) for i in range(len(osm))] + extra
    return {
        "buildings": [
            {"ring": ring, "height_m": h, "source": src} for h, ring, src in rows[:MAX_BUILDINGS]
        ],
        "roads": [{"line": line, "kind": kind, "name": name} for _b, kind, name, line in roads[:MAX_ROADS]],
        "details": [],  # T-1066: trees, lamps and parking come only from a pack
        "ground": [],  # T-1105: and so do the ground's heights; from the lists the place is flat
        "truncated": len(rows) > MAX_BUILDINGS or len(roads) > MAX_ROADS,
    }


# --- live work zones (T-1108) ---------------------------------------------------
WORKZONES_TTL_S = 600.0  # a works feed changes by the hour; ten minutes is fresh and is polite
WORKZONES_DEADLINE_S = 30.0  # the whole read; the client's 10 s is per piece, and a slow drip holds the lock
WORKZONES_KEEP = 3  # after this many TTLs with no good read the last one is not "live" any more


async def _feed(jarvis: "Jarvis", url: str) -> list[dict[str, Any]]:
    """Every zone of the feed at [url] in the region. Raises ValueError, httpx.HTTPError or TimeoutError.

    The body is read in pieces and dropped past the cap, so a wrong URL (a video, a national
    archive) costs 20 MB of traffic and no memory. No redirect is followed (single_client).
    """
    try:
        box = tuple(region(jarvis)["bbox"])
    except ApiError:
        box = (-180.0, -90.0, 180.0, 90.0)
    body = bytearray()
    async with asyncio.timeout(WORKZONES_DEADLINE_S):
        async with single_client(jarvis, DOMAIN, timeout=_TIMEOUT).stream("GET", url) as r:
            if r.status_code != 200:
                raise ValueError(f"the feed answered {r.status_code}")
            async for piece in r.aiter_bytes():
                body += piece
                if len(body) > workzones.MAX_BYTES:
                    raise ValueError("feed too large")
    return await asyncio.to_thread(workzones.read, bytes(body), box)


async def live(jarvis: "Jarvis", bbox: tuple[float, float, float, float], now: float | None = None) -> dict[str, Any]:
    """Roadworks and closures under way in [bbox]: ``{zones: [{kind, line}], source, as_of}``.

    ``source`` is ``off`` with no ``maps.workzones_url`` (or one that is not https, or the
    privacy switch on): nothing is asked and nothing is claimed. ``down`` when the feed has
    not been read for WORKZONES_KEEP TTLs: no zones, since an old list is not a live one.
    Otherwise ``on``, with ``as_of`` the last good read. One read serves every ask for
    ``maps.workzones_ttl_s``; a failed read is not retried before then either.

    Does not cover: traffic speeds, a zone its publisher never entered, or lanes.
    """
    from ..sky import quiet_house

    o = _options(jarvis)
    url = str(o.get("workzones_url") or "").strip()
    if not url.startswith("https://") or quiet_house():
        return {"zones": [], "source": "off", "as_of": None}
    try:
        ttl = max(60.0, float(o.get("workzones_ttl_s") or WORKZONES_TTL_S))
    except ValueError:
        ttl = WORKZONES_TTL_S
    now = time.time() if now is None else now
    store = _store(jarvis)
    async with store.setdefault("workzones_lock", asyncio.Lock()):
        kept = store.setdefault("workzones", {"tried": None, "as_of": None, "zones": []})
        if kept["tried"] is None or now - kept["tried"] >= ttl:
            kept["tried"] = now
            try:
                kept["zones"], kept["as_of"] = await _feed(jarvis, url), now
            except (ValueError, httpx.HTTPError, TimeoutError) as err:
                # The URL can carry a key in its query: the log names the failure, not the feed.
                _LOGGER.warning("work-zone feed not read (%s)", type(err).__name__)
    if kept["as_of"] is None or now - kept["as_of"] > WORKZONES_KEEP * ttl:
        return {"zones": [], "source": "down", "as_of": kept["as_of"]}
    return {"zones": workzones.live(kept["zones"], bbox, now), "source": "on", "as_of": kept["as_of"]}


# --- geocode, route, match ----------------------------------------------------
async def _call(jarvis: "Jarvis", service: str, method: str, path: str, **kw: Any) -> dict[str, Any]:
    o = _options(jarvis)
    base = {
        "photon": o.get("photon_url") or "http://127.0.0.1:2322",
        "valhalla": o.get("valhalla_url") or "http://127.0.0.1:8002",
    }[service]
    client = single_client(jarvis, DOMAIN, timeout=_TIMEOUT)
    try:
        r = await client.request(method, base.rstrip("/") + path, **kw)
    except httpx.HTTPError as err:
        raise ApiError(f"{service}_down", f"{service} is not answering ({type(err).__name__})", 502) from err
    if r.status_code >= 500:
        raise ApiError(f"{service}_down", f"{service} failed ({r.status_code})", 502)
    if r.status_code >= 400:
        # Valhalla says why a route is impossible; pass the reason, not the stack.
        try:
            why = str(r.json().get("error") or r.status_code)
        except ValueError:
            why = str(r.status_code)
        raise ApiError(f"{service}_refused", f"{service}: {why}", 404 if service == "valhalla" else 400)
    try:
        return r.json()
    except ValueError as err:
        raise ApiError(f"{service}_down", f"{service} sent something that is not JSON", 502) from err


async def geocode(jarvis: "Jarvis", q: str, limit: int, lat: float | None, lon: float | None) -> dict[str, Any]:
    q = q.strip()
    if not q:
        raise ApiError("bad_query", "q is empty", 400)
    if lat is None or lon is None:  # bias to the house: "the shop" means the near one
        house = (jarvis.config or {}).get("jarvis") or {}
        lat, lon = house.get("latitude"), house.get("longitude")
    params: dict[str, Any] = {"q": q[:200], "limit": max(1, min(limit, 10))}
    if lat is not None and lon is not None:
        params.update(lat=lat, lon=lon)
    data = await _call(jarvis, "photon", "GET", "/api", params=params)
    results = []
    for f in data.get("features") or []:
        try:
            x, y = f["geometry"]["coordinates"][:2]
            p = f.get("properties") or {}
        except (KeyError, TypeError, ValueError):
            continue
        name = p.get("name") or p.get("street")
        if not name:
            continue
        label = ", ".join(str(v) for v in dict.fromkeys(p.get(k) for k in ("name", "city", "country")) if v)
        results.append({"name": name, "label": label, "lat": y, "lon": x})
    return {"results": results}


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742.0 * math.asin(math.sqrt(a))


async def places(jarvis: "Jarvis", q: str, kind: str, lat: float | None, lon: float | None, limit: int) -> dict[str, Any]:
    """Places by name or address ([q]), by kind within NEAR_KM, or the address at the point.

    Not covered: spelling. A word must start a word of the place's name or address
    ("st" finds "Street", "Starbuck" finds "Starbucks", "Strabucks" finds nothing).
    """
    near = (lat, lon) if lat is not None and lon is not None else None
    if near is None and (kind or not q.strip()):
        raise ApiError("bad_point", "a kind of place, or the address here, needs lat and lon", 400)
    rows = await _cached(jarvis, PLACES_FILE, _load_places)
    if not rows:
        raise ApiError("no_places", f"no place index: scripts/map-region.sh with an extract writes {PLACES_FILE}", 404)

    def km(r: dict[str, Any]) -> float:
        return _km(near[0], near[1], r["lat"], r["lon"]) if near else 0.0

    # ponytail: a scan of the region's places per ask; an FTS5 table if a state-sized region is slow
    if kind:
        hits = [r for r in rows if r["kind"] == kind and km(r) <= NEAR_KM]
    elif q.strip():
        terms = q.lower().replace(",", " ").split()
        hits = [r for r in rows if all(any(w.startswith(t) for w in r["words"]) for t in terms)]
    else:
        hits = [r for r in rows if r["address"] and km(r) * 1000 <= REVERSE_M]
    hits.sort(key=km)
    out = []
    for r in hits[:max(1, min(int(limit), 10))]:
        row = {k: v for k, v in r.items() if k != "words"}
        if near:
            row["distance_km"] = round(km(r), 2)
        out.append(row)
    return {"results": out}


def _point(value: Any) -> dict[str, float]:
    try:
        lat, lon = float(value[0]), float(value[1])
        if len(value) != 2 or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (TypeError, ValueError, IndexError) as err:
        raise ApiError("bad_point", "a point is [lat, lon] on Earth", 400) from err
    return {"lat": lat, "lon": lon}


def decode_polyline6(text: str) -> list[list[float]]:
    out, i, lat, lon = [], 0, 0, 0
    while i < len(text):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(text[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        out.append([lat / 1e6, lon / 1e6])
    return out


async def route(jarvis: "Jarvis", origin: Any, dest: Any, costing: str = "auto") -> dict[str, Any]:
    if costing not in COSTINGS:
        raise ApiError("bad_costing", f"costing is one of {sorted(COSTINGS)}", 400)
    body = {"locations": [_point(origin), _point(dest)], "costing": costing, "directions_options": {"units": "kilometers"}}
    trip = (await _call(jarvis, "valhalla", "POST", "/route", json=body)).get("trip") or {}
    legs = trip.get("legs") or []
    if not legs:
        raise ApiError("no_route", "no route between those points", 404)
    leg = legs[0]
    shape = decode_polyline6(leg.get("shape") or "")
    moves = leg.get("maneuvers") or []

    def street(m: dict[str, Any]) -> str:
        names = m.get("street_names") or []
        return str(names[0]) if names else ""

    # Only the street you are on and the next turn are named: the screen shows
    # nothing else. Maneuver 0 is "start" and its length runs to the next one.
    nxt = None
    if len(moves) > 1:
        m = moves[1]
        idx = m.get("begin_shape_index")
        nxt = {
            "instruction": m.get("instruction", ""),
            "street": street(m),
            "distance_m": round(float(moves[0].get("length") or 0) * 1000),
            "at": shape[idx] if isinstance(idx, int) and 0 <= idx < len(shape) else None,
        }
    summary = trip.get("summary") or {}
    return {
        "distance_m": round(float(summary.get("length") or 0) * 1000),
        "duration_s": round(float(summary.get("time") or 0)),
        "current_street": street(moves[0]) if moves else "",
        "next_turn": nxt,
        "shape": shape,
    }


async def directions(jarvis: "Jarvis", origin: Any, via: Any, dest: Any) -> dict[str, Any]:
    """Valhalla's answer in OSRM's format (geometry as GeoJSON, a leg per stop), as it came.

    Not covered: the maneuver words are Valhalla's mapping onto OSRM's types; a type OSRM
    never sends is said as a plain turn by the car.
    """
    if not isinstance(via, list) or len(via) > MAX_VIA:
        raise ApiError("bad_via", f"via is up to {MAX_VIA} [lat, lon] stops, in order", 400)
    body = {"locations": [_point(p) for p in [origin, *via, dest]], "costing": "auto",
            "format": "osrm", "shape_format": "geojson"}
    answer = await _call(jarvis, "valhalla", "POST", "/route", json=body)
    if not answer.get("routes"):
        raise ApiError("no_route", "no route between those points", 404)
    return answer


async def match(jarvis: "Jarvis", points: Any) -> dict[str, Any]:
    if not isinstance(points, list) or not 1 <= len(points) <= MAX_MATCH_POINTS:
        raise ApiError("bad_points", f"points is 1 to {MAX_MATCH_POINTS} [lat, lon] fixes, oldest first", 400)
    shape = [_point(p) for p in points]
    # Before Valhalla: with no region fetched yet it cannot answer, and the drive is how the
    # house learns which region to fetch.
    await _learn(jarvis, shape[-1])
    body = {
        # Valhalla refuses a shape of one ("Insufficient shape provided"), and a drive's first
        # ask is one fix: the car showed no limit for its first 30 s (T-0755).
        "shape": shape * 2 if len(shape) == 1 else shape,
        "costing": "auto",
        "shape_match": "map_snap",
        "filters": {"attributes": ["matched.point", "matched.edge_index", "edge.names",
                                   "edge.speed_limit", "edge.road_class", "edge.use"], "action": "include"},
    }
    data = await _call(jarvis, "valhalla", "POST", "/trace_attributes", json=body)
    matched = data.get("matched_points") or []
    if not matched:
        raise ApiError("no_match", "those fixes are not on a road", 404)
    last = matched[-1]
    edges = data.get("edges") or []
    idx = last.get("edge_index")
    edge = edges[idx] if isinstance(idx, int) and 0 <= idx < len(edges) else {}
    return {"lat": last["lat"], "lon": last["lon"], "street": (edge.get("names") or [""])[0], **_limit(edge)}


def _limit(edge: dict[str, Any]) -> dict[str, Any]:
    """The speed limit of the road a match ends on, for the car (T-0755): OSM's posted one, which
    Valhalla keeps in whole km/h, else the usual one for the road's class. Does not cover a
    variable, school-zone or time-of-day limit, or a country whose usual limits are not the US's."""
    road = str(edge.get("road_class") or "")
    kph = edge.get("speed_limit")
    if isinstance(kph, (int, float)) and not isinstance(kph, bool) and 0 < kph < 200:
        return {"limit_mph": round(kph / _KPH_PER_MPH), "limit_source": "posted", "road": road}
    usual = RAMP_MPH.get(road) if edge.get("use") == "ramp" else None
    usual = usual or USUAL_MPH.get(road)
    if usual is None:
        return {"limit_mph": None, "limit_source": "none", "road": road}
    return {"limit_mph": usual, "limit_source": "typical", "road": road}


from . import regions as regions  # noqa: E402  (it reads this module's helpers)
