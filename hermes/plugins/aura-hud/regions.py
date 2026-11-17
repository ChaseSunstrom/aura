"""The map regions the house holds, for AURA (T-0979): list, download, remove, and a trip's own.

The house (jarvis-core ``/api/maps/regions``, ``/catalogue``, ``/cover``) keeps the list and
leaves the work to its maps-worker; these tools only ask it. A region is Geofabrik's, named
by an id like ``us-utah``, and a download is a few hundred MB of public map data fetched by
the house: Geofabrik hears the region's name and nothing about where anyone is.

``cover_trip`` is what ``car_navigate`` calls for the owner: a route that ends where no pack
covers asks the house for that region, so the map is there offline by the time it is
needed. ``maps.auto_fetch`` turns it off, and so does the privacy switch
(``privacy.quiet_house``).

Not covered: progress while a region downloads (``map_regions`` says its state), and whether
the phone has copied the pack yet (the phone's Maps settings do that on Wi-Fi).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from _shared import _house

_LOGGER = logging.getLogger(__name__)

#: tests/contracts/map-pack.json `id`.
REGION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TIMEOUT_S = 8.0

#: method, path, JSON body -> HTTP status, JSON answer.
House = Callable[..., tuple[int, Any]]

MAP_REGIONS = {
    "name": "map_regions",
    "description": (
        "The map regions the house holds for the car's offline map (a state, a country), with "
        "their state (queued, working, ready, failed, held, removing) and size. With `query`, also "
        "the regions that can be downloaded whose name matches (\"utah\", \"germany\"): pass an "
        "`id` from there to map_region_download."
    ),
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "A region's name to look up in the catalogue."}}, "required": []},
}

MAP_REGION_DOWNLOAD = {
    "name": "map_region_download",
    "description": (
        "Have the house download a map region so the car's map and turn-by-turn work there with "
        "no connection (\"download all of Utah\"). `id` comes from map_regions with a query. It "
        "takes minutes to an hour; the phone copies it on Wi-Fi afterwards."
    ),
    "parameters": {"type": "object", "properties": {
        "id": {"type": "string", "description": "The region's id from map_regions, e.g. us-utah."}}, "required": ["id"]},
}

MAP_REGION_REMOVE = {
    "name": "map_region_remove",
    "description": "Remove a downloaded map region from the house to free its disk space. `id` from map_regions.",
    "parameters": {"type": "object", "properties": {
        "id": {"type": "string", "description": "The region's id from map_regions."}}, "required": ["id"]},
}


def _ask_house(method: str, path: str, body: Optional[dict] = None) -> tuple[int, Any]:
    if not _house.configured():
        raise OSError("the house is not configured")
    with _house.client(timeout=TIMEOUT_S) as c:
        r = c.request(method, path, json=body)
    return r.status_code, _house.body_of(r)


def _size(n: Any) -> str:
    mb = int(n or 0) / 2**20
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _why(status: int, body: Any) -> str:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    return f"the house answered {status}"


def _asked(method: str, path: str, body: Optional[dict], house: House) -> tuple[Optional[Any], str]:
    """The house's answer, or why there is none."""
    try:
        status, answer = house(method, path, body)
    except Exception as exc:  # noqa: BLE001 - any fault here is "the house did not answer"
        _LOGGER.debug("the house's map regions did not answer: %s", exc)
        return None, "the house did not answer, so the map regions cannot be read or changed right now"
    if status != 200:
        return None, _why(status, answer)
    return answer, ""


def _row(r: dict) -> dict:
    row = {"id": str(r.get("id") or ""), "name": str(r.get("name") or ""), "state": str(r.get("state") or "")}
    if r.get("bytes"):
        row["size"] = _size(r["bytes"])
    if r.get("note"):
        row["note"] = str(r["note"])[:200]
    if r.get("auto"):  # T-1074: the house fetched it on its own, and drops the oldest such at its cap
        row["fetched_by"] = "the house, on its own; dropped first when the maps run out of room"
    return row


def handle_map_regions(args: dict, house: House = _ask_house, **kwargs: Any) -> str:
    del kwargs
    held, problem = _asked("GET", "/api/maps/regions", None, house)
    if held is None:
        return json.dumps({"ok": False, "error": problem})
    out: dict[str, Any] = {"ok": True, "regions": [_row(r) for r in held.get("regions") or [] if isinstance(r, dict)]}
    query = str(args.get("query") or "").strip()[:80]
    if query:
        found, problem = _asked("GET", "/api/maps/catalogue?" + urlencode({"q": query}), None, house)
        if found is None:
            out["note"] = problem
        elif not found.get("ready"):
            out["note"] = "the catalogue of regions has not arrived on the house yet; try again in a minute"
        else:
            out["available"] = [{"id": r["id"], "name": r["name"], "parent": r.get("parent") or ""}
                                for r in found.get("results") or [] if isinstance(r, dict) and r.get("id")]
    return json.dumps(out)


def _region(args: dict) -> Optional[str]:
    rid = args.get("id")
    return rid if isinstance(rid, str) and REGION_ID.fullmatch(rid) else None


def handle_map_region_download(args: dict, house: House = _ask_house, **kwargs: Any) -> str:
    del kwargs
    rid = _region(args)
    if rid is None:
        return json.dumps({"ok": False, "error": "id is a region's id from map_regions (with a query), e.g. us-utah"})
    answer, problem = _asked("POST", "/api/maps/regions", {"id": rid}, house)
    if answer is None:
        return json.dumps({"ok": False, "error": problem})
    return json.dumps({"ok": True, **_row(answer),
                       "note": "the house is fetching it; map_regions says when it is ready, and the phone copies it on Wi-Fi"})


def handle_map_region_remove(args: dict, house: House = _ask_house, **kwargs: Any) -> str:
    del kwargs
    rid = _region(args)
    if rid is None:
        return json.dumps({"ok": False, "error": "id is a region's id from map_regions"})
    answer, problem = _asked("DELETE", f"/api/maps/regions/{rid}", None, house)
    if answer is None:
        return json.dumps({"ok": False, "error": problem})
    return json.dumps({"ok": True, **_row(answer)})


def _auto_fetch() -> bool:
    try:
        from aura_schema import from_env
        settings = from_env()
        # The privacy switch: a download nobody asked for is a call out on the house's own.
        return bool(settings.maps_auto_fetch) and not settings.privacy_quiet_house
    except Exception:  # noqa: BLE001 - unreadable settings fetch nothing the owner did not ask for
        return False


def cover_trip(lat: float, lon: float, house: House = _ask_house, on: Optional[bool] = None) -> Optional[str]:
    """Ask the house to hold the region a route ends in; the region's name when it was just
    asked for, else None. Never raises: a route opens whether or not its map can be fetched."""
    if not (_auto_fetch() if on is None else on):
        return None
    answer, _problem = _asked("POST", "/api/maps/cover", {"lat": lat, "lon": lon}, house)
    asked = answer.get("asked") if isinstance(answer, dict) else None
    return str(asked.get("name") or asked.get("id")) if isinstance(asked, dict) else None


def after_navigate(result: str, guest: bool, house: House = _ask_house, on: Optional[bool] = None) -> str:
    """``car_navigate``'s reply; for the owner, a route that opened where no pack covers also
    asks the house for that region and says so. A guest's route fetches nothing."""
    if guest:
        return result
    try:
        out = json.loads(result)
        to = out.get("to") or {}
        if out.get("ok") and "lat" in to:
            name = cover_trip(float(to["lat"]), float(to["lon"]), house=house, on=on)
            if name:
                out["map"] = f"the house had no offline map there and is now downloading {name}; map_regions says when it is ready"
                return json.dumps(out)
    except Exception:  # noqa: BLE001 - a map download must never cost the route
        pass
    return result
