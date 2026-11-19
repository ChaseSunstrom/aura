"""jarvis-adapter — the one origin the JARVIS apps talk to.

It adds no agent logic. It terminates the apps' bearer key, then hands every
request to the native Hermes interface that already does the work:

* ``/ws``                      ↔ ``hermes serve`` TUI-gateway JSON-RPC over WebSocket
                                 (sessions, prompt.submit, command.dispatch for /goal …,
                                 approvals, streamed events). Frames pass through verbatim.
* ``/api/audio/transcribe``    → ``hermes serve`` ``POST /api/audio/transcribe`` (faster-whisper),
                                 then jarvis-core's speaker gate on the same audio: the reply
                                 carries ``speaker`` (who, at what level) and a signed
                                 ``speaker_ticket``; a voice the gate refuses has its transcript
                                 dropped here, before any prompt is built from it.
* ``/api/audio/wake-check``    — the wake word's second stage (T-1573): jarvis-core's speaker gate
                                 on the ~2 s the phone's head fired on, ``learn=0``, no ticket;
                                 ``wake: drop`` only for a voice the gate would drop.
* ``/api/speaker/current``     — who a session's latest prompt came from (the guard reads the level).
* ``/api/surface/current``     — which screen a session's latest prompt came from, as the client
                                 reported it on ``prompt.submit`` (``params.surface``, stripped here);
                                 ``/api/surface/sessions?surface=`` lists the sessions on one, newest
                                 first, for ``artifact_send``.
* ``/api/audio/speak``         → ``hermes serve`` ``POST /api/audio/speak`` (Piper)
* ``/api/audio/speak-stream``  ↔ ``hermes serve`` speech WebSocket
* ``/api/audio/voice-config``  → ``hermes serve``
* ``/v1/*``, ``/api/sessions*``, ``/api/jobs*`` → the Hermes API server (OpenAI-compatible,
                                 runs, sessions, cron jobs) with the server-side API key
* ``/api/status``              — what Hermes does not expose: llama-swap's loaded model and
                                 cold-start state, gateway/backend health, the model JARVIS is
                                 configured for.
* ``/api/pair``                — GET: the payload a phone scans (``key`` only for the master key);
                                 POST (master key): mint a device's own key (T-0569).
* ``/api/devices/keys``        — list and revoke device keys (master key).
* ``/health``                  — liveness, unauthenticated.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import hmac
import io
import ipaddress
import datetime
import json
import logging
import re
from collections import deque
from collections.abc import Awaitable
from dataclasses import asdict
import time
import wave
from pathlib import Path
from typing import Any

import httpx
from urllib.parse import unquote, urlsplit
import websockets
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import noise as noise_mod
from . import artifacts
from . import pcm_rate
from . import board as board_mod
from . import verified as verified_mod
from . import approval_proof, device_keys
from . import fast_intent
from . import tts_cache
from .server_requests import RequestBridge
from . import turn_log
from .turn_log import Deadlines, TurnLog
from . import speaker as speaker_mod
from . import surface as surface_mod
from .memory_layers import collect as collect_memory_layers
from .clause import split_first_clause
from .speech_text import taught_from
from .spoken import QUIET_SPEED, Softer, quiet_now, spoken
from .status import percentiles
from starlette.responses import JSONResponse, StreamingResponse

from . import effort as effort_mod
from .config import HERMES_HOME, Settings, load, reasoning_efforts, voice_speech
from .status import _get_json, build_status, read_persona, read_theme, summarize_llama_swap

#: Past this the card is busy (a llama-swap load, a long TTS line) and Hermes is quicker.
STT_CARD_TIMEOUT_S = 4.0
#: How long /ws waits for hermes serve to connect and speak before it hangs up on the app
#: with 1013. Shorter than the phone's own 10 s gateway.ready deadline (HermesClient), so the
#: adapter's reason reaches the phone's log first.
UPSTREAM_READY_S = 6.0
#: The house's names for the card's Whisper (T-0426): how long one read of them is used,
#: and how much of a prompt they may take (whisper keeps only ~223 prompt tokens).
HOUSE_NAMES_TTL_S = 300.0
HOUSE_NAMES_MAX = 500
#: The Speech language chosen on the console, for the card's Whisper (T-1487): how long one
#: read of it is used. Short, so a change is heard within a turn or two; the read is one
#: small request (GET /api/voice/language), not the names' two lists.
HOUSE_LANGUAGE_TTL_S = 30.0
_SWITCHABLE = {"light", "switch", "cover", "lock", "climate", "fan", "media_player", "scene", "script"}
#: Hermes' store of the owner's contacts and places (aura-hud's routines.py), on the home the
#: compose file mounts here read-only: the card's Whisper is primed with them too (T-0586).
MEMORY_FILE = Path(HERMES_HOME) / "plugin-data" / "jarvis-routines" / "memory.json"

log = logging.getLogger("aura_adapter")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "host", "content-length", "authorization",
}


#: A persona with a theme pack of her own speaks in the pack's stock voice
#: (themes/<id>/theme.json ``voice.voiceId``, T-0861): clients send the persona's name and
#: this turns it into the voice, so switching theme switches voice on every surface at once.
#: The other personas keep the clip named after them. tests/test_theme_voices.py pins it.
THEME_VOICES: dict[str, str] = {"aura": "af_heart", "jarvis": "bm_george"}
#: The default theme's voice: what a voice the shim lacks falls back to.
DEFAULT_VOICE = THEME_VOICES["aura"]


def line_voice(body: Any) -> str:
    """The voice a line is said in: the persona it names, else the primary one's.

    A line that names no voice (the smoke, a fixed line) was said in AURA's while JARVIS
    was primary (the deploy smoke of a55e109b heard a 200 Hz voice for him). Does not cover
    a drafted theme on another base pack: a draft keeps its persona's voice (drafts.py).
    """
    name = str((body or {}).get("voice") or read_persona()["active"])
    return THEME_VOICES.get(name, name)
#: What a voice name may look like: it becomes `<name>.wav` on the shim's side.
VOICE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
#: A model file name the house serves: openWakeWord's ONNX stages and heads, nothing with a path.
MODEL_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\.onnx")
#: The house's map calls the car's hologram makes, by method, and the bbox it writes
#: (tests/contracts/house-maps.json, which aura-web's relay keeps too).
MAPS_OPS = {"route": "POST", "match": "POST", "buildings": "GET", "live": "GET"}
MAPS_BBOX = re.compile(r"^-?\d{1,3}(?:\.\d{1,8})?(?:,-?\d{1,3}(?:\.\d{1,8})?){3}$")
MAPS_BODY_MAX = 64 * 1024
#: A map region's id (tests/contracts/map-pack.json): one path segment, a file name on the house.
MAPS_REGION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


async def _capped_body(request: Request, limit: int = MAPS_BODY_MAX) -> bytes:
    """The request's body, refused with a 413 as soon as more than [limit] bytes have arrived.

    A Content-Length check alone (T-0979) let a chunked body, which carries none, be read whole
    first. The header only refuses early; the count of bytes read is what decides. Not covered:
    the bytes of one chunk already in the server's buffer when the limit is crossed.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="a maps request is a few KiB")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="a maps request is a few KiB")
    return bytes(body)


def _climbs_out(path: str) -> bool:
    """True when a relayed path could leave its relay's prefix upstream (T-1565).

    uvicorn decodes %2f into the path before routing, and httpx collapses dot
    segments when it builds the upstream URL, so `/api/jobs/..%2f..%2fapi%2fcron%2ffire`
    reached Hermes' /api/cron/fire with the adapter's own Hermes key. A segment that is
    `.` or `..` after up to four percent-decodings, or holds a slash, a backslash or a
    control character at any round, is refused, and so is one still encoded after them. It does not check what the upstream does with a path that
    stays under the prefix: that is the upstream's own routing.
    """
    return any(_segment_climbs(segment) for segment in path.split("/"))


def _segment_climbs(segment: str) -> bool:
    # Every decoding round is checked (T-1571): `..%2f` decodes to `../`, which a check of
    # the last form for `..` alone missed, and a segment still changing after four rounds
    # is refused rather than passed on half-decoded. The console's climbsOut is the same rule.
    for round_ in range(5):  # %252e%252e decodes to %2e%2e, then to ..
        if segment in (".", "..") or "/" in segment or "\\" in segment:
            return True
        if any(ord(c) < 32 or ord(c) == 127 for c in segment):
            return True
        decoded = unquote(segment)
        if decoded == segment:
            return False
        if round_ == 4:
            return True
        segment = decoded
    return True


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _ws_bearer(ws: WebSocket) -> str:
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Browsers cannot set headers on a WebSocket upgrade; accept the key as a
    # subprotocol ("jarvis-key.<key>") or a query parameter.
    for proto in ws.headers.get("sec-websocket-protocol", "").split(","):
        proto = proto.strip()
        if proto.startswith("jarvis-key."):
            return proto[len("jarvis-key."):]
    return ws.query_params.get("key", "")


#: How long a signed artifact URL stays good. A `<video src>` cannot send a bearer
#: header, so the console hands the element a URL that carries its own proof; an
#: hour outlives any panel that is showing it, and a leaked URL is dead by dinner.
SIGNED_URL_TTL_S = 60 * 60


def _sign(key: str, ident: str, exp: int) -> str:
    return hmac.new(key.encode(), f"{ident}|{exp}".encode(), hashlib.sha256).hexdigest()


def signed_path(key: str, ident: str, exp: int) -> str:
    """`/api/artifacts/<id>?exp=<unix>&sig=<hmac>`: a GET a media element can make."""
    return f"/api/artifacts/{ident}?exp={exp}&sig={_sign(key, ident, exp)}"


def _signature_ok(key: str, ident: str, exp: str, sig: str, now: float | None = None) -> bool:
    """True only for an unexpired signature over exactly this id and this expiry."""
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i <= (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(_sign(key, ident, exp_i), sig or "")


#: How often expired artifacts are taken. Once a day matches the default TTL:
#: an artifact lives at most a day past its expiry, never a week.
SWEEP_INTERVAL_S = 24 * 60 * 60


async def sweep_forever(interval: float = SWEEP_INTERVAL_S) -> None:
    """Sweep the store now and then every `interval` seconds, until cancelled.

    Nothing else calls `artifacts.sweep`: without this the TTL was a number in
    a sidecar and expired artifacts were merely hidden, never deleted, so the
    store only ever grew. One failed sweep (a sidecar half-written by a crash,
    a disk hiccup) is logged and the next one still runs.
    """
    while True:
        try:
            taken = await asyncio.to_thread(artifacts.sweep)
            if taken:
                log.info("artifacts: swept %d expired", len(taken))
        except Exception:  # noqa: BLE001 - the loop must outlive any one sweep
            log.exception("artifacts: sweep failed; next in %ss", interval)
        await asyncio.sleep(interval)


def remembered_names() -> list[str]:
    """The owner's contacts, then places, as Hermes keeps them (people_find, place_list), read
    at each call so a contact added a moment ago is primed on the next turn. An unreadable
    store is no names. Does not cover a contact's relations or a place's owner."""
    try:
        data = json.loads(MEMORY_FILE.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for key, field in (("people", "name"), ("places", "label")):
        rows = data.get(key)
        out += [str(r[field]) for r in rows if isinstance(r, dict) and r.get(field)] if isinstance(rows, list) else []
    return out


def chosen_language_of(payload: Any) -> str | None:
    """The language the house says the owner chose, or None: anything but a 2-10 character
    code (the setting's own bound) is no choice. Does not check the code is one Whisper
    knows; the house's setting offers only those."""
    value = payload.get("language") if isinstance(payload, dict) else None
    if isinstance(value, str) and 2 <= len(value.strip()) <= 10:
        return value.strip()
    return None


def house_names(states: Any, first: list[str] | None = None) -> str:
    """[first] (the owner's contacts, places and rooms), then the house's friendly names,
    people first, then what can be switched, then the rest, de-duplicated and cut on a whole
    name at HOUSE_NAMES_MAX characters."""
    def rank(s: dict[str, Any]) -> int:
        domain = str(s.get("entity_id") or "").partition(".")[0]
        return 0 if domain == "person" else 1 if domain in _SWITCHABLE else 2

    def friendly(s: dict[str, Any]) -> str:
        attrs = s.get("attributes")
        return str(attrs.get("friendly_name") or "") if isinstance(attrs, dict) else ""

    rows = [s for s in states if isinstance(s, dict)] if isinstance(states, list) else []
    out: list[str] = []
    seen: set[str] = set()
    size = 0
    for raw in [*(first or []), *(friendly(s) for s in sorted(rows, key=rank))]:
        name = " ".join(raw.split())
        if not name or name.lower() in seen:
            continue
        size += len(name) + (2 if out else 0)
        if size > HOUSE_NAMES_MAX:
            break
        seen.add(name.lower())
        out.append(name)
    return ", ".join(out)


#: A private chat's persona (T-0462). Fixed here, never the caller's: it goes to the model
#: directly, with no Hermes, so none of the persona files, memory or tools are in it.
PRIVATE_PERSONA = (
    "You are Jarvis, a personal assistant with a dry British wit. This is a private "
    "conversation: nothing in it is saved, logged or remembered once it ends, and you have no "
    "tools, no memory of other conversations and no access to the house in it. If asked to do "
    "something that needs a tool, say so briefly and suggest leaving private mode. Answer "
    "plainly and briefly, in sentences that read well aloud."
)
#: A private conversation lives in the caller's memory and is re-sent whole each turn; past
#: these it is cut by the caller, not grown here.
PRIVATE_MAX_TURNS = 80
PRIVATE_MAX_CHARS = 40_000
#: A model the caller picks for a private chat (the owner wanted the uncensored one): a name
#: as llama-swap lists them, so the body cannot smuggle anything else into the model call.
#: An unknown name is refused by llama-swap itself (a 502 naming its status).
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


#: A secret in a query string: a browser cannot set a header on a WebSocket, so the console
#: opens /ws?key=<the bearer key>, and uvicorn's lines print the path whole (T-0773).
_SECRET_QUERY = re.compile(r"([?&](?:key|token|access_token)=)[^&\s\"]*")


class _NoPrivateLines(logging.Filter):
    """A private chat leaves no line: uvicorn's access line names its path, and httpx's (at
    INFO in the deployed adapter) names the model call. httpx's line for Hermes' own
    /v1/chat/completions proxy goes too; the access line still records that one. And no
    uvicorn line carries a key from a query string: the WebSocket's accepted and refused lines
    are uvicorn.error's. Does not cover a key a handler logs itself."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple):
            return True
        if record.name.startswith("uvicorn"):
            record.args = args = tuple(
                _SECRET_QUERY.sub(r"\1…", a) if isinstance(a, str) else a for a in args)
        if len(args) < 3:
            return True
        if record.name == "httpx":
            return not str(args[1]).endswith("/v1/chat/completions")
        return record.name != "uvicorn.access" or not str(args[2]).startswith("/api/private")


#: One instance, so creating a second app (the tests do) adds nothing twice.
_NO_PRIVATE_LINES = _NoPrivateLines()

#: The console's error reports (T-0475): one is a message, a source and a line, so a body
#: past this is not one; and a page stuck in an error loop must not fill the log.
CLIENT_ERROR_MAX_BYTES = 4096
CLIENT_ERRORS_PER_MINUTE = 30
#: One recording to transcribe (AUDIT B13): a minute of 16 kHz WAV, base64'd, is about 2.5 MB,
#: so a body past this is not a turn, and holding it held the shared upstream timeout.
TRANSCRIBE_MAX_BYTES = 10 * 1024 * 1024
# A wake window is about 2 s of 16 kHz PCM, ~85 KB as base64 in JSON: room for a slower rate, no more.
WAKE_CHECK_MAX_BYTES = 512 * 1024


def _is_local_peer(request: Request) -> bool:
    """Whether the TCP peer is this machine and no relay says it spoke for someone else. A relay
    beside the adapter makes every caller a loopback peer; the header it adds is what is left, so
    any of them refuses. Does not cover a relay that adds none."""
    if any(h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip")):
        return False
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


#: How many recent voice turns /api/status's p50/p95 are taken over.
VOICE_WINDOW = 200
# How long the pre-warm waits out a warming voice before it gives up and says so.
PREWARM_DEADLINE_S = 600
#: How often the pre-warm looks at which persona is primary, to make the lines again after a switch.
PERSONA_WATCH_S = 15
#: How long a voice the shim had no clip for is said as JARVIS without asking again: the shim
#: reads its clips at start, so a clip dropped in is heard within this of the shim's restart.
MISSING_VOICE_S = 300.0
#: When each report in the last minute came in. ponytail: one window for every caller, so a
#: noisy console can mute another's; per-device windows if that ever matters.
_CLIENT_ERRORS: list[float] = []


def _one_line(value: Any, cap: int) -> str:
    """A report's field as one bounded line: a newline in it would forge a log line, and an
    ESC would drive the terminal `docker logs` is read in."""
    flat = " ".join(str(value or "").split())
    return "".join(c if c.isprintable() else "?" for c in flat)[:cap]


def _private_turns(messages: Any) -> list[dict[str, str]] | None:
    """The person's and Jarvis's own lines, or None when the body is anything else: no
    system prompt of the caller's, no tool turns, bounded, and ending with the person."""
    if not isinstance(messages, list) or not 0 < len(messages) <= PRIVATE_MAX_TURNS:
        return None
    size = 0
    for m in messages:
        if not isinstance(m, dict) or set(m) != {"role", "content"}:
            return None
        if m["role"] not in ("user", "assistant") or not isinstance(m["content"], str):
            return None
        size += len(m["content"])
    if messages[-1]["role"] != "user" or size > PRIVATE_MAX_CHARS:
        return None
    return messages


def _delta_content(line: str) -> str:
    """The reply text in one SSE line of an OpenAI stream; "" for anything else (a role,
    reasoning, usage, [DONE], a malformed line)."""
    if not line.startswith("data:"):
        return ""
    try:
        return str(json.loads(line[5:].strip())["choices"][0]["delta"].get("content") or "")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return ""


def _clock() -> datetime.time:
    """The house's time of day (the container's TZ); a test replaces it."""
    return datetime.datetime.now().time()


async def _quietly(what: str, step: Awaitable[Any], level: int = logging.INFO) -> None:
    """Await a last word to a client that may already be gone (AUDIT B11), logging, not raising.

    Names only the exception's class: the socket's frames may be a private turn's.
    """
    try:
        await step
    except Exception as exc:  # the relay's teardown must finish regardless
        log.log(level, "ws relay: %s failed: %s", what, exc.__class__.__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load()
    app = FastAPI(title="jarvis-adapter", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.started = time.time()
    # The server's share of a voice reply (brief §2.7): audio in -> words, and a line's text in
    # -> its first PCM. Does not cover the model's own time to a first sentence, nor the phone's.
    voice_ms: dict[str, deque[float]] = {k: deque(maxlen=VOICE_WINDOW) for k in (
        "transcribe_ms", "first_pcm_ms", "receive_ms", "decode_ms", "gate_ms", "gate_wait_ms")}
    deadlines = Deadlines()

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
        )

    client = httpx.AsyncClient(timeout=httpx.Timeout(settings.upstream_timeout, connect=10.0))
    app.state.client = client
    phrases = tts_cache.PhraseCache()
    # Voice -> when the shim said it had no clip for it (T-0807).
    missing_voices: dict[str, float] = {}

    def said_as(voice: str) -> str:
        """JARVIS's voice for one the shim lately had no clip for, so its lines hit the cache."""
        at = missing_voices.get(voice)
        return DEFAULT_VOICE if at is not None and time.monotonic() - at < MISSING_VOICE_S else voice
    # On the logger, not a handler: uvicorn's dictConfig later replaces handlers but keeps this.
    logging.getLogger("uvicorn.access").addFilter(_NO_PRIVATE_LINES)
    logging.getLogger("uvicorn.error").addFilter(_NO_PRIVATE_LINES)
    logging.getLogger("httpx").addFilter(_NO_PRIVATE_LINES)

    @app.on_event("startup")
    async def _start_sweeper() -> None:
        app.state.sweeper = asyncio.create_task(sweep_forever())

    @app.on_event("shutdown")
    async def _close() -> None:  # pragma: no cover - lifecycle
        sweeper = getattr(app.state, "sweeper", None)
        if sweeper:
            sweeper.cancel()
        await client.aclose()

    #: The open sockets of each device key, so a revoke can hang them up.
    device_sockets: dict[str, set[WebSocket]] = {}

    #: Every app socket on /ws, so a sensitive ask (approval_proof.Desk) reaches each screen.
    app_sockets: set[WebSocket] = set()

    def _show_everywhere(frame: str) -> None:
        async def _send(ws: WebSocket) -> None:
            try:
                await ws.send_text(frame)
            except Exception:  # a socket closing under us is that socket's business
                pass
        for ws in list(app_sockets):
            asyncio.get_running_loop().create_task(_send(ws))

    desk = approval_proof.Desk(device_keys.approval_keys, _show_everywhere)

    def _is_master(presented: str) -> bool:
        return bool(presented) and hmac.compare_digest(presented, settings.api_key)

    def require_key(request: Request) -> None:
        # The master key first and unchanged, so a fault in the device-key path can never lock
        # out a client that holds AURA_API_KEY (T-0569).
        presented = _bearer(request)
        if _is_master(presented):
            return
        ident = device_keys.check(presented) if presented else None
        if not ident:
            raise HTTPException(status_code=401, detail="Unauthorized")
        request.state.device = ident

    def require_master(request: Request) -> None:
        """Minting, listing and revoking device keys: a device key could otherwise mint itself
        a spare that outlives its own revoke, or revoke the others."""
        if not _is_master(_bearer(request)):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    # ------------------------------------------------------------------ health
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "jarvis-adapter", "uptime_s": round(time.time() - app.state.started, 1)}

    # ------------------------------------------------------------------ status
    @app.get("/api/status", dependencies=[Depends(require_key)])
    async def status() -> dict[str, Any]:
        body = await build_status(client, settings)
        body["voice"] = {step: percentiles(ms) for step, ms in voice_ms.items()}
        body["voice_deadlines"] = deadlines.status()
        # What a deploy waits on before it recreates this process (deploy/wait_quiet.py, T-0577).
        body["busy"] = turn_log.busy()
        # What was last proved, per check and per surface (T-0580).
        body["verified"] = verified_mod.board()
        return body

    @app.get("/api/themes/{theme_id}", dependencies=[Depends(require_key)])
    async def theme(theme_id: str) -> dict[str, Any]:
        """A theme AURA drafted and the person applied (theme_apply, T-0861): the pack a screen
        wears when /api/status or theme.apply names an id it was not built with."""
        pack = read_theme(theme_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="no such applied theme")
        return pack

    # ------------------------------------------------------------- memory layers
    #
    # The scene brief's mid field is built from Hermes' own memory, and nothing
    # served it: the console's memory feed reads jarvis-core's store, which is a
    # different thing. The adapter already mounts the Hermes home, so this is a
    # read-only file walk rather than a new service or an agent turn.
    @app.get("/api/memory/layers", dependencies=[Depends(require_key)])
    async def memory_layers() -> dict[str, Any]:
        return await asyncio.to_thread(collect_memory_layers)

    # ---------------------------------------------------------------- artifacts
    #
    # The media brief's first step: a content-addressed store so a tool that
    # makes a video, an image or a document has somewhere to put it that every
    # surface can reach. It retires "here's a path on the container", which is
    # useless to a phone.
    #
    # Auth is the same bearer key as everything else and the bind is the
    # tailnet's, so "nothing reachable off the tailnet" is the compose file's
    # guarantee rather than a second one made here.
    def _row(art: artifacts.Artifact, request: Request) -> dict[str, Any]:
        # `signed_url` is what a media element gets: it cannot send the bearer.
        # Built the way /api/pair builds its url, so the phone and the console
        # see the same origin they already reach.
        base = settings.public_url or str(request.base_url).rstrip("/")
        exp = int(time.time()) + SIGNED_URL_TTL_S
        return {**asdict(art), "signed_url": base + signed_path(settings.api_key, art.id, exp)}

    @app.post("/api/artifacts", dependencies=[Depends(require_key)])
    async def artifact_publish(request: Request) -> dict[str, Any]:
        mime = request.headers.get("x-artifact-mime", "application/octet-stream")
        title = request.headers.get("x-artifact-title", "untitled")
        ttl_header = request.headers.get("x-artifact-ttl")
        prov = artifacts.Provenance(
            session=request.headers.get("x-artifact-session"),
            tool_call=request.headers.get("x-artifact-tool-call"),
            source_url=request.headers.get("x-artifact-source-url"),
            tool=request.headers.get("x-artifact-tool"),
        )
        try:
            ttl = None if ttl_header == "never" else float(ttl_header or artifacts.DEFAULT_TTL)
        except ValueError:
            raise HTTPException(status_code=400, detail="x-artifact-ttl must be seconds or 'never'")
        body = await request.body()
        try:
            art = await asyncio.to_thread(
                artifacts.publish, io.BytesIO(body), mime=mime, title=title, provenance=prov, ttl=ttl
            )
        except artifacts.ArtifactTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc))
        except OSError as exc:
            # A store that cannot write: the volume missing, mounted read-only
            # (every publish in production until 2026-09-18) or owned by root.
            # The answer names the reason; a traceback named nothing.
            log.error("artifacts: cannot write %s: %s", artifacts.ROOT, exc)
            raise HTTPException(status_code=507, detail=f"artifact store is not writable: {exc.strerror or exc}")
        return _row(art, request)

    @app.get("/api/artifacts", dependencies=[Depends(require_key)])
    async def artifact_list(request: Request, kind: str = "") -> dict[str, Any]:
        rows = await asyncio.to_thread(artifacts.listing, None, kind)
        return {"artifacts": [_row(a, request) for a in rows]}

    @app.get("/api/artifacts/{ident}")
    async def artifact_get(ident: str, request: Request) -> Response:
        # The bearer, or a signed URL from `_row`: a `<video src>` can carry the
        # second and not the first. A bad or stale signature falls through to
        # the bearer check, so it is refused exactly as no key at all is.
        q = request.query_params
        if not _signature_ok(settings.api_key, ident, q.get("exp", ""), q.get("sig", "")):
            require_key(request)
        try:
            art = await asyncio.to_thread(artifacts.get, ident)
        except (artifacts.ArtifactNotFound, artifacts.BadArtifactId):
            raise HTTPException(status_code=404, detail="no such artifact")

        # `accept-ranges` on every response, not only on a 206: a player decides
        # whether it can seek from the full response's headers, and one that
        # cannot see the header downloads the whole file before showing a frame.
        # The store holds whatever a tool produced. A published text/html or SVG
        # served inline would run its script on the tailnet origin that also
        # serves the console and this API, so those come back as a download
        # instead. Media types are unaffected: a video still streams inline.
        ctype, disposition = artifacts.safe_disposition(art.mime)
        base = {
            "accept-ranges": "bytes",
            "content-type": ctype,
            "content-disposition": f'{disposition}; filename="{art.id}"',
            "x-artifact-title": art.title,
        }
        rng = request.headers.get("range", "")
        if rng:
            span = artifacts.parse_range(rng, art.size)
            if span is None:
                return Response(
                    status_code=416, headers={**base, "content-range": f"bytes */{art.size}"}
                )
            start, end = span
            return StreamingResponse(
                artifacts.read_range(ident, start, end),
                status_code=206,
                headers={
                    **base,
                    "content-range": f"bytes {start}-{end}/{art.size}",
                    "content-length": str(end - start + 1),
                },
            )
        return StreamingResponse(
            artifacts.read_range(ident, 0, art.size - 1),
            headers={**base, "content-length": str(art.size)},
        )

    @app.post("/api/artifacts/{ident}/pin", dependencies=[Depends(require_key)])
    async def artifact_pin(ident: str, request: Request, pinned: bool = True) -> dict[str, Any]:
        try:
            return _row(await asyncio.to_thread(artifacts.pin, ident, pinned), request)
        except (artifacts.ArtifactNotFound, artifacts.BadArtifactId):
            raise HTTPException(status_code=404, detail="no such artifact")

    @app.delete("/api/artifacts/{ident}", dependencies=[Depends(require_key)])
    async def artifact_delete(ident: str) -> dict[str, Any]:
        try:
            await asyncio.to_thread(artifacts.delete, ident)
        except artifacts.BadArtifactId:
            raise HTTPException(status_code=404, detail="no such artifact")
        return {"ok": True, "id": ident}

    @app.post("/api/artifacts/forget_window", dependencies=[Depends(require_master)])
    async def artifact_forget_window(request: Request) -> dict[str, Any]:
        """Delete the artifacts made in the last `minutes` (body `{minutes}`, 1 to 1440): the
        Hermes `forget_recent` tool's door, after the owner approved it there. Master key only: a
        paired device's key must not be able to wipe the day's files. Answers the count, or a
        503 with a fixed text when any delete failed, so the caller never reports a half erase."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        minutes = body.get("minutes") if isinstance(body, dict) else None
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
            raise HTTPException(status_code=400, detail="minutes is a whole number from 1 to 1440")
        try:
            count = await asyncio.to_thread(artifacts.delete_window, minutes)
        except OSError as exc:
            log.error("artifacts: forget_window did not finish: %s", exc)
            raise HTTPException(status_code=503, detail="some artifacts could not be erased")
        return {"minutes": minutes, "artifacts": count}

    def _pair_url(request: Request) -> str:
        return settings.public_url or str(request.base_url).rstrip("/")

    @app.get("/api/pair", dependencies=[Depends(require_key)])
    async def pair(request: Request) -> dict[str, Any]:
        # The phone probes this with whatever key it holds; only the master key reads the
        # master key back, or any device key could fetch it and outlive its revoke.
        answer: dict[str, Any] = {"url": _pair_url(request), "model": settings.model, "kind": "hermes-adapter"}
        ident = getattr(request.state, "device", None)
        if ident is None:
            answer["key"] = settings.api_key
        else:
            # Which device this key is: the console admits a phone's page load by it and
            # tags the session it opens, so a revoke can end that session too.
            answer["device"] = await asyncio.to_thread(device_keys.describe, ident)
        return answer

    @app.post("/api/pair", dependencies=[Depends(require_master)])
    async def pair_mint(request: Request) -> dict[str, Any]:
        """A new device's own key, minted by the console's pairing claim; shown once."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        name, kind, house = (body.get(k) for k in ("name", "kind", "house_token"))
        try:
            device, key = await asyncio.to_thread(
                device_keys.mint, *(v if isinstance(v, str) else "" for v in (name, kind, house)))
        except OSError as exc:
            log.error("device keys: cannot write %s: %s", device_keys.ROOT, exc)
            raise HTTPException(status_code=503, detail="cannot store device keys")
        return {"url": _pair_url(request), "key": key, "model": settings.model,
                "kind": "hermes-adapter", "device": device}

    @app.post("/api/approval/key", dependencies=[Depends(require_key)])
    async def approval_key(request: Request) -> dict[str, Any]:
        """A paired phone registers the key its fingerprint unlocks (T-0945); first one wins."""
        ident = getattr(request.state, "device", None)
        if not ident:
            # The master key names no device, so there is no record to keep a key on.
            raise HTTPException(status_code=403, detail="only a paired device registers an approval key")
        try:
            body = await request.json()
        except ValueError:
            body = {}
        public = body.get("public_key") if isinstance(body, dict) else None
        if not isinstance(public, str) or not approval_proof.valid_key(public):
            raise HTTPException(status_code=400, detail="public_key must be a base64 DER P-256 key")
        try:
            outcome = await asyncio.to_thread(device_keys.set_approval_key, ident, public)
        except OSError as exc:
            log.error("device keys: cannot write %s: %s", device_keys.ROOT, exc)
            raise HTTPException(status_code=503, detail="cannot store device keys")
        if outcome == "gone":
            raise HTTPException(status_code=401, detail="Unauthorized")
        if outcome == "not-a-phone":
            raise HTTPException(status_code=403, detail="only a phone registers an approval key")
        if outcome == "second-signer":
            # Its own status: the phone says something different from a 409, because pairing
            # again does not help here and revoking the other phone does.
            raise HTTPException(status_code=423, detail="another phone already signs approvals; "
                                "revoke it in Devices before this one can")
        if outcome == "conflict":
            raise HTTPException(status_code=409, detail="this device already has an approval key; pair it again")
        return {"ok": True}

    @app.post("/api/approval/proof", dependencies=[Depends(require_master)])
    async def approval_proof_ask(request: Request) -> dict[str, Any]:
        """The approval gate's question for a sensitive call: held until a phone signs it.

        Master key only (Hermes holds it): a device key must not put asks on every screen.
        ``unavailable`` when no phone has a key yet; the gate then asks the ordinary way.
        ``error`` when one had and the key table is lost or damaged; the gate then refuses.
        """
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        tool, message, rule_key, session = (
            str(body[k]) if isinstance(body.get(k), str) else "" for k in ("tool", "message", "rule_key", "session_id"))
        if not await asyncio.to_thread(desk.available):
            if await asyncio.to_thread(device_keys.approval_keys_lost):
                # A phone had a key and the table no longer yields one: a fault must not turn
                # a fingerprint into a tap, so the gate is told "error" and refuses.
                log.error("approval proof: a phone registered a key but %s yields none; refusing",
                          device_keys.ROOT / device_keys.FILE)
                return {"choice": "error"}
            return {"choice": "unavailable"}
        wait = body.get("wait_s")
        try:
            choice = await desk.ask(session, tool, message, rule_key,
                                    float(wait) if isinstance(wait, (int, float)) else approval_proof.WAIT_S)
        except ValueError:
            raise HTTPException(status_code=400, detail="rule_key is not a sensitive ask's")
        log.info("sensitive approval for %s: %s", tool[:64], choice)
        return {"choice": choice}

    @app.get("/api/devices/keys", dependencies=[Depends(require_master)])
    async def device_keys_list() -> dict[str, Any]:
        return {"keys": await asyncio.to_thread(device_keys.list_keys)}

    @app.delete("/api/devices/keys/{ident}", dependencies=[Depends(require_master)])
    async def device_keys_revoke(ident: str) -> dict[str, Any]:
        try:
            gone = await asyncio.to_thread(device_keys.revoke, ident)
        except OSError as exc:
            log.error("device keys: cannot write %s: %s", device_keys.ROOT, exc)
            raise HTTPException(status_code=503, detail="cannot store device keys")
        if not gone:
            raise HTTPException(status_code=404, detail="no such device key")
        # Hang up what it holds open: a phone keeps /ws for days, and "revoked" must not
        # mean "revoked at the next reconnect" (jarvis-core's token revoke does the same).
        for ws in device_sockets.pop(ident, set()):
            await _quietly("hanging up a revoked device", ws.close(code=4401), logging.DEBUG)
        return {"ok": True, "id": ident}

    # ------------------------------------------------------------- HTTP proxies
    async def _proxy(request: Request, base: str, path: str, headers: dict[str, str],
                     body: bytes | None = None) -> Response:
        if _climbs_out(path):
            raise HTTPException(status_code=404, detail="Not Found")
        url = f"{base}{path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        fwd = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        fwd.update(headers)
        if body is None:
            body = await request.body()
        req = client.build_request(request.method, url, headers=fwd, content=body)
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc.__class__.__name__}")
        resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}

        async def body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(body_iter(), status_code=upstream.status_code, headers=resp_headers,
                                 media_type=upstream.headers.get("content-type"))

    # ------------------------------------------------------------ model manager
    # The console's Models panel (tests/contracts/models-api.json). The manager sits next to
    # llama-swap on port 8001 unless MODELS_MANAGER_URL says otherwise; with neither set the
    # panel is off (a 503 below), not a request to "//None:8001".
    ls_url = urlsplit(settings.llama_swap_url)
    manager_url = settings.models_manager_url or (
        ls_url._replace(netloc=f"{ls_url.hostname}:8001").geturl().rstrip("/") if ls_url.hostname else "")

    @app.get("/api/verified", dependencies=[Depends(require_key)])
    async def verified_board() -> dict[str, Any]:
        """What the deploy's smoke last proved, check by check (T-0580)."""
        return verified_mod.board()

    @app.put("/api/verified", dependencies=[Depends(require_key)])
    async def verified_record(request: Request) -> dict[str, Any]:
        """One smoke run from ``scripts/smoke-report.py``. A body that is not a run is a 422."""
        raw = await request.body()
        try:
            run = verified_mod.parse(json.loads(raw)) if len(raw) <= 16 * 1024 else None
        except ValueError:
            run = None
        if run is None:
            raise HTTPException(status_code=422, detail="not a smoke run: tag, at, passed (known checks), failed")
        verified_mod.record(run)
        return verified_mod.board()

    @app.get("/api/board", dependencies=[Depends(require_key)])
    async def board() -> dict[str, Any]:
        """The console's Board (T-0733): the aura-board plugin's counts and the models' live load.

        Each source answers on its own with a short budget: a slow models server leaves the
        board's counts standing, with that panel marked unavailable.
        """
        async def live(url: str, path: str, headers: dict[str, str]) -> tuple[Any, dict[str, Any]]:
            if not url:
                return None, board_mod.source("unavailable", why="not configured")
            try:
                r = await client.get(f"{url}{path}", timeout=2.0, headers=headers)
                r.raise_for_status()
                return r.json(), board_mod.source("ok", 0)
            except (httpx.HTTPError, ValueError) as exc:
                return None, board_mod.source("unavailable", why=f"{path}: {type(exc).__name__}")

        swap_headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        (swap, swap_src), (cards, cards_src) = await asyncio.gather(
            live(settings.llama_swap_url, "/running", swap_headers),
            live(manager_url if settings.models_api_key else "", "/api/models/gpus",
                 {"authorization": f"Bearer {settings.models_api_key}"}),
        )
        data, hermes_src = board_mod.read()
        gpus = cards.get("gpus") if isinstance(cards, dict) else None
        return {
            "at": time.time(),
            "sources": {"hermes": hermes_src, "models": swap_src, "gpus": cards_src},
            "hermes": data,
            "models": board_mod.running(swap) if swap is not None else None,
            "gpus": gpus if isinstance(gpus, list) else None,
            # What the deploy's smoke last proved (T-0580): the board's seventh panel.
            "verified": verified_mod.board(),
        }

    @app.api_route("/api/models", methods=["GET", "POST"], dependencies=[Depends(require_key)])
    @app.api_route("/api/models/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"],
                   dependencies=[Depends(require_key)])
    async def models_manager(request: Request, rest: str = "") -> Response:
        # GET /api/models/<name> was the phone's wake-word fetch (model_file below) before the
        # manager existed; the manager's only GETs are the list, /gpus and /search, so any
        # other GET stays with model_file (which also keeps its 404 for a path that is no name).
        if request.method == "GET" and rest not in ("", "gpus", "search"):
            return await model_file(rest)
        # The manager's own key replaces the app's bearer: the manager never sees the apps' key,
        # and nothing else on the compose network (the model's tools run in `jarvis`) can drive it.
        if not settings.models_api_key:
            raise HTTPException(status_code=503, detail="The model manager is off: MODELS_API_KEY is not set.")
        if not manager_url:
            raise HTTPException(status_code=503, detail="The model manager is off: LLAMA_SWAP_URL is not set.")
        return await _proxy(request, manager_url, "/api/models" + (f"/{rest}" if rest else ""),
                            {"authorization": f"Bearer {settings.models_api_key}"})

    api_headers = {"Authorization": f"Bearer {settings.hermes_api_key}"}
    serve_headers = {"X-Hermes-Session-Token": settings.hermes_serve_token}

    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "DELETE", "PATCH"], dependencies=[Depends(require_key)])
    async def v1(path: str, request: Request) -> Response:
        return await _proxy(request, settings.hermes_api_url, f"/v1/{path}", api_headers)

    @app.api_route("/api/sessions{path:path}", methods=["GET", "POST", "DELETE", "PATCH"], dependencies=[Depends(require_key)])
    async def sessions(path: str, request: Request) -> Response:
        return await _proxy(request, settings.hermes_api_url, f"/api/sessions{path}", api_headers)

    @app.api_route("/api/jobs{path:path}", methods=["GET", "POST", "DELETE", "PATCH"], dependencies=[Depends(require_key)])
    async def jobs(path: str, request: Request) -> Response:
        return await _proxy(request, settings.hermes_api_url, f"/api/jobs{path}", api_headers)

    # CosyVoice never refuses to start: with no usable GPU it loads on the CPU
    # and a line takes minutes (2026-10-06: a host driver update, ~170 s a
    # line). Its /health says so (`device`), and while it does the house's own
    # TTS (Piper, about a second) speaks instead. Asked at most every 30 s.
    voice_device: dict[str, Any] = {"device": "", "at": -1e9}

    async def _cosyvoice_on_cpu() -> bool:
        if not settings.house_url or not settings.house_token:
            return False
        if time.monotonic() - voice_device["at"] > 30:
            base = settings.speech_url.rsplit("/v1/", 1)[0]
            ok, health = await _get_json(client, f"{base}/health", timeout=2.0)
            voice_device.update(device=str(health.get("device") or "") if ok else "", at=time.monotonic())
        return voice_device["device"] == "cpu"

    async def _house_wav(text: str) -> bytes | None:
        """One line from the house's TTS as WAV, or None if the house cannot say it."""
        try:
            r = await client.post(f"{settings.house_url.rstrip('/')}/api/voice/tts", json={"text": text},
                                  headers={"Authorization": f"Bearer {settings.house_token}"}, timeout=30.0)
        except httpx.HTTPError as exc:
            log.warning("house voice unreachable: %s", exc.__class__.__name__)
            return None
        if r.status_code != 200:
            log.warning("house voice answered %s", r.status_code)
            return None
        return r.content

    # Declared before the catch-all below: Starlette matches routes in order.
    @app.post("/api/audio/speak", dependencies=[Depends(require_key)])
    async def speak(request: Request) -> Response:
        """One line of speech, in a persona's voice.

        `{"text", "voice"?}` -> `{"ok", "voice", "data_url"}` (MP3), the shape the
        console and the apps already read from hermes serve's speak. With the engine
        on `cosyvoice` the shim is called directly, because every persona -- and
        every subagent, which is one of them -- speaks with its own reference clip
        and hermes serve's speak knows one voice. Any other engine relays to
        hermes serve as before, and `voice` is ignored there: Piper has one voice.
        """
        if settings.tts_engine != "cosyvoice":
            return await _proxy(request, settings.hermes_serve_url, "/api/audio/speak", serve_headers)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="a JSON body is required")
        text = spoken(str((body or {}).get("text") or ""), taught_from(voice_speech()[0]))
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        voice = line_voice(body)
        # A voice is a file name on the shim's side; only a plain name reaches it.
        if not VOICE_NAME.fullmatch(voice):
            raise HTTPException(status_code=400, detail="voice must be a short plain name")
        if await _cosyvoice_on_cpu():
            wav = await _house_wav(text)
            if wav is not None:
                data = base64.b64encode(wav).decode("ascii")
                return JSONResponse({"ok": True, "voice": voice, "engine": "house",
                                     "data_url": f"data:audio/wav;base64,{data}"},
                                    headers={"x-jarvis-tts-engine": "house"})
        voice = said_as(voice)
        try:
            upstream = await client.post(
                settings.speech_url,
                json={"model": "kokoro", "input": text, "voice": voice, "response_format": "mp3"},
            )
            if upstream.status_code == 404 and voice != DEFAULT_VOICE:
                # No reference clip for this voice: said as JARVIS and named (x-jarvis-voice), not
                # a 404 that every client turned into silence (T-0807).
                missing_voices[voice] = time.monotonic()
                voice = DEFAULT_VOICE
                upstream = await client.post(
                    settings.speech_url,
                    json={"model": "kokoro", "input": text, "voice": voice, "response_format": "mp3"},
                )
        except httpx.HTTPError as exc:
            return await _spoken_by_hermes(request, f"the voice is unreachable: {exc.__class__.__name__}")
        if upstream.status_code == 404:
            raise HTTPException(status_code=404, detail=f"no voice named {voice}")
        if upstream.status_code != 200:
            # 503 while it warms, 5xx when it is broken: Hermes' own speak is
            # the fallback (Piper, when `TTS_PROVIDER=piper`; otherwise the
            # same shim, and its answer is what the caller gets). The reply
            # says which engine spoke, so the HUD can show it (V3 PART 2).
            detail = upstream.text[:200] if upstream.text else ""
            return await _spoken_by_hermes(request, f"the voice answered {upstream.status_code} {detail}".strip())
        data = base64.b64encode(upstream.content).decode("ascii")
        return JSONResponse({"ok": True, "voice": voice, "engine": "cosyvoice", "data_url": f"data:audio/mpeg;base64,{data}"},
                            headers={"x-jarvis-voice": voice})

    @app.post("/api/audio/speak-pcm", dependencies=[Depends(require_key)])
    async def speak_pcm(request: Request) -> Response:
        """One line of speech as it is made: 16-bit mono PCM, chunked.

        `{"text", "voice"?}` -> `audio/pcm` with `x-sample-rate`, the first
        piece out while the rest is still being synthesised. This is what
        time-to-first-word is measured on: `speak` above answers only when the
        whole line exists, which for CosyVoice is about six tenths of the
        line's own length (measured 2026-09-10: 3.9 s for a 97-character
        sentence, 10 s for 247), and the owner heard that as "slower than
        Piper". Only the CosyVoice engine streams; any other answers 404 so a
        caller falls back to `speak`. The shim's 503 while it warms and its
        404 for an unknown voice pass through with their bodies.
        """
        started = time.monotonic()
        if settings.tts_engine != "cosyvoice":
            raise HTTPException(status_code=404, detail="this voice does not stream; use /api/audio/speak")
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="a JSON body is required")
        pronunciations, quiet_span = voice_speech()
        text = spoken(str((body or {}).get("text") or ""), taught_from(pronunciations))
        # Quiet hours: slower and softer, and never a cached line (made at full voice) nor one
        # kept for the daytime (T-0587).
        quiet = quiet_now(quiet_span, _clock())
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        voice = line_voice(body)
        if not VOICE_NAME.fullmatch(voice):
            raise HTTPException(status_code=400, detail="voice must be a short plain name")
        # The rate the caller plays at (the car's guidance channel, T-0920): resampled here, so
        # the bytes the phone's link carries are the ones the car plays. Kept at the voice's own
        # rate in the phrase cache.
        want = body.get("rate") if isinstance(body, dict) else None
        if want is not None and (isinstance(want, bool) or want not in pcm_rate.RATES):
            raise HTTPException(status_code=400, detail=f"rate must be one of {sorted(pcm_rate.RATES)}")
        want_hz = int(want) if want is not None else 0

        def at(rate: str | int) -> pcm_rate.Resampler | None:
            return pcm_rate.Resampler(int(rate), want_hz) if want_hz and int(rate) != want_hz else None

        def whole(rate: str | int, pcm: bytes) -> tuple[str, bytes]:
            conv = at(rate)
            return (str(want_hz), conv.feed(pcm)) if conv else (str(rate), pcm)

        # A private line (T-0462) may be said from the cache but is never put in it, and never
        # goes to the house: its engine is not ours to vouch for, so it waits for CosyVoice.
        private = isinstance(body, dict) and body.get("private") is True
        # Before the CPU check: a line CosyVoice already made beats the house's fallback voice.
        voice = said_as(voice)
        hit = None if quiet else phrases.get(voice, text)
        if hit is not None:
            hit_rate, hit_pcm = whole(hit[0], hit[1])
            return Response(hit_pcm, media_type="audio/pcm", headers={
                "x-sample-rate": hit_rate, "x-jarvis-tts-engine": "cosyvoice", "x-jarvis-tts-cache": "hit",
                "x-jarvis-voice": voice})
        if not private and await _cosyvoice_on_cpu():
            wav = await _house_wav(text)
            if wav is not None:
                with wave.open(io.BytesIO(wav)) as w:
                    house_rate, pcm = whole(w.getframerate(), w.readframes(w.getnframes()))
                if quiet:
                    pcm = Softer().feed(pcm)
                return Response(pcm, media_type="audio/pcm",
                                headers={"x-sample-rate": house_rate, "x-jarvis-tts-engine": "house"})
        async def open_voice(chunk: str) -> httpx.Response:
            req = client.build_request(
                "POST",
                settings.speech_url,
                json={"model": "kokoro", "input": chunk, "voice": voice, "response_format": "pcm",
                      **({"speed": QUIET_SPEED} if quiet else {})},
            )
            try:
                up = await client.send(req, stream=True)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=503, detail=f"the voice is unreachable: {exc.__class__.__name__}")
            if up.status_code != 200:
                detail = (await up.aread()).decode("utf-8", "replace")[:200]
                await up.aclose()
                raise HTTPException(status_code=up.status_code, detail=detail or f"the voice answered {up.status_code}")
            return up

        # The first clause alone goes first, so its first word is not waiting on a long
        # sentence (T-0605); the rest is asked for once the clause has been sent.
        # Only when the client says this is the start of a reply: a later sentence would pay a
        # second hop's wait between its clauses for nothing.
        first_of_reply = isinstance(body, dict) and body.get("first_clause") is True
        cut = split_first_clause(text) if first_of_reply else None
        try:
            upstream = await open_voice(cut[0] if cut else text)
        except HTTPException as exc:
            # As speak: no reference clip is JARVIS's voice, named, not silence (T-0807). The
            # line is then kept under JARVIS's voice, which is whose it is.
            if exc.status_code != 404 or voice == DEFAULT_VOICE:
                raise
            missing_voices[voice] = time.monotonic()
            voice = DEFAULT_VOICE
            upstream = await open_voice(cut[0] if cut else text)

        rate = upstream.headers.get("x-sample-rate", "24000")
        keep = not private and not quiet and len(text) <= tts_cache.PhraseCache.MAX_CHARS
        conv = at(rate)
        soft = Softer() if quiet else None

        async def pieces():
            made: list[bytes] = []
            first = True
            current = upstream
            try:
                while True:
                    async for piece in current.aiter_bytes():
                        # Only lines made now: a cache hit or the house's voice is no reply's timing.
                        if first:
                            took = (time.monotonic() - started) * 1000
                            voice_ms["first_pcm_ms"].append(took)
                            deadlines.check("first_pcm_ms", took, f"voice={voice}")
                            first = False
                        if keep:
                            made.append(piece)
                        out = conv.feed(piece) if conv else piece
                        if soft and out:
                            out = soft.feed(out)
                        if out:
                            yield out
                    await current.aclose()
                    if not cut or current is not upstream:
                        break
                    current = await open_voice(cut[1])
            finally:
                await current.aclose()
            # Reached only when the stream ended on its own: a client gone mid-line or the
            # voice failing raises out of the loop, and a cut line is never kept.
            if keep:
                phrases.put(voice, text, rate, b"".join(made))

        return StreamingResponse(
            pieces(),
            media_type="audio/pcm",
            headers={"x-sample-rate": str(want_hz) if conv else rate, "x-jarvis-tts-engine": "cosyvoice",
                     "x-jarvis-voice": voice},
        )

    @app.post("/api/client-error", dependencies=[Depends(require_key)])
    async def client_error(request: Request) -> Response:
        """One uncaught console error (T-0475) -> one warning line, so a console broken in the
        phone's WebView shows up in the adapter's log. The console sends none during a private
        chat (errorReport.ts). Does NOT cover: an error's message quoting what the page held
        (a JSON parse error can); it is cut to 300 characters, not scrubbed."""
        raw = await request.body()
        if len(raw) > CLIENT_ERROR_MAX_BYTES:
            raise HTTPException(status_code=413, detail="an error report is at most 4 KB")
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="a JSON object is required")
        # Counted only once it is a report: junk must not spend another device's slots.
        now = time.monotonic()
        _CLIENT_ERRORS[:] = [t for t in _CLIENT_ERRORS if now - t < 60]
        if len(_CLIENT_ERRORS) >= CLIENT_ERRORS_PER_MINUTE:
            raise HTTPException(status_code=429, detail="too many error reports this minute")
        _CLIENT_ERRORS.append(now)
        source = _one_line(body.get("source"), 200)
        # A rejection, or a cross-origin "Script error.", has no place to name.
        where = f" at {source}:{_one_line(body.get('line'), 10)}" if source else ""
        log.warning("console %s: %s%s", _one_line(body.get("kind") or "error", 20),
                    _one_line(body.get("message"), 300), where)
        return Response(status_code=204)

    @app.post("/api/private/chat", dependencies=[Depends(require_key)])
    async def private_chat(request: Request) -> Response:
        """A private conversation (T-0462): `{"messages": [{role, content}...]}` -> SSE of
        `{"content"}` pieces, then `[DONE]`.

        Straight to the model, not through Hermes, because Hermes writes every turn to its
        session store, its logs and its memory. Nothing here is logged (the access line is
        filtered too) and nothing is kept: the conversation lives in the caller's memory and
        is sent whole each turn. Does NOT cover: the model server's own GPU cache while it
        runs, tools (there are none here), or the caller's device.
        """
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="a JSON body is required")
        turns = _private_turns(body.get("messages") if isinstance(body, dict) else None)
        if turns is None:
            raise HTTPException(status_code=400, detail="messages must be user/assistant lines ending with the user's")
        model = body.get("model", settings.model)
        if "model" in body and not (isinstance(model, str) and _MODEL_NAME.fullmatch(model)):
            raise HTTPException(status_code=400, detail="model must be a model's name")
        req = client.build_request(
            "POST", f"{settings.llama_swap_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {},
            json={"model": model, "stream": True,
                  "messages": [{"role": "system", "content": PRIVATE_PERSONA}, *turns],
                  "chat_template_kwargs": {"enable_thinking": False}},
        )
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"the model is unreachable: {exc.__class__.__name__}")
        if upstream.status_code != 200:
            await upstream.aclose()
            raise HTTPException(status_code=502, detail=f"the model answered {upstream.status_code}")

        async def pieces():
            # Only the model's own [DONE] ends a reply: a stream that just closes was cut off,
            # and passing on [DONE] would show half a reply as a whole one.
            finished = False
            try:
                async for line in upstream.aiter_lines():
                    finished = finished or line.strip() == "data: [DONE]"
                    piece = _delta_content(line)
                    if piece:
                        yield f"data: {json.dumps({'content': piece})}\n\n"
            except httpx.HTTPError:
                finished = False
            finally:
                await upstream.aclose()
            yield ("data: [DONE]\n\n" if finished
                   else f"data: {json.dumps({'error': 'the model stopped answering'})}\n\n")

        return StreamingResponse(pieces(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def _prewarm_phrases() -> None:
        """The fast path's fixed lines in the primary persona's voice, made once the voice answers,
        and again after each switch of the primary (T-1320).

        Waits out the shim's warm-up (503) for up to ten minutes; stops on any other refusal
        and while CosyVoice is on the CPU, where the house speaks instead. A switch is picked
        up within PERSONA_WATCH_S. Does not cover a voice that was refused or never answered:
        it is not retried until the next switch, and the lines are then kept as first said.
        """
        if settings.tts_engine != "cosyvoice":
            return
        warmed = None
        while True:
            if (active := read_persona()["active"]) != warmed:
                warmed = active
                await _prewarm_persona(active)
            # The persona is one small local file: polling it every few seconds costs nothing,
            # and a file watcher would be a dependency for a switch that happens a few times a day.
            await asyncio.sleep(PERSONA_WATCH_S)

    async def _prewarm_persona(name: str) -> None:
        voice = THEME_VOICES.get(name, name)
        deadline = time.monotonic() + PREWARM_DEADLINE_S
        for line in tts_cache.prewarm_lines():
            text = spoken(line)
            while True:
                if time.monotonic() > deadline:
                    log.warning("tts pre-warm gave up: the voice at %s never answered in %ss",
                                settings.speech_url, PREWARM_DEADLINE_S)
                    return
                if await _cosyvoice_on_cpu():
                    return
                try:
                    r = await client.post(settings.speech_url, timeout=60.0, json={
                        "model": "kokoro", "input": text, "voice": voice, "response_format": "pcm"})
                except httpx.HTTPError:
                    r = None
                if r is not None and r.status_code == 200:
                    phrases.put(voice, text, r.headers.get("x-sample-rate", "24000"), r.content)
                    break
                if r is not None and r.status_code == 404 and voice != DEFAULT_VOICE:
                    # No clip for the persona: its lines will be said as JARVIS, so those are made.
                    missing_voices[voice] = time.monotonic()
                    voice = DEFAULT_VOICE
                    continue
                if r is not None and r.status_code != 503:
                    log.warning("tts pre-warm stopped: the voice answered %s at %s", r.status_code, settings.speech_url)
                    return
                await asyncio.sleep(30)

    @app.on_event("startup")
    async def _start_prewarm() -> None:
        app.state.prewarm = asyncio.create_task(_prewarm_phrases())

    @app.on_event("shutdown")
    async def _stop_prewarm() -> None:  # pragma: no cover - lifecycle
        app.state.prewarm.cancel()

    async def _spoken_by_hermes(request: Request, why: str) -> Response:
        """Relay to hermes serve's speak, and say why the shim was not used."""
        log.warning("speak: falling back to hermes serve: %s", why)
        relayed = await _proxy(request, settings.hermes_serve_url, "/api/audio/speak", serve_headers)
        # Only the failure's reason rides in a header: the body is streamed
        # through as hermes serve wrote it, and rewriting it would mean
        # buffering audio to add one field.
        relayed.headers["x-jarvis-tts-engine"] = "hermes"
        relayed.headers["x-jarvis-tts-fallback"] = why.replace("\n", " ")[:200]
        return relayed

    speakers = speaker_mod.Speakers(key=settings.api_key)
    unrecognised = speaker_mod.Unrecognised()
    surfaces = surface_mod.Surfaces()
    efforts = effort_mod.Efforts(reasoning_efforts)

    async def _verify_speaker(data_url: str, surface: str = "console", learn: bool = False) -> dict[str, Any] | None:
        """Ask the house who this is. None when it cannot say, for any reason.

        Only WAV or raw PCM reaches the gate — that is what it can embed — and
        an answer that is not a verdict (nobody enrolled: 409, the house down,
        a timeout) is no answer: the turn is the key holder's, which is what
        every turn was before this existed. Failing open here mirrors
        jarvis-core's own rule that a verifier that crashes must not lock the
        owner out; the gate being *strict* is its ``mode``, not our error path.
        ``learn`` lets the house keep a print of a turn it is sure of, for ``surface``'s
        microphone (T-0771); a private turn never asks it to.
        """
        if not settings.house_token or not settings.house_url:
            return None
        head, sep, payload = data_url.partition(",")
        mime = head[5:].split(";")[0].strip().lower() if head.startswith("data:") else ""
        if not sep or mime not in ("audio/wav", "audio/x-wav", "audio/wave", "audio/pcm", "audio/l16"):
            return None
        try:
            audio = base64.b64decode(payload, validate=False)
        except (ValueError, TypeError):
            return None
        try:
            reply = await client.post(
                f"{settings.house_url.rstrip('/')}/api/voice/speaker/verify",
                params={"source": surface, "learn": "1" if learn else "0"},
                content=audio,
                headers={"Authorization": f"Bearer {settings.house_token}",
                         "Content-Type": "audio/wav" if "wav" in mime or "wave" in mime else "audio/pcm"},
                timeout=httpx.Timeout(10.0, connect=3.0),
            )
        except httpx.HTTPError as exc:
            log.warning("speaker gate unreachable: %s", exc.__class__.__name__)
            return None
        if reply.status_code != 200:
            return None
        try:
            return speaker_mod.speaker_of(reply.json())
        except ValueError:
            return None

    def _surface_of(raw: bytes) -> str:
        """Which microphone the client says this was ("car", "android", "console", "desktop")."""
        try:
            said = str((json.loads(raw) or {}).get("surface") or "")
        except (ValueError, AttributeError):
            said = ""
        return said if said in ("car", "android", "console", "desktop") else "console"

    def _audio_of(data_url: str) -> tuple[bytes, str]:
        head, sep, payload = data_url.partition(",")
        mime = head[5:].split(";")[0].strip().lower() if head.startswith("data:") else ""
        if not sep or mime not in ("audio/wav", "audio/x-wav", "audio/wave", "audio/pcm", "audio/l16"):
            return b"", ""
        try:
            return base64.b64decode(payload, validate=False), ("audio/wav" if "wav" in mime or "wave" in mime
                                                                else "audio/pcm")
        except (ValueError, TypeError):
            return b"", ""

    @app.get("/api/models/{name}", dependencies=[Depends(require_key)])
    async def model_file(name: str) -> Response:
        """A wake-word model for the phone's on-device detector, from the house.

        jarvis-core serves openWakeWord's ONNX files at /api/models/<name>, and the
        phone's one server is this adapter, which answered 404: the phone could never
        fetch them, so "Hey Jarvis" fell back to streaming 32 KB/s of microphone audio
        to the server around the clock (the owner, 2026-10-08: "Jarvis kills my
        battery"). Only a plain .onnx name is passed on, so no other house path is
        reachable through here.
        """
        if not MODEL_NAME.fullmatch(name):
            raise HTTPException(status_code=404, detail="not a model")
        if not settings.house_url or not settings.house_token:
            raise HTTPException(status_code=503, detail="the house is not configured")
        try:
            r = await client.get(f"{settings.house_url.rstrip('/')}/api/models/{name}",
                                 headers={"Authorization": f"Bearer {settings.house_token}"}, timeout=60.0)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"the house is unreachable: {exc.__class__.__name__}")
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code if r.status_code == 404 else 502,
                                detail=f"the house answered {r.status_code}")
        return Response(r.content, media_type="application/octet-stream")

    @app.get("/api/phone/reachable", dependencies=[Depends(require_key)])
    async def phone_reachable() -> JSONResponse:
        """Is a phone on the house's device channel (or heard in the last ten minutes): one boolean.

        The leave-by job's gate script runs with the API key and no house token, so it asks here
        instead of the house. Read-only; the body is the same for a phone that is off and one
        never paired and names no device. An unreachable or confused house is a 502, never a
        "no": the gate must wake the model then, not skip the nudge. Does not say the phone
        will answer an action.
        """
        if not settings.house_url or not settings.house_token:
            raise HTTPException(status_code=503, detail="the house is not configured")
        try:
            r = await client.get(f"{settings.house_url.rstrip('/')}/api/config/companion/reachable",
                                 headers={"Authorization": f"Bearer {settings.house_token}"}, timeout=5.0)
            body = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"the house is unreachable: {exc.__class__.__name__}")
        if not isinstance(body, dict) or not isinstance(body.get("reachable"), bool):
            raise HTTPException(status_code=502, detail=f"the house answered {r.status_code} without a yes or no")
        return JSONResponse({"reachable": body["reachable"]})

    async def _house_regions(request: Request, path: str) -> Response:
        """One of the house's region calls (tests/contracts/map-pack.json), streamed both ways
        so a pack of some hundred MB passes with its Range and is never held here."""
        if not settings.house_url or not settings.house_token:
            raise HTTPException(status_code=503, detail="the house is not configured")
        body = await _capped_body(request)
        return await _proxy(request, settings.house_url.rstrip("/"), path,
                            {"Authorization": f"Bearer {settings.house_token}"}, body)

    @app.api_route("/api/maps/regions", methods=["GET", "POST"], dependencies=[Depends(require_key)])
    async def house_map_regions(request: Request) -> Response:
        """The regions the house holds, and an ask for one more (T-0979: the phone's Maps settings)."""
        return await _house_regions(request, "/api/maps/regions")

    @app.get("/api/maps/catalogue", dependencies=[Depends(require_key)])
    async def house_map_catalogue(request: Request) -> Response:
        return await _house_regions(request, "/api/maps/catalogue")

    @app.delete("/api/maps/regions/{region}", dependencies=[Depends(require_key)])
    async def house_map_region_remove(region: str, request: Request) -> Response:
        if not MAPS_REGION_ID.fullmatch(region):
            raise HTTPException(status_code=404, detail="no such region")
        return await _house_regions(request, f"/api/maps/regions/{region}")

    @app.get("/api/maps/packs/{region}", dependencies=[Depends(require_key)])
    async def house_map_pack(region: str, request: Request) -> Response:
        """A region's pack, for the phone to keep: the car's map with the house out of reach."""
        if not MAPS_REGION_ID.fullmatch(region):
            raise HTTPException(status_code=404, detail="no such region")
        return await _house_regions(request, f"/api/maps/packs/{region}")

    @app.api_route("/api/maps/{op}", methods=["GET", "POST"], dependencies=[Depends(require_key)])
    async def house_maps(op: str, request: Request) -> Response:
        """The house's route, map-match and buildings, for the car's hologram drive (HouseMaps.kt).

        A phone paired to this adapter built no maps client, so the car's ground drew no
        buildings and matched nothing (the owner, 2026-11-10: "why do buildings and the map not
        show in the car"). Only [MAPS_OPS] by their methods, and a bbox of four numbers, reach
        the house; its status and body come back as they are, since the phone tells "no route"
        (a 404 with a code) from "the house is away" by them. Not covered: anything else of
        the house's maps (places, tiles), which the phone does not ask for here.
        """
        if MAPS_OPS.get(op) != request.method:
            raise HTTPException(status_code=404, detail="no such maps call")
        url = f"{settings.house_url.rstrip('/')}/api/maps/{op}"
        body = None
        if op in ("buildings", "live"):  # live (T-1108): the work zones under way in the box
            bbox = request.query_params.get("bbox", "")
            if not MAPS_BBOX.fullmatch(bbox):
                raise HTTPException(status_code=404, detail="a bbox is west,south,east,north")
            url += f"?bbox={bbox}"
        if request.method == "POST":
            body = await _capped_body(request)
        if not settings.house_url or not settings.house_token:
            raise HTTPException(status_code=503, detail="the house is not configured")
        try:
            r = await client.request(request.method, url, content=body, timeout=20.0,
                                     headers={"Authorization": f"Bearer {settings.house_token}",
                                              **({"Content-Type": "application/json"} if body is not None else {})})
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"the house is unreachable: {exc.__class__.__name__}")
        content, packed = r.content, {}
        # T-1074: a tile of footprints is JSON of coordinates, a megabyte in a town and a fifth
        # of that packed; this hop is the phone's mobile data, where the same bytes starved a
        # whole drive (2026-11-11). Only for a client that asked (OkHttp does, and unpacks).
        if op == "buildings" and r.status_code == 200 and "gzip" in request.headers.get("accept-encoding", ""):
            content = await asyncio.to_thread(gzip.compress, content, 5)
            packed = {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
        return Response(content, status_code=r.status_code, headers=packed,
                        media_type=r.headers.get("content-type", "application/json"))

    @app.get("/api/speaker/unrecognised", dependencies=[Depends(require_key)])
    async def unrecognised_list() -> JSONResponse:
        """Voices the gate refused in the last minutes that were close to someone enrolled."""
        return JSONResponse({"unrecognised": unrecognised.listing()})

    @app.post("/api/speaker/unrecognised/{claim}/mine", dependencies=[Depends(require_key)])
    async def unrecognised_mine(claim: str) -> JSONResponse:
        """"That was me": file the refused utterance as a print for its microphone.

        Enrolled through jarvis-core's own enrol (which refuses a sample with no pitch),
        under the person it was nearest and the surface it came from, so the car is next
        matched against the car's prints. The utterance is used once and dropped.
        """
        held = unrecognised.take(claim)
        if held is None:
            return JSONResponse({"ok": False, "error": "that voice is no longer held (they keep two hours)"},
                                status_code=404)
        if not settings.house_token or not settings.house_url:
            return JSONResponse({"ok": False, "error": "the house is not configured"}, status_code=503)
        try:
            reply = await client.post(
                f"{settings.house_url.rstrip('/')}/api/voice/speaker/enrol",
                params={"label": held["nearest"], "source": held["surface"]},
                content=held["audio"],
                headers={"Authorization": f"Bearer {settings.house_token}", "Content-Type": held["content_type"]},
                timeout=httpx.Timeout(20.0, connect=3.0),
            )
        except httpx.HTTPError as exc:
            return JSONResponse({"ok": False, "error": f"the house is unreachable: {exc.__class__.__name__}"},
                                status_code=502)
        log.info("a refused %s utterance was claimed as %s's (similarity %s): house answered %s",
                 held["surface"], held["nearest"], held["similarity"], reply.status_code)
        if reply.status_code != 200:
            try:
                detail = str(reply.json().get("detail") or reply.text[:200])
            except ValueError:
                detail = reply.text[:200]
            return JSONResponse({"ok": False, "error": detail}, status_code=reply.status_code)
        return JSONResponse({"ok": True, "label": held["nearest"], "source": held["surface"]})

    names_read: dict[str, Any] = {"at": float("-inf"), "states": [], "areas": []}

    async def _house_read(key: str, path: str) -> None:
        try:
            r = await client.get(f"{settings.house_url.rstrip('/')}{path}",
                                 headers={"Authorization": f"Bearer {settings.house_token}"}, timeout=1.0)
            if r.status_code == 200:
                names_read[key] = r.json()
            else:
                log.info("the house's %s: it answered %s", key, r.status_code)
        except (httpx.HTTPError, ValueError) as exc:
            log.info("the house's %s: %s", key, exc.__class__.__name__)

    async def _house_names() -> str:
        """The owner's contacts and places, the house's rooms, then its names. The house is
        read at most every HOUSE_NAMES_TTL_S, both lists at once; a read that fails keeps the
        last (or none) until the next, so a down house costs one try per period. The
        contacts are read at every turn (one small file)."""
        if settings.house_url and settings.house_token \
                and time.monotonic() - names_read["at"] >= HOUSE_NAMES_TTL_S:
            names_read["at"] = time.monotonic()
            await asyncio.gather(_house_read("states", "/api/states"),
                                 _house_read("areas", "/api/config/area_registry/list"))
        areas = names_read["areas"] if isinstance(names_read["areas"], list) else []
        rooms = [str(a["name"]) for a in areas if isinstance(a, dict) and a.get("name")]
        return house_names(names_read["states"], [*remembered_names(), *rooms])

    language_read: dict[str, Any] = {"at": float("-inf"), "language": None}

    async def _house_language() -> str | None:
        """The Speech language chosen on the console, read at most every
        HOUSE_LANGUAGE_TTL_S. A read that fails keeps the last answer (or none) until the
        next period; an answer of null is "nothing chosen" and clears it."""
        if settings.house_url and settings.house_token \
                and time.monotonic() - language_read["at"] >= HOUSE_LANGUAGE_TTL_S:
            language_read["at"] = time.monotonic()
            try:
                r = await client.get(f"{settings.house_url.rstrip('/')}/api/voice/language",
                                     headers={"Authorization": f"Bearer {settings.house_token}"},
                                     timeout=1.0)
                if r.status_code == 200:
                    language_read["language"] = chosen_language_of(r.json())
                else:
                    log.info("the house's voice language: it answered %s", r.status_code)
            except (httpx.HTTPError, ValueError) as exc:
                log.info("the house's voice language: %s", exc.__class__.__name__)
        return language_read["language"]

    async def _words_on_the_card(raw: bytes) -> dict[str, Any] | None:
        """Whisper on the voice's card (~0.7 s) before Hermes' CPU one (~3 s for a car
        request, 2026-10-08), primed with the house's names and asked for the Speech
        language chosen on the console (T-1487; an app that names one keeps its own; none
        chosen leaves the card's English). None on any failure -- no card, no weights, the
        shim down or slow -- and the caller asks Hermes as it always has; Hermes' Whisper
        keeps its fixed prompt and its own configured language, which this does not set."""
        names, language = await asyncio.gather(_house_names(), _house_language())
        if names or language:
            try:
                sent = json.loads(raw)
                if isinstance(sent, dict):
                    added: dict[str, Any] = {"names": names} if names else {}
                    if language and not sent.get("language"):
                        added["language"] = language
                    raw = json.dumps({**sent, **added}).encode()
            except ValueError:
                pass
        try:
            r = await client.post(settings.transcribe_url, content=raw,
                                  headers={"Content-Type": "application/json"},
                                  timeout=httpx.Timeout(STT_CARD_TIMEOUT_S, connect=1.0))
        except httpx.HTTPError as exc:
            log.info("whisper on the card unreachable (%s); Hermes transcribes", exc.__class__.__name__)
            return None
        if r.status_code != 200:
            log.info("whisper on the card answered %s; Hermes transcribes", r.status_code)
            return None
        try:
            body = r.json()
        except ValueError:
            return None
        if not isinstance(body, dict) or not isinstance(body.get("transcript"), str):
            return None
        return {"success": True, "transcript": body["transcript"], "provider": "whisper-gpu"}

    @app.post("/api/audio/transcribe", dependencies=[Depends(require_key)])
    async def transcribe(request: Request) -> Response:
        """Words from audio, and who said them.

        The audio goes to hermes serve for the words as it always has, and to
        the house's speaker gate for the person. The reply is hermes serve's
        with ``speaker`` added — ``null`` when the gate had no say — and, for a
        voice the gate accepted, a ``speaker_ticket`` the client hands back on
        ``prompt.submit`` so the turn is credited to that person. A voice an
        enforcing gate refuses has its transcript replaced by an empty string
        HERE: the words never reach a prompt, and the client reads
        ``speaker.dropped`` to say so. ``"speaker_gate": false`` in the body skips
        the gate (``speaker`` null, no ticket, Hermes' own Whisper, no voice figure) from
        a caller on this machine only (a loopback peer carrying none of X-Forwarded-For,
        Forwarded, X-Real-IP): for a file a tool transcribes, not a voice that
        speaks to the house. From any other address the flag is ignored.

        The flag is a convenience for the in-container tool, not an authorisation: the
        bearer key is what authorises the call. It does not cover a process inside this
        container's own network namespace (the adapter shares it with Hermes, so such a
        process is a loopback peer with no header) nor a relay that forwards without
        adding one of those headers.
        """
        started = time.monotonic()
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > TRANSCRIBE_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"a recording is at most {TRANSCRIBE_MAX_BYTES} bytes")
        # Counted as it arrives, so a chunked body with no Content-Length is held to it too.
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > TRANSCRIBE_MAX_BYTES:
                raise HTTPException(status_code=413, detail=f"a recording is at most {TRANSCRIBE_MAX_BYTES} bytes")
        raw = bytes(body)
        # "_mark" is when the body was in; _gated turns it into decode_ms and removes it.
        stages: dict[str, float] = {"receive_ms": (time.monotonic() - started) * 1000,
                                    "_mark": time.monotonic()}
        try:
            sent = json.loads(raw) or {}
            data_url = str(sent.get("data_url") or "")
            # The phone's own words (it transcribes on the phone, 2026-10-08), sent with
            # the audio only so the gate can hear who spoke: no second Whisper for them.
            # Words with no audio are no voice turn and take the ordinary path. Trusting
            # them gives a client nothing new: the key it sent them with signs tickets
            # (issue_ticket) and owns every prompt sent without one. An honest phone's
            # words are of the same recording the gate hears.
            phone_words = sent.get("transcript") if data_url else None
            # A private turn (T-0462): the gate still decides who spoke, but its audio is
            # never held for a claim, its words never go to Hermes, and no line is logged.
            private = sent.get("private") is True
            # A recording someone asked a tool to transcribe (T-0825) is not a voice turn: the
            # gate would blank a stranger's words and mint a ticket for an accepted voice.
            # Only an explicit false skips it, and only from this machine: the flag turns off who-spoke,
            # so a key holder on the network must not be able to. Hermes never sees the flag.
            gated = not (sent.get("speaker_gate") is False and _is_local_peer(request))
            if "speaker_gate" in sent:
                raw = json.dumps({k: v for k, v in sent.items() if k != "speaker_gate"}).encode()
        except (ValueError, AttributeError):
            data_url, phone_words, private, gated = "", None, False, True
        # Who is speaking is asked alongside the words, not after them: the two are
        # independent, and in series every voice turn paid both (2026-10-08).
        verifying = asyncio.create_task(
            _timed_gate(data_url, stages, _surface_of(raw), not private)) if data_url and gated else None
        try:
            if isinstance(phone_words, str):
                answer = await _gated(_surface_of(raw), data_url, verifying,
                                      {"success": True, "transcript": phone_words.strip(), "provider": "phone"},
                                      private, stages)
            else:
                answer = await _transcribed(request, raw, data_url, verifying, private, stages)
            # An ungated recording piece is minutes of audio, not a person speaking: it is no voice
            # latency and no missed deadline.
            if answer.status_code == 200 and gated:
                stages["transcribe_ms"] = (time.monotonic() - started) * 1000
                deadlines.check("transcribe_ms", stages["transcribe_ms"], f"surface={_surface_of(raw) or '-'}")
                for step, ms in stages.items():
                    if step in voice_ms:
                        voice_ms[step].append(ms)
                # So a client or a probe sees this turn's own figures, not only the window's.
                answer.headers["Server-Timing"] = ", ".join(
                    f"{step.removesuffix('_ms')};dur={ms:.1f}" for step, ms in stages.items() if step in voice_ms)
            return answer
        finally:
            # A client gone mid-request must not leave the gate's call running.
            if verifying is not None and not verifying.done():
                verifying.cancel()

    async def _timed_gate(data_url: str, stages: dict[str, float], surface: str = "console",
                          learn: bool = False) -> dict[str, Any] | None:
        t0 = time.monotonic()
        try:
            return await _verify_speaker(data_url, surface, learn)
        finally:
            stages["gate_ms"] = (time.monotonic() - t0) * 1000

    async def _transcribed(request: Request, raw: bytes, data_url: str,
                           verifying: asyncio.Task[Any] | None, private: bool = False,
                           stages: dict[str, float] | None = None) -> Response:
        answer = await _words_on_the_card(raw) if verifying is not None else None
        if answer is None and private:
            # Hermes' Whisper writes the clip to its own disk: a private turn is refused instead,
            # unless the gate drops the voice anyway -- then "not you" is the truer answer.
            speaker = await verifying if verifying else None
            if not (speaker and speaker["dropped"]):
                raise HTTPException(status_code=503, detail="no private transcription here; type it, or use the phone's own")
            answer = {"success": True, "transcript": "", "provider": "none"}
        if answer is None:
            fwd = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
            fwd.update(serve_headers)
            try:
                upstream = await client.post(f"{settings.hermes_serve_url}/api/audio/transcribe", headers=fwd, content=raw)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"upstream unreachable: {exc.__class__.__name__}")
            try:
                answer = upstream.json()
            except ValueError:
                answer = None
            if upstream.status_code != 200 or not isinstance(answer, dict):
                if answer is None:
                    return Response(content=upstream.content, status_code=upstream.status_code,
                                    media_type=upstream.headers.get("content-type"))
                return JSONResponse(answer, status_code=upstream.status_code)
        return await _gated(_surface_of(raw), data_url, verifying, answer, private, stages)

    async def _gated(surface: str, data_url: str, verifying: asyncio.Task[Any] | None,
                     answer: dict[str, Any], private: bool = False,
                     stages: dict[str, float] | None = None) -> Response:
        """The gate's verdict and the noise filter, applied to words from wherever they came."""
        # decode = the words' own time once the body was in; gate_wait = what the gate still
        # added after the words were ready (0 = the concurrent gate cost the turn nothing).
        t_words = time.monotonic()
        if stages is not None:
            stages["decode_ms"] = (t_words - stages.pop("_mark", t_words)) * 1000
        speaker = await verifying if verifying else None
        if stages is not None:
            stages["gate_wait_ms"] = (time.monotonic() - t_words) * 1000
        answer["speaker"] = speaker
        if speaker is not None:
            words = str(answer.get("transcript") or "")
            if speaker["dropped"] and answer.get("provider") != "none" and (
                    not words.strip() or noise_mod.reason(words)):
                # No words, so nothing was refused: a cabin's noise or a clip cut short scores
                # as nobody's voice, and the car told the owner it "did not recognise your
                # voice" twice on one drive (2026-11-11, similarity 0.06 and 0.04). The client
                # reads the blank as "heard nothing". Not for a private turn's stand-in blank
                # (provider none): there the words were never made, and the refusal stands.
                if not private:
                    log.info("speaker gate refused a clip with no words from %s: %s (similarity %s)",
                             surface, speaker["reason"], speaker.get("similarity"))
                speaker["dropped"] = False
            if speaker["dropped"]:
                if not private:
                    log.info("speaker gate dropped a turn from %s: %s (nearest %s, similarity %s)",
                             surface, speaker["reason"], speaker["nearest"], speaker.get("similarity"))
                answer["transcript"] = ""
                audio, kind = _audio_of(data_url)
                if audio and not private:
                    claim = unrecognised.hold(audio, kind, speaker, surface)
                    if claim:
                        speaker["claim"] = claim
            elif speaker["accepted"] and speaker["label"] and speaker["level"]:
                answer["speaker_ticket"] = speaker_mod.issue_ticket(settings.api_key, speaker["label"], speaker["level"])
        # Noise is dropped here too, for every surface: a word looping or a line cut off
        # mid-word is not a request (noise.py). The client reads a blank as "heard nothing".
        why = noise_mod.reason(str(answer.get("transcript") or ""))
        if why:
            if not private:
                log.info("dropped a noise transcript from %s: %s", surface, why)
            answer["transcript"] = ""
            answer["noise"] = why
            answer.pop("speaker_ticket", None)
        return JSONResponse(answer, status_code=200)

    @app.post("/api/audio/wake-check", dependencies=[Depends(require_key)])
    async def wake_check(request: Request) -> Response:
        """The wake word's second stage (T-1573): who said the name, before any turn opens.

        The phone posts the ~2 s its wake head fired on (``data_url``, ``surface``). The house's
        speaker gate is asked with ``learn=0`` and no ticket is issued: a wake is not a turn.
        The answer is ``wake`` (``drop`` only for a voice the gate would drop), ``reason`` and
        ``similarity``; a refused clip close to someone is held for a claim like a refused
        turn (``claim``). Does not cover the words: none are transcribed here.
        """
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > WAKE_CHECK_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"a wake window is at most {WAKE_CHECK_MAX_BYTES} bytes")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > WAKE_CHECK_MAX_BYTES:
                raise HTTPException(status_code=413, detail=f"a wake window is at most {WAKE_CHECK_MAX_BYTES} bytes")
        try:
            sent = json.loads(bytes(body)) or {}
            data_url = str(sent.get("data_url") or "")
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="expected JSON with a data_url") from None
        surface = _surface_of(bytes(body))
        speaker = await _verify_speaker(data_url, surface, learn=False)
        answer = speaker_mod.wake_answer(speaker)
        if answer["wake"] == "drop":
            log.info("wake-check dropped a wake from %s: %s (similarity %s)", surface, answer["reason"], answer["similarity"])
            audio, kind = _audio_of(data_url)
            claim = unrecognised.hold(audio, kind, speaker, surface) if audio else None
            if claim:
                answer["claim"] = claim
        return JSONResponse(answer, status_code=200)

    @app.get("/api/speaker/current", dependencies=[Depends(require_key)])
    async def speaker_current(session: str = "") -> dict[str, Any]:
        """Who a session's latest prompt came from: the label, the level, the source."""
        return speakers.current(session)

    @app.get("/api/surface/current", dependencies=[Depends(require_key)])
    async def surface_current(session: str = "") -> dict[str, Any]:
        """Which screen a session's latest prompt came from; ``surface`` null when it never said."""
        return surfaces.current(session)

    @app.get("/api/surface/sessions", dependencies=[Depends(require_key)])
    async def surface_sessions(surface: str = "") -> dict[str, Any]:
        """The sessions whose latest prompt reported ``surface``, newest first."""
        return {"surface": surface.strip().lower(), "sessions": surfaces.on(surface)}

    @app.api_route("/api/audio/{path:path}", methods=["GET", "POST"], dependencies=[Depends(require_key)])
    async def audio(path: str, request: Request) -> Response:
        return await _proxy(request, settings.hermes_serve_url, f"/api/audio/{path}", serve_headers)

    # -------------------------------------------------------------- WS proxies
    async def _swap_state() -> str:
        # llama-swap alone, with a short budget: build_status also asks both
        # Hermes ends, and a prompt must not wait on them to learn the model is cold.
        r = await client.get(f"{settings.llama_swap_url}/running", timeout=2.0,
                             headers={"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {})
        r.raise_for_status()
        return str(summarize_llama_swap(r.json(), settings.model)["state"])

    _model_state = fast_intent.SwapProbe(_swap_state)

    def _surface_name(session_id: str) -> str | None:
        surface = surfaces.current(session_id)["surface"]
        return surface["name"] if surface else None

    async def _pump(ws: WebSocket, upstream_url: str, ready_s: float | None = None,
                    device: dict[str, Any] | None = None) -> None:
        """Relay between an app socket and a hermes serve socket; with [ready_s], an app
        whose upstream has not connected and spoken by then is closed 1013 ("try again").

        Not covered: an upstream that spoke and then stalled; the app's own ping catches that."""
        spoke = asyncio.Event()
        relay = asyncio.create_task(_relay(ws, upstream_url, spoke, ready_s is not None, device))
        gave_up = False
        try:
            await asyncio.wait({relay}, timeout=ready_s)
            if not relay.done() and not spoke.is_set():
                log.warning("upstream not ready in %s s; closing the app socket (1013)", ready_s)
                gave_up = True
                relay.cancel()
            await relay
        except asyncio.CancelledError:
            if not gave_up:
                raise
        finally:
            if not relay.done():  # this handler was cancelled: the relay must not outlive it
                relay.cancel()

    async def _relay(ws: WebSocket, upstream_url: str, spoke: asyncio.Event, expect_ready: bool,
                     device: dict[str, Any] | None = None) -> None:
        turns = TurnLog(_surface_name, deadlines)
        bridge = RequestBridge(desk)
        photos: set[str] = set()  # sessions with a photo waiting in Hermes (fast_intent.intercept)
        try:
            async with websockets.connect(upstream_url, max_size=None, ping_interval=20, ping_timeout=20) as up:
                # Without this Hermes withdraws every approval and clarify on this socket
                # (server_requests.py says why); the apps never send it themselves.
                await up.send(bridge.hello())
                # From here this app is shown every sensitive ask, the ones already waiting first.
                app_sockets.add(ws)
                for waiting in desk.shown():
                    await ws.send_text(waiting)
                async def app_to_up() -> None:
                    while True:
                        msg = await ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            return
                        if msg.get("text") is not None:
                            # A prompt is where a turn's identity is fixed: the
                            # session is bound to the voice ticket the frame carries,
                            # or to the key holder, and the ticket is stripped so
                            # Hermes sees the frame it always saw. The screen the
                            # prompt came from is recorded and stripped the same way.
                            # A spoken prompt (voice:true, or a ticket) runs at the voice level
                            # (equal to the typed one by default, T-0814): the effort frame goes
                            # ahead, before they are stripped.
                            try:
                                efforts_before = efforts.before(msg["text"])
                            except Exception:  # a settings read must never cost a turn
                                log.exception("reasoning effort not applied")
                                efforts_before = []
                            for out in efforts_before:
                                await up.send(out)
                            frame = speaker_mod.rewrite_prompt(msg["text"], speakers)
                            frame = surface_mod.rewrite_prompt(frame, surfaces, device=device)
                            # A cold model would hold "what time is it" for minutes;
                            # a closed grammar of FREE asks is answered here instead.
                            local = await fast_intent.intercept(frame, _model_state, _surface_name, photos=photos)
                            turns.sent(frame, local=local is not None)
                            if local is not None:
                                for reply in local:
                                    await ws.send_text(reply)
                                continue
                            answered = bridge.from_app(frame)
                            if answered is not None:
                                for out in answered[0]:
                                    await up.send(out)
                                for out in answered[1]:
                                    await ws.send_text(out)
                                continue
                            await up.send(frame)
                        elif msg.get("bytes") is not None:
                            await up.send(msg["bytes"])

                async def up_to_app() -> None:
                    async for frame in up:
                        spoke.set()
                        if isinstance(frame, (bytes, bytearray)):
                            await ws.send_bytes(bytes(frame))
                        elif effort_mod.is_ours(frame):
                            continue  # the reply to our own config.set; the app never asked
                        else:
                            translated = bridge.from_upstream(frame)
                            if translated is not None:
                                for out in translated[1]:
                                    await up.send(out)
                                for out in translated[0]:
                                    await ws.send_text(out)
                                continue
                            turns.received(frame)
                            await ws.send_text(frame)

                t1 = asyncio.create_task(app_to_up())
                t2 = asyncio.create_task(up_to_app())
                try:
                    done, _ = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    # Also when this relay is itself cancelled (the give-up on a silent upstream,
                    # T-1187): a pump left running would send a late prompt into a closed socket.
                    for t in (t1, t2):
                        t.cancel()
                for t in done:
                    exc = t.exception() if not t.cancelled() else None
                    if exc and not isinstance(exc, (WebSocketDisconnect, websockets.ConnectionClosed)):
                        log.warning("ws relay ended: %s", exc)
                    else:
                        # AUDIT B11: a client gone mid-reply ended the relay without a word.
                        client_gone = isinstance(exc, WebSocketDisconnect) or (exc is None and t is t1)
                        log.info("ws relay ended: %s closed", "the client" if client_gone else "hermes")
        except (OSError, websockets.InvalidURI, websockets.InvalidHandshake) as exc:
            log.warning("upstream ws unavailable: %s", exc)
            await _quietly("telling the client the backend is down", ws.send_text(json.dumps({
                "jsonrpc": "2.0", "method": "event",
                "params": {"type": "adapter.error", "payload": {"error": "backend unavailable"}}})))
        finally:
            app_sockets.discard(ws)
            turns.close()
            # Debug: the close after a client hang-up fails on every ordinary drop.
            # 1013 ("try again later") when the upstream never spoke: the app retries on a close.
            unready = expect_ready and not spoke.is_set()
            await _quietly("closing the client socket",
                           ws.close(code=1013 if unready else 1000,
                                    reason="upstream not ready" if unready else ""), logging.DEBUG)

    async def _accept_authed(ws: WebSocket) -> str | None:
        """None when refused (closed 4401); else the device id, or "" for the master key."""
        key = _ws_bearer(ws)
        subprotocol = None
        for proto in ws.headers.get("sec-websocket-protocol", "").split(","):
            proto = proto.strip()
            if proto.startswith("jarvis-key."):
                subprotocol = proto
        # Accept first so the client sees a clean 4401 close frame (a refusal
        # before accept surfaces as an opaque HTTP 403 in browsers).
        await ws.accept(subprotocol=subprotocol)
        if _is_master(key):
            return ""
        ident = await asyncio.to_thread(device_keys.check, key) if key else None
        if not ident:
            await ws.close(code=4401)
            return None
        return ident

    async def _relay_held(ws: WebSocket, upstream_url: str, ready_s: float | None = None) -> None:
        holder = await _accept_authed(ws)
        if holder is None:
            return
        held = device_sockets.setdefault(holder, set()) if holder else None
        if held is not None:
            held.add(ws)
        try:
            # A revoke between the key check and the add above found nothing here to hang up.
            described = await asyncio.to_thread(device_keys.describe, holder) if holder else None
            if held is not None and described is None:
                await ws.close(code=4401)
                return
            # Which paired device this socket is, for the house's per-turn device (T-1498): read
            # once from the key it opened with, never from what the client says.
            device = {k: described.get(k) for k in ("id", "name", "kind", "house_token")} if described else None
            await _pump(ws, upstream_url, ready_s, device)
        finally:
            if held is not None:
                held.discard(ws)
                if not held and device_sockets.get(holder) is held:
                    device_sockets.pop(holder)

    @app.websocket("/ws")
    async def gateway_ws(ws: WebSocket) -> None:
        await _relay_held(ws, settings.serve_ws_url, UPSTREAM_READY_S)

    @app.websocket("/api/audio/speak-stream")
    async def speak_stream(ws: WebSocket) -> None:
        await _relay_held(ws, settings.serve_speak_ws_url)

    return app
