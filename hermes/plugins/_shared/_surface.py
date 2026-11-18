"""The adapter's record of a session's latest prompt: one reader for every plugin that asks.

``GET /api/surface/current?session=…`` with the apps' bearer key answers which surface sent
the session's newest prompt, what its screen can show and which paired device it was. aura-hud
asks whether this is the phone's turn, aura-artifacts what the screen can show, smart-home which
device rings a timer. Each keeps its own mapping from the id a tool call carries to the ids the
adapter recorded (the reasons live beside each); this module only reads one id.

The request never follows a redirect and never goes through a proxy: urllib re-sends the
Authorization header to wherever a redirect points, and HTTP_PROXY in the gateway's environment
would receive the key with every read. The adapter is on this machine, reached directly or not
at all; a redirect is an error, so the read is None, as for an adapter that is down.

Not covered: the adapter URL itself is the owner's setting; a wrong one is trusted.
"""
from __future__ import annotations

import json
from typing import Any
from urllib import parse, request

from aura_schema import from_env

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
