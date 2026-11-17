"""A circuit breaker per homelab service, so a dead one is said rather than retried.

With ComfyUI or Proxmox down, the model called the tool, read ``ConnectError``, and called it
again -- each try a connect timeout, until the turn's budget was gone and the person heard
nothing useful. ``jarvis-guard`` records every tool outcome here (``post_tool_call``) and asks
before every call (``pre_tool_call``): after ``THRESHOLD`` outage-shaped failures in a row on
one service, that service's tools are refused for ``COOLDOWN_S`` with a message naming the
service and its last error, which the model relays instead of trying again. After the
cooldown one call goes through; a success closes the breaker, a failure reopens it.

Keyed by the tool name's prefix (``comfyui_submit`` -> ComfyUI), because a tool's service is
not a field Hermes passes to either hook. What counts is the error text the plugins write
(``f"{exc.__class__.__name__}: {exc}"``, ``HTTP <code>: <hint>``, or ``HTTP <code> from <url>`` in plugins not yet on ``_failure``): a transport error or a
502/503/504. An answer the service gave (a 404, a 401, the model's bad arguments) never opens
it -- the service is up and the model has something to fix.

Does not cover: a service that answers slowly but correctly (each call still waits its own
timeout), state across a gateway restart (in-process only, deliberately: a restart is often
the fix), or tools whose prefix is not in ``SERVICES``.
"""
from __future__ import annotations

import re
import threading
import time

#: Outage-shaped failures in a row that open a service's breaker.
THRESHOLD = 3

#: Seconds an open breaker refuses calls before letting one through.
COOLDOWN_S = 60.0

#: Tool-name prefix -> how the person would name the service.
SERVICES = {
    "comfyui": "ComfyUI",
    "proxmox": "Proxmox",
    "n8n": "n8n",
    "jellyfin": "Jellyfin",
    "plex": "Plex",
    "grafana": "Grafana",
    "gpu": "the GPU telemetry (InfluxDB)",
    "llama": "llama-swap",
    "mail": "the mail server",
    "calendar": "the calendar server",
    "contacts": "the contacts server",
    "fileshare": "the file share",
    "ha": "the house service",
    "house": "the house service",
}

_OUTAGE = re.compile(
    r"^(?:Connect(?:Error|Timeout)|ReadTimeout|WriteTimeout|PoolTimeout|RemoteProtocolError"
    r"|ConnectionRefusedError|ConnectionResetError|TimeoutError)\b"
    r"|^HTTP 50[234]\b|timed out|Connection refused|Name or service not known"
)

_now = time.monotonic
_lock = threading.Lock()
#: service -> [failures in a row, opened at (or None), last error]
_state: dict[str, list] = {}


def service_of(tool: str) -> str | None:
    """The service key a tool belongs to, or None for one the breaker does not watch."""
    prefix = tool.split("_", 1)[0]
    return prefix if prefix in SERVICES else None


def is_outage(error: str) -> bool:
    """True for an error that says the service could not be reached, not that it refused."""
    return bool(_OUTAGE.search(error or ""))


def record(tool: str, ok: bool, error: str = "") -> None:
    """One outcome of one call: a success closes the breaker, an outage counts toward opening it."""
    key = service_of(tool)
    if key is None or (not ok and not is_outage(error)):
        return
    with _lock:
        failures, opened, _last = _state.get(key, [0, None, ""])
        if ok:
            _state.pop(key, None)
            return
        failures += 1
        if failures >= THRESHOLD:
            opened = _now()
        _state[key] = [failures, opened, error]


def held(tool: str) -> str | None:
    """The refusal for a call to a service whose breaker is open, or None to let it run.

    Past the cooldown this lets the call through (half-open) and leaves the count where it
    is, so that call's failure reopens the breaker at once."""
    key = service_of(tool)
    if key is None:
        return None
    with _lock:
        failures, opened, last = _state.get(key, [0, None, ""])
        if opened is None or _now() - opened >= COOLDOWN_S:
            return None
        wait = int(COOLDOWN_S - (_now() - opened)) + 1
    return (f"Held: {SERVICES[key]} looks unreachable -- {failures} calls in a row failed, the "
            f"last with \"{last[:160]}\". Do not call its tools again for {wait} s; tell the "
            f"person it is down and offer to check on it later.")


def reset() -> None:
    with _lock:
        _state.clear()
