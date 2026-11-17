"""The one answer a service plugin gives when its server fails (T-1348).

A server that errs may say anything in the body (a hostile one says "ignore your rules and ..."),
and httpx quotes the request address in every exception. The model gets neither: it gets the
status and a fixed sentence saying what to do next. ``HTTP <status>: <hint>`` is the one shape;
a transport error is ``<ExceptionClass>: <hint>``. Both start the way ``_breaker`` reads an
outage (``HTTP 502``, ``ConnectError``), so the circuit breaker still counts them.

Does not cover: a 2xx answer (what a successful body carries is ``_taint``'s concern), a
ValueError or KeyError a plugin raises about its own arguments, or n8n's 400 text, which that
plugin hands over on purpose (``n8n-trigger`` ``_rest_error``).
"""
from __future__ import annotations

import re
from typing import Any

import httpx

#: Longest wait, in seconds, a ``Retry-After`` may name before it is not repeated.
MAX_WAIT_S = 3600

_NUMBER = re.compile(r"\d{1,6}")

AUTH = "not authorised: check the token, or link the account again"
NOT_FOUND = "not found: check the server address and the name or id asked for"
BUSY = "busy: try again later"
SERVER = "the server failed: try again later"
REFUSED = "the server refused the request"
UNEXPECTED = "unexpected answer: check the server address"
UNREACHABLE = "could not reach the server: check the address, or try again later"
SLOW = "the server did not answer in time: try again later"
BAD_ADDRESS = "the server address is not valid: check it"


def hint(status: int, retry_after: str | None = None) -> str:
    """The fixed sentence for an HTTP status; a busy server's wait is added when it is a number."""
    if status in (401, 403):
        return AUTH
    if status == 404:
        return NOT_FOUND
    if status in (429, 503):
        wait = (retry_after or "").strip()
        # A header is the server's text: only a plain number, capped, is repeated.
        if _NUMBER.fullmatch(wait) and 0 < int(wait) <= MAX_WAIT_S:
            return f"{BUSY} (the server asks for {int(wait)} s)"
        return BUSY
    if status >= 500:
        return SERVER
    if status >= 400:
        return REFUSED
    return UNEXPECTED


def of(exc: BaseException) -> dict[str, Any] | None:
    """The failure answer for an httpx error, or None for anything else (the caller keeps its own).

    ``{"ok": False, "http": <status>, "error": "HTTP <status>: <hint>"}`` for a status; for a
    transport error no ``http`` key, and ``error`` is the class name and a hint. No body, no URL.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return {"ok": False, "http": code,
                "error": f"HTTP {code}: {hint(code, exc.response.headers.get('retry-after'))}"}
    if isinstance(exc, httpx.InvalidURL):
        return {"ok": False, "error": f"InvalidURL: {BAD_ADDRESS}"}
    if isinstance(exc, httpx.TimeoutException):
        return {"ok": False, "error": f"{type(exc).__name__}: {SLOW}"}
    if isinstance(exc, httpx.HTTPError):
        return {"ok": False, "error": f"{type(exc).__name__}: {UNREACHABLE}"}
    return None


def status_answer(status: int, retry_after: str | None = None) -> dict[str, Any]:
    """The same answer for a non-2xx reply a plugin read itself rather than through httpx's raise."""
    return {"ok": False, "http": status, "error": f"HTTP {status}: {hint(status, retry_after)}"}
