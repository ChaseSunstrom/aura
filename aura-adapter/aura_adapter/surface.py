"""Which screen a session's person is on, carried from the client to the tool call.

The media brief opens with the failure this prevents: *"JARVIS must know, before
it plans, what the surface I'm on can actually render."* The registry of what
each surface can show lives in the ``jarvis-artifacts`` plugin; what it cannot
know on its own is which surface a given session *is*. The client knows, so it
says so on every ``prompt.submit`` as ``params.surface`` (the shape is
``tests/contracts/surface-capabilities.json``), and this module is where that
report is kept — beside :mod:`speaker`, for the same reason the speaker record
lives here: the adapter is the one place every app's prompt passes through.

Two readers, both over the loopback with the apps' key:

* ``/api/surface/current?session=`` — the plugin's prompt line and its
  ``surface_capabilities`` tool, so the model is told rather than guessing.
* ``/api/surface/sessions?surface=`` — ``artifact_send``: the sessions that
  last reported a given surface, newest first, so a video asked for from the
  terminal can be put on the desktop.

Not a defence against the client: a client that lies about its screen is the
one that watches the video fail to play, and the plugin merges the report
conservatively anyway (a report may only take capabilities away). What it does
guard is the shape — a report is a dict of the contract's fields or it is
nothing, so a page that injects ``surface: "web"`` into a frame gets no record
rather than a crash in the relay.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

#: The report's keys; the same names as the plugin's ``Surface`` dataclass so
#: the merge there is a lookup. ``name`` is the registry row it narrows.
FIELDS = ("name", "images", "video", "audio", "documents", "interactive", "downloads",
          "notifications", "max_bytes")

#: Sessions remembered at most; oldest out. Mirrors ``speaker.MAX_SESSIONS``.
MAX_SESSIONS = 512


def clean(report: Any) -> dict[str, Any] | None:
    """The contract's fields out of a client's report, or ``None`` for anything else.

    ``name`` is required and lower-cased; the booleans are coerced; ``max_bytes``
    is kept only as a positive int. Unknown keys are dropped so the stash never
    carries a client's stray fields to the plugin.
    """
    if not isinstance(report, dict):
        return None
    name = report.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"name": name.strip().lower()[:32]}
    for key in FIELDS[1:-1]:
        if key in report:
            out[key] = bool(report[key])
    cap = report.get("max_bytes")
    if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
        out["max_bytes"] = cap
    return out


@dataclass
class Surfaces:
    """Which surface each session's latest prompt came from."""

    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The paired device whose socket sent each session's latest prompt (T-1498), or ``None``
    #: for the master key. Kept apart from ``sessions``: it is rebound on every prompt, report
    #: or not, because it comes from the socket's key and never from what the client says.
    devices: dict[str, tuple[dict[str, Any] | None, float]] = field(default_factory=dict)

    def bind_device(self, session_id: str, device: dict[str, Any] | None, now: float | None = None) -> None:
        """Record the device of the socket that just sent a prompt on ``session_id``, and when:
        a tool asks under every id its conversation goes by and takes the newest prompt's."""
        self.devices.pop(session_id, None)
        if len(self.devices) >= MAX_SESSIONS:
            del self.devices[next(iter(self.devices))]
        self.devices[session_id] = (device, now if now is not None else time.time())

    def bind(self, session_id: str, report: Any, now: float | None = None) -> dict[str, Any] | None:
        """Record the surface a ``prompt.submit`` on ``session_id`` reported.

        A prompt with no report leaves the last one standing: the TUI and the
        phone send it on every prompt, but a client that sends it once and then
        stops has not moved screens. A malformed report is treated as none.
        """
        surface = clean(report)
        if surface is None:
            return None
        at = now if now is not None else time.time()
        record = {"surface": surface, "at": at}
        if session_id in self.sessions:
            del self.sessions[session_id]
        elif len(self.sessions) >= MAX_SESSIONS:
            del self.sessions[next(iter(self.sessions))]
        self.sessions[session_id] = record
        return record

    def current(self, session_id: str) -> dict[str, Any]:
        """The record for a session; ``surface`` is ``None`` for one that never reported, and
        ``device`` is ``None`` for one whose latest prompt came from no paired device;
        ``device_at`` is when that prompt came, ``None`` for a session that sent none."""
        device, device_at = self.devices.get(session_id, (None, None))
        known = self.sessions.get(session_id)
        if known is not None:
            return {"session_id": session_id, **known, "device": device, "device_at": device_at}
        return {"session_id": session_id, "surface": None, "at": None, "device": device, "device_at": device_at}

    def on(self, name: str) -> list[str]:
        """The sessions whose latest report named ``name``, newest first."""
        want = (name or "").strip().lower()
        rows = [(rec["at"], sid) for sid, rec in self.sessions.items() if rec["surface"]["name"] == want]
        return [sid for _, sid in sorted(rows, reverse=True)]


def rewrite_prompt(frame: str, surfaces: Surfaces, now: float | None = None,
                   device: dict[str, Any] | None = None) -> str:
    """Record a ``prompt.submit`` frame's surface and strip it before Hermes sees it, and
    record ``device`` (the socket's paired device, ``None`` for the master key) for its session.

    Any other frame, and anything that is not JSON-RPC, passes through untouched
    — byte for byte, which is what the relay promises the apps. Stripped for the
    same reason the speaker ticket is: Hermes sees the frame it always saw, and
    a gateway that one day validates its params strictly does not start
    refusing every prompt from a newer app.
    """
    try:
        msg = json.loads(frame)
    except ValueError:
        return frame
    if not isinstance(msg, dict) or msg.get("method") != "prompt.submit":
        return frame
    params = msg.get("params")
    if isinstance(params, dict) and params.get("session_id"):
        surfaces.bind_device(str(params["session_id"]), device, now)
    if not isinstance(params, dict) or "surface" not in params:
        return frame
    report = params.pop("surface")
    session_id = str(params.get("session_id") or "")
    if session_id:
        surfaces.bind(session_id, report, now)
    return json.dumps(msg, separators=(",", ":"))
