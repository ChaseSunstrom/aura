"""Map regions (T-0979): Geofabrik's catalogue, what the owner asked to hold, and the packs.

The house only reads the map data (its mount is read-only and its image has no
OSM reader), so it does not download or build anything itself. It writes an ask,
``<jobs>/<id>.want`` holding ``present`` or ``absent``, and ``scripts/map_worker.py``
(the ``maps-worker`` service) carries it out and says how far it is in
``<data>/regions/<id>.json``. A region is ready when its pack is on disk.

A region's id is Geofabrik's with ``/`` written ``-`` ("us/utah" is ``us-utah``),
so it is a file name and one URL segment. ``home`` is the pack
``scripts/map-region.sh`` builds for the configured region; it is listed and
never removed here.

Not covered: the catalogue is whatever the worker last fetched (``ready`` false
until it has); nothing here knows whether the worker is running, so an ask can
stay ``queued``; a region is found for a point by Geofabrik's simplified outline,
which is a few km off at a border.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...api.common import ApiError
from . import _data_dir, _options, _store, pack

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

INDEX_FILE = "geofabrik-index.json"
PACKS_DIR = "packs"
STATUS_DIR = "regions"
HOME = "home"
#: Only Geofabrik's own downloads are offered: the worker fetches the URL the index names.
PBF_PREFIX = "https://download.geofabrik.de/"
#: Self-fetched regions that may be waiting on the worker at once (a drive crosses a border or two).
AUTO_PENDING = 3
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def jobs_dir(jarvis: "Jarvis") -> Path:
    return Path(_options(jarvis).get("jobs_dir") or _data_dir(jarvis) / "jobs")


def pack_path(jarvis: "Jarvis", region: str) -> Path:
    """The region's pack on disk; 404 for an id that is no region's or has no pack yet."""
    path = _data_dir(jarvis) / PACKS_DIR / f"{region}.pack"
    if not _ID.fullmatch(region) or not path.is_file():
        raise ApiError("no_pack", "no such region on this house", 404)
    return path


def _outline(geometry: dict[str, Any]) -> list[list]:
    """The outer rings of a Polygon or MultiPolygon (a hole is ignored: see the module's limits)."""
    coords = geometry.get("coordinates") or []
    polys = [coords] if geometry.get("type") == "Polygon" else coords if geometry.get("type") == "MultiPolygon" else []
    return [p[0] for p in polys if p and len(p[0]) >= 4]


def _load_index(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        features = json.loads(path.read_text(encoding="utf-8")).get("features") or []
    except (OSError, ValueError, AttributeError):
        return out
    for f in features:
        try:
            p = f["properties"]
            rid, pbf = str(p["id"]).replace("/", "-"), str(p["urls"]["pbf"])
            rings = _outline(f["geometry"])
            xs = [float(pt[0]) for r in rings for pt in r]
            ys = [float(pt[1]) for r in rings for pt in r]
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not _ID.fullmatch(rid) or rid == HOME or not pbf.startswith(PBF_PREFIX) or not xs:
            continue
        out.append({"id": rid, "name": str(p.get("name") or rid), "parent": str(p.get("parent") or "").replace("/", "-"),
                    "pbf": pbf, "bbox": [min(xs), min(ys), max(xs), max(ys)], "rings": rings})
    return out


async def _index(jarvis: "Jarvis") -> list[dict[str, Any]]:
    path = _data_dir(jarvis) / INDEX_FILE
    try:
        st = path.stat()
    except OSError:
        return []
    cache = _store(jarvis).setdefault("cache", {})
    key = (st.st_mtime_ns, st.st_size)
    hit = cache.get(INDEX_FILE)
    if hit is None or hit[0] != key:
        hit = cache[INDEX_FILE] = (key, await asyncio.to_thread(_load_index, path))
    return hit[1]


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "name", "parent", "bbox")}


async def catalogue(jarvis: "Jarvis", q: str, limit: int = 20) -> dict[str, Any]:
    """Regions whose name or id holds every word of [q]: a name that starts with it first, then the shortest."""
    rows = await _index(jarvis)
    terms = q.lower().split()
    hits = [r for r in rows if terms and all(t in f"{r['name']} {r['id']}".lower() for t in terms)]
    hits.sort(key=lambda r: (not r["name"].lower().startswith(terms[0]), len(r["name"]), r["id"]))
    return {"ready": bool(rows), "results": [_public(r) for r in hits[:max(1, min(int(limit), 50))]]}


def _inside(lon: float, lat: float, ring: list) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


async def _regions_at(jarvis: "Jarvis", lat: float, lon: float) -> list[dict[str, Any]]:
    """Every catalogue region whose outline holds the point, smallest first."""
    found = []
    for r in await _index(jarvis):
        w, s, e, n = r["bbox"]
        if w <= lon <= e and s <= lat <= n and any(_inside(lon, lat, [p[:2] for p in ring]) for ring in r["rings"]):
            found.append(((e - w) * (n - s), r))
    return [r for _area, r in sorted(found, key=lambda f: f[0])]


async def region_at(jarvis: "Jarvis", lat: float, lon: float) -> dict[str, Any] | None:
    """The smallest catalogue region round the point (a state before its country), or None."""
    found = await _regions_at(jarvis, lat, lon)
    return _public(found[0]) if found else None


def _packs(jarvis: "Jarvis") -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted((_data_dir(jarvis) / PACKS_DIR).glob("*.pack")):
        about = pack.info(path)
        if about is not None and _ID.fullmatch(path.stem):
            out[path.stem] = about
    return out


async def covered(jarvis: "Jarvis", lat: float, lon: float) -> bool:
    """Whether a pack on disk holds the point.

    A catalogue region's pack covers its outline, not its bounding box: a state's box holds
    the city just over its border, and a trip there got no neighbouring state while this
    tested the box. A pack that is no
    catalogue region (the home pack) is a box, and its box is what it holds.
    """
    packs = await asyncio.to_thread(_packs, jarvis)
    if any(r["id"] in packs for r in await _regions_at(jarvis, lat, lon)):
        return True
    known = {r["id"] for r in await _index(jarvis)}
    return any(p["bbox"][0] <= lon <= p["bbox"][2] and p["bbox"][1] <= lat <= p["bbox"][3]
               for rid, p in packs.items() if rid not in known)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256(jarvis: "Jarvis", region: str) -> str:
    """The pack file's sha256, for the phone to check its copy against (T-1024).

    A state's pack is gigabytes and the list is asked every few hours by every phone, so the
    hash is kept by the file's size and mtime and read again only when either moves. Not
    covered: a pack changed in place with the same size and mtime (the worker writes a new
    file and renames it, so its mtime moves); a file replaced between this and the download
    (the phone then refuses the copy and tries again at the next run). Blocking.
    """
    path = _data_dir(jarvis) / PACKS_DIR / f"{region}.pack"
    try:
        st = path.stat()
    except OSError:
        return ""
    cache = _store(jarvis).setdefault("sha256", {})
    key = (st.st_size, st.st_mtime_ns)
    hit = cache.get(region)
    if hit is None or hit[0] != key:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as f:
                while block := f.read(1 << 20):
                    digest.update(block)
        except OSError:
            return ""
        hit = cache[region] = (key, digest.hexdigest())
    return hit[1]


async def pack_etag(jarvis: "Jarvis", region: str) -> str:
    """The pack's ETag: its sha256 in quotes, "" when it cannot be read.

    Starlette's own is the file's mtime and size, which says nothing to a phone that checks its
    copy against the sha256 the list gives (T-1024). Not covered: a pack replaced between this
    and the file's opening (the worker renames a new file over it; the phone's own check then
    refuses the copy).
    """
    digest = await asyncio.to_thread(_sha256, jarvis, region)
    return f'"{digest}"' if digest else ""


def tag_held(if_none_match: str | None, tag: str) -> bool:
    """Whether an If-None-Match names [tag] (weak or not, alone or in a list, or ``*``)."""
    if not tag or not if_none_match:
        return False
    return any(t.strip().removeprefix("W/") in (tag, "*") for t in if_none_match.split(","))


def _listing(jarvis: "Jarvis", names: dict[str, str]) -> list[dict[str, Any]]:
    packs = _packs(jarvis)
    wants = {p.stem: p.read_text(encoding="utf-8").strip() for p in jobs_dir(jarvis).glob("*.want") if _ID.fullmatch(p.stem)}
    rows = []
    for rid in sorted(set(packs) | set(wants)):
        said = _read(_data_dir(jarvis) / STATUS_DIR / f"{rid}.json")
        said = said if isinstance(said, dict) else {}
        if wants.get(rid) == "absent":
            state = "removing"
        elif rid in wants and said.get("state") in ("working", "failed", "held"):
            state = said["state"]
        elif rid in packs and (rid not in wants or said.get("state") == "ready"):
            state = "ready"
        else:
            state = "queued"
        # auto: the house fetched it on its own (`cover`), so the worker may drop it at its cap
        # and a phone does not copy it whole (T-1074); an owner's ask (`want`) clears it.
        row = {"id": rid, "name": names.get(rid) or ("Home" if rid == HOME else rid), "state": state,
               "auto": (jobs_dir(jarvis) / f"{rid}.auto").is_file(),
               "note": str(said.get("note") or "") if state in ("working", "failed", "held") else ""}
        if rid in packs and state == "ready" and (digest := _sha256(jarvis, rid)):
            row["sha256"] = digest
        rows.append({**row, **{k: v for k, v in packs.get(rid, {}).items() if k != "name"}})
    return rows


async def listing(jarvis: "Jarvis") -> dict[str, Any]:
    names = {r["id"]: r["name"] for r in await _index(jarvis)}
    return {"regions": await asyncio.to_thread(_listing, jarvis, names)}


async def _one(jarvis: "Jarvis", region: str) -> dict[str, Any]:
    return next(r for r in (await listing(jarvis))["regions"] if r["id"] == region)


def _ask(jarvis: "Jarvis", region: str, what: str) -> None:
    jobs = jobs_dir(jarvis)
    try:
        jobs.mkdir(parents=True, exist_ok=True)
        (jobs / f"{region}.want").write_text(what, encoding="utf-8")
    except OSError as err:  # the read-only map data, or a directory the house's user cannot write
        raise ApiError("no_jobs_dir", f"the house cannot leave its ask in {jobs} (docs/runbooks/maps.md)", 503) from err


async def want(jarvis: "Jarvis", region: Any) -> dict[str, Any]:
    """Ask the worker to fetch and pack a catalogue region; asking again is the same ask."""
    known = {r["id"] for r in await _index(jarvis)}
    if not isinstance(region, str) or region not in known:
        raise ApiError("no_region", "no such region in the catalogue (GET /api/maps/catalogue?q=)", 404)
    _ask(jarvis, region, "present")
    (jobs_dir(jarvis) / f"{region}.auto").unlink(missing_ok=True)  # the owner's own now: never evicted
    (jobs_dir(jarvis) / f"{region}.removed").unlink(missing_ok=True)  # asked for by name: lifted
    return await _one(jarvis, region)


async def unwant(jarvis: "Jarvis", region: str) -> dict[str, Any]:
    """Ask the worker to remove a region's pack and extract."""
    if region == HOME:
        raise ApiError("home_region", "the home region is the house's own; change maps.bbox to move it", 400)
    jobs = jobs_dir(jarvis)
    held = _ID.fullmatch(region) and ((jobs / f"{region}.want").is_file() or (_data_dir(jarvis) / PACKS_DIR / f"{region}.pack").is_file())
    if not held:
        raise ApiError("no_region", "no such region on this house", 404)
    _ask(jarvis, region, "absent")
    # The tombstone: the worker deletes the ask once it has removed the pack, and without a mark
    # the next drive ending here made `cover` fetch the region the owner had just refused. It is
    # a marker the worker never reads (it globs `*.want` and `*.auto` only); `want` lifts it.
    # Not covered: a tombstone made while the jobs directory is unwritable (the ask failed first).
    (jobs / f"{region}.removed").touch()
    return await _one(jarvis, region)


async def cover(jarvis: "Jarvis", lat: Any, lon: Any) -> dict[str, Any]:
    """Make sure a pack covers the point: ask for the smallest catalogue region round it when
    none does. ``asked`` is that region (None when covered, already asked for, or in no region)."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError) as err:
        raise ApiError("bad_point", "lat and lon are numbers", 400) from err
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError("bad_point", "lat and lon are out of range", 400)
    if await covered(jarvis, lat, lon):
        return {"covered": True, "asked": None}
    # A download nobody asked for is a call out on the house's own: the privacy switch stops it
    # (an asked-for region, `want`, is the owner's own and still goes).
    from ..sky import quiet_house
    if quiet_house():
        return {"covered": False, "asked": None, "pending": None, "quiet": True}
    region = await region_at(jarvis, lat, lon)
    if region is None or (jobs_dir(jarvis) / f"{region['id']}.want").is_file():
        return {"covered": False, "asked": None, "pending": region["id"] if region else None}
    if (jobs_dir(jarvis) / f"{region['id']}.removed").is_file():
        return {"covered": False, "asked": None, "pending": None, "removed": True}
    # T-1074: the buildings call asks through here for wherever a box is, so a client with the
    # key could name a box in every country: at most AUTO_PENDING self-fetched regions wait at once.
    packs = _data_dir(jarvis) / PACKS_DIR
    waiting = [a.stem for a in jobs_dir(jarvis).glob("*.auto") if not (packs / f"{a.stem}.pack").is_file()]
    if len(waiting) >= AUTO_PENDING:
        return {"covered": False, "asked": None, "pending": None, "busy": sorted(waiting)}
    _ask(jarvis, region["id"], "present")
    (jobs_dir(jarvis) / f"{region['id']}.auto").touch()
    return {"covered": False, "asked": {"id": region["id"], "name": region["name"]}}
