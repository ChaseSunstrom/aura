#!/usr/bin/env python3
"""Carry out the house's asks for map regions (T-0979).

    map_worker.py [--once]        MAPS_DATA_DIR (default aura-core/maps-data), MAPS_JOBS_DIR
                                  (default <data>/jobs), MAPS_MAX_GB (default 20, all the maps),
                                  MAPS_REGION_MAX_GB (default 1.25, one extract), MAPS_ROUTE_MAX_GB (default 0.6, one routed)

jarvis-core reads its map data and never fetches or builds it. It leaves an ask,
``<jobs>/<id>.want`` holding ``present`` or ``absent``; this loop (the
``maps-worker`` service in aura-core/docker-compose.yml) looks every 30 s and:

* keeps Geofabrik's catalogue (``geofabrik-index.json``) at most a week old (with
  ``AURA_QUIET_HOUSE`` on it is fetched once and never refreshed);
* ``present``: downloads the region's extract to ``regions/<id>.osm.pbf``, reads
  its footprints, roads and places out, builds ``packs/<id>.pack`` (the house's
  ``maps/pack.py``), and gives the extract to Valhalla (``valhalla/<id>.osm.pbf``
  and ``valhalla/.rebuild``, which the valhalla service restarts on);
* ``absent``: removes all of that, and the ask;
* once a week looks at each ready region's extract upstream (a HEAD: its ETag and
  Last-Modified, which the download left in the status file) and rebuilds one that
  changed, one region per look;
* says where it is in ``regions/<id>.json`` (``state``: working, ready, failed, held).

With ``AURA_QUIET_HOUSE`` on, nothing is called out for but the first catalogue: a
``present`` ask is ``held`` (the status says why; it runs when the switch is off), no
extract is fetched, pyosmium is not pip-installed and no region is looked at upstream.
An ``absent`` ask is local work and still runs.

Only ``https://download.geofabrik.de/`` is fetched, by the URL the catalogue
names for the id. Geofabrik hears a region's name, never where anyone is.

Not covered: Overture footprints (OSM's only, outside the home region); a failed
ask is tried again after an hour, for ever; Valhalla is down while it rebuilds
(minutes per state); two workers on one data directory.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
INDEX_URL = "https://download.geofabrik.de/index-v1.json"
INDEX_FILE = "geofabrik-index.json"
PBF_PREFIX = "https://download.geofabrik.de/"
INDEX_DAYS = 7
REFRESH_S = 7 * 86400  # how often a ready region's extract is looked at upstream
RETRY_S = 3600
#: A cut download is resumed only this soon after: Geofabrik replaces its extracts daily, and
#: yesterday's head with today's tail is a corrupt file that then fails every hour.
PART_FRESH_S = 6 * 3600
EVERY_S = 30
#: One extract's cap. The three readers keep every node's position in memory (pyosmium's
#: default index): measured at 4.5 times the extract's size (581 MB for a 128 MB one), so the
#: default is what the worker's 6 GB holds, any US state. Past it the worker is killed mid-read.
# T-1270: past osm_nodes.THRESHOLD the readers keep the node index in a file, so the node index
# no longer sets this cap; the area pass's multipolygon ways still do, and a country-sized extract
# has not been read. ponytail: raise the cap and mem_limit together once one has.
REGION_MAX = int(float(os.environ.get("MAPS_REGION_MAX_GB") or 1.25) * 2**30)
ROUTE_MAX = int(float(os.environ.get("MAPS_ROUTE_MAX_GB") or 0.6) * 2**30)
HOME = "home"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_spec = importlib.util.spec_from_file_location(
    "map_pack", HERE.parent / "aura-core" / "aura" / "integrations" / "maps" / "pack.py")
assert _spec and _spec.loader
pack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pack)

Box = tuple[float, float, float, float]


def _validators(headers: Any) -> dict[str, str]:
    """What Geofabrik says identifies this version of an extract (capped: it is outside text)."""
    return {"etag": str(headers.get("ETag") or "")[:200], "modified": str(headers.get("Last-Modified") or "")[:200]}


def _changed(old: dict[str, str], new: dict[str, str]) -> bool:
    """True when a validator both sides have differs; one side missing is no verdict."""
    return any(old.get(k) and new.get(k) and old[k] != new[k] for k in ("etag", "modified"))


def _head(url: str) -> dict[str, str]:
    """The ETag and Last-Modified of [url], without its body (Geofabrik's host only, as _fetch)."""
    if not url.startswith(PBF_PREFIX):
        raise ValueError("only Geofabrik's downloads are fetched")
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "map-worker"})
    try:
        r = urllib.request.urlopen(req, timeout=60)  # noqa: S310 (https only, checked above)
    except urllib.error.HTTPError as err:
        raise ValueError(f"Geofabrik answered {err.code}") from err
    with r:
        if not r.geturl().startswith(PBF_PREFIX):
            raise ValueError("only Geofabrik's downloads are fetched")
        return _validators(r.headers)


def _fetch(url: str, dest: Path, max_bytes: int) -> dict[str, str]:
    """Download [url] to [dest] through ``.part``, resuming one a cut connection left; says what it got."""
    if not url.startswith(PBF_PREFIX):
        raise ValueError("only Geofabrik's downloads are fetched")
    part = dest.with_name(dest.name + ".part")
    if part.exists() and time.time() - part.stat().st_mtime > PART_FRESH_S:
        part.unlink()
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "map-worker", **({"Range": f"bytes={have}-"} if have else {})})
    try:
        r = urllib.request.urlopen(req, timeout=60)  # noqa: S310 (https only, checked above)
    except urllib.error.HTTPError as err:
        if have:  # 416: the part is the whole file, or not this file's; start clean next time
            part.unlink()
        raise ValueError(f"Geofabrik answered {err.code}") from err
    with r:
        if not r.geturl().startswith(PBF_PREFIX):  # a redirect is followed; one off the host is not read
            raise ValueError("only Geofabrik's downloads are fetched")
        resumed = r.status == 206
        total = int(r.headers.get("Content-Length") or 0) + (have if resumed else 0)
        if total > max_bytes:
            raise ValueError(f"the extract is {total / 2**30:.1f} GB, over the {max_bytes / 2**30:.1f} GB this house's maps may take (docs/runbooks/maps.md)")
        with part.open("ab" if resumed else "wb") as fh:
            got = have if resumed else 0
            while chunk := r.read(1 << 20):
                got += len(chunk)
                if got > max_bytes:
                    raise ValueError("the extract is larger than it said, over what this house's maps may take")
                fh.write(chunk)
        got_validators = _validators(r.headers)
    os.replace(part, dest)
    return got_validators


def _osmium(data: Path) -> None:
    """Make pyosmium importable, installing it beside the data once (the image has none)."""
    lib = data / "tools" / f"py{sys.version_info.major}{sys.version_info.minor}"
    sys.path.insert(0, str(lib))
    try:
        import osmium  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(lib), "osmium"], check=True)
        importlib.invalidate_caches()


def _extract(pbf: Path, out_dir: Path, bbox: Box) -> dict[str, Path]:
    """The extract's footprints, roads and places as the lists pack.build reads."""
    _osmium(pbf.parent.parent)
    sys.path.insert(0, str(HERE))
    out = {}
    # ponytail: four passes over the extract, one per script; one handler if a country is slow
    for kind, module in (("osm", "osm_buildings"), ("roads", "osm_roads"), ("places", "osm_places"),
                         ("detail", "osm_detail")):
        out[kind] = out_dir / f"{kind}.geojsonl"
        importlib.import_module(module).main(str(pbf), str(out[kind]), *bbox)
    return out


def _index(data: Path) -> dict[str, dict[str, Any]]:
    """id -> name, pbf and bbox, for the regions the catalogue file names."""
    out: dict[str, dict[str, Any]] = {}
    try:
        features = json.loads((data / INDEX_FILE).read_text(encoding="utf-8"))["features"]
    except (OSError, ValueError, KeyError, TypeError):
        return out
    for f in features:
        try:
            p, g = f["properties"], f["geometry"]
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            pts = [pt for poly in polys for pt in poly[0]]
            box = (min(float(q[0]) for q in pts), min(float(q[1]) for q in pts),
                   max(float(q[0]) for q in pts), max(float(q[1]) for q in pts))
            out[str(p["id"]).replace("/", "-")] = {"name": str(p.get("name") or p["id"]), "pbf": str(p["urls"]["pbf"]), "bbox": box}
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return out


def _say(data: Path, region: str, state: str, note: str, now: float, ask: int = 0, **more: Any) -> None:
    path = data / "regions" / f"{region}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({**more, "state": state, "note": note, "at": int(now), "ask": ask}), encoding="utf-8")
    os.replace(tmp, path)


def _said(data: Path, region: str) -> dict[str, Any]:
    try:
        said = json.loads((data / "regions" / f"{region}.json").read_text(encoding="utf-8"))
        return said if isinstance(said, dict) else {}
    except (OSError, ValueError):
        return {}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _to_valhalla(data: Path, region: str, pbf: Path, route_max: int) -> None:
    """Give Valhalla the extract, unless it has the same one already (the home region's state)."""
    valhalla = data / "valhalla"
    if not valhalla.is_dir():
        return  # no routing on this house
    if pbf.stat().st_size > route_max:
        # Valhalla builds in memory: past its container's limit the build is killed, the
        # wrapper restarts it, and routing is down for every region, home included.
        print(f"{region}: too large to route here ({pbf.stat().st_size / 2**30:.1f} GB, MAPS_ROUTE_MAX_GB); the map is packed without it")
        return
    dest = valhalla / f"{region}.osm.pbf"
    same = [p for p in valhalla.glob("*.pbf") if p != dest and p.stat().st_size == pbf.stat().st_size and _sha(p) == _sha(pbf)]
    if same:
        return
    if dest.exists():
        dest.unlink()
    try:
        os.link(pbf, dest)
    except OSError:
        shutil.copyfile(pbf, dest)
    (valhalla / ".rebuild").touch()


def _present(data: Path, jobs: Path, region: str, fetch: Callable, extract: Callable, max_bytes: int, now: float,
             region_max: int, route_max: int) -> None:
    entry = _index(data).get(region)
    if entry is None:
        raise ValueError("no such region in the catalogue")
    if not entry["pbf"].startswith(PBF_PREFIX):
        raise ValueError("only Geofabrik's downloads are fetched")
    pbf = data / "regions" / f"{region}.osm.pbf"
    pbf.parent.mkdir(parents=True, exist_ok=True)
    # MAPS_MAX_GB is everything the maps hold, not one extract: with trips fetching their own
    # regions, a per-file cap filled the disk one state at a time.
    def room_left() -> int:
        return max_bytes - sum(f.stat().st_size for d in ("regions", "packs", "valhalla") for f in (data / d).rglob("*")
                               if f.is_file() and not f.name.startswith(f"{region}."))

    room = room_left()
    # T-1074: the regions the house fetched on its own (a `.auto` beside the ask) make room,
    # oldest pack first, so driving on never ends in "remove a region first"; home and what the
    # owner asked for by name are never dropped. ponytail: oldest built, not least used: the
    # house's map mount is read-only, so nothing records a read.
    autos = sorted((p for a in jobs.glob("*.auto") if a.stem != region and _ID.fullmatch(a.stem)
                    and (p := data / "packs" / f"{a.stem}.pack").is_file()), key=lambda p: p.stat().st_mtime)
    for old in autos:
        if room >= min(region_max, max_bytes):
            break
        _absent(data, old.stem)
        for left in (jobs / f"{old.stem}.want", jobs / f"{old.stem}.auto"):
            left.unlink(missing_ok=True)
        print(f"map-worker: dropped {old.stem} to make room", file=sys.stderr)
        room = room_left()
    if room <= 0:
        raise ValueError(f"the maps already hold {(max_bytes - room) / 2**30:.1f} GB, MAPS_MAX_GB is {max_bytes / 2**30:.0f}: remove a region first")
    _say(data, region, "working", "downloading the extract", now)
    upstream = fetch(entry["pbf"], pbf, min(room, region_max)) or {}
    _say(data, region, "working", "reading the extract", now)
    with tempfile.TemporaryDirectory(dir=pbf.parent, prefix=f".{region}-") as tmp:
        lists = extract(pbf, Path(tmp), entry["bbox"])
        _say(data, region, "working", "packing", now)
        (data / "packs").mkdir(exist_ok=True)
        pack.build(data / "packs" / f"{region}.pack", entry["name"], entry["bbox"], **lists)
    _to_valhalla(data, region, pbf, route_max)
    # etag/modified: what the weekly look compares (tick); checked: when it last looked.
    _say(data, region, "ready", "", now, etag=upstream.get("etag", ""), modified=upstream.get("modified", ""), checked=int(now))


def _absent(data: Path, region: str) -> None:
    routed = data / "valhalla" / f"{region}.osm.pbf"
    if routed.exists():
        routed.unlink()
        # Valhalla rebuilds only for an extract it has not seen: forget what it saw.
        hashes = data / "valhalla" / "file_hashes.txt"
        if hashes.exists():
            hashes.unlink()
        (data / "valhalla" / ".rebuild").touch()
    for path in (data / "packs" / f"{region}.pack", data / "packs" / f"{region}.pack.part",
                 data / "regions" / f"{region}.osm.pbf", data / "regions" / f"{region}.osm.pbf.part",
                 data / "regions" / f"{region}.json"):
        if path.exists():
            path.unlink()


def _refresh(data: Path, jobs: Path, head: Callable, build: Callable[[str], None], now: float) -> None:
    """Look upstream at each ready region a week after its last look; rebuild the first that changed.

    One rebuild per look so a long one does not hold every new ask for a week's worth of regions.
    A failed look or rebuild leaves the pack as it is (still ready) and is tried again in an hour.
    Not covered: an extract replaced upstream between the look and the download (the download's own
    validators are kept, so the next look compares with what was really built); a region built
    before validators were kept (its first look only learns them)."""
    index = _index(data)
    for want in sorted(jobs.glob("*.want")):
        region = want.stem
        said = _said(data, region)
        if (not _ID.fullmatch(region) or region == HOME or region not in index or said.get("state") != "ready"
                or not (data / "packs" / f"{region}.pack").is_file() or not index[region]["pbf"].startswith(PBF_PREFIX)
                or _read_want(want) != "present"):
            continue
        if now - float(said.get("checked") or said.get("at") or 0) < REFRESH_S:
            continue
        old = {"etag": str(said.get("etag") or ""), "modified": str(said.get("modified") or "")}

        def settle(validators: dict[str, str], checked: float, region: str = region, at: float = float(said.get("at") or now)) -> None:
            _say(data, region, "ready", "", at, **validators, checked=int(checked))

        retry = now - REFRESH_S + RETRY_S  # looks again in an hour, not at every 30 s
        try:
            new = head(index[region]["pbf"])
        except (OSError, ValueError) as err:
            print(f"map-worker: {region} was not looked at upstream: {err}", file=sys.stderr)
            settle(old, retry)
            continue
        if not any(old.values()) or not _changed(old, new):
            settle(old if any(old.values()) else new, now)
            continue
        try:
            build(region)
        except Exception as err:  # noqa: BLE001 (the pack it has still serves; said here, tried again)
            print(f"map-worker: {region} was not refreshed: {err}", file=sys.stderr)
            settle(old, retry)
        return


def _read_want(want: Path) -> str:
    try:
        return want.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def tick(data: Path, jobs: Path, *, fetch: Callable = _fetch, extract: Callable = _extract, head: Callable = _head,
         now: Callable[[], float] = time.time, max_bytes: int = 20 * 2**30, quiet: bool = False,
         region_max: int = REGION_MAX, route_max: int = ROUTE_MAX) -> None:
    """One look at the asks. Each ask fails alone, into its status file."""
    index = data / INDEX_FILE
    # The privacy switch (privacy.quiet_house): no call out on the worker's own, so a catalogue
    # it has is not refreshed. The first one is still fetched: without it nothing can be asked for.
    if not index.exists() or (not quiet and now() - index.stat().st_mtime > INDEX_DAYS * 86400):
        try:
            fetch(INDEX_URL, index, 64 * 2**20)
            os.utime(index, (now(), now()))
        except (OSError, ValueError) as err:
            print(f"map-worker: the catalogue was not fetched: {err}", file=sys.stderr)
    for want in sorted(jobs.glob("*.want")):
        region = want.stem
        if not _ID.fullmatch(region):
            continue  # not an ask the house writes
        try:
            wanted = want.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if region == HOME:
            want.unlink()  # map-region.sh owns the home pack
        elif wanted == "absent":
            _absent(data, region)
            want.unlink()
            (jobs / f"{region}.auto").unlink(missing_ok=True)
        elif wanted == "present":
            said = _said(data, region)
            done = said.get("state") == "ready" and (data / "packs" / f"{region}.pack").is_file()
            # A failure waits an hour, unless the ask was written again since (the owner
            # asked once more): the house rewrites the file, so its mtime is the ask's name.
            ask = want.stat().st_mtime_ns
            if done or (said.get("state") == "failed" and said.get("ask") == ask
                        and now() - float(said.get("at") or 0) < RETRY_S):
                continue
            if quiet:  # no call out, no pip install: the ask waits for the switch, and says so once
                if said.get("state") != "held":
                    _say(data, region, "held", "the quiet house is on: nothing is fetched until it is off", now(), ask)
                continue
            try:
                _present(data, jobs, region, fetch, extract, max_bytes, now(), region_max, route_max)
            except Exception as err:  # noqa: BLE001 (any fault is this ask's, said in its status)
                _say(data, region, "failed", str(err)[:200] or err.__class__.__name__, now(), ask)
    if not quiet:
        _refresh(data, jobs, head, lambda region: _present(data, jobs, region, fetch, extract, max_bytes, now(), region_max, route_max), now())


def orphans(data: Path, jobs: Path, now: float) -> None:
    """A region still "working" when the worker starts was being built by a worker that died
    (killed for memory, a reboot). Left as it was, every start downloaded and died again; as a
    failure it waits the hour, or for the owner to ask once more."""
    for want in jobs.glob("*.want"):
        if _said(data, want.stem).get("state") == "working":
            _say(data, want.stem, "failed", "the map worker stopped while building this (too large for its memory?)",
                 now, want.stat().st_mtime_ns)


def main(argv: list[str]) -> int:
    data = Path(os.environ.get("MAPS_DATA_DIR") or HERE.parent / "aura-core" / "maps-data")
    jobs = Path(os.environ.get("MAPS_JOBS_DIR") or data / "jobs")
    max_bytes = int(float(os.environ.get("MAPS_MAX_GB") or 20) * 2**30)
    # Anything that is not a plain no keeps it quiet: a typo must not fail open.
    quiet = (os.environ.get("AURA_QUIET_HOUSE") or "").strip().lower() not in ("", "0", "false", "f", "no", "n", "off")
    jobs.mkdir(parents=True, exist_ok=True)
    orphans(data, jobs, time.time())
    while True:
        tick(data, jobs, max_bytes=max_bytes, quiet=quiet)
        if "--once" in argv:
            return 0
        time.sleep(EVERY_S)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
