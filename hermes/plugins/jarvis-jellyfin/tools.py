"""Jellyfin HTTP client: libraries, search, resume, sessions and remote playback.

Endpoints (Jellyfin 10.9+ names; the ``/Users/{id}/…`` spellings they replaced
are deprecated but still answer, so an older server works too):

* ``GET  /Users``                    — the users, for the id fallback
* ``GET  /UserViews?userId=``        — the media libraries a user can see
* ``GET  /Items?userId=&searchTerm=``— search, recursive, optionally by type
* ``GET  /UserItems/Resume?userId=`` — continue watching
* ``GET  /Sessions``                 — every client the server can see
* ``POST /Sessions/{id}/Playing``    — tell one client to play something

Every ``JELLYFIN_*`` value is read through the one settings schema
(``aura_schema.from_env()``), and the API key travels only in request headers —
never in a URL, which would put it in the server's access log.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

import httpx
from aura_schema import from_env

from _shared import _egress, _failure  # the plugin's __init__ has put the plugins root on sys.path

#: Jellyfin measures time in 100-nanosecond ticks, everywhere.
TICKS_PER_SECOND = 10_000_000
#: Tools whose call changes the world, and so pass through the approval gate.
GATED_TOOLS = {"jellyfin_play"}
#: What ``item_types`` accepts, spelled the way the server spells them.
ITEM_TYPES = ("Movie", "Series", "Episode", "Audio", "MusicAlbum", "BoxSet", "Video")


def base_url() -> str:
    return from_env().jellyfin_url.rstrip("/")


def _client(timeout: float = 20.0) -> httpx.Client:
    cfg = from_env()
    token = cfg.jellyfin_token
    headers = {
        # Jellyfin accepts an API key two ways, and which one arrives depends on
        # the deployment: 10.9+ reads `Authorization: MediaBrowser Token="…"`,
        # while older servers only look at `X-Emby-Token` — and a reverse proxy
        # that strips or rewrites Authorization (a common auth-forwarding setup)
        # takes the first one away without saying so. Sending both costs one
        # header and turns an unexplainable 401 into a working call.
        "Authorization": f'MediaBrowser Token="{token}"',
        "X-Emby-Token": token,
        "Accept": "application/json",
    }
    return httpx.Client(base_url=_egress.service_url(cfg.jellyfin_url, "JELLYFIN_URL"),
                        headers=headers, timeout=timeout, verify=cfg.jellyfin_verify_ssl)


def user_id(client: httpx.Client) -> str:
    """The configured Jellyfin user, or the first one the server lists.

    The fallback is a convenience for a single-user household. It does **not**
    pick "the owner": on a multi-user server ``/Users`` returns whatever order
    the server likes, so the libraries and the resume list may belong to a
    housemate. Set ``JELLYFIN_USER_ID`` to pin it.
    """
    configured = from_env().jellyfin_user_id.strip()
    if configured:
        return configured
    users = client.get("/Users").raise_for_status().json()
    if not users:
        raise ValueError("Jellyfin lists no users; set JELLYFIN_USER_ID")
    return str(users[0].get("Id") or "")


def _seconds(ticks: Any) -> int | None:
    try:
        value = int(ticks)
    except (TypeError, ValueError, OverflowError):  # JSON may carry Infinity
        return None
    return round(value / TICKS_PER_SECOND) if value > 0 else 0


def item_row(item: dict[str, Any]) -> dict[str, Any]:
    """One search/resume row: what identifies a title to a person and to a tool.

    Series and season/episode numbers are only present when the item has them,
    so a row stays short; ``id`` is what ``jellyfin_play`` needs.
    """
    row: dict[str, Any] = {"id": item.get("Id"), "name": item.get("Name"),
                           "type": item.get("Type"), "year": item.get("ProductionYear")}
    if item.get("SeriesName"):
        row["series"] = item["SeriesName"]
    if item.get("ParentIndexNumber") is not None:
        row["season"] = item["ParentIndexNumber"]
    if item.get("IndexNumber") is not None:
        row["episode"] = item["IndexNumber"]
    if item.get("Album"):
        row["album"] = item["Album"]
    if item.get("AlbumArtist"):
        row["artist"] = item["AlbumArtist"]
    runtime = _seconds(item.get("RunTimeTicks"))
    if runtime:
        row["runtime_s"] = runtime
    return row


def libraries() -> dict[str, Any]:
    with _client() as client:
        uid = user_id(client)
        data = client.get("/UserViews", params={"userId": uid}).raise_for_status().json()
    rows = [{"id": i.get("Id"), "name": i.get("Name"),
             "collection_type": i.get("CollectionType"), "type": i.get("Type")}
            for i in data.get("Items", [])]
    return {"ok": True, "user_id": uid, "count": len(rows), "libraries": rows}


def search(query: str | None, item_types: Iterable[str] | None = None,
           limit: int = 20) -> dict[str, Any]:
    """Search the libraries by name.

    Does **not** cover: full-text search of subtitles or descriptions — Jellyfin's
    ``searchTerm`` matches names (and, for episodes, the series name).
    """
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "error": "query is required"}
    params: dict[str, Any] = {
        "searchTerm": query, "recursive": "true", "limit": int(limit),
        # Without these fields the rows come back without a year or a series name,
        # and two identically named films become indistinguishable.
        "fields": "ProductionYear,SeriesName",
    }
    types = [t for t in (item_types or []) if str(t).strip()]
    if types:
        params["includeItemTypes"] = ",".join(str(t).strip() for t in types)
    with _client() as client:
        params["userId"] = user_id(client)
        data = client.get("/Items", params=params).raise_for_status().json()
    rows = [item_row(i) for i in data.get("Items", [])]
    return {"ok": True, "query": query, "count": len(rows),
            "total": data.get("TotalRecordCount", len(rows)), "items": rows}


def resume(limit: int = 12) -> dict[str, Any]:
    """What the user can continue watching, with how far in they are."""
    with _client() as client:
        uid = user_id(client)
        data = client.get("/UserItems/Resume", params={
            "userId": uid, "limit": int(limit),
            "fields": "ProductionYear,SeriesName"}).raise_for_status().json()
    rows = []
    for item in data.get("Items", []):
        row = item_row(item)
        user_data = item.get("UserData") or {}
        row["position_s"] = _seconds(user_data.get("PlaybackPositionTicks"))
        percent = user_data.get("PlayedPercentage")
        if percent is not None:
            row["percent"] = round(float(percent), 1)
        rows.append(row)
    return {"ok": True, "user_id": uid, "count": len(rows), "items": rows}


def session_row(session: dict[str, Any]) -> dict[str, Any]:
    """One session: the device, who is on it, and what it is playing.

    Idle sessions are reported too — a device has to have a session before
    ``jellyfin_play`` can target it, so hiding the idle ones would hide exactly
    the devices somebody wants to start something on.
    """
    playing = session.get("NowPlayingItem") or {}
    state = session.get("PlayState") or {}
    row: dict[str, Any] = {
        "session_id": session.get("Id"),
        "device": session.get("DeviceName"),
        "client": session.get("Client"),
        "user": session.get("UserName"),
        "remote_control": bool(session.get("SupportsRemoteControl")),
        "playing": bool(playing),
    }
    if playing:
        row["item"] = item_row(playing)
        row["position_s"] = _seconds(state.get("PositionTicks"))
        row["paused"] = bool(state.get("IsPaused"))
    return row


def sessions() -> dict[str, Any]:
    with _client() as client:
        data = client.get("/Sessions").raise_for_status().json()
    rows = [session_row(s) for s in data or []]
    return {"ok": True, "count": len(rows), "active": sum(1 for r in rows if r["playing"]),
            "sessions": rows}


def match_session(rows: list[dict[str, Any]], device: str) -> dict[str, Any] | None:
    """The session a device name refers to, or None.

    Matched against the session id, the device name, the client name and the
    user name, exactly first and then case-insensitively by substring, because
    people say "the telly" for a device Jellyfin calls "Living Room TV".
    Sessions that cannot be remote-controlled are considered last: a
    ``jellyfin_play`` sent to one is accepted by the server and does nothing.

    Does **not** cover: two devices with the same name — the first wins.
    """
    device = str(device or "").strip()
    if not device:
        return None
    ordered = sorted(rows, key=lambda r: not r.get("remote_control"))
    for field in ("session_id", "device", "client", "user"):
        for row in ordered:
            if str(row.get(field) or "") == device:
                return row
    needle = device.casefold()
    for field in ("device", "client", "user"):
        for row in ordered:
            if needle in str(row.get(field) or "").casefold():
                return row
    return None


def play(item_id: str | None, device: str | None, mode: str = "PlayNow",
         start_position_s: int | None = None) -> dict[str, Any]:
    """Start playback of one item on one device.

    Does **not** cover: whether anything is actually audible in that room. The
    server returns 204 as soon as it has handed the command to the client; a
    client that is asleep or has lost the socket fails silently afterwards.
    """
    item_id = str(item_id or "").strip()
    device = str(device or "").strip()
    if not item_id or not device:
        return {"ok": False, "error": "item_id and device are both required"}
    with _client() as client:
        rows = [session_row(s) for s in (client.get("/Sessions").raise_for_status().json() or [])]
        target = match_session(rows, device)
        if target is None:
            return {"ok": False, "error": f"no Jellyfin session matches {device!r}",
                    "devices": [r["device"] for r in rows]}
        params: dict[str, Any] = {"playCommand": mode, "itemIds": item_id}
        if start_position_s:
            params["startPositionTicks"] = int(start_position_s) * TICKS_PER_SECOND
        response = client.post(f"/Sessions/{target['session_id']}/Playing", params=params)
        response.raise_for_status()
    return {"ok": True, "item_id": item_id, "device": target.get("device"),
            "client": target.get("client"), "session_id": target.get("session_id"),
            "mode": mode, "http": response.status_code}


def approval_for(tool_name: str, args: dict) -> dict[str, Any] | None:
    """The ``pre_tool_call`` verdict: approve ``jellyfin_play``, pass the rest through.

    Playback starts in a room somebody may be sitting in, so the message names
    the item and the device. The item is named by whatever the caller passed —
    an opaque id if that is all it has — because resolving it here would mean an
    HTTP call inside the approval prompt, and a Jellyfin that is down must not
    stop the owner being asked.
    """
    if tool_name not in GATED_TOOLS:
        return None
    args = args if isinstance(args, dict) else {}
    item = str(args.get("item_name") or args.get("item_id") or "?").strip() or "?"
    device = str(args.get("device") or "?").strip() or "?"
    return {
        "action": "approve",
        "message": f"jellyfin_play will start {item!r} on {device!r} — it will begin playing there",
        "rule_key": f"jellyfin:play:{device}",
    }


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _scrub(value: Any) -> Any:
    """Blank the API key anywhere in a payload before it leaves.

    The key is only ever sent as a header, so this is a backstop rather than the
    first line of defence: httpx puts the failing request in
    ``HTTPStatusError``, and a future change that put the token in a query
    string would otherwise leak it into an error message unnoticed.

    Does **not** cover: what httpx logs itself at DEBUG level.
    """
    try:
        token = from_env().jellyfin_token
    except Exception:  # a broken environment must not stop the scrub from running
        return value
    if not token:
        return value
    if isinstance(value, str):
        return value.replace(token, "***")
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _safe(fn):
    def handler(params: dict, **kwargs):
        del kwargs
        try:
            return json.dumps(_scrub(fn(params or {})))
        # httpx.InvalidURL is deliberately NOT an HTTPError, so a mistyped
        # JELLYFIN_URL ("…:800O" with a letter O) would otherwise raise straight
        # out of the handler instead of coming back as an answer. A server failure is the
        # shared status-and-hint answer (T-1348): never the body, never the request address.
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError, KeyError) as exc:
            return json.dumps(_scrub(_failure.of(exc) or {
                "ok": False, "error": f"{exc.__class__.__name__}: {exc}"}))
    return handler


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def _types(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(i).strip() for i in items if str(i).strip()]


def _search_params(p: dict) -> dict[str, Any]:
    return search(p.get("query"), _types(p.get("item_types")), _int(p.get("limit"), 20, 1, 100))


def _play_params(p: dict) -> dict[str, Any]:
    return play(p.get("item_id"), p.get("device"), str(p.get("mode") or "PlayNow"),
                _int(p.get("start_position_s"), 0, 0, 24 * 60 * 60) or None)


handle_libraries = _safe(lambda p: libraries())
handle_search = _safe(_search_params)
handle_resume = _safe(lambda p: resume(_int(p.get("limit"), 12, 1, 50)))
handle_sessions = _safe(lambda p: sessions())
handle_play = _safe(_play_params)


def command(raw: str = "") -> str:
    """``/jellyfin`` — the libraries, and whatever is playing right now.

    Never raises: the gateway's command dispatcher runs this inline.
    """
    del raw
    libs = json.loads(handle_libraries({}))
    if not libs.get("ok"):
        return f"jellyfin error: {libs.get('error')}"
    lines = ["Libraries: " + (", ".join(str(row["name"]) for row in libs["libraries"]) or "none")]
    live = json.loads(handle_sessions({}))
    if not live.get("ok"):
        return "\n".join(lines + [f"sessions unavailable: {live.get('error')}"])
    playing = [row for row in live["sessions"] if row["playing"]]
    if not playing:
        return "\n".join(lines + [f"No active playback ({live['count']} session(s) connected)."])
    for row in playing:
        item = row.get("item") or {}
        where = row.get("device") or row.get("client") or "unknown device"
        state = "paused" if row.get("paused") else "playing"
        lines.append(f"  {where}: {state} {item.get('name') or 'something'}")
    return "\n".join(lines)
