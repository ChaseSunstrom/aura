"""Live work zones from an open WZDx feed (T-1108; the owner, 2026-11-12: "traffic data,
construction, road barriers"). One GeoJSON document a state or a city publishes, fetched on a
timer by the house and cut to what is live in the region: roadworks along a road, or the road
closed.

Off unless ``maps.workzones_url`` is set. The fetch names nothing: it asks for the whole feed
and the house filters it, so the publisher learns only that this address reads its feed.

Does not cover: congestion or speeds (a feed of works, not of traffic), a zone the feed's
owner has not entered or left stale (its dates are believed), lanes (one closed lane and
three are both "works"), or detours and restrictions (left out: neither is works), or a feed in another shape (CWZ and
WZDx 4 share these fields; 3.x does not and reads as empty).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

#: Bigger is not a work-zone feed for one region; a national one is tens of megabytes.
MAX_BYTES = 20 * 1024 * 1024
MAX_ZONES = 5000
MAX_POINTS = 2000
_DEAD = frozenset({"planned", "pending", "completed", "cancelled", "canceled"})
#: Only works. A detour is an open road and a restriction (a width, a weight) is not works:
#: boards along either would say the road is being dug up.
_KINDS = frozenset({"work-zone"})

Box = tuple[float, float, float, float]


def _when(text: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _line(geometry: Any) -> list[list[float]] | None:
    """The zone's points as [[lon, lat], ...], or None when they are not a line on the earth."""
    if not isinstance(geometry, dict) or geometry.get("type") not in ("LineString", "MultiPoint"):
        return None
    pts = geometry.get("coordinates")
    if not isinstance(pts, list) or not 2 <= len(pts) <= MAX_POINTS:
        return None
    out = []
    for p in pts:
        if not (isinstance(p, list) and len(p) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p[:2])):
            return None
        lon, lat = float(p[0]), float(p[1])
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        out.append([round(lon, 6), round(lat, 6)])
    return out


def parse(doc: Any, box: Box) -> list[dict[str, Any]]:
    """The feed's zones that touch [box], whatever their dates: [{kind, line, start, end}].

    ``kind`` is ``closure`` when every lane is shut, else ``roadworks``. A feature that does
    not read (no line, no start, a status that says it is not happening) is left out; so is
    everything past MAX_ZONES.
    """
    out: list[dict[str, Any]] = []
    features = doc.get("features") if isinstance(doc, dict) else None
    for f in features if isinstance(features, list) else []:
        p = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict):
            continue
        core = p.get("core_details")
        if not isinstance(core, dict):
            continue
        if core.get("event_type") not in _KINDS or str(p.get("event_status") or "").lower() in _DEAD:
            continue
        line = _line(f.get("geometry"))
        start = _when(p.get("start_date"))
        if line is None or start is None:
            continue
        xs, ys = [q[0] for q in line], [q[1] for q in line]
        if min(xs) > box[2] or max(xs) < box[0] or min(ys) > box[3] or max(ys) < box[1]:
            continue
        end = _when(p.get("end_date"))
        out.append({
            "box": (min(xs), min(ys), max(xs), max(ys)),
            "kind": "closure" if p.get("vehicle_impact") == "all-lanes-closed" else "roadworks",
            "line": line,
            "start": start.timestamp(),
            "end": end.timestamp() if end else None,
        })
        if len(out) == MAX_ZONES:
            break
    return out


def live(zones: list[dict[str, Any]], box: Box, now: float) -> list[dict[str, Any]]:
    """Of [zones], those under way at [now] that touch [box], as the car reads them: {kind, line}."""
    out = []
    for z in zones:
        if z["start"] > now or (z["end"] is not None and z["end"] < now):
            continue
        b = z["box"]  # kept by parse: an ask is every few seconds, a zone up to MAX_POINTS long
        if b[0] <= box[2] and b[2] >= box[0] and b[1] <= box[3] and b[3] >= box[1]:
            out.append({"kind": z["kind"], "line": z["line"]})
    return out


def read(body: bytes, box: Box) -> list[dict[str, Any]]:
    """[parse] of a downloaded feed; ValueError when it is not JSON or is past MAX_BYTES."""
    if len(body) > MAX_BYTES:
        raise ValueError("feed too large")
    try:
        return parse(json.loads(body), box)
    except RecursionError as err:  # nested past the parser's depth: not a feed
        raise ValueError("feed too deep") from err
