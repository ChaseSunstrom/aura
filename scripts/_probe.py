"""The one JSON-RPC-over-WebSocket client the live probes share.

Every script under ``scripts/hermes-probes/`` drives a running gateway the way
the apps do: open the socket, read the hello frame, number each request, and
read frames until the one whose ``id`` matches comes back — while event frames
that arrive in between are handed to a callback rather than dropped on the
floor. Six probes carried six copies of that loop (jscpd's largest Python
clones after ssrf), and two of them had quietly diverged on whether an event
that lands mid-call is delivered. This is the loop, once; each probe keeps only
what it is proving.

Two JSON-RPC sockets exist (and :func:`house` opens the third, the house's own). ``hermes serve`` exposes the HUD/TUI-gateway socket
(:data:`SERVE_URL`, ``/api/ws?token=…``); the jarvis-adapter exposes the app
socket (:data:`ADAPTER_URL`, ``/ws?key=…``). Both speak the same JSON-RPC
frames. The defaults are the dev box's throwaway credentials — the same values
the probes always carried — and an environment variable overrides each so a
probe can be pointed at another host without editing it.

Not a test double and not imported by anything that ships: ``scripts/`` is
tooling. ``deploy/tests/test_probe.py`` checks :class:`Rpc` against a fake
socket so the loop cannot regress without a gateway to notice.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

#: The ``hermes serve`` HUD socket on the dev box.
SERVE_URL = os.environ.get("HERMES_SERVE_WS", "ws://127.0.0.1:9119/api/ws?token=devservetoken123456")
#: The jarvis-adapter app socket on the dev box.
ADAPTER_URL = os.environ.get("AURA_ADAPTER_WS", "ws://127.0.0.1:8643/ws?key=devadapterkey0123456789")

EventHandler = Callable[[dict], None]


class Rpc:
    """Numbered JSON-RPC 2.0 calls over one open socket, events routed aside.

    ``on_event`` receives the ``params`` of every ``event`` frame that arrives
    while a call is waiting for its reply; without it those frames are skipped,
    which is what a probe that only dispatches commands wants.
    """

    def __init__(self, ws: Any, *, on_event: EventHandler | None = None) -> None:
        self.ws = ws
        self.on_event = on_event
        self.sid: str | None = None
        self._rid = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self._rid += 1
        mine = self._rid
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": mine,
                                       "method": method, "params": params or {}}))
        while True:
            frame = json.loads(await self.ws.recv())
            if frame.get("id") == mine:
                return frame
            if frame.get("method") == "event" and self.on_event is not None:
                self.on_event(frame.get("params") or {})

    async def create_session(self) -> str:
        """``session.create``; the id is kept for :meth:`dispatch`."""
        self.sid = (await self.call("session.create", {}))["result"]["session_id"]
        return self.sid

    async def dispatch(self, name: str, arg: str = "", *, prefix_session: bool = False) -> dict:
        """``command.dispatch`` on the current session.

        Plugin commands (``/jsettings``, ``/hud``, ``/jgoal`` …) are never handed
        the session by Hermes, so the apps prefix their argument with
        ``@<session_id>``; ``prefix_session=True`` does the same here.
        """
        assert self.sid is not None, "create_session() first"
        return await self.call("command.dispatch", {
            "session_id": self.sid, "name": name,
            "arg": f"@{self.sid} {arg}" if prefix_session else arg,
        })

    async def events(self, seconds: float) -> AsyncIterator[dict]:
        """Every event's ``params`` for up to ``seconds``; stops quietly at the deadline."""
        deadline = time.time() + seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            frame = json.loads(raw)
            if frame.get("method") == "event":
                yield frame.get("params") or {}


@asynccontextmanager
async def connect(url: str, *, on_event: EventHandler | None = None) -> AsyncIterator[Rpc]:
    """Open ``url``, read the hello frame (kept as ``rpc.hello``), yield an :class:`Rpc`."""
    import websockets  # a probe-only dependency; keep the module importable without it

    async with websockets.connect(url, max_size=None) as ws:
        rpc = Rpc(ws, on_event=on_event)
        rpc.hello = await ws.recv()  # type: ignore[attr-defined]
        yield rpc


@asynccontextmanager
async def session(url: str, *, on_event: EventHandler | None = None) -> AsyncIterator[Rpc]:
    """:func:`connect`, then ``session.create``, printing the hello frame and the id as it goes.

    The three HUD-socket probes all open with exactly these two lines of output;
    the first frame is printed because a wrong token shows up there, not as an
    exception, and the session id is what the reader pastes into a follow-up.
    """
    async with connect(url, on_event=on_event) as rpc:
        print("first frame:", rpc.hello[:120])  # type: ignore[attr-defined]
        sid = await rpc.create_session()
        print("session:", sid)
        yield rpc


@asynccontextmanager
async def house(core: str, token: str) -> AsyncIterator[Any]:
    """The house's own socket -- jarvis-core's HA-shaped ``/api/websocket`` -- authenticated.

    Not JSON-RPC: the house speaks Home Assistant's frames (``auth_required``, ``auth``,
    ``auth_ok``, then ``{id, type}`` commands and ``event`` frames), so this yields the raw
    socket for a probe that plays a phone or a desktop on the device channel. Raises
    ``RuntimeError`` when the token is refused, rather than letting the first command hang.
    """
    import websockets  # a probe-only dependency; keep the module importable without it

    url = core.rstrip("/").replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        answer = json.loads(await ws.recv())
        if answer.get("type") != "auth_ok":
            raise RuntimeError(f"the house refused the token: {answer.get('message') or answer.get('type')}")
        yield ws


def outcome(reply: dict) -> Any:
    """The result of a reply, or its error when there is no result."""
    return reply.get("result") or reply.get("error")


def brief(reply: dict, limit: int = 200) -> str:
    """``outcome`` as JSON, cut to ``limit`` characters for a one-line print."""
    return json.dumps(outcome(reply))[:limit]


def http_json(base: str, key: str, path: str, body: dict, *, timeout: float = 900) -> Any:
    """POST ``body`` as JSON to the adapter with its bearer key; the decoded reply."""
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def post(url: str, data: bytes, headers: dict[str, str], *, timeout: float = 60) -> Any:
    """POST raw bytes; the open response, for a probe that reads its headers (Server-Timing)."""
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout)


def adapter_ws_url(base: str, key: str) -> str:
    """The adapter's socket URL from its HTTP base and key."""
    return base.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?key={key}"
