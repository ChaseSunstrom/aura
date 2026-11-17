"""The console's loop: the HUD socket, the keyboard, the frames, and ``--demo``.

The socket is the adapter's ``/ws`` (``docs/hermes-hud-protocol.md``): JSON-RPC
2.0 text frames, the key as a bearer header, events as ``method: "event"``
frames. One rule of that protocol shapes :class:`Gateway`: **events a handler
emits arrive before that handler's reply**, so the reader task queues every
event frame it sees while a request is waiting, and nothing is read "for one
id" and thrown away.

The keyboard is raw stdin: printable characters go to the composer, ``Enter``
submits, ``Esc`` alone dismisses the focused HUD panel or clears the composer,
``alt`` chords (an escape followed by the letter, which is what terminals send
for a held alt) are the layout verbs of GLASS_HUD §9.8. The approvals panel
answers with the first letter of the choice while it has the keyboard; the goal
panel takes ``p`` and ``c``.

``--demo <mode>`` draws one frame from a fixed tape of protocol events, no
socket, no raw terminal — the captured frames in ``docs/design/screens`` come
from it, and so does the test that proves every mode draws.

Not covered: Windows (no ``termios``; the console says so and exits), voice
(the console has no microphone path; the reactor's listening and speaking
states are the phone's and the web's), and a terminal narrower than the
smallest panel plus the strip, where panels hide and the strip says how many.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from .. import reactor
from . import panes, render
from .state import APPROVAL_CHOICES, SURFACE_REPORT, Hud
from .theme import Caps, Theme, detect

_LOGGER = logging.getLogger(__name__)

__all__ = ["Gateway", "GatewayError", "Key", "parse_keys", "Console", "demo_hud", "main", "DEMO_TAPE"]

#: The surface name this console reports and asks layouts for.
SURFACE = "console"
#: How often ``GET /api/status`` is read: while a turn waits for its first token, and at rest.
STATUS_PENDING_S = 5.0
STATUS_REST_S = 30.0


class GatewayError(RuntimeError):
    """A JSON-RPC error reply, with its code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def ws_url(adapter: str) -> str:
    """``https://host:8643`` (or ``wss://…/ws``) -> the HUD socket URL."""
    parts = urlsplit(adapter if "://" in adapter else f"http://{adapter}")
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    path = parts.path if parts.path.rstrip("/").endswith("/ws") else parts.path.rstrip("/") + "/ws"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def http_url(adapter: str, path: str) -> str:
    parts = urlsplit(adapter if "://" in adapter else f"http://{adapter}")
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    base = parts.path.rstrip("/")
    if base.endswith("/ws"):
        base = base[: -len("/ws")]
    return urlunsplit((scheme, parts.netloc, base + path, "", ""))


class Gateway:
    """A JSON-RPC client on the HUD socket that never drops an event frame."""

    def __init__(self, url: str, key: str) -> None:
        self.url = url
        self.key = key
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._ws: Any = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None

    async def connect(self, open_timeout: float = 15.0) -> None:
        import websockets

        headers = {"Authorization": f"Bearer {self.key}"}
        try:
            self._ws = await websockets.connect(self.url, additional_headers=headers,
                                                open_timeout=open_timeout, max_size=None)
        except TypeError:
            # websockets < 13 spells the header argument differently.
            self._ws = await websockets.connect(self.url, extra_headers=headers,
                                                open_timeout=open_timeout, max_size=None)
        self._reader = asyncio.create_task(self._read())

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 — closing is best effort
                pass

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                self.feed(raw)
        except Exception as exc:  # noqa: BLE001 — the loop reports it as offline
            _LOGGER.info("socket closed: %s", exc)
        finally:
            await self.events.put({"type": "console.closed"})
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(GatewayError(-1, "socket closed"))

    def feed(self, raw: str | bytes) -> None:
        """One frame from the wire: an event is queued, a reply resolves its request."""
        try:
            frame = json.loads(raw)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        if frame.get("method") == "event" and isinstance(frame.get("params"), dict):
            self.events.put_nowait(frame["params"])
            return
        rid = frame.get("id")
        future = self._pending.pop(rid, None) if isinstance(rid, int) else None
        if future is None or future.done():
            return
        if "error" in frame:
            err = frame["error"] if isinstance(frame["error"], dict) else {}
            future.set_exception(GatewayError(int(err.get("code", -1)), str(err.get("message", "error"))))
        else:
            result = frame.get("result")
            future.set_result(result if isinstance(result, dict) else {"value": result})

    async def request(self, method: str, params: dict[str, Any] | None = None,
                      timeout: float = 30.0) -> dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[rid] = future
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(rid, None)


# --- keys ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Key:
    """A keystroke: ``kind`` is ``char``, ``enter``, ``backspace``, ``esc``, ``alt``, ``ctrl``."""

    kind: str
    value: str = ""


def parse_keys(data: bytes) -> list[Key]:
    """Raw stdin bytes into keys. An escape followed by a letter is ``alt``;
    an escape on its own is ``esc``; ``ESC [`` sequences (arrows) are dropped."""
    out: list[Key] = []
    text = data.decode("utf-8", errors="replace")
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\x1b":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt == "[" or nxt == "O":
                j = i + 2
                while j < len(text) and not text[j].isalpha() and text[j] != "~":
                    j += 1
                i = j + 1
                continue
            if nxt and nxt != "\x1b":
                out.append(Key("alt", nxt))
                i += 2
                continue
            out.append(Key("esc"))
            i += 1
            continue
        if ch in ("\r", "\n"):
            out.append(Key("enter"))
        elif ch in ("\x7f", "\x08"):
            out.append(Key("backspace"))
        elif ord(ch) < 32:
            out.append(Key("ctrl", chr(ord(ch) + 96)))
        else:
            out.append(Key("char", ch))
        i += 1
    return out


# --- the console ----------------------------------------------------------------------


@dataclass
class Console:
    """Everything the loop holds; ``on_key`` and ``draw`` are pure enough to test."""

    hud: Hud
    theme: Theme
    caps: Caps
    cols: int = 80
    rows: int = 24
    focused: str | None = None
    composer: str = ""
    presets: list[str] = field(default_factory=list)
    preset_at: int = -1
    reduced: bool = False
    animator: reactor.Animator | None = None
    #: What ``on_key`` wants sent: ``(method, params)`` requests, in order.
    outbox: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    quit: bool = False
    #: The last frame's geometry, for the focus verbs.
    arrangement: panes.Arrangement | None = None

    def frame(self, now: float) -> render.Frame:
        fr = render.frame(self.hud, self.cols, self.rows, self.theme, self.caps, now=now,
                          animator=self.animator, focused=self.focused, composer=self.composer,
                          reduced=self.reduced)
        self.arrangement = fr.arrangement
        self.focused = fr.arrangement.focused
        return fr

    # --- verbs ---------------------------------------------------------------------

    def _command(self, name: str, arg: str) -> None:
        sid = self.hud.session_id or ""
        self.outbox.append(("command.dispatch", {"session_id": sid, "name": name, "arg": arg}))

    def _layout(self, arg: str) -> None:
        self._command("hud_layout", f"@{self.hud.session_id or ''} {arg}")

    def focused_type(self) -> str:
        if self.arrangement is None:
            return ""
        for p in self.arrangement.placed:
            if p.pid == self.focused:
                return p.type
        return ""

    def on_key(self, key: Key) -> None:  # noqa: C901 — one switch over the chords
        if key.kind == "ctrl" and key.value in ("c", "q", "d"):
            self.quit = True
            return
        if key.kind == "alt":
            self._alt(key.value)
            return
        if key.kind == "esc":
            if self.composer:
                self.composer = ""
            elif self.hud.panels:
                self._command("hud", f"@{self.hud.session_id or ''} dismiss {self.hud.panels[-1].id}")
            return
        if key.kind == "backspace":
            self.composer = self.composer[:-1]
            return
        if key.kind == "enter":
            text = self.composer.strip()
            self.composer = ""
            if text:
                self._submit(text)
            return
        if key.kind != "char":
            return
        ftype = self.focused_type()
        if ftype == "approvals" and not self.composer and self._answer(key.value):
            return
        if ftype == "goals" and not self.composer and key.value in ("p", "r", "c"):
            self._command("goal", {"p": "pause", "r": "resume", "c": "clear"}[key.value])
            return
        self.composer += key.value

    def _alt(self, letter: str) -> None:
        if letter.isdigit() and self.arrangement is not None:
            target = panes.ordinal(self.arrangement, int(letter))
            if target:
                self.focused = target
        elif letter in ("H", "J", "K", "L", "h", "j") and self.arrangement is not None and self.focused:
            direction = {"H": "left", "h": "left", "J": "down", "j": "down", "K": "up", "L": "right"}[letter]
            target = panes.neighbour(self.arrangement, self.focused, direction)
            if target:
                self.focused = target
        elif letter == "l" and self.presets:
            self.preset_at = (self.preset_at + 1) % len(self.presets)
            self._layout(f"arrange {self.presets[self.preset_at]}")
        elif letter == "z":
            self._layout("undo")
        elif letter == "k":
            self._layout("unlock" if self.hud.lock else "lock")
            self.hud.lock = not self.hud.lock

    def _answer(self, letter: str) -> bool:
        sid = self.hud.session_id or ""
        if self.hud.approvals:
            first = self.hud.approvals[0]
            for choice in APPROVAL_CHOICES:
                if choice[0] == letter and choice in first.choices:
                    self.outbox.append(("approval.respond", {"session_id": sid, "request_id": first.request_id,
                                                             "choice": choice}))
                    self.hud.approvals = self.hud.approvals[1:]
                    return True
            return False
        if self.hud.clarifies and letter.isdigit():
            question = self.hud.clarifies[0]
            index = int(letter) - 1
            if 0 <= index < len(question.options):
                self.outbox.append(("clarify.respond", {"session_id": sid, "request_id": question.request_id,
                                                        "answer": question.options[index]}))
                self.hud.clarifies = self.hud.clarifies[1:]
                return True
        return False

    def _submit(self, text: str) -> None:
        sid = self.hud.session_id or ""
        lowered = text.lower()
        if lowered.startswith("/goal "):
            self._command("goal", text[6:].strip())
            return
        if lowered.startswith("/"):
            name, _, arg = text[1:].partition(" ")
            self._command(name, arg)
            return
        self.hud.begin_turn(text, time.time())
        self.outbox.append(("prompt.submit", {"session_id": sid, "text": text, "surface": SURFACE_REPORT}))


# --- the live loop ------------------------------------------------------------------


class _Terminal:
    """Raw mode, the alternate screen, and the size — restored on exit whatever happens."""

    def __init__(self) -> None:
        self._saved: Any = None
        self.out = sys.stdout

    def __enter__(self) -> _Terminal:
        import termios
        import tty

        fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(fd)
        tty.setraw(fd)
        self.out.write("\x1b[?1049h\x1b[?25l\x1b[2J")
        self.out.flush()
        return self

    def __exit__(self, *_: Any) -> None:
        import termios

        self.out.write("\x1b[?25h\x1b[?1049l")
        self.out.flush()
        if self._saved is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved)

    @staticmethod
    def size() -> tuple[int, int]:
        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines

    def paint(self, rows: list[str]) -> None:
        self.out.write("\x1b[H" + "\r\n".join(rows))
        self.out.flush()


async def _fetch_status(url: str, key: str) -> dict[str, Any] | None:
    """``GET /api/status`` through urllib in a thread — the package has no HTTP client."""
    import urllib.request

    def go() -> dict[str, Any] | None:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — the adapter the person named
                data = json.loads(resp.read().decode("utf-8"))
                return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001 — a status read that fails is "unknown", not fatal
            _LOGGER.debug("status: %s", exc)
            return None

    return await asyncio.to_thread(go)


async def run(adapter: str, key: str, *, reduced: bool = False,
              status_fetch: Callable[[str, str], Awaitable[Optional[dict[str, Any]]]] = _fetch_status) -> int:
    if sys.platform == "win32":  # pragma: no cover
        print("The console needs a POSIX terminal; on Windows use the web console.", file=sys.stderr)
        return 2
    caps = detect(stream=sys.stdout)
    theme = Theme()
    hud = Hud()
    console = Console(hud, theme, caps, reduced=reduced,
                      animator=reactor.Animator(caps=caps, reduced=reduced))
    gateway = Gateway(ws_url(adapter), key)
    loop = asyncio.get_running_loop()
    keys: asyncio.Queue[Key] = asyncio.Queue()

    def on_stdin() -> None:
        try:
            data = os.read(sys.stdin.fileno(), 1024)
        except OSError:
            return
        for k in parse_keys(data):
            keys.put_nowait(k)

    resized = asyncio.Event()
    with _Terminal() as term:
        loop.add_reader(sys.stdin.fileno(), on_stdin)
        try:
            loop.add_signal_handler(signal.SIGWINCH, resized.set)
        except (NotImplementedError, AttributeError):  # pragma: no cover
            pass
        console.cols, console.rows = term.size()
        try:
            await gateway.connect()
            hud.link(True)
            created = await gateway.request("session.create", {})
            hud.session_id = str(created.get("session_id") or "")
            hud.stored_session_id = str(created.get("stored_session_id") or "") or None
            info = created.get("info")
            if isinstance(info, dict):
                hud.apply({"type": "session.info", "session_id": hud.session_id, "payload": info}, time.time())
            if isinstance(created.get("messages"), list):
                hud.load_history(created["messages"], time.time())
            await _join(gateway, console)
        except Exception as exc:  # noqa: BLE001 — shown in the console, which is the point
            hud.link(False)
            hud.error = str(exc)

        next_status = 0.0
        frame_s = 1.0 / theme.fps
        try:
            while not console.quit:
                now = time.time()
                if resized.is_set():
                    resized.clear()
                    console.cols, console.rows = term.size()
                while not gateway.events.empty():
                    params = gateway.events.get_nowait()
                    if params.get("type") == "console.closed":
                        hud.link(False)
                    else:
                        hud.apply(params, now)
                while not keys.empty():
                    console.on_key(keys.get_nowait())
                await _flush(gateway, console)
                if now >= next_status:
                    report = await status_fetch(http_url(adapter, "/api/status"), key)
                    hud.apply_status(report)
                    pending = hud.turn.running and not hud.turn.got_token
                    next_status = now + (STATUS_PENDING_S if pending else STATUS_REST_S)
                if console.animator is not None:
                    console.animator.tick()
                term.paint(list(console.frame(now).rows))
                await asyncio.sleep(frame_s)
        finally:
            loop.remove_reader(sys.stdin.fileno())
            await gateway.close()
    return 0


async def _join(gateway: Gateway, console: Console) -> None:
    """A late joiner's reads: the layout state, the presets, the panels pulled up."""
    hud = console.hud
    sid = hud.session_id or ""
    try:
        state = await gateway.request("command.dispatch", {"session_id": sid, "name": "hud_layout", "arg": f"@{sid} get"})
        hud.apply_layout_state(_plugin_json(state), SURFACE)
        presets = await gateway.request("command.dispatch", {"session_id": sid, "name": "hud_layout", "arg": f"@{sid} presets"})
        table = _plugin_json(presets).get("presets")
        if isinstance(table, dict):
            console.presets = sorted(table)
            # Before anything is applied the plugin itself shows `working` (§9.2), so
            # the console starts on the same default rather than on an empty room.
            working = table.get("working")
            if not hud.layout and isinstance(working, dict) and isinstance(working.get("layout"), dict):
                hud.layout = working["layout"]
                hud.layout_activity = hud.layout_activity or str(working.get("activity") or "default")
    except GatewayError as exc:
        _LOGGER.info("hud_layout is not routed here (%s); layouts come by event only", exc)
    try:
        listed = await gateway.request("command.dispatch", {"session_id": sid, "name": "hud", "arg": f"@{sid} list"})
        cards = json.loads(str(listed.get("output") or "[]"))
        for card in cards if isinstance(cards, list) else []:
            hud.apply({"type": "hud.panel.show", "session_id": sid, "payload": card}, time.time())
    except (GatewayError, ValueError) as exc:
        _LOGGER.info("hud list unavailable: %s", exc)


def _plugin_json(result: dict[str, Any]) -> dict[str, Any]:
    output = result.get("output")
    try:
        data = json.loads(str(output)) if isinstance(output, str) else output
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _flush(gateway: Gateway, console: Console) -> None:
    """Send what the keys asked for; a ``send`` result starts the turn it describes."""
    while console.outbox:
        method, params = console.outbox.pop(0)
        try:
            result = await gateway.request(method, params)
        except GatewayError as exc:
            console.hud.error = str(exc)
            continue
        if method == "command.dispatch":
            kind = result.get("type")
            if kind == "send" and result.get("message"):
                console.hud.begin_turn(str(result.get("notice") or result["message"]), time.time())
                console.outbox.append(("prompt.submit", {"session_id": params["session_id"],
                                                         "text": str(result["message"]), "surface": SURFACE_REPORT}))
            elif kind in ("exec", "plugin") and result.get("output"):
                console.hud.turn.status = str(result["output"])[:200]
            elif kind == "error":
                console.hud.error = str(result.get("message") or result.get("output") or "error")


# --- the demo tape ----------------------------------------------------------------------

#: A short session in the protocol's own shapes: what every mode's captured frame shows.
DEMO_TAPE: list[dict[str, Any]] = [
    {"type": "session.info", "payload": {"model": "qwen3-30b", "provider": "llama-swap",
                                         "approval_mode": "smart", "tools": [{} for _ in range(14)]}},
    {"type": "status.update", "payload": {"kind": "goal", "text": "⊙ Goal set (20-turn budget): Keep the study at 21°"}},
    {"type": "console.you", "payload": {"text": "Warm the study"}},
    {"type": "tool.start", "payload": {"tool_id": "t1", "name": "ha_call_service",
                                       "args": {"entity_id": "climate.study", "temperature": 21}}},
    {"type": "tool.complete", "payload": {"tool_id": "t1", "name": "ha_call_service", "duration": 0.412}},
    {"type": "tool.start", "payload": {"tool_id": "t2", "name": "gpu_status", "args": {}}},
    {"type": "tool.complete", "payload": {"tool_id": "t2", "name": "gpu_status", "duration": 0.088}},
    {"type": "hud.panel.show", "payload": {"id": "p_gpus", "kind": "telemetry", "title": "GPUs", "origin": "agent",
                                           "created_at": "2026-09-06T09:41:12Z", "rev": 1,
                                           "payload": {"gpus": [{"gpu": "0", "utilization": 0, "vram_pct": 93.6,
                                                                 "temperature": 50, "power": 22}]}}},
    {"type": "tool.start", "payload": {"tool_id": "t3", "name": "web_search", "args": {"query": "study heater"}}},
    {"type": "message.delta", "payload": {"text": "Done. The heater is on and set to 21°."}},
    {"type": "status.update", "payload": {"kind": "goal", "text": "↻ Continuing toward goal (3/20): The study is still at 19°, so the heater stays on."}},
]


def demo_hud(mode: str, contract: dict[str, Any] | None = None) -> Hud:
    """The tape applied, with the layout the mode is best shown in.

    The layouts are the contract's presets when the contract file is beside
    this checkout (``tests/contracts/hud-layout.json``); an installed agent has
    the same trees typed here, since a demo that needed the repo would draw
    nothing on the machine the console is for.
    """
    hud = Hud(session_id="demo")
    hud.link(True)
    now = 0.0
    for event in DEMO_TAPE:
        now += 0.4
        if event["type"] == "console.you":
            hud.begin_turn(str(event["payload"]["text"]), now)
            continue
        hud.apply({**event, "session_id": "demo"}, now)
    hud.apply_status({"model": {"state": "ready"}, "kill_switch": {"engaged": False}})
    presets = (contract or {}).get("presets") or {}
    layouts: dict[str, dict[str, Any]] = {
        "task": (presets.get("working") or {}).get("layout") or {
            "mode": "task",
            "tree": {"split": "row", "ratio": 0.5, "children": [
                "chat", {"split": "column", "ratio": 0.6, "children": [
                    "timeline", {"split": "row", "ratio": 0.5, "children": ["goals", "model"]}]}]},
            "panels": [{"id": "chat", "type": "chat"}, {"id": "timeline", "type": "timeline"},
                       {"id": "goals", "type": "goals"}, {"id": "model", "type": "model"}]},
        "focus": (presets.get("approval-focus") or {}).get("layout") or {
            "mode": "focus", "tree": "approvals",
            "panels": [{"id": "approvals", "type": "approvals"}, {"id": "chat", "type": "chat"},
                       {"id": "timeline", "type": "timeline"}]},
        "overview": {
            "mode": "overview", "tree": "chat",
            "panels": [{"id": "chat", "type": "chat", "rect": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.6}},
                       {"id": "telemetry", "type": "telemetry", "rect": {"x": 0.5, "y": 0.2, "w": 0.4, "h": 0.4}},
                       {"id": "goals", "type": "goals", "rect": {"x": 0.2, "y": 0.6, "w": 0.4, "h": 0.3}}]},
        "ambient": (presets.get("voice-ambient") or {}).get("layout") or {
            "mode": "ambient", "tree": None, "panels": [{"id": "voice", "type": "voice"}]},
    }
    activity = {"task": "default", "focus": "approval", "overview": "image", "ambient": "voice"}[mode]
    if mode == "focus":
        hud.apply({"type": "approval.request", "session_id": "demo", "payload": {
            "request_id": "r1", "command": "<ha_call_service> (plugin approval rule)",
            "pattern_key": "plugin_rule:house:service-call",
            "description": "Turning the boiler up past 60° needs your say-so.",
            "choices": ["once", "session", "always", "deny"]}}, now + 1)
    hud.apply({"type": "hud.layout.apply", "session_id": "demo", "payload": {
        "op": "arrange", "surface": SURFACE, "activity": activity, "layout": layouts[mode],
        "animate": True, "reversible": True, "reason": f"preset for {mode}", "rev": 1, "undo_depth": 1}}, now + 2)
    if mode == "focus":
        hud.apply({"type": "hud.layout.highlight", "session_id": "demo", "payload": {
            "panel": "approvals", "surface": SURFACE, "duration_ms": 1200, "reason": "approval needed"}}, now + 2)
    return hud


def demo_frame(mode: str, cols: int, rows: int, caps: Caps, *, plain: bool = False,
               contract: dict[str, Any] | None = None) -> list[str]:
    hud = demo_hud(mode, contract)
    fr = render.frame(hud, cols, rows, Theme(), caps, now=2.5, focused=None, composer="")
    return render.rows_plain(fr.rows) if plain else list(fr.rows)


# --- the command ----------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--adapter", help="the adapter, e.g. https://jarvis.example:8643 (or AURA_ADAPTER_URL)")
    parser.add_argument("--key", help="the adapter's bearer key (prefer --key-file or AURA_ADAPTER_KEY)")
    parser.add_argument("--key-file", help="read the key from this file")
    parser.add_argument("--reduced-motion", action="store_true", help="the reactor's still glyphs")
    parser.add_argument("--demo", choices=list(panes.MODES), help="draw one frame of this mode from a tape and exit")
    parser.add_argument("--cols", type=int, default=0, help="with --demo: columns (default: the terminal's)")
    parser.add_argument("--rows", type=int, default=0, help="with --demo: rows")
    parser.add_argument("--plain", action="store_true", help="with --demo: no escapes, for a text file")


def main(args: argparse.Namespace, env: Mapping[str, str] | None = None) -> int:
    """The ``console`` subcommand.

    The adapter's address and key are the settings schema's ``adapter.url`` and
    ``adapter.key`` (``AURA_ADAPTER_URL`` / ``AURA_ADAPTER_KEY``), the same
    two values the web console is configured with — shared names, filed as such
    in ``aura-schema/tests/test_desktop_env_conformance.py``. ``env`` is
    injected the way the agent's own config reader takes it, so a test can hand
    in a mapping and nothing here reads the process environment by surprise.
    """
    if getattr(args, "demo", None):
        cols, rows = shutil.get_terminal_size((96, 30))
        cols = args.cols or cols
        rows = args.rows or rows
        caps = Caps("none", True, True) if args.plain else detect(stream=sys.stdout)
        for row in demo_frame(args.demo, cols, rows, caps, plain=args.plain):
            print(row)
        return 0
    env = os.environ if env is None else env
    adapter = getattr(args, "adapter", None) or env.get("AURA_ADAPTER_URL", "")
    key = getattr(args, "key", None) or env.get("AURA_ADAPTER_KEY", "")
    if getattr(args, "key_file", None):
        with open(args.key_file, encoding="utf-8") as fh:
            key = fh.read().strip()
    if not adapter or not key:
        print("the console needs --adapter and --key (or AURA_ADAPTER_URL / AURA_ADAPTER_KEY)",
              file=sys.stderr)
        return 2
    return asyncio.run(run(adapter, key, reduced=bool(getattr(args, "reduced_motion", False))))
