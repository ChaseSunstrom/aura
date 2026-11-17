"""``json.loads`` for text another machine or a client wrote: no NaN, Infinity or ``1e999``.

Python's decoder accepts ``NaN``/``Infinity`` literals, and reads ``1e999`` as inf,
though JSON has neither. Everything downstream assumes a finite number: ``int(inf)``
raises OverflowError (``int(nan)`` ValueError), and a non-finite float re-emitted by
``json.dumps`` is a bare ``NaN`` that every strict parser, the browser HUD included,
rejects mid-stream. Refusing at the door keeps one device from corrupting another
client's socket (T-1345).

Does not cover a float that is merely huge but finite (``1e300``): a reader that
turns one into an int still wants its own bound.
"""
from __future__ import annotations

import json
import math
from typing import Any


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _finite_float(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        # The literal is not echoed: it can be as long as the body, and this text reaches a 400.
        raise ValueError("a number is out of range")
    return number


def loads_finite(raw: str | bytes | bytearray) -> Any:
    """Like ``json.loads``; raises ValueError (JSONDecodeError's base) on a non-finite number."""
    return json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float)


def _missing_constant(name: str) -> None:
    return None


def _finite_or_none(text: str) -> float | None:
    number = float(text)
    return number if math.isfinite(number) else None


def loads_device(raw: str | bytes | bytearray) -> Any:
    """Like ``loads_finite``, but a non-finite number reads as None (missing) and the rest parses.

    For what a device publishes: one sensor reporting NaN must not make its other readings
    unreadable (T-1364). Not for a door that takes commands (WebSocket, REST, webhook,
    Wyoming): those keep ``loads_finite`` and refuse. Still raises ValueError on text that
    is not JSON at all.
    """
    return json.loads(raw, parse_constant=_missing_constant, parse_float=_finite_or_none)
