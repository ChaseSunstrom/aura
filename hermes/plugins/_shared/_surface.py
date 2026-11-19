"""The adapter's record of a session's latest prompt: one reader for every plugin that asks.

``GET /api/surface/current?session=…`` with the apps' bearer key answers which surface sent
the session's newest prompt, what its screen can show and which paired device it was. aura-hud
asks whether this is the phone's turn, aura-artifacts what the screen can show, smart-home which
device rings a timer. ``current`` reads one id; ``latest`` and ``turn_device`` map the durable
key a tool call carries to the live ids the adapter recorded, one mapping for all three (T-1579).

The request never follows a redirect and never goes through a proxy: urllib re-sends the
Authorization header to wherever a redirect points, and HTTP_PROXY in the gateway's environment
would receive the key with every read. The adapter is on this machine, reached directly or not
at all; a redirect is an error, so the read is None, as for an adapter that is down.

Not covered: the adapter URL itself is the owner's setting; a wrong one is trusted.
"""
from __future__ import annotations

import json
from typing import Any, Callable
from urllib import parse, request

from aura_schema import from_env

from . import _speaker

_DEFAULT_URL = "http://127.0.0.1:8643"


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = request.build_opener(_NoRedirect, request.ProxyHandler({}))


def _adapter() -> tuple[str, str]:
    settings = from_env()
    return str(settings.adapter_url or _DEFAULT_URL).rstrip("/"), str(settings.adapter_key or "")


def _get(url: str, key: str, timeout: float) -> Any:
    req = request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.load(resp)


def current(session: str | None, *, timeout: float = 3.0) -> dict | None:
    """The record for ``session`` (``{"surface": {...} | None, "device": …, …}``), or None when
    nobody can say: no session, no key, the adapter down, slow or redirecting, an answer that
    is no record."""
    if not session:
        return None
    base, key = _adapter()
    if not key:
        return None
    try:
        out = _get(f"{base}/api/surface/current?" + parse.urlencode({"session": session}), key, timeout)
    except (OSError, ValueError):  # URLError, HTTPError (a refused redirect too) and timeouts
        return None
    return out if isinstance(out, dict) else None


def latest(session: str | None, read: Callable[[str], Any] | None = None) -> dict | None:
    """The record of this conversation's newest prompt, asked under every id it goes by (T-1579).

    The adapter records the live sid the app sent on ``prompt.submit``; a tool call and a hook
    carry the durable key (``hud._state_key`` says why they differ), and asked by the key alone
    the adapter has no record, so the phone's turn read as the console's and a deploy probe as
    the owner's. ``_speaker._bound_ids`` is the gateway's own table from the key to each live
    sid on it; of their records, the one whose prompt came last (``at``) is the answer, and with
    no prompt on any, the record for the id as given (the adapter's empty one). ``read`` is the
    record reader (``current`` unless a caller passes its own, with its own timeout).

    Not covered: two screens on one key answer for the screen that spoke last, which may not be
    the one whose turn is running; ``turn_device(strict=True)`` is for a tool that acts on it."""
    if not session:
        return None
    reader = read or current
    records = [r for r in (reader(sid) for sid in _speaker._bound_ids(session)) if isinstance(r, dict)]
    timed = [r for r in records if isinstance(r.get("at"), (int, float))]
    if timed:
        return max(timed, key=lambda r: r["at"])
    return records[0] if records else None


def turn_device(session: str, read: Callable[[str], Any] | None = None, *, strict: bool = False) -> dict | None:
    """The paired device whose socket sent this conversation's newest prompt (``{"kind",
    "house_token", …}`` as the adapter recorded it, T-1498), or None for the console's turn, an
    unknown session or an adapter that cannot say.

    The adapter records the live id the app sent; a tool call carries the durable key, so it is
    asked under every id the conversation goes by (``_speaker._bound_ids``, the speaker gate's
    own mapping) and the newest prompt's device is the turn's. ``read`` is the record reader
    (``current`` unless a caller passes its own).

    ``strict`` is for a tool that acts on the device it names: the adapter records a prompt's
    device when it is submitted, before Hermes' per-session lock lets it run, so a prompt from a
    second screen on the same conversation, sent mid-turn, would be the newest and would redirect
    the running turn's actions to it. Strict, the answer is None unless every id that recorded a
    paired device recorded the same one (by house token); the console, which names none, cannot
    be acted on and does not count. Not covered: a device paired before its adapter key recorded
    the house token has none."""
    reader = read or current
    records = [reader(sid) for sid in (_speaker._bound_ids(session) if session else [])]
    timed = [r for r in records if isinstance(r, dict) and isinstance(r.get("device_at"), (int, float))]
    newest = max(timed, key=lambda r: r["device_at"]) if timed else None
    device = newest.get("device") if newest else None
    if not isinstance(device, dict):
        return None
    if strict:
        tokens = {r["device"].get("house_token") for r in timed if isinstance(r.get("device"), dict)}
        if tokens != {device.get("house_token")}:
            return None
    return device
