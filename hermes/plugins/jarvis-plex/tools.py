"""Plex HTTP client: libraries, recently added, what is playing, and search. Read-only.

Endpoints (Plex Media Server, asked for JSON with ``Accept: application/json``):

* ``GET /library/sections``      — the libraries
* ``GET /library/recentlyAdded`` — what arrived lately, newest first
* ``GET /status/sessions``       — what each client is playing right now
* ``GET /hubs/search?query=``    — search by name, grouped in hubs

Every request is a GET; there is no tool that plays, stops or changes anything. ``PLEX_URL`` and
``PLEX_TOKEN`` are read through the one settings schema (``aura_schema.from_env()``), and the
token travels only in the ``X-Plex-Token`` header: never in a URL (which would put it in the
server's access log), never in a log line, never in an answer. Redirects are not followed, since
a redirect would carry the header to a host nobody configured.

The answer shapes are written from Plex's documentation, not from a server this plugin has seen.
"""
from __future__ import annotations

import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from aura_schema import from_env

_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared import _egress, _failure  # noqa: E402

#: Rows a tool returns at most, whatever the model asks for or the server sends.
MAX_ROWS = 50
#: Characters of any one title, name or label that reaches the model.
CLIP = 120
#: Bytes of an answer read before it is refused: the size parameters bound it, this is the backstop.
MAX_BYTES = 2_000_000


class PlexError(ValueError):
    """A refusal or a bad answer whose text is safe to hand to the model."""


def check_url(raw: Any) -> str:
    """The server address, or a PlexError saying why this plugin will not talk to it.

    The rule is the stack's one service-address rule, ``_egress.service_url``; see it for what
    it does not cover.
    """
    try:
        return _egress.service_url(raw, "PLEX_URL")
    except _egress.ServiceUrlError as exc:
        raise PlexError(str(exc)) from exc


def _clip(value: Any, cap: int = CLIP) -> str:
    """One line of text with control characters gone and a length bound: a title is someone
    else's words, and it must neither flood the reply nor carry a terminal escape."""
    kept = "".join(c for c in str(value if value is not None else "")
                   if unicodedata.category(c)[0] != "C" or c in " \t\n")
    flat = " ".join(kept.split())
    return flat if len(flat) <= cap else flat[:cap].rstrip() + "…"


def _num(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):  # JSON may carry Infinity
        return None


def _int(value: Any, default: int, low: int, high: int) -> int:
    number = _num(value)
    return default if number is None else max(low, min(high, number))


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """One GET to the configured server; returns its ``MediaContainer`` (``{}`` if it has none)."""
    cfg = from_env()
    if not cfg.plex_url or not cfg.plex_token:
        raise PlexError("no Plex server is set: PLEX_URL and PLEX_TOKEN are not set")
    base = check_url(cfg.plex_url)
    headers = {"X-Plex-Token": cfg.plex_token, "Accept": "application/json"}
    body = bytearray()
    with httpx.Client(base_url=base, headers=headers, timeout=20.0, verify=True,
                      follow_redirects=False) as client:
        with client.stream("GET", path, params=params) as response:
            response.raise_for_status()  # a 3xx raises too: it is not followed
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise PlexError("Plex's answer is too large")
    try:
        data = json.loads(bytes(body))
    except ValueError as exc:
        raise PlexError("Plex's answer is not JSON") from exc
    box = data.get("MediaContainer") if isinstance(data, dict) else None
    return box if isinstance(box, dict) else {}


def item_row(item: dict[str, Any]) -> dict[str, Any]:
    """One title: what identifies it to a person. Fields appear only when the item has them."""
    row: dict[str, Any] = {"title": _clip(item.get("title")), "type": _clip(item.get("type"), 24)}
    if (year := _num(item.get("year"))) is not None:
        row["year"] = year
    # A show's episode sits under its season under the series; an album's track under the album
    # under the artist. One joined label covers both without naming either.
    context = " / ".join(_clip(item[k]) for k in ("grandparentTitle", "parentTitle") if item.get(k))
    if context:
        row["context"] = context
    for field, key in (("season", "parentIndex"), ("episode", "index")):
        if item.get("type") == "episode" and (n := _num(item.get(key))) is not None:
            row[field] = n
    if item.get("librarySectionTitle"):
        row["library"] = _clip(item["librarySectionTitle"])
    if (added := _num(item.get("addedAt"))) and added > 0:
        try:
            row["added"] = datetime.fromtimestamp(added, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            pass
    return row


def libraries() -> dict[str, Any]:
    box = _get("/library/sections")
    rows = [{"id": _clip(d.get("key"), 24), "title": _clip(d.get("title")),
             "type": _clip(d.get("type"), 24)} for d in _dicts(box.get("Directory"))[:MAX_ROWS]]
    return {"ok": True, "count": len(rows), "libraries": rows}


def recent(limit: int = 12) -> dict[str, Any]:
    """What was added lately across every library, newest first."""
    box = _get("/library/recentlyAdded", {"X-Plex-Container-Start": 0,
                                          "X-Plex-Container-Size": limit})
    rows = [item_row(m) for m in _dicts(box.get("Metadata"))[:limit]]
    return {"ok": True, "count": len(rows), "total": _num(box.get("totalSize")) or len(rows),
            "items": rows}


def now_playing() -> dict[str, Any]:
    """What each client is playing, who is watching and how far in.

    Does **not** return the player's network address, which the server includes.
    """
    box = _get("/status/sessions")
    rows = []
    for m in _dicts(box.get("Metadata"))[:MAX_ROWS]:
        row = item_row(m)
        user, player = m.get("User") or {}, m.get("Player") or {}
        if not isinstance(user, dict) or not isinstance(player, dict):
            user, player = {}, {}
        row.update({"user": _clip(user.get("title")), "device": _clip(player.get("title")),
                    "client": _clip(player.get("product")), "state": _clip(player.get("state"), 24)})
        for field, key in (("position_s", "viewOffset"), ("duration_s", "duration")):
            if (ms := _num(m.get(key))) is not None:
                row[field] = round(ms / 1000)
        rows.append(row)
    return {"ok": True, "count": len(rows), "sessions": rows}


def search(query: Any, limit: int = 20) -> dict[str, Any]:
    """Search the libraries by name. Does **not** cover subtitles or plot summaries."""
    query = _clip(query, 200)
    if not query:
        return {"ok": False, "error": "query is required"}
    box = _get("/hubs/search", {"query": query, "limit": limit})
    found = [m for hub in _dicts(box.get("Hub")) for m in _dicts(hub.get("Metadata"))]
    found += _dicts(box.get("Metadata"))
    rows = [item_row(m) for m in found[:limit]]
    return {"ok": True, "query": query, "count": len(rows), "items": rows}


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _answer(payload: dict[str, Any]) -> str:
    """The payload as the JSON text the model gets, with the token blanked wherever it is.

    The token is only ever sent as a header, so this is a backstop: a future change that put it
    in a query string, or a server that echoes it, would otherwise reach the model. It runs on
    the serialised text, so it needs no walk of the payload and misses no nesting.

    Does **not** cover: what httpx logs itself at DEBUG level.
    """
    text = json.dumps(payload)
    try:
        token = from_env().plex_token
    except Exception:  # a broken environment must not stop the answer from going out
        return text
    # Both spellings: the raw token, and the one json.dumps wrote if it had to escape it.
    return text.replace(json.dumps(token)[1:-1], "***").replace(token, "***") if token else text


def _failure_answer(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, PlexError):
        return {"ok": False, "error": str(exc)}
    # The shared status-and-hint answer (T-1348): no body, no request address.
    return _failure.of(exc) or {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def _safe(fn):
    def handler(params: dict, **kwargs):
        del kwargs
        try:
            return _answer(fn(params if isinstance(params, dict) else {}))
        # httpx.InvalidURL is deliberately NOT an HTTPError, so a mistyped PLEX_URL would
        # otherwise raise straight out of the handler instead of coming back as an answer.
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError, KeyError) as exc:
            return _answer(_failure_answer(exc))
    return handler


handle_libraries = _safe(lambda p: libraries())
handle_recent = _safe(lambda p: recent(_int(p.get("limit"), 12, 1, MAX_ROWS)))
handle_now_playing = _safe(lambda p: now_playing())
handle_search = _safe(lambda p: search(p.get("query"), _int(p.get("limit"), 20, 1, MAX_ROWS)))
