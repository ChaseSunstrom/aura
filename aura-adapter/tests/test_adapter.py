"""Adapter tests: auth, HTTP proxying, the WebSocket relay and /api/status.

Two fake upstreams (an "API server" and a "serve backend") run in-process on
random loopback ports so the relay is exercised over real sockets.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from aura_adapter.config import Settings
from aura_adapter.app import create_app, house_names
from aura_adapter import app as app_mod
from aura_adapter import device_keys
from aura_adapter.status import summarize_llama_swap

APP_KEY = "app-key-0123456789abcdef"
API_KEY = "hermes-api-key"
SERVE_TOKEN = "serve-token"


#: Sockets ``_free_port`` bound and still holds, until ``_serve`` gives each to its server.
_HELD: dict[int, socket.socket] = {}


def _free_port() -> int:
    # Held, not closed: a port released here and bound again by uvicorn later could be taken in
    # between, or handed out twice among the stack's seven, and that server died at start while
    # the fixture waited out its 60 s (T-0821). Unserved, it refuses connections, as a dead host.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    _HELD[port] = s
    return port


_SERVERS: list[tuple[uvicorn.Server, threading.Thread]] = []


def _serve(app: FastAPI, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    sock = _HELD.pop(port, None)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]} if sock else {}, daemon=True)
    thread.start()
    _SERVERS.append((server, thread))
    # A real deadline, not a hundred ticks: the first server's start pays for importing
    # uvloop/httptools, and on the shared CI runner that exceeded the old five seconds
    # ("server did not start" in job test:adapter), while a laptop needs 50 ms.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.started:
            return server
        if not thread.is_alive():
            raise RuntimeError(f"uvicorn on 127.0.0.1:{port} stopped before it started; its log is above")
        time.sleep(0.05)
    raise RuntimeError(f"uvicorn on 127.0.0.1:{port} did not start within 60 s")


def fake_api() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.21.0"}

    @app.get("/v1/models")
    async def models(request: Request):
        return {"auth": request.headers.get("authorization"), "data": [{"id": "jarvis"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()

        async def gen():
            for tok in ["Hel", "lo"]:
                yield f"data: {json.dumps({'choices': [{'delta': {'content': tok}}]})}\n\n".encode()
                await asyncio.sleep(0.01)
            yield b"data: [DONE]\n\n"

        assert body["stream"] is True
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/sessions")
    async def sessions(request: Request):
        return {"sessions": [], "auth": request.headers.get("authorization"), "q": request.query_params.get("limit")}

    # A Hermes route the adapter never relays: reaching it through a relay is the climb T-1565 refuses.
    @app.api_route("/api/cron/fire", methods=["GET", "POST"])
    async def cron_fire():
        return {"fired": True}

    return app


def fake_serve() -> FastAPI:
    app = FastAPI()

    @app.get("/api/health")
    async def health(request: Request):
        return {"ok": request.headers.get("x-hermes-session-token") == SERVE_TOKEN, "version": "0.21.0"}

    @app.post("/api/audio/transcribe")
    async def transcribe(request: Request):
        body = await request.json()
        # "hush": the clip had no words in it (a cabin's noise, a prompt cut short).
        # Keys Hermes was not meant to see (the adapter's own flags) are echoed back to prove they were stripped.
        stray = sorted(k for k in body if k == "speaker_gate")
        return {"ok": True, "transcript": "" if body.get("hush") else "echo:" + body["data_url"][-4:],
                "token": request.headers.get("x-hermes-session-token"), **({"stray": stray} if stray else {})}

    @app.post("/api/audio/speak")
    async def serve_speak(request: Request):
        return {"ok": True, "data_url": "data:audio/mpeg;base64,c2VydmU=", "via": "hermes-serve"}

    @app.websocket("/api/ws")
    async def ws(ws: WebSocket):
        if ws.query_params.get("token") != SERVE_TOKEN:
            await ws.close(code=4401)
            return
        await ws.accept()
        await ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready"}}))
        while True:
            msg = await ws.receive_text()
            req = json.loads(msg)
            await ws.send_text(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                                           "result": {"echo": req["method"], "params": req.get("params")}}))

    @app.websocket("/api/audio/speak-stream")
    async def speak_ws(ws: WebSocket):
        """A long reply, a piece at a time, so a client can hang up in the middle of it."""
        await ws.accept()
        for n in range(200):
            await ws.send_bytes(f"PCM{n};".encode())
            await asyncio.sleep(0.02)

    return app


#: What the fake house says to "is a phone on the device channel": yes, no, or error.
PHONE_REACH = ["no"]
ENROLLED: list[dict] = []
#: What each verify asked for: the turn's microphone and whether the house may learn from it.
VERIFIED: list[tuple[str | None, str | None]] = []
#: The Speech language the owner chose on the console, as the house answers it (T-1487).
HOUSE_LANGUAGE: list[str | None] = [None]


def fake_house() -> FastAPI:
    """jarvis-core's speaker gate, as the adapter sees it: WAV in, a verdict out.

    The body decides the verdict, so a test can ask for each answer: audio
    whose bytes begin ``OWNER`` is the owner, ``GUEST`` a guest, ``NOBODY`` a
    stranger the gate would block, anything else a stranger it only observes.
    """
    app = FastAPI()

    @app.get("/api/models/{name}")
    async def model(name: str, request: Request):
        from starlette.responses import JSONResponse, Response as Raw
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if name != "hey_jarvis_v0.1.onnx":
            return JSONResponse({"detail": "no such model"}, status_code=404)
        return Raw(b"ONNX-hey-jarvis", media_type="application/octet-stream")

    @app.get("/api/config/companion/reachable")
    async def reachable(request: Request):
        from starlette.responses import JSONResponse
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if PHONE_REACH[0] == "error":
            return JSONResponse({"detail": "boom"}, status_code=500)
        # A device's name rides along here to prove the adapter does not pass it on.
        return JSONResponse({"reachable": PHONE_REACH[0] == "yes", "name": "Pixel 8"})

    @app.api_route("/api/maps/{kind}/{region}", methods=["GET", "DELETE"])
    async def map_region(kind: str, region: str, request: Request):
        from starlette.responses import JSONResponse, Response as Raw
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if kind == "packs" and request.method == "GET":
            data = b"SQLite format 3\x00" + bytes(range(200))
            rng = request.headers.get("range")
            if rng:
                a, b = (int(v) for v in rng.removeprefix("bytes=").split("-"))
                return Raw(data[a:b + 1], status_code=206, media_type="application/octet-stream",
                           headers={"Content-Range": f"bytes {a}-{b}/{len(data)}"})
            return Raw(data, media_type="application/octet-stream")
        return JSONResponse({"kind": kind, "region": region, "method": request.method})

    @app.api_route("/api/maps/{op}", methods=["GET", "POST"])
    async def maps(op: str, request: Request):
        from starlette.responses import JSONResponse
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if op == "route":
            return JSONResponse({"detail": {"code": "no_route", "message": "no route"}}, status_code=404)
        body = (await request.body()).decode() or None
        return JSONResponse({"op": op, "method": request.method, "bbox": request.query_params.get("bbox"), "body": body})

    @app.post("/api/voice/speaker/verify")
    async def verify(request: Request):
        from starlette.responses import JSONResponse
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        body = await request.body()
        VERIFIED.append((request.query_params.get("source"), request.query_params.get("learn")))
        who = body[:6].decode("ascii", "replace")
        if who.startswith("OWNER"):
            verdict = {"accepted": True, "label": "owner", "level": "owner", "reason": "match", "nearest": "owner"}
            block = False
        elif who.startswith("GUEST"):
            verdict = {"accepted": True, "label": "Ted", "level": "guest", "reason": "match", "nearest": "Ted"}
            block = False
        elif who.startswith("NOBODY"):
            verdict = {"accepted": False, "label": None, "level": None, "reason": "mismatch", "nearest": "owner",
                       "similarity": 0.1}
            block = True
        elif who.startswith("NEARLY"):
            # The owner on a microphone they never enrolled on: close, but under the line.
            verdict = {"accepted": False, "label": None, "level": None, "reason": "neural-mismatch",
                       "nearest": "owner", "similarity": 0.41}
            block = True
        else:
            verdict = {"accepted": False, "label": None, "level": None, "reason": "mismatch", "nearest": "owner"}
            block = False
        return {"verdict": verdict, "would_block": block, "speech_ms": 1200.0, "has_pitch": True}

    @app.get("/api/states")
    async def states(request: Request):
        from starlette.responses import JSONResponse
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return [{"entity_id": "sensor.den_temp", "attributes": {"friendly_name": "Den temperature"}},
                {"entity_id": "light.den_lamp", "attributes": {"friendly_name": "Den lamp"}},
                {"entity_id": "person.isa", "attributes": {"friendly_name": "Isa"}},
                {"entity_id": "light.den_lamp_2", "attributes": {"friendly_name": "Den lamp"}},
                {"entity_id": "switch.nameless", "attributes": {}}]

    @app.get("/api/voice/language")
    async def language(request: Request):
        from starlette.responses import JSONResponse
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return {"language": HOUSE_LANGUAGE[0]}

    @app.get("/api/config/area_registry/list")
    async def areas(request: Request):
        from starlette.responses import JSONResponse
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return [{"id": "den", "name": "Den", "aliases": []}, {"id": "loft", "name": "Sun loft", "aliases": []}]

    @app.post("/api/voice/speaker/enrol")
    async def enrol(request: Request):
        ENROLLED.append({"label": request.query_params.get("label"), "source": request.query_params.get("source"),
                         "body": await request.body()})
        return {"accepted": True}

    @app.post("/api/voice/tts")
    async def tts(request: Request):
        """The house's Piper: one line as WAV (22.05 kHz), its text in the frames."""
        import io
        import wave
        from starlette.responses import JSONResponse, Response as R
        if request.headers.get("authorization") != "Bearer house-token":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        text = (await request.json())["text"]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(f"PIPER:{text}".encode())
        return R(buf.getvalue(), media_type="audio/wav")

    return app


def fake_cosyvoice() -> FastAPI:
    """The shim's OpenAI-shaped speech: known voices answer bytes, unknown ones 404. AURA and JARVIS
    are Kokoro stock voices there (T-0861); the other personas are clips named after them."""
    app = FastAPI()

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        from starlette.responses import Response as R
        body = await request.json()
        if body["voice"] not in ("af_heart", "bm_george"):
            return R(status_code=404, content="no such voice")
        if body["input"].startswith("WARMING"):
            return R(status_code=503, content="the voice is still warming up", headers={"Retry-After": "15"})
        if body["response_format"] == "pcm":
            # Three pieces, a pause between: the adapter must pass each on as it
            # comes, not gather the line first.
            async def pieces():
                for n in range(3):
                    yield f"PCM{n}:{body['voice']}:{body['input']};".encode()
                    await asyncio.sleep(0.05)

            from starlette.responses import StreamingResponse
            return StreamingResponse(pieces(), media_type="audio/pcm", headers={"x-sample-rate": "24000"})
        return R(content=f"MP3:{body['voice']}:{body['input']}:{body['response_format']}".encode(),
                 media_type="audio/mpeg")

    @app.post("/api/audio/speak")
    async def serve_speak(request: Request):
        return {"ok": True, "data_url": "data:audio/mpeg;base64,c2VydmU=", "via": "hermes-serve"}

    return app


def fake_llama() -> FastAPI:
    app = FastAPI()

    @app.get("/running")
    async def running(request: Request):
        # llama-swap's apiKeys: no bearer, no answer.
        if request.headers.get("authorization") != "Bearer llm-key":
            raise HTTPException(status_code=401)
        return {"running": [{"model": "qwen3.8-27b", "state": "starting"}]}

    return app


@pytest.fixture(scope="module")
def stack():
    api_port, serve_port, llama_port, voice_port, adapter_port, piper_port, house_port = (_free_port() for _ in range(7))
    _serve(fake_api(), api_port)
    _serve(fake_serve(), serve_port)
    _serve(fake_llama(), llama_port)
    _serve(fake_cosyvoice(), voice_port)
    _serve(fake_house(), house_port)
    settings = Settings(api_key=APP_KEY, hermes_api_url=f"http://127.0.0.1:{api_port}", hermes_api_key=API_KEY,
                        hermes_serve_url=f"http://127.0.0.1:{serve_port}", hermes_serve_token=SERVE_TOKEN,
                        llama_swap_url=f"http://127.0.0.1:{llama_port}", llm_api_key="llm-key",
                        model="qwen3.8-27b",
                        cors_origins=["http://console.test"], upstream_timeout=10,
                        tts_engine="cosyvoice", tts_url=f"http://127.0.0.1:{voice_port}",
                        house_url=f"http://127.0.0.1:{house_port}", house_token="house-token")
    _serve(create_app(settings), adapter_port)
    # A second adapter on the Piper path: speak relays to hermes serve (the fake
    # CosyVoice app doubles as that serve, answering /api/audio/speak).
    _serve(create_app(dataclasses.replace(settings, tts_engine="wyoming", hermes_serve_url=f"http://127.0.0.1:{voice_port}")), piper_port)
    _PIPER_PORT[:] = [piper_port]
    yield f"http://127.0.0.1:{adapter_port}"
    # Stop every server and join its thread before pytest exits. Left as daemon threads
    # inside uvloop, the interpreter tore them down mid-loop and the CI job died with
    # exit code 139 *after* "13 passed" (pipeline 5, job 51) — a segfault, not a test.
    for server, thread in _SERVERS:
        server.should_exit = True
    for server, thread in _SERVERS:
        thread.join(timeout=10)
        # A thread still alive here keeps its event loop running into the next module's tests.
        assert not thread.is_alive(), "stack: the test server thread outlived its teardown"
    _SERVERS.clear()


AUTH = {"Authorization": f"Bearer {APP_KEY}"}
_PIPER_PORT: list[int] = []


def test_health_is_open(stack):
    r = httpx.get(f"{stack}/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_everything_else_needs_the_key(stack):
    for path in ("/api/status", "/v1/models", "/api/sessions", "/api/audio/voice-config", "/api/pair"):
        assert httpx.get(f"{stack}{path}").status_code == 401, path
        assert httpx.get(f"{stack}{path}", headers={"Authorization": "Bearer wrong"}).status_code == 401, path


def test_v1_proxy_swaps_the_key_and_keeps_the_query(stack):
    r = httpx.get(f"{stack}/v1/models", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["auth"] == f"Bearer {API_KEY}"          # the app key never reaches Hermes
    r = httpx.get(f"{stack}/api/sessions?limit=5", headers=AUTH)
    assert r.json()["q"] == "5" and r.json()["auth"] == f"Bearer {API_KEY}"


@pytest.mark.parametrize("raw_path", [
    "/api/jobs/..%2f..%2fapi%2fcron%2ffire",        # uvicorn decodes %2f; httpx then collapsed the ..
    "/api/sessions/../../api/cron/fire",            # sent as is, as curl --path-as-is does
    "/v1/..%2fapi%2fcron%2ffire",
    "/api/jobs/%252e%252e/%252e%252e/api/cron/fire",  # double-encoded dots
    "/api/jobs/.%2f..%2f..%2fapi%2fcron%2ffire",
    "/api/jobs/x%00",                                # a NUL answered 500 (httpx InvalidURL)
    "/api/jobs/..%5c..%5capi%5ccron%5cfire",         # backslashes
])
def test_a_relayed_path_that_climbs_out_never_reaches_hermes(stack, raw_path):
    # T-1565: the jobs, sessions and v1 relays are prefix relays. A key holder, a paired
    # device included, reached Hermes routes the adapter never relays (here /api/cron/fire)
    # with the adapter's own Hermes key. http.client sends the path exactly as written.
    import http.client
    from urllib.parse import urlsplit

    host = urlsplit(stack)
    conn = http.client.HTTPConnection(host.hostname, host.port, timeout=10)
    conn.request("GET", raw_path, headers=AUTH)
    answer = conn.getresponse()
    body = answer.read().decode(errors="replace")
    conn.close()
    assert answer.status == 404, (raw_path, answer.status, body)
    assert "fired" not in body


def test_the_climb_check_looks_at_every_decoding_round():
    # T-1571: only the last decoded form was checked, and never for a slash, so
    # `..%252f` (handed over by uvicorn as `..%2f`, decoding to `../`) and a segment still
    # encoded after the four rounds passed. The console's climbsOut checks each round (T-1567).
    from aura_adapter.app import _climbs_out

    assert _climbs_out("/api/jobs/..%2fx")
    assert _climbs_out("/api/jobs/a%2fb")
    assert _climbs_out("/api/jobs/x%5c..")
    assert _climbs_out("/api/jobs/" + ("%" + "25" * 5 + "2e") * 2)   # .. encoded six deep
    assert not _climbs_out("/api/jobs/abc123")
    assert not _climbs_out("/api/jobs/a%20b")
    assert not _climbs_out("/v1/chat/completions")


def test_the_audio_relay_cannot_climb_to_the_rest_of_hermes_serve(stack):
    # T-1565: /api/audio/{path} relays to hermes serve with the serve token, whose API is wider
    # than the HUD's audio; the fake serve's /api/health stands in for the rest of it.
    import http.client
    from urllib.parse import urlsplit

    host = urlsplit(stack)
    conn = http.client.HTTPConnection(host.hostname, host.port, timeout=10)
    conn.request("GET", "/api/audio/..%2fhealth", headers=AUTH)
    answer = conn.getresponse()
    body = answer.read().decode(errors="replace")
    conn.close()
    assert answer.status == 404, (answer.status, body)
    assert "version" not in body


def test_streaming_completion_passes_through_as_sse(stack):
    chunks = []
    with httpx.stream("POST", f"{stack}/v1/chat/completions", headers=AUTH,
                      json={"stream": True, "messages": [{"role": "user", "content": "hi"}]}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                chunks.append(line[6:])
    assert chunks[-1] == "[DONE]" and len(chunks) == 3


def test_audio_proxy_uses_the_serve_token(stack):
    r = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                   json={"data_url": "data:audio/webm;base64,AAAA", "mime_type": "audio/webm"})
    assert r.status_code == 200
    # webm is nothing the gate can embed, so the gate had no say: `speaker` is
    # null and there is no ticket -- the turn is the key holder's, as before.
    assert r.json() == {"ok": True, "transcript": "echo:AAAA", "token": SERVE_TOKEN, "speaker": None}


def _wav_url(who: bytes) -> str:
    import base64
    return "data:audio/wav;base64," + base64.b64encode(who + b"\x00" * 64).decode()


_WAKE_CHECK = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "wake_check.json").read_text())


@pytest.mark.parametrize("row", _WAKE_CHECK["gate"], ids=lambda r: r["name"])
def test_wake_check_answers_from_the_gate_verdict(row):
    """T-1573 (tests/contracts/wake_check.json): a wake is dropped only when the gate itself
    would drop the voice; a house that cannot say never costs the owner a wake."""
    from aura_adapter import speaker as speaker_mod
    answer = speaker_mod.wake_answer(speaker_mod.speaker_of(row["verify"]))
    assert (answer["wake"], answer["reason"]) == (row["wake"], row["reason"])


def test_wake_check_route_drops_a_refused_voice_without_learning_or_a_ticket(stack):
    """T-1573: the route asks the gate with learn=0, answers drop for a voice an enforcing gate
    refuses and open for the owner, never carries a ticket, and needs the key."""
    VERIFIED.clear()
    drop = httpx.post(f"{stack}/api/audio/wake-check", headers=AUTH,
                      json={"data_url": _wav_url(b"NOBODY"), "surface": "car"})
    assert drop.status_code == 200 and drop.json()["wake"] == "drop", drop.text
    owner = httpx.post(f"{stack}/api/audio/wake-check", headers=AUTH,
                       json={"data_url": _wav_url(b"OWNER"), "surface": "android"}).json()
    assert owner["wake"] == "open" and "speaker_ticket" not in owner and "speaker_ticket" not in drop.json()
    assert VERIFIED == [("car", "0"), ("android", "0")]
    assert httpx.post(f"{stack}/api/audio/wake-check", json={"data_url": _wav_url(b"OWNER")}).status_code == 401
    assert httpx.post(f"{stack}/api/audio/wake-check", headers=AUTH,
                      content=b"x" * 600_000).status_code == 413


def test_wake_check_holds_a_nearly_owner_refusal_for_a_claim(stack):
    """T-1573: the owner on a microphone they never enrolled on is refused like any voice; the
    clip is held the way a refused turn is, so the console can claim it and teach that mic."""
    body = httpx.post(f"{stack}/api/audio/wake-check", headers=AUTH,
                      json={"data_url": _wav_url(b"NEARLY"), "surface": "car"}).json()
    assert body["wake"] == "drop" and body.get("claim"), body
    # Claimed from the console like a refused turn: the print is learnt for that microphone
    # (and the shared store is left empty for the tests after this one).
    mine = httpx.post(f"{stack}/api/speaker/unrecognised/{body['claim']}/mine", headers=AUTH).json()
    assert mine == {"ok": True, "label": "owner", "source": "car"}


def _prompt_with(stack, ticket, session_id="s-1"):
    """Submit a prompt over the relay, and return what Hermes was sent."""
    async def run():
        async with websockets.connect(stack.replace("http://", "ws://") + "/ws", additional_headers=AUTH) as ws:
            await ws.recv()  # gateway.ready
            params = {"session_id": session_id, "text": "turn the lights off"}
            if ticket is not None:
                params["speaker_ticket"] = ticket
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit", "params": params}))
            return json.loads(await ws.recv())["result"]["params"]
    return asyncio.run(run())


def test_the_relay_drops_the_reply_to_the_reasoning_effort_frame(stack):
    """The fake Hermes echoes every frame: the app must see only its own prompt's reply (T-0641)."""
    async def run():
        async with websockets.connect(stack.replace("http://", "ws://") + "/ws", additional_headers=AUTH) as ws:
            await ws.recv()  # gateway.ready
            out = []
            for ticket in ("t", None):  # a spoken prompt lowers the effort, the typed one puts it back
                params = {"session_id": "s-effort", "text": "hi"}
                if ticket:
                    params["speaker_ticket"] = ticket
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit", "params": params}))
                out.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))
            return out
    for reply in asyncio.run(run()):
        assert reply["id"] == 1 and reply["result"]["echo"] == "prompt.submit"


def test_a_verified_voice_is_carried_from_the_transcript_to_the_prompt(stack):
    """The brief's path: the gate says who, the ticket survives the round trip,
    the session is credited to that person, and Hermes never sees the ticket."""
    r = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                   json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"})
    body = r.json()
    assert body["transcript"].startswith("echo:")
    assert body["speaker"] == {"accepted": True, "label": "Ted", "level": "guest", "reason": "match",
                               "nearest": "Ted", "dropped": False, "similarity": None}
    ticket = body["speaker_ticket"]

    sent = _prompt_with(stack, ticket, "s-guest")
    assert sent == {"session_id": "s-guest", "text": "turn the lights off"}
    who = httpx.get(f"{stack}/api/speaker/current", headers=AUTH, params={"session": "s-guest"}).json()
    assert (who["label"], who["level"], who["source"]) == ("Ted", "guest", "voice")

    # The next prompt on that session with no ticket is the key holder's again.
    _prompt_with(stack, None, "s-guest")
    who = httpx.get(f"{stack}/api/speaker/current", headers=AUTH, params={"session": "s-guest"}).json()
    assert (who["label"], who["level"], who["source"]) == (None, "owner", "key")
    # A session nobody has spoken on is the key holder's too.
    assert httpx.get(f"{stack}/api/speaker/current", headers=AUTH, params={"session": "never"}).json()["level"] == "owner"


def test_a_recording_asks_for_no_speaker_gate_and_gets_words_and_no_ticket(stack):
    """T-0825: a recording a person asks about is not a voice turn. The gate would drop a
    stranger's words and mint a ticket for an accepted voice; a tool that transcribes a
    file asks for neither with ``speaker_gate: false``, and Hermes never sees the flag."""
    for who in (b"GUEST", b"NOBODY"):
        body = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                          json={"data_url": _wav_url(who), "mime_type": "audio/wav",
                                "speaker_gate": False}).json()
        assert body["transcript"].startswith("echo:"), who
        assert body["speaker"] is None and "speaker_ticket" not in body and "stray" not in body
    # Only an explicit false skips the gate: anything else is a voice turn as before.
    body = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                      json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav",
                            "speaker_gate": "no"}).json()
    assert body["speaker"]["label"] == "Ted" and "speaker_ticket" in body


def _adapter_seen_from(peer: str | None = None, monkeypatch=None, deadline_ms: float | None = None) -> str:
    """A fresh adapter beside the fake Hermes and the fake house, whose callers all appear to come
    from [peer] (the way a caller on another machine does); None leaves the real loopback peer."""
    from aura_adapter import turn_log
    if deadline_ms is not None:
        monkeypatch.setattr(turn_log, "DEADLINES_MS", {**turn_log.DEADLINES_MS, "transcribe_ms": deadline_ms})
    serve_port, adapter_port, house_port = _free_port(), _free_port(), _free_port()
    _serve(fake_serve(), serve_port)
    _serve(fake_house(), house_port)
    inner = create_app(Settings(api_key=APP_KEY, hermes_api_url="http://127.0.0.1:9", hermes_api_key=API_KEY,
                                hermes_serve_url=f"http://127.0.0.1:{serve_port}", hermes_serve_token=SERVE_TOKEN,
                                llama_swap_url="http://127.0.0.1:9", model="m", tts_engine="cosyvoice",
                                tts_url="http://127.0.0.1:9", house_url=f"http://127.0.0.1:{house_port}",
                                house_token="house-token"))

    async def outer(scope, receive, send):
        if peer and scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (peer, 40000)}
        await inner(scope, receive, send)

    _serve(outer, adapter_port)
    return f"http://127.0.0.1:{adapter_port}"


def test_the_no_gate_flag_is_honoured_only_from_this_machine(stack):
    """T-0825 review: the flag skips who-spoke, so any key holder on the network could blank the
    gate with it. From another address it is ignored (the gate runs, a stranger's words are
    dropped) and is still stripped from what Hermes is sent."""
    far = _adapter_seen_from("203.0.113.7")
    body = httpx.post(f"{far}/api/audio/transcribe", headers=AUTH,
                      json={"data_url": _wav_url(b"NOBODY"), "mime_type": "audio/wav",
                            "speaker_gate": False}).json()
    assert body["transcript"] == "" and body["speaker"]["dropped"] is True
    body = httpx.post(f"{far}/api/audio/transcribe", headers=AUTH,
                      json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav",
                            "speaker_gate": False}).json()
    assert body["speaker"]["label"] == "Ted" and "speaker_ticket" in body and "stray" not in body
    # The same body from a loopback peer (IPv4 or IPv6 form) is a recording, as before.
    for near in ("127.0.0.1", "::1"):
        body = httpx.post(f"{_adapter_seen_from(near)}/api/audio/transcribe", headers=AUTH,
                          json={"data_url": _wav_url(b"NOBODY"), "mime_type": "audio/wav",
                                "speaker_gate": False}).json()
        assert body["speaker"] is None and body["transcript"].startswith("echo:"), near


@pytest.mark.parametrize("relay", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_the_no_gate_flag_is_ignored_when_a_relay_forwarded_the_request(relay):
    """A relay beside the adapter makes every caller a loopback peer; the header it adds is the
    only sign left, so a request carrying one stays gated (a stranger's words are dropped)."""
    base = _adapter_seen_from()
    piece = {"data_url": _wav_url(b"NOBODY"), "mime_type": "audio/wav", "speaker_gate": False}
    gated = httpx.post(f"{base}/api/audio/transcribe", headers={**AUTH, relay: "203.0.113.7"}, json=piece).json()
    assert gated["transcript"] == "" and gated["speaker"]["dropped"] is True
    # The control: without the header the same request is ungated, so the test can fail.
    free = httpx.post(f"{base}/api/audio/transcribe", headers=AUTH, json=piece).json()
    assert free["speaker"] is None and free["transcript"].startswith("echo:")


def test_an_ungated_recording_piece_is_no_voice_latency_and_no_deadline_miss(monkeypatch):
    """A four-minute piece takes seconds on a CPU: logged as a missed voice deadline, or counted in
    the voice percentiles, it would hide the real figure of a person speaking."""
    base = _adapter_seen_from(None, monkeypatch, deadline_ms=0.001)
    piece = {"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav", "speaker_gate": False}
    r = httpx.post(f"{base}/api/audio/transcribe", headers=AUTH, json=piece)
    assert r.json()["transcript"].startswith("echo:") and "server-timing" not in r.headers
    status = httpx.get(f"{base}/api/status", headers=AUTH, timeout=10).json()
    assert status["voice"]["transcribe_ms"]["n"] == 0
    assert status["voice_deadlines"]["transcribe"]["breaches"] == 0
    # The same instrument does see a voice turn (it is the control: it can fail).
    httpx.post(f"{base}/api/audio/transcribe", headers=AUTH,
               json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"})
    status = httpx.get(f"{base}/api/status", headers=AUTH, timeout=10).json()
    assert status["voice"]["transcribe_ms"]["n"] == 1
    assert status["voice_deadlines"]["transcribe"]["breaches"] == 1


def test_a_refused_voice_never_becomes_a_prompt(stack):
    r = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                   json={"data_url": _wav_url(b"NOBODY"), "mime_type": "audio/wav"})
    body = r.json()
    assert body["transcript"] == "" and body["speaker"]["dropped"] is True
    assert body["speaker"]["accepted"] is False and body["speaker"]["nearest"] == "owner"
    assert "speaker_ticket" not in body
    # An observing gate lets a stranger through, unticketed: the key holder's turn.
    r = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                   json={"data_url": _wav_url(b"STRANGER"), "mime_type": "audio/wav"})
    body = r.json()
    assert body["transcript"].startswith("echo:") and body["speaker"]["dropped"] is False
    assert "speaker_ticket" not in body


def test_a_refused_clip_with_no_words_is_nothing_heard_not_a_stranger(stack):
    """The owner's drive, 2026-11-11: two car-microphone clips with no words in them scored as
    nobody's voice, and the car said "did not recognise your voice" for each."""
    body = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                      json={"data_url": _wav_url(b"NOBODY"), "mime_type": "audio/wav", "hush": True}).json()
    assert body["transcript"] == "" and body["speaker"]["dropped"] is False
    assert body["speaker"]["accepted"] is False and "speaker_ticket" not in body
    assert "claim" not in body["speaker"]


def test_a_forged_or_stale_ticket_is_the_key_holder_not_the_person_it_names(stack):
    from aura_adapter import speaker as sp

    forged = sp.issue_ticket("not-the-key", "owner", "owner")
    _prompt_with(stack, forged, "s-forged")
    who = httpx.get(f"{stack}/api/speaker/current", headers=AUTH, params={"session": "s-forged"}).json()
    assert who["source"] == "key" and who["rejected_ticket"] is True and who["level"] == "owner"

    stale = sp.issue_ticket(APP_KEY, "Ted", "guest", now=time.time() - sp.TICKET_TTL_S - 1)
    assert sp.open_ticket(APP_KEY, stale) is None
    fresh = sp.issue_ticket(APP_KEY, "Ted", "guest")
    assert sp.open_ticket(APP_KEY, fresh)["label"] == "Ted"
    assert sp.open_ticket(APP_KEY, fresh + "x") is None
    assert sp.open_ticket(APP_KEY, "nonsense") is None
    assert sp.open_ticket(APP_KEY, sp.issue_ticket(APP_KEY, "Ted", "king")) is None


def test_the_relay_passes_every_other_frame_through_untouched():
    from aura_adapter import speaker as sp

    speakers = sp.Speakers(key=APP_KEY)
    for frame in ("not json", "[1,2]", '{"method":"session.create","params":{}}',
                  '{"method":"prompt.submit","params":"nope"}'):
        assert sp.rewrite_prompt(frame, speakers) == frame
    # A prompt with no ticket is passed through byte for byte, and still binds.
    frame = '{"jsonrpc": "2.0", "id": 3, "method": "prompt.submit", "params": {"session_id": "abc", "text": "hi"}}'
    assert sp.rewrite_prompt(frame, speakers) == frame
    assert speakers.current("abc")["source"] == "key"


def _prompt_from(stack, surface, session_id):
    """Submit a prompt that reports a surface, and return what Hermes was sent."""
    async def run():
        async with websockets.connect(stack.replace("http://", "ws://") + "/ws", additional_headers=AUTH) as ws:
            await ws.recv()  # gateway.ready
            params = {"session_id": session_id, "text": "play it", "surface": surface}
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit", "params": params}))
            return json.loads(await ws.recv())["result"]["params"]
    return asyncio.run(run())


def test_the_surface_a_prompt_reports_is_recorded_and_stripped(stack):
    """`tests/contracts/surface-capabilities.json`: the report rides `prompt.submit`,
    the adapter keeps it per session, Hermes never sees it, and `artifact_send`
    can find the newest session on a surface."""
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/surface-capabilities.json").read_text())
    web, console = contract["surfaces"]["web"], contract["surfaces"]["console"]

    sent = _prompt_from(stack, console, "s-tui")
    assert sent == {"session_id": "s-tui", "text": "play it"}
    who = httpx.get(f"{stack}/api/surface/current", headers=AUTH, params={"session": "s-tui"}).json()
    assert who["surface"] == console and who["at"] is not None

    _prompt_from(stack, web, "s-web-old")
    _prompt_from(stack, web, "s-web-new")
    on = httpx.get(f"{stack}/api/surface/sessions", headers=AUTH, params={"surface": "WEB"}).json()
    assert on == {"surface": "web", "sessions": ["s-web-new", "s-web-old"]}
    assert httpx.get(f"{stack}/api/surface/sessions", headers=AUTH, params={"surface": "android"}).json()["sessions"] == []

    # A session that never said is null, not the poorest profile: the plugin
    # decides what "unknown" means, and this route only reports.
    assert httpx.get(f"{stack}/api/surface/current", headers=AUTH, params={"session": "never"}).json()["surface"] is None
    # A page that injects `surface: "web"` gets no record, and the frame still goes through.
    sent = _prompt_from(stack, "web", "s-lied")
    assert sent == {"session_id": "s-lied", "text": "play it"}
    assert httpx.get(f"{stack}/api/surface/current", headers=AUTH, params={"session": "s-lied"}).json()["surface"] is None


def test_a_probe_turns_bare_surface_name_is_recorded_for_the_habit_filter(stack):
    """T-1201: smoke.sh and golden-turn.mjs send only `{"name": probe_surface}`
    (`tests/contracts/rig-session.json`); the plugin reads it back to skip the turn."""
    probe = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/rig-session.json").read_text())["probe_surface"]
    sent = _prompt_from(stack, {"name": probe}, "s-probe")
    assert sent == {"session_id": "s-probe", "text": "play it"}
    got = httpx.get(f"{stack}/api/surface/current", headers=AUTH, params={"session": "s-probe"}).json()
    assert got["surface"] == {"name": probe}


def test_a_surface_report_is_reduced_to_the_contract_and_the_relay_leaves_other_frames_alone():
    from aura_adapter import surface as sf

    assert sf.clean({"name": " Web ", "video": 0, "junk": 1, "max_bytes": "9", "images": "yes"}) == {
        "name": "web", "video": False, "images": True}
    assert sf.clean({"name": "web", "max_bytes": True}) == {"name": "web"}, "a bool is not a byte count"
    assert sf.clean({"video": True}) is None and sf.clean("web") is None
    surfaces = sf.Surfaces()
    for frame in ("not json", "[1,2]", '{"method":"session.create","params":{}}',
                  '{"method":"prompt.submit","params":"nope"}',
                  '{"jsonrpc": "2.0", "id": 3, "method": "prompt.submit", "params": {"session_id": "abc", "text": "hi"}}'):
        assert sf.rewrite_prompt(frame, surfaces) == frame
    assert surfaces.current("abc")["surface"] is None
    # A later prompt without a report keeps the last one: the person has not moved.
    surfaces.bind("abc", {"name": "android"}, now=1.0)
    assert sf.rewrite_prompt('{"method":"prompt.submit","params":{"session_id":"abc","text":"x"}}', surfaces) \
        == '{"method":"prompt.submit","params":{"session_id":"abc","text":"x"}}'
    assert surfaces.current("abc")["surface"] == {"name": "android"}
    assert surfaces.on("android") == ["abc"]


def test_status_reports_model_warming_up(stack):
    r = httpx.get(f"{stack}/api/status", headers=AUTH)
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == {"model": "qwen3.8-27b", "state": "starting", "warming_up": True, "loaded": ["qwen3.8-27b"]}
    assert body["hermes"]["api_server"]["ok"] and body["hermes"]["backend"]["ok"]


def test_pair_payload(stack):
    r = httpx.get(f"{stack}/api/pair", headers=AUTH)
    assert r.json()["key"] == APP_KEY and r.json()["url"] == stack


def test_cors_preflight_for_the_console(stack):
    r = httpx.options(f"{stack}/api/status", headers={"Origin": "http://console.test",
                                                       "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://console.test"
    r = httpx.options(f"{stack}/api/status", headers={"Origin": "http://evil.test",
                                                       "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") is None


@pytest.mark.parametrize("how", ["header", "subprotocol", "query"])
def test_ws_relay_round_trip(stack, how):
    url = stack.replace("http://", "ws://") + "/ws"
    kwargs = {}
    if how == "header":
        kwargs["additional_headers"] = AUTH
    elif how == "subprotocol":
        kwargs["subprotocols"] = [f"jarvis-key.{APP_KEY}"]
    else:
        url += f"?key={APP_KEY}"

    async def run():
        async with websockets.connect(url, **kwargs) as ws:
            first = json.loads(await ws.recv())
            assert first["params"]["type"] == "gateway.ready"
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "session.create", "params": {}}))
            reply = json.loads(await ws.recv())
            assert reply == {"jsonrpc": "2.0", "id": 7, "result": {"echo": "session.create", "params": {}}}

    asyncio.run(run())


def test_ws_without_key_is_closed_4401(stack):
    url = stack.replace("http://", "ws://") + "/ws"

    async def run():
        async with websockets.connect(url) as ws:
            with pytest.raises(websockets.ConnectionClosed) as exc:
                await ws.recv()
            assert exc.value.rcvd.code == 4401

    asyncio.run(run())


def test_summarize_llama_swap_states():
    assert summarize_llama_swap({"running": []}, "m")["state"] == "unloaded"
    assert summarize_llama_swap({"running": [{"model": "other", "state": "ready"}]}, "m")["state"] == "other-model"
    s = summarize_llama_swap({"running": [{"model": "m", "state": "ready"}]}, "m")
    assert s["state"] == "ready" and s["warming_up"] is False


def test_speak_calls_the_voice_directly_with_the_persona(stack):
    """Every persona -- and every subagent, which is one -- speaks with its own clip."""
    import base64
    r = httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "Good evening.", "voice": "bm_george"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["voice"] == "bm_george" and body["engine"] == "cosyvoice"
    assert body["data_url"].startswith("data:audio/mpeg;base64,")
    assert base64.b64decode(body["data_url"].split(",", 1)[1]) == b"MP3:bm_george:Good evening.:mp3"
    # No voice named: the default theme's (AURA's af_heart, T-0861).
    r = httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "Hello."})
    assert r.json()["voice"] == "af_heart"


def test_a_line_with_no_voice_is_said_in_the_primary_personas(stack, tmp_path, monkeypatch):
    """The deploy smoke of a55e109b: JARVIS primary, a line naming no voice came out in AURA's."""
    from aura_adapter import status as status_mod
    persona = tmp_path / "persona.json"
    persona.write_text(json.dumps({"schema": "jarvis.persona.v1", "active": "jarvis"}))
    monkeypatch.setattr(status_mod, "PERSONA_PATH", str(persona))
    r = httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "Hello."})
    assert r.json()["voice"] == "bm_george"
    p = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "Hello there."})
    assert p.headers["x-jarvis-voice"] == "bm_george", p.text


def test_speak_refuses_what_is_not_a_line_or_not_a_voice(stack):
    assert httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "  "}).status_code == 400
    assert httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "x", "voice": "../etc"}).status_code == 400
    assert httpx.post(f"{stack}/api/audio/speak", json={"text": "x"}).status_code == 401


def test_voice_fallback_speaks_a_voice_the_house_lacks_as_jarvis(stack):
    """T-0807: a persona whose reference clip the shim lacks was a 404, and every client went
    silent. It is spoken in the default theme's voice (AURA's since T-0861), and the answer names the voice used so a client can
    say it fell back."""
    import base64
    r = httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "Hello.", "voice": "nobody"})
    assert r.status_code == 200 and r.json()["voice"] == "af_heart" and r.headers["x-jarvis-voice"] == "af_heart"
    assert base64.b64decode(r.json()["data_url"].split(",", 1)[1]) == b"MP3:af_heart:Hello.:mp3"
    p = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "Hello there, again.", "voice": "nobody"})
    assert p.status_code == 200 and p.headers["x-jarvis-voice"] == "af_heart", p.text
    assert p.content.startswith(b"PCM0:af_heart:Hello there, again.;")
    # Said again from memory, as the default voice's line it is: the missing voice is not asked for again.
    again = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "Hello there, again.", "voice": "nobody"})
    assert again.status_code == 200 and again.headers["x-jarvis-voice"] == "af_heart"
    assert again.headers.get("x-jarvis-tts-cache") == "hit"
    # A voice the house has is its own, and is named too.
    own = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "Hi.", "voice": "bm_george"})
    assert own.headers["x-jarvis-voice"] == "bm_george"
    assert httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "Hi.", "voice": "bm_george"}).headers["x-jarvis-voice"] == "bm_george"


def test_speak_falls_back_to_hermes_serve_while_the_voice_warms(stack):
    """The shim answers 503 for its first minutes after a start (the warm-up
    holds its lock); the line still gets spoken, by hermes serve, and the reply
    says so -- the HUD shows which engine is speaking (V3 PART 2)."""
    r = httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "WARMING: hello", "voice": "bm_george"})
    assert r.status_code == 200 and r.json()["via"] == "hermes-serve"
    assert r.headers["x-jarvis-tts-engine"] == "hermes"
    assert "503" in r.headers["x-jarvis-tts-fallback"]


def test_speak_pcm_streams_the_line_as_it_is_made(stack):
    """The first piece arrives before the last exists: what time-to-first-word rides on."""
    with httpx.stream("POST", f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "Good evening.", "voice": "bm_george"}) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("audio/pcm") and r.headers["x-sample-rate"] == "24000"
        assert r.headers["x-jarvis-tts-engine"] == "cosyvoice"
        began = time.monotonic()
        arrivals = [(chunk, time.monotonic() - began) for chunk in r.iter_bytes()]
    assert b"".join(c for c, _ in arrivals) == b"PCM0:bm_george:Good evening.;PCM1:bm_george:Good evening.;PCM2:bm_george:Good evening.;"
    assert arrivals[0][1] < arrivals[-1][1] - 0.04, arrivals
    # The shim's warming 503 passes through (an unknown voice: test_voice_fallback_*).
    assert httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "WARMING: x", "voice": "bm_george"}).status_code == 503
    assert httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": " "}).status_code == 400
    assert httpx.post(f"{stack}/api/audio/speak-pcm", json={"text": "x"}).status_code == 401
    # Any other engine does not stream; the caller falls back to speak.
    assert httpx.post(f"http://127.0.0.1:{_PIPER_PORT[0]}/api/audio/speak-pcm", headers=AUTH, json={"text": "x"}).status_code == 404


def test_speak_pcm_at_the_rate_the_car_plays(stack):
    """T-0920: the car's guidance channel plays 16 kHz; a third of the 24 kHz bytes crossed the
    phone's link to be thrown away there. Streamed, from memory, or at the voice's own rate."""
    from aura_adapter.pcm_rate import Resampler

    line = {"text": "Turning left at the lights.", "voice": "bm_george"}
    voiced = b"".join(f"PCM{n}:bm_george:{line['text']};".encode() for n in range(3))
    expect = Resampler(24000, 16000).feed(voiced)
    made = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={**line, "rate": 16000})
    assert made.status_code == 200 and made.headers["x-sample-rate"] == "16000"
    assert made.content == expect and len(made.content) < len(voiced) * 0.7
    again = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={**line, "rate": 16000})
    assert again.headers.get("x-jarvis-tts-cache") == "hit" and again.content == expect
    # The cache keeps the voice's own rate: a caller that names none still gets 24 kHz.
    plain = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json=line)
    assert plain.headers["x-sample-rate"] == "24000" and plain.content == voiced
    assert httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={**line, "rate": 24000}).content == voiced
    for bad in (12345, "16000", True):
        assert httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={**line, "rate": bad}).status_code == 400


def test_speak_pcm_sends_the_first_clause_to_the_voice_first(stack):
    """T-0605: two requests to the voice, the clause then the rest, one unbroken PCM answer."""
    line = {"text": "The kitchen is warm, and the lab is cool.", "voice": "bm_george"}
    r = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={**line, "first_clause": True})
    body = r.content.decode()
    clause, rest = "The kitchen is warm,", "and the lab is cool."
    assert body.index(f"PCM2:bm_george:{clause};") < body.index(f"PCM0:bm_george:{rest};")
    assert body.count("PCM0:") == 2 and r.status_code == 200
    # Not the start of a reply (no flag): one request, as before.
    # (another voice: the flagged line above is in the phrase cache under "bm_george", whole.)
    plain = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={**line, "voice": "af_heart"})
    assert plain.content.decode().count("PCM0:") == 1


def test_speak_pcm_says_a_short_line_again_from_memory(stack):
    """'Pausing your music.' cost 1.1-1.3 s to the first byte every time on production (T-0211)."""
    line = {"text": "Skipping ahead, again.", "voice": "bm_george"}
    began = time.monotonic()
    first = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json=line)
    first_s = time.monotonic() - began
    assert first.status_code == 200 and "x-jarvis-tts-cache" not in first.headers
    began = time.monotonic()
    again = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json=line)
    again_s = time.monotonic() - began
    assert again.headers.get("x-jarvis-tts-cache") == "hit" and again.content == first.content
    assert again.headers["x-sample-rate"] == "24000" and again.headers["x-jarvis-tts-engine"] == "cosyvoice"
    # Against the first request, not a fixed 0.1 s: on a loaded runner (pipeline 713, beside the
    # hud jobs) one HTTP round trip alone took longer, and both requests pay that alike. The
    # shim's three 0.05 s pauses are the only difference, so a repeat that paid them again is
    # never 0.1 s quicker.
    assert again_s < first_s - 0.1, f"the shim's pauses were paid again ({again_s:.3f}s vs {first_s:.3f}s)"
    # Another voice is another line; a warming 503 is never kept.
    assert "x-jarvis-tts-cache" not in httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH,
                                                   json={**line, "voice": "af_heart"}).headers
    for _ in range(2):
        assert httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "WARMING: a"}).status_code == 503


def test_the_fast_paths_lines_are_made_before_anyone_asks(stack):
    """The first line of the pre-warm takes the fake voice ~0.2 s; it is never requested before here."""
    time.sleep(1.0)
    r = httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "Pausing your music."})
    assert r.status_code == 200 and r.headers.get("x-jarvis-tts-cache") == "hit"


def test_the_tts_cache_keeps_short_whole_lines_under_its_cap():
    from aura_adapter.tts_cache import PhraseCache
    cache = PhraseCache(max_bytes=10)
    assert cache.put("jarvis", "Done.", "24000", b"12345") and cache.get("jarvis", "Done.") == ("24000", b"12345")
    assert cache.get("bm_george", "Done.") is None
    assert not cache.put("jarvis", "x" * (PhraseCache.MAX_CHARS + 1), "24000", b"1"), "a reply, not an acknowledgement"
    assert not cache.put("jarvis", "Empty.", "24000", b""), "a stream with no audio is not a line"
    assert not cache.put("jarvis", "Huge.", "24000", b"x" * 11)
    cache.put("jarvis", "Two.", "24000", b"123")
    cache.get("jarvis", "Done.")  # used: the newest now
    cache.put("jarvis", "Three.", "24000", b"1234")  # 5 + 3 + 4 > 10: the least recent goes
    assert cache.get("jarvis", "Two.") is None and cache.get("jarvis", "Done.") and cache.get("jarvis", "Three.")
    assert cache.size <= 10


def test_the_tts_prewarm_list_is_the_fast_paths_own_lines():
    """Whole, as the phone sends a reply, and by sentence, as the console does."""
    from aura_adapter import fast_intent
    from aura_adapter.tts_cache import prewarm_lines
    lines = prewarm_lines()
    assert "Pausing your music." in lines and fast_intent.WAKING in lines and fast_intent.GONE in lines
    assert "Pausing your music. The main model is still waking up." in lines
    assert fast_intent.UNREACHABLE_LINE in lines and "I can still tell you the time or the date." in lines
    assert len(lines) == len(set(lines)) and all(len(line) <= 120 for line in lines)


def test_the_prewarm_makes_the_lines_in_the_active_personas_voice(stack, tmp_path, monkeypatch):
    """T-0807: the fast path's lines were made in one voice whoever was primary, so another
    persona's first 'Pausing your music.' paid the whole synthesis. (`stack` only so its teardown stops this server.)"""
    from aura_adapter import status as status_mod
    persona = tmp_path / "persona.json"
    persona.write_text(json.dumps({"schema": "jarvis.persona.v1", "active": "jarvis"}))
    monkeypatch.setattr(status_mod, "PERSONA_PATH", str(persona))
    heard: list[str] = []
    shim = FastAPI()

    @shim.post("/v1/audio/speech")
    async def speech(request: Request):
        from starlette.responses import Response as R
        heard.append((await request.json())["voice"])
        return R(b"PCM", media_type="audio/pcm", headers={"x-sample-rate": "24000"})

    port = _free_port()
    _serve(shim, port)
    with TestClient(create_app(Settings(api_key=APP_KEY, tts_engine="cosyvoice", tts_url=f"http://127.0.0.1:{port}"))):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not heard:
            time.sleep(0.05)
    assert heard and set(heard) == {"bm_george"}, heard


def test_the_prewarm_makes_the_lines_again_after_a_persona_switch_in_the_new_voice(stack, tmp_path, monkeypatch):
    """T-1320: the pre-warm ran once at start, so the persona switched to later paid the whole
    synthesis for its first fixed line. A switch is picked up within the watch period, once per switch."""
    from aura_adapter import status as status_mod
    from aura_adapter.tts_cache import prewarm_lines
    monkeypatch.setattr(app_mod, "PERSONA_WATCH_S", 0.05)
    persona = tmp_path / "persona.json"
    persona.write_text(json.dumps({"schema": "jarvis.persona.v1", "active": "jarvis"}))
    monkeypatch.setattr(status_mod, "PERSONA_PATH", str(persona))
    heard: list[str] = []
    shim = FastAPI()

    @shim.post("/v1/audio/speech")
    async def speech(request: Request):
        from starlette.responses import Response as R
        heard.append((await request.json())["voice"])
        return R(b"PCM", media_type="audio/pcm", headers={"x-sample-rate": "24000"})

    def wait_for(voice: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and voice not in heard:
            time.sleep(0.05)

    port = _free_port()
    _serve(shim, port)
    with TestClient(create_app(Settings(api_key=APP_KEY, tts_engine="cosyvoice", tts_url=f"http://127.0.0.1:{port}"))):
        wait_for("bm_george")
        time.sleep(0.5)  # ten watch periods: the unchanged persona is not warmed again
        first = heard.count("bm_george")
        assert first == len(prewarm_lines()) and set(heard) == {"bm_george"}, heard
        persona.write_text(json.dumps({"schema": "jarvis.persona.v1", "active": "aura"}))
        wait_for("af_heart")
        time.sleep(0.5)
        assert heard.count("af_heart") == first and heard.count("bm_george") == first, heard


def test_voice_fallback_in_the_prewarm_warms_jarvis_when_the_active_persona_has_no_clip(tmp_path, monkeypatch, caplog):
    """Review of T-0807: a house without the primary persona's voice, active at start, stopped the
    pre-warm on the first 404, so not even the default voice's lines (what she is then said in; AURA's
    af_heart since T-0861) were made. JARVIS stands in for any persona whose voice is missing."""
    from aura_adapter import status as status_mod
    persona = tmp_path / "persona.json"
    persona.write_text(json.dumps({"schema": "jarvis.persona.v1", "active": "jarvis"}))
    monkeypatch.setattr(status_mod, "PERSONA_PATH", str(persona))
    heard: list[str] = []
    shim = FastAPI()

    @shim.post("/v1/audio/speech")
    async def speech(request: Request):
        from starlette.responses import Response as R
        voice = (await request.json())["voice"]
        heard.append(voice)
        if voice != "af_heart":
            return R(status_code=404, content="no such voice")
        return R(b"PCM", media_type="audio/pcm", headers={"x-sample-rate": "24000"})

    port = _free_port()
    _serve(shim, port)
    caplog.set_level(logging.WARNING, logger="aura_adapter")
    with TestClient(create_app(Settings(api_key=APP_KEY, tts_engine="cosyvoice", tts_url=f"http://127.0.0.1:{port}"))):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and heard.count("af_heart") < 2:
            time.sleep(0.05)
    assert heard[:2] == ["bm_george", "af_heart"] and heard.count("bm_george") == 1, heard
    assert not [r for r in caplog.records if "pre-warm stopped" in r.getMessage()]


def test_voice_fallback_with_no_jarvis_clip_either_is_still_a_404(tmp_path):
    """The retry is one step, to JARVIS: a speech URL that has no voices at all (AUDIT B12) is a
    404 naming the voice, not a loop and not a 200."""
    port = _free_port()
    _serve(FastAPI(), port)
    with TestClient(create_app(Settings(api_key=APP_KEY, tts_engine="cosyvoice", tts_url=f"http://127.0.0.1:{port}"))) as c:
        for route in ("/api/audio/speak", "/api/audio/speak-pcm"):
            r = c.post(route, headers=AUTH, json={"text": "Hello.", "voice": "bm_george"})
            assert r.status_code == 404, (route, r.text)


def test_a_refused_prewarm_says_so(stack, caplog):
    """AUDIT B12: a speech URL answering 404 left the cache cold without a line in the log,
    and the first reply of the day paid for it. (`stack` only so its teardown stops this server.)"""
    port = _free_port()
    _serve(FastAPI(), port)  # no routes: 404 to everything
    caplog.set_level(logging.WARNING, logger="aura_adapter")
    said = lambda: [r.getMessage() for r in caplog.records if "pre-warm" in r.getMessage()]
    with TestClient(create_app(Settings(api_key=APP_KEY, tts_engine="cosyvoice", tts_url=f"http://127.0.0.1:{port}"))):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not said():
            time.sleep(0.05)
    assert said() and "404" in said()[0], said()


def test_a_prewarm_that_never_hears_the_voice_says_so(stack, caplog, monkeypatch):
    """AUDIT B12's other half: a dead speech host was retried to the deadline, then dropped in silence."""
    monkeypatch.setattr(app_mod, "PREWARM_DEADLINE_S", 0)
    caplog.set_level(logging.WARNING, logger="aura_adapter")
    said = lambda: [r.getMessage() for r in caplog.records if "pre-warm gave up" in r.getMessage()]
    with TestClient(create_app(Settings(api_key=APP_KEY, tts_engine="cosyvoice", tts_url=f"http://127.0.0.1:{_free_port()}"))):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not said():
            time.sleep(0.05)
    assert said(), [r.getMessage() for r in caplog.records]


def test_a_clip_over_the_limit_is_refused_before_whisper(stack, monkeypatch):
    """AUDIT B13: a ten-minute upload held the shared upstream timeout. Sized and chunked
    bodies are both counted; under the limit the same body reaches the words as before."""
    monkeypatch.setattr(app_mod, "TRANSCRIBE_MAX_BYTES", 1000)
    body = json.dumps({"data_url": "data:audio/webm;base64," + "A" * 2000, "mime_type": "audio/webm"}).encode()
    for content in (body, iter([body[:500], body[500:]])):
        r = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH, content=content)
        assert r.status_code == 413 and "at most" in r.json()["detail"], r.text
    monkeypatch.setattr(app_mod, "TRANSCRIBE_MAX_BYTES", 10_000)
    assert httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH, content=body).json()["transcript"] == "echo:AAAA"


def test_a_client_gone_mid_stream_is_logged(stack, caplog):
    """AUDIT B11's repro: drop the client in the middle of a spoken reply; the relay says how it ended."""
    caplog.set_level(logging.INFO, logger="aura_adapter")
    said = lambda: [r.getMessage() for r in caplog.records if r.getMessage().startswith("ws relay ended")]

    async def run():
        async with websockets.connect(stack.replace("http://", "ws://") + "/api/audio/speak-stream",
                                      additional_headers=AUTH) as ws:
            assert (await ws.recv()).startswith(b"PCM")
    asyncio.run(run())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not said():
        time.sleep(0.05)
    assert "ws relay ended: the client closed" in said(), said()


def test_a_client_gone_before_the_relays_last_word_is_logged(caplog):
    """AUDIT B11: the relay's goodbye to a client already gone was `except Exception: pass`.
    The send says so at info; the close, which fails on every ordinary hang-up, at debug."""
    class Gone:
        async def send_text(self, text):
            raise RuntimeError("closed")

        async def close(self):
            raise RuntimeError("closed")

    caplog.set_level(logging.DEBUG, logger="aura_adapter")
    asyncio.run(app_mod._quietly("telling the client the backend is down", Gone().send_text("{}")))
    asyncio.run(app_mod._quietly("closing the client socket", Gone().close(), logging.DEBUG))
    got = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "aura_adapter"]
    assert (logging.INFO, "ws relay: telling the client the backend is down failed: RuntimeError") in got, got
    assert (logging.DEBUG, "ws relay: closing the client socket failed: RuntimeError") in got, got


def test_status_reports_the_voices_own_timings(stack):
    """Brief section 2.7: p50/p95 of the server's share of a voice reply, from a rolling window."""
    from aura_adapter.status import percentiles

    r = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                   json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"})
    # T-0605: the turn's own stages ride along as Server-Timing, and the window keeps them.
    timing = dict(p.split(";dur=") for p in r.headers["server-timing"].split(", "))
    assert set(timing) == {"receive", "decode", "gate", "gate_wait", "transcribe"}, timing
    assert float(timing["transcribe"]) >= float(timing["decode"]) >= 0
    httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": "Timing this line.", "voice": "bm_george"})
    voice = httpx.get(f"{stack}/api/status", headers=AUTH).json()["voice"]
    for step in ("transcribe_ms", "first_pcm_ms", "receive_ms", "decode_ms", "gate_ms", "gate_wait_ms"):
        assert voice[step]["n"] >= 1 and 0 <= voice[step]["p50"] <= voice[step]["p95"], voice
    # Nearest rank: every figure is a timing that happened, none interpolated.
    assert percentiles(range(10, 101, 10)) == {"n": 10, "p50": 50, "p95": 100}
    assert percentiles([]) == {"n": 0, "p50": None, "p95": None}


def test_spoken_drops_the_marks_a_voice_would_pronounce():
    """The owner heard "asterisk" and "double quote" on a drive (2026-10-08)."""
    from aura_adapter.spoken import spoken

    assert spoken('She said "on my way" -- **noted**.') == "She said on my way -- noted."
    assert spoken("# Plan\n- `ledger_add` the [renewal](https://example.com/z)\n- done") == "Plan ledger add the renewal. done."
    assert spoken("It\u2019s Lori\u2019s \u201cyes\u201d, isn't it?") == "It\u2019s Lori\u2019s yes, isn't it?"
    assert spoken('"**"') == ""


def test_spoken_says_money_units_and_numbers_as_words():
    """The house's Piper has said "$4.50" as dollars since M73 (jarvis-core
    speech_text.spoken_form); the phone, the car and the console speak through
    this door instead, and CosyVoice read the signs as written (2026-10-08)."""
    from aura_adapter.spoken import spoken

    assert spoken("It is 22°C, ~$4.50 a litre, up 3% and 2x faster.") == (
        "It is 22 degrees Celsius, about 4.50 dollars a litre, up 3 percent and 2 times faster."
    )
    assert spoken("Disk at 40 GB, 49/100 & falling → fine.") == (
        "Disk at 40 gigabytes, 49 out of 100 and falling to fine."
    )


def test_spoken_never_spells_what_the_screen_shows():
    """The owner heard a reply spelled out (T-0729, 2026-11-08): what reaches the voice is
    words, never an id, a path, code or a symbol it would name character by character."""
    from aura_adapter.spoken import spoken

    # An id is the one on screen; a UUID's dashes were read as "to".
    assert spoken("Session 4f9c2a7e-1b3d-4e8f-9a6b-2c5d7e9f1a3b is open.") == "Session the one on screen is open."
    assert spoken("Deployed `3fed2454` at noon.") == "Deployed the one on screen at noon."
    assert spoken("The key sk_live_51HxYz9KqLmN0pQrStUv is set.") == "The key the one on screen is set."
    assert spoken("Pulled sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08.") == (
        "Pulled the one on screen."
    )
    # A path is its file name, not slash by slash.
    assert spoken("I changed /opt/jarvis/deploy/docker-compose.yml for you.") == "I changed docker-compose.yml for you."
    assert spoken("See deploy/entrypoint.sh.") == "See entrypoint.sh."
    # A fenced block is said to be there: never read out, never silently gone.
    assert spoken("Run this:\n```bash\nmake test\n```\nThen tell me.") == "Run this: The code is on screen. Then tell me."
    assert spoken("Run this:\n```bash\nmake te") == "Run this: The code is on screen."
    # Each list item ends a sentence, so the voice pauses between them.
    assert spoken("Options:\n- **Google**\n- Facebook\n1. MetaMask") == "Options: Google. Facebook. MetaMask."
    # Symbols a voice would name are words, or nothing.
    assert spoken("If a+b != c then x=y => {done} [ok] <3 ^") == "If a plus b is not c then x equals y to done ok 3"
    # Nothing is left standing before the full stop where an image was dropped.
    assert spoken("Read ![chart](https://example.com/c.png) .") == "Read."


def test_spoken_leaves_prose_alone():
    """T-0729's rules take ids, paths and code, never words: these are said exactly as before."""
    from aura_adapter.spoken import spoken

    assert spoken("Internationalisation finished on 2026-11-09 at 10:30, on v1.2.3, for $4.50 and 1,500 users.") == (
        "Internationalisation finished on November 9, 2026 at 10:30, on version 1 point 2 point 3, "
        "for 4.50 dollars and 1,500 users."
    )
    assert spoken("The qwen3-embedding-0.6b model and claude-sonnet-4-5 both answered in 1.5 s.") == (
        "The qwen3-embedding-0.6b model and claude-sonnet-4-5 both answered in 1.5 s."
    )
    assert spoken("Call +447700900123, use email/password, or wait until 20261109.") == (
        "Call plus 447700900123, use email/password, or wait until 20261109."
    )


def test_spoken_two_identifiers_in_one_sentence_are_one_mention():
    from aura_adapter.spoken import spoken

    assert spoken("Compare 3fed2454 and 6e18631f, then pick one.") == "Compare the ones on screen, then pick one."
    assert spoken("Ids 3fed2454, 6e18631f, b41a8dae.") == "Ids the ones on screen."


def test_spoken_keeps_what_the_words_mean():
    """T-0729's review: each of these lost its meaning, or the rest of the reply, to a rule
    meant for code."""
    from aura_adapter.spoken import spoken

    # Only a fence that starts a line opens a block; three ticks in prose are not one.
    assert spoken("Wrap it in ``` fences. Then reload the page and tell me.") == (
        "Wrap it in fences. Then reload the page and tell me."
    )
    assert spoken("Run:\n~~~\nmake test\n~~~\nDone.") == "Run: The code is on screen. Done."
    # Slashes between words are words; a path needs a root or a file name.
    assert spoken("Choose yes/no/maybe, he/she/they, e.g./i.e. both.") == (
        "Choose yes/no/maybe, he/she/they, for example/that is both."  # spoken_form's, as before
    )
    assert spoken("It lives in /var/lib/postgres15/data now.") == "It lives in data now."
    # Comparisons and powers are said; brackets part words instead of joining them.
    assert spoken("2^10 = 1024, x > 5, y < 3, z >= 1, w <= 9.") == (
        "2 to the power of 10 equals 1024, x greater than 5, y less than 3, z at least 1, w at most 9."
    )
    assert spoken("array[0] holds it.") == "array 0 holds it."
    # A dotfile keeps its space; a URL keeps the full stop after it.
    assert spoken("Edit your .env file.") == "Edit your .env file."
    assert spoken("Open https://wallet.nu.fi/auth. Then log in.") == "Open wallet dot nu dot fi. Then log in."


def test_spoken_reads_a_long_run_of_marks_in_linear_time():
    """A token of 20,000 dots and a letter took seconds through a backtracking regex."""
    import time

    from aura_adapter.spoken import spoken

    started = time.monotonic()
    spoken("x" + "." * 20000 + "y")
    assert time.monotonic() - started < 1.0


_NUFI = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "speech-nufi.json").read_text(encoding="utf-8"))


def test_spoken_says_the_nufi_reply_in_whole_sentences():
    """T-0729, the owner's reply (tests/contracts/speech-nufi.json). The console's lines, which
    speech.test.ts streams the reply into, pass this door unchanged; the phone sends the whole
    reply in one call and hears the same words."""
    from aura_adapter.spoken import spoken

    for line in _NUFI["said"]:
        assert spoken(line) == line
    assert spoken(_NUFI["reply"]) == _NUFI["phone"]
    for said in [*_NUFI["said"], _NUFI["phone"]]:
        lone = [t for t in said.split() if len(t) == 1 and t not in {"a", "A", "I", "—", "–"}]
        assert "*" not in said and "- " not in said and not lone, said


def test_the_adapter_speaks_by_the_house_rules_word_for_word():
    """One set of rules, not two: the adapter's image cannot install jarvis-core,
    so it carries a copy of speech_text.py, and this is what keeps it a copy."""
    import aura_adapter

    copy = (Path(aura_adapter.__file__).parent / "speech_text.py").read_text(encoding="utf-8")
    source = (Path(__file__).resolve().parents[2] / "aura-core" / "aura" / "voice" / "speech_text.py").read_text(encoding="utf-8")
    header, _, body = copy.partition("\n\n")
    assert "@generated" in header and body == source, (
        "aura_adapter/speech_text.py drifted from aura-core/aura/voice/speech_text.py: "
        "edit jarvis-core's, then run `make speech-copy`"
    )


_SPEECH_CORPUS = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "contracts" / "speech-text.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", _SPEECH_CORPUS, ids=[c["why"][:40] for c in _SPEECH_CORPUS])
def test_the_adapters_copy_says_every_case_of_the_speech_corpus(case):
    """The phone, the car and the console speak through this copy (T-0218): it answers the
    same golden corpus jarvis-core's suite reads."""
    from aura_adapter.speech_text import spoken_form

    assert spoken_form(case["written"]) == case["said"]


def test_speak_pcm_says_the_words_not_the_marks(stack):
    with httpx.stream("POST", f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": '**"Hi."**', "voice": "bm_george"}) as r:
        assert r.status_code == 200
        assert b"".join(r.iter_bytes()).startswith(b"PCM0:bm_george:Hi.;")
    # A line that was only marks is nothing to say, not a request for silence audio.
    assert httpx.post(f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": '"*"'}).status_code == 400
    # The phone and the car send a whole reply, the console one sentence: both reach the voice
    # through spoken() on this route (T-0729, the owner's NUFI reply).
    for text, said in [(_NUFI["reply"], _NUFI["phone"]), *((line, line) for line in _NUFI["said"])]:
        with httpx.stream("POST", f"{stack}/api/audio/speak-pcm", headers=AUTH, json={"text": text, "voice": "bm_george"}) as r:
            assert b"".join(r.iter_bytes()).startswith(f"PCM0:bm_george:{said};".encode()), text


def test_speak_relays_to_hermes_serve_on_any_other_engine(stack):
    """Piper has one voice; the relay is what it always was."""
    r = httpx.post(f"http://127.0.0.1:{_PIPER_PORT[0]}/api/audio/speak", headers=AUTH, json={"text": "Hello.", "voice": "bm_george"})
    assert r.status_code == 200 and r.json()["via"] == "hermes-serve"


def test_a_cold_model_answers_the_time_without_relaying(stack):
    """The fake llama-swap says `starting`: the time is answered here, with the
    frames a Hermes turn ends with, and Hermes (the echo) never sees the prompt.
    Anything outside the grammar is still relayed, as `_prompt_with` shows for lights."""
    async def run():
        async with websockets.connect(stack.replace("http://", "ws://") + "/ws", additional_headers=AUTH) as ws:
            await ws.recv()  # gateway.ready
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "prompt.submit",
                                      "params": {"session_id": "s-cold", "text": "What time is it?"}}))
            return [json.loads(await ws.recv()) for _ in range(2)]
    result, done = asyncio.run(run())
    assert result == {"jsonrpc": "2.0", "id": 5, "result": {"status": "streaming"}}
    assert done["params"]["type"] == "message.complete" and done["params"]["session_id"] == "s-cold"
    assert "still waking up" in done["params"]["payload"]["text"]


def test_cpu_voice_is_replaced_by_the_house_voice(stack):
    """A CosyVoice that lost its GPU says `device: cpu` on /health and takes minutes a line
    (2026-10-06); while it does, both speak paths answer from the house's Piper instead."""
    cpu_shim = fake_cosyvoice()

    @cpu_shim.get("/health")
    async def health():
        return {"status": "ok", "device": "cpu"}

    base = _adapter_beside(cpu_shim)

    r = httpx.post(f"{base}/api/audio/speak-pcm", headers=AUTH, json={"text": "Lights on.", "voice": "jarvis"})
    assert r.status_code == 200 and r.headers["x-jarvis-tts-engine"] == "house"
    assert r.headers["x-sample-rate"] == "22050" and r.content == b"PIPER:Lights on."
    r = httpx.post(f"{base}/api/audio/speak", headers=AUTH, json={"text": "Lights on.", "voice": "jarvis"})
    assert r.status_code == 200 and r.json()["engine"] == "house"
    assert r.json()["data_url"].startswith("data:audio/wav;base64,")
    # The shared stack's shim has no /health device: it stays CosyVoice.
    r = httpx.post(f"{stack}/api/audio/speak", headers=AUTH, json={"text": "Hi.", "voice": "jarvis"})
    assert r.json()["engine"] == "cosyvoice"



def _adapter_beside(shim, house: bool = True) -> str:
    """An adapter whose voice is [shim], with the fake house (or a dead house port) and no
    Hermes: its base URL."""
    shim_port, adapter_port, house_port = _free_port(), _free_port(), _free_port()
    _serve(shim, shim_port)
    if house:
        _serve(fake_house(), house_port)
    settings = Settings(api_key=APP_KEY, hermes_api_url="http://127.0.0.1:9", hermes_api_key=API_KEY,
                        hermes_serve_url="http://127.0.0.1:9", hermes_serve_token=SERVE_TOKEN,
                        llama_swap_url="http://127.0.0.1:9", model="m", tts_engine="cosyvoice",
                        tts_url=f"http://127.0.0.1:{shim_port}",
                        house_url=f"http://127.0.0.1:{house_port}", house_token="house-token")
    _serve(create_app(settings), adapter_port)
    return f"http://127.0.0.1:{adapter_port}"

def test_a_refused_voice_close_to_the_owner_can_be_claimed_once_as_a_print_for_its_microphone(stack):
    """The owner, 2026-10-07: enrolled on the console, refused in the car. "That was me"
    files the car's utterance under the car, so the car is next matched by car prints."""
    ENROLLED.clear()
    r = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                   json={"data_url": _wav_url(b"NEARLY"), "mime_type": "audio/wav", "surface": "car"})
    body = r.json()
    assert body["transcript"] == "" and body["speaker"]["dropped"] is True
    claim = body["speaker"]["claim"]
    held = httpx.get(f"{stack}/api/speaker/unrecognised", headers=AUTH).json()["unrecognised"]
    assert [(h["id"], h["surface"], h["nearest"]) for h in held] == [(claim, "car", "owner")]
    assert "audio" not in held[0], "the listing never carries the recording"

    mine = httpx.post(f"{stack}/api/speaker/unrecognised/{claim}/mine", headers=AUTH).json()
    assert mine == {"ok": True, "label": "owner", "source": "car"}
    assert [(e["label"], e["source"], e["body"][:6]) for e in ENROLLED] == [("owner", "car", b"NEARLY")]
    again = httpx.post(f"{stack}/api/speaker/unrecognised/{claim}/mine", headers=AUTH)
    assert again.status_code == 404, "an utterance is claimed once"


def test_a_turn_asks_the_house_to_learn_for_its_microphone_but_a_private_one_does_not(stack):
    """T-0771: the house keeps a print of a turn it is sure of, for the microphone it came
    from. A private turn (T-0462) keeps nothing of the voice."""
    VERIFIED.clear()
    httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
               json={"data_url": _wav_url(b"OWNER"), "mime_type": "audio/wav", "surface": "car",
                     "transcript": "lights on"})
    httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
               json={"data_url": _wav_url(b"OWNER"), "mime_type": "audio/wav", "surface": "android",
                     "transcript": "lights off", "private": True})
    assert VERIFIED == [("car", "1"), ("android", "0")]


def test_a_voice_nowhere_near_anyone_is_never_held_to_be_claimed(stack):
    body = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                      json={"data_url": _wav_url(b"NOBODY"), "mime_type": "audio/wav"}).json()
    assert body["speaker"]["dropped"] is True and "claim" not in body["speaker"]
    assert httpx.post(f"{stack}/api/speaker/unrecognised/x/mine", headers=AUTH).status_code == 404



def test_words_come_from_the_card_when_its_whisper_answers_and_the_gate_still_decides(stack):
    """2026-10-08: Hermes' CPU Whisper took ~3 s a car request. A shim that answers
    /v1/audio/transcriptions is asked first; the speaker gate applies to its words the
    same; a shim without it (the shared stack's) leaves Hermes answering."""
    gpu_shim = fake_cosyvoice()

    @gpu_shim.post("/v1/audio/transcriptions")
    async def words(request: Request):
        body = await request.json()
        assert body["data_url"].startswith("data:audio/wav;base64,")
        return {"transcript": "from the card", "engine": "whisper-gpu"}

    base = _adapter_beside(gpu_shim)

    body = httpx.post(f"{base}/api/audio/transcribe", headers=AUTH,
                      json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"}).json()
    assert body["transcript"] == "from the card" and body["provider"] == "whisper-gpu"
    assert body["speaker"]["label"] == "Ted" and "speaker_ticket" in body
    refused = httpx.post(f"{base}/api/audio/transcribe", headers=AUTH,
                         json={"data_url": _wav_url(b"NOBODY"), "mime_type": "audio/wav"}).json()
    assert refused["transcript"] == "" and refused["speaker"]["dropped"] is True
    # The shared stack's shim has no transcriptions route: Hermes answers, as before.
    r = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                   json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"})
    assert r.json()["transcript"].startswith("echo:")


def test_the_turn_probe_cuts_the_first_sentence_as_the_console_does_and_times_the_first_byte(stack):
    """T-0605: scripts/hermes-probes/turn_latency_probe.py, end of input -> first sentence -> first PCM byte."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "turn_latency_probe", Path(__file__).resolve().parents[2] / "scripts/hermes-probes/turn_latency_probe.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.first_sentence("A kilowatt hour is one kilowatt for an hour. It is") == \
        "A kilowatt hour is one kilowatt for an hour."
    assert probe.first_sentence("A kilowatt hour is one kilowatt") is None  # still arriving
    assert probe.first_sentence("It is 22.5 degrees") is None  # a decimal is not an end
    assert 0 < probe.first_pcm_ms(stack, APP_KEY, "Good evening, sir.", True) < 5000
    assert probe.summarize({"a": [10, 20, 30, 40]}) == {"a": {"p50": 20, "p95": 40}}

    # T-0644: the probe times a spoken turn unless told it is typed, or it measures the typed effort.
    sent: list[dict] = []

    class Rpc:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def create_session(self):
            return "s1"

        async def call(self, method, params):
            sent.append(params)

        async def events(self, timeout):
            yield {"type": "message.complete", "payload": {"text": "A kilowatt hour is energy."}}

    probe.connect = lambda url: Rpc()
    probe.first_pcm_ms = lambda *a: 1.0
    for voice in (True, False):
        asyncio.run(probe.turn("http://x", "k", "q", True, 5, voice))
    assert sent[0]["voice"] is True and "voice" not in sent[1]


def test_the_latency_probe_reads_each_stage_from_a_private_turn(tmp_path, capsys, monkeypatch):
    """T-0605: scripts/hermes-probes/voice_latency_probe.py prints p50/p95 per stage as one JSON line."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "voice_latency_probe", Path(__file__).resolve().parents[2] / "scripts/hermes-probes/voice_latency_probe.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.pct([10, 20, 30, 40], 0.5) == 20 and probe.pct([], 0.95) is None
    assert probe.parse_timing("receive;dur=1.2, decode;dur=900.0") == {"receive": 1.2, "decode": 900.0}

    private_seen: list[object] = []
    gpu_shim = fake_cosyvoice()

    @gpu_shim.post("/v1/audio/transcriptions")
    async def words(request: Request):
        private_seen.append(await request.json())
        return {"transcript": "from the card", "engine": "whisper-gpu"}

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"GUEST" + b"\x00" * 64)
    monkeypatch.setattr("sys.argv", ["p", "--base", _adapter_beside(gpu_shim), "--wav", str(clip),
                                     "--n", "3", "--key", AUTH["Authorization"].split()[-1]])
    assert probe.main() == 0
    line = json.loads(capsys.readouterr().out)
    assert line["n"] == 3 and len(private_seen) == 3
    assert {"client", "receive", "decode", "gate", "gate_wait", "transcribe"} <= set(line["stages"])
    assert line["stages"]["transcribe"]["p50"] <= line["stages"]["transcribe"]["p95"]


def test_the_card_is_primed_with_the_houses_names_people_then_devices(tmp_path, monkeypatch):
    """T-0426: the card's Whisper had only a fixed example prompt, so a person's or a
    device's own name came back misspelt. The adapter reads the house's rooms and names (it
    holds the house token) and sends them with the audio; a house it cannot reach sends none."""
    monkeypatch.setattr(app_mod, "MEMORY_FILE", tmp_path / "none.json")
    sent: list[object] = []
    gpu_shim = fake_cosyvoice()

    @gpu_shim.post("/v1/audio/transcriptions")
    async def words(request: Request):
        sent.append((await request.json()).get("names"))
        return {"transcript": "text Isa", "engine": "whisper-gpu"}

    for base in (_adapter_beside(gpu_shim), _adapter_beside(gpu_shim, house=False)):
        r = httpx.post(f"{base}/api/audio/transcribe", headers=AUTH,
                       json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"})
        assert r.json()["transcript"] == "text Isa"
    assert sent == ["Den, Sun loft, Isa, Den lamp, Den temperature", None]


def test_the_card_hears_the_language_chosen_on_the_console_and_english_when_none(monkeypatch):
    """T-1487: the console's Speech language reached the house's own pipelines (T-1484) but
    not the apps': the adapter sent the card no language and the card's Whisper assumed
    English. The adapter now asks the house what was chosen and sends it; an app that names
    a language itself is not overruled; nothing chosen, or no house, sends none, as before."""
    monkeypatch.setattr(app_mod, "HOUSE_LANGUAGE_TTL_S", 0.0)
    sent: list[object] = []
    gpu_shim = fake_cosyvoice()

    @gpu_shim.post("/v1/audio/transcriptions")
    async def words(request: Request):
        sent.append((await request.json()).get("language"))
        return {"transcript": "hallo", "engine": "whisper-gpu"}

    house, no_house = _adapter_beside(gpu_shim), _adapter_beside(gpu_shim, house=False)

    def turn(base: str, **extra: str) -> None:
        r = httpx.post(f"{base}/api/audio/transcribe", headers=AUTH,
                       json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav", **extra})
        assert r.json()["transcript"] == "hallo"

    try:
        turn(house)
        HOUSE_LANGUAGE[0] = "de"
        turn(house)
        turn(house, language="fr")
        turn(no_house)
        HOUSE_LANGUAGE[0] = None
        turn(house)
    finally:
        HOUSE_LANGUAGE[0] = None
    assert sent == [None, "de", "fr", None, None]


def test_the_chosen_language_is_read_once_a_period_and_nonsense_is_no_choice(monkeypatch):
    """The read is one small request per HOUSE_LANGUAGE_TTL_S, not one per turn; a change
    is heard after it. A house that answers nonsense is not a choice."""
    monkeypatch.setattr(app_mod, "HOUSE_LANGUAGE_TTL_S", 60.0)
    sent: list[object] = []
    gpu_shim = fake_cosyvoice()

    @gpu_shim.post("/v1/audio/transcriptions")
    async def words(request: Request):
        sent.append((await request.json()).get("language"))
        return {"transcript": "hallo", "engine": "whisper-gpu"}

    base = _adapter_beside(gpu_shim)
    try:
        HOUSE_LANGUAGE[0] = "de"
        for _ in range(2):
            httpx.post(f"{base}/api/audio/transcribe", headers=AUTH,
                       json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"})
            HOUSE_LANGUAGE[0] = None
    finally:
        HOUSE_LANGUAGE[0] = None
    assert sent == ["de", "de"]
    from aura_adapter.app import chosen_language_of
    assert chosen_language_of({"language": "de"}) == "de"
    assert chosen_language_of({"language": None}) is None
    assert chosen_language_of({"language": "  "}) is None
    assert chosen_language_of({"language": 7}) is None and chosen_language_of(["de"]) is None
    assert chosen_language_of({"language": "x" * 11}) is None, "the setting's own bound is 2..10"


def test_a_slow_transcriber_breaches_its_deadline_once_logged_and_counted_on_status(monkeypatch, caplog):
    """T-0589: each voice hop has a deadline, and one past it is a warning line and a count on
    /api/status, not only a slower p95. Two turns, one slow: one breach."""
    from aura_adapter import turn_log
    monkeypatch.setattr(turn_log, "DEADLINES_MS", {**turn_log.DEADLINES_MS, "transcribe_ms": 250.0})
    slow = {"s": 0.4}
    gpu_shim = fake_cosyvoice()

    @gpu_shim.post("/v1/audio/transcriptions")
    async def words(request: Request):
        await asyncio.sleep(slow["s"])
        return {"transcript": "lights on", "engine": "whisper-gpu"}

    base = _adapter_beside(gpu_shim)
    caplog.set_level(logging.WARNING, logger="aura_adapter.turns")
    for wait in (0.4, 0.0):
        slow["s"] = wait
        r = httpx.post(f"{base}/api/audio/transcribe", headers=AUTH, timeout=10,
                       json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"})
        assert r.json()["transcript"] == "lights on"
    hops = httpx.get(f"{base}/api/status", headers=AUTH, timeout=10).json()["voice_deadlines"]
    assert hops["transcribe"]["deadline_ms"] == 250 and hops["transcribe"]["breaches"] == 1
    # T-1431: the count has a time beside it, so an old breach does not read as a present one.
    assert hops["transcribe"]["last_hour"] == 1 and 0 <= hops["transcribe"]["last_breach_s"] <= 10
    assert hops["first_token"]["last_hour"] == 0 and hops["first_token"]["last_breach_s"] is None
    assert hops["first_token"]["breaches"] == 0 and hops["first_audio"]["breaches"] == 0
    breached = [r.getMessage() for r in caplog.records if "deadline" in r.getMessage()]
    assert len(breached) == 1 and breached[0].startswith("voice deadline breached hop=transcribe ")


def test_the_card_is_primed_with_the_owners_contacts_and_places_a_new_one_on_the_next_turn(tmp_path, monkeypatch):
    """T-0586: the card's Whisper knew the house's devices but not the people and places the
    owner had told Jarvis about (people_find, place_list), so "text Marisol" came back as
    "text Mary Sol". Hermes keeps them on the home the adapter mounts read-only; it reads
    them at each turn, so a contact added a moment ago is primed on the very next one, even
    with the house unreachable."""
    store = tmp_path / "memory.json"
    monkeypatch.setattr(app_mod, "MEMORY_FILE", store)
    store.write_text(json.dumps({"people": [{"name": "Marisol", "relations": ["sister"]}],
                                 "places": [{"label": "Gran's"}], "routines": []}))
    sent: list[object] = []
    gpu_shim = fake_cosyvoice()

    @gpu_shim.post("/v1/audio/transcriptions")
    async def words(request: Request):
        sent.append((await request.json()).get("names"))
        return {"transcript": "text Marisol", "engine": "whisper-gpu"}

    house, no_house = _adapter_beside(gpu_shim), _adapter_beside(gpu_shim, house=False)

    def turn(base: str) -> None:
        httpx.post(f"{base}/api/audio/transcribe", headers=AUTH,
                   json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav"})

    turn(house)
    store.write_text(json.dumps({"people": [{"name": "Marisol"}, {"name": "Thibault"}],
                                 "places": [{"label": "Gran's"}]}))
    turn(house)
    turn(no_house)
    store.write_text("{not json")
    turn(no_house)
    assert sent == ["Marisol, Gran's, Den, Sun loft, Isa, Den lamp, Den temperature",
                    "Marisol, Thibault, Gran's, Den, Sun loft, Isa, Den lamp, Den temperature",
                    "Marisol, Thibault, Gran's", None]


def test_house_names_are_people_first_then_what_can_be_switched_capped_on_a_name():
    states = [{"entity_id": f"light.l{i}", "attributes": {"friendly_name": f"Lamp {i}"}} for i in range(300)]
    states.insert(0, {"entity_id": "sensor.t", "attributes": {"friendly_name": "Attic temperature"}})
    states.append({"entity_id": "person.isa", "attributes": {"friendly_name": "Isa"}})
    names = house_names(states)
    assert names.startswith("Isa, Lamp 0, ") and len(names) <= 500
    assert all(n.startswith(("Isa", "Lamp ")) for n in names.split(", ")), "cut on a whole name"
    assert house_names([]) == "" and house_names([{"entity_id": "x"}, "junk"]) == ""


def test_the_phones_own_words_are_used_and_the_gate_still_hears_the_voice(stack):
    """2026-10-08: the phone transcribes on the phone. With the speaker gate enforcing, its
    audio still comes so the gate can hear who spoke; the words are the phone's, not a
    second Whisper's, and a voice the gate refuses still has them dropped."""
    body = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                      json={"data_url": _wav_url(b"GUEST"), "mime_type": "audio/wav",
                            "transcript": "  lights off in the den "}).json()
    assert body["transcript"] == "lights off in the den" and body["provider"] == "phone"
    assert body["speaker"]["label"] == "Ted" and "speaker_ticket" in body
    refused = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH,
                         json={"data_url": _wav_url(b"NOBODY"), "mime_type": "audio/wav",
                               "transcript": "unlock the door"}).json()
    assert refused["transcript"] == "" and refused["speaker"]["dropped"] is True
    # Words without audio are not a voice turn: there is nothing for the gate to hear.
    bare = httpx.post(f"{stack}/api/audio/transcribe", headers=AUTH, json={"data_url": "", "transcript": "hi"})
    assert bare.json()["transcript"].startswith("echo:")


def test_the_phone_reaches_the_house_s_map_regions_and_packs_through_the_adapter(stack):
    """T-0979: the phone lists regions, asks for one, removes one and downloads a pack (with a
    Range, so a cut download resumes); only the calls of tests/contracts/map-pack.json, and an id
    that is one plain path segment."""
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/map-pack.json").read_text())
    from aura_adapter.app import MAPS_REGION_ID
    assert MAPS_REGION_ID.pattern == contract["id"]
    for verb, path in contract["calls"].values():
        url = stack + path.replace("{id}", "us-utah") + ("?q=utah" if "catalogue" in path else "")
        r = httpx.request(verb, url, headers=AUTH, **({"json": {"id": "us-utah"}} if verb == "POST" else {}))
        assert r.status_code == 200, (verb, path, r.text)
        assert httpx.request(verb, url).status_code == 401
    asked = httpx.post(f"{stack}/api/maps/regions", headers=AUTH, json={"id": "us-utah"}).json()
    assert asked["op"] == "regions" and json.loads(asked["body"]) == {"id": "us-utah"}
    assert httpx.delete(f"{stack}/api/maps/regions/us-utah", headers=AUTH).json() == {"kind": "regions", "region": "us-utah", "method": "DELETE"}
    part = httpx.get(f"{stack}/api/maps/packs/us-utah", headers={**AUTH, "Range": "bytes=0-15"})
    assert part.status_code == 206 and part.content == b"SQLite format 3\x00" and part.headers["content-range"] == "bytes 0-15/216"
    for bad in ("/api/maps/packs/..%2Fregion", "/api/maps/packs/UPPER", "/api/maps/packs/a.b"):
        assert httpx.get(stack + bad, headers=AUTH).status_code == 404, bad
    assert httpx.delete(f"{stack}/api/maps/regions/a%2Fb", headers=AUTH).status_code == 404
    assert httpx.post(f"{stack}/api/maps/regions", headers=AUTH, content=b"x" * 70000).status_code == 413


def test_the_maps_relay_refuses_a_body_over_its_limit_by_the_bytes_it_reads_t1019(stack):
    """T-1019: the limit was a Content-Length check, which a chunked body does not carry, so a
    request with no length was read whole (and, on /api/maps/regions, forwarded whole). Both relays
    count what arrives; a body at the limit still passes, whichever way it is framed."""
    from aura_adapter.app import MAPS_BODY_MAX

    def chunked(n: int):
        sent = 0
        while sent < n:  # no Content-Length: httpx sends a generator body as Transfer-Encoding: chunked
            step = min(4096, n - sent)
            sent += step
            yield b"x" * step

    for path in ("/api/maps/regions", "/api/maps/match"):
        over = httpx.post(stack + path, headers=AUTH, content=chunked(MAPS_BODY_MAX + 1))
        assert over.status_code == 413, path
        flat = httpx.post(stack + path, headers=AUTH, content=b"x" * (MAPS_BODY_MAX + 1))
        assert flat.status_code == 413, path
        at = httpx.post(stack + path, headers=AUTH, content=chunked(MAPS_BODY_MAX))
        assert at.status_code == 200, path
        assert httpx.post(stack + path, headers=AUTH, content=b"x" * MAPS_BODY_MAX).status_code == 200, path


def test_the_car_s_hologram_reaches_the_house_s_maps_through_the_adapter(stack):
    """The owner, 2026-11-10: "why do buildings and the map not show in the car". A phone paired
    to this adapter built no maps client, so the car's ground asked for no buildings and matched
    nothing. The three calls of tests/contracts/house-maps.json pass, by their methods; the house's
    own "no route" comes back as it is; nothing else of the house is reachable through here."""
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/house-maps.json").read_text())
    from aura_adapter.app import MAPS_BBOX, MAPS_OPS
    assert MAPS_OPS == contract["ops"] and MAPS_BBOX.pattern == contract["bbox"]
    got = httpx.get(f"{stack}/api/maps/buildings?bbox=-71.2,44.6,-71.19,44.61", headers=AUTH)
    assert got.status_code == 200 and got.json() == {"op": "buildings", "method": "GET", "bbox": "-71.2,44.6,-71.19,44.61", "body": None}
    # T-1074: packed for a client that takes it (the phone's mobile data), plain for one that does not.
    assert got.headers["content-encoding"] == "gzip"
    plain = httpx.get(f"{stack}/api/maps/buildings?bbox=-71.2,44.6,-71.19,44.61", headers={**AUTH, "Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.json() == got.json()
    live = httpx.get(f"{stack}/api/maps/live?bbox=-71.2,44.6,-71.19,44.61", headers=AUTH)  # T-1108
    assert live.status_code == 200 and live.json()["op"] == "live" and live.json()["bbox"] == "-71.2,44.6,-71.19,44.61"
    assert httpx.get(f"{stack}/api/maps/live?bbox=1,2,3", headers=AUTH).status_code == 404
    m = httpx.post(f"{stack}/api/maps/match", headers=AUTH, json={"points": [[44.6, -71.2]]})
    assert m.status_code == 200 and json.loads(m.json()["body"]) == {"points": [[44.6, -71.2]]}
    r = httpx.post(f"{stack}/api/maps/route", headers=AUTH, json={"from": [1, 2], "to": [3, 4]})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_route"
    for bad in (f"{stack}/api/maps/buildings?bbox=1,2,3", f"{stack}/api/maps/buildings?bbox=1,2,3,4%26x=1",
                f"{stack}/api/maps/places?q=home", f"{stack}/api/maps/..%2Fstates"):
        assert httpx.get(bad, headers=AUTH).status_code == 404, bad
    assert httpx.get(f"{stack}/api/maps/match", headers=AUTH).status_code == 404
    assert httpx.post(f"{stack}/api/maps/match", headers=AUTH, content=b"x" * (contract["body_max"] + 1)).status_code == 413
    assert httpx.get(f"{stack}/api/maps/buildings?bbox=1,2,3,4").status_code == 401


def test_the_phone_fetches_its_wake_word_models_through_the_adapter(stack):
    """The adapter answered 404 for /api/models, so the phone could never run "Hey Jarvis"
    on-device and streamed its microphone to the server all day (2026-10-08)."""
    r = httpx.get(f"{stack}/api/models/hey_jarvis_v0.1.onnx", headers=AUTH)
    assert r.status_code == 200 and r.content == b"ONNX-hey-jarvis"
    assert httpx.get(f"{stack}/api/models/missing.onnx", headers=AUTH).status_code == 404
    # Only a plain .onnx name reaches the house: no other path through here.
    assert httpx.get(f"{stack}/api/models/..%2Fstates", headers=AUTH).status_code == 404
    assert httpx.get(f"{stack}/api/models/config.yaml", headers=AUTH).status_code == 404
    assert httpx.get(f"{stack}/api/models/hey_jarvis_v0.1.onnx").status_code == 401


def test_the_phone_reachable_read_is_one_boolean_behind_the_key(stack):
    """The leave-by gate's script has no house token: it asks this, and gets yes or no, the same
    body for a phone that is off and one that was never paired, and no device's name (T-1227)."""
    assert httpx.get(f"{stack}/api/phone/reachable").status_code == 401
    assert httpx.post(f"{stack}/api/phone/reachable", headers=AUTH).status_code == 405
    PHONE_REACH[0] = "yes"
    r = httpx.get(f"{stack}/api/phone/reachable", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"reachable": True}
    PHONE_REACH[0] = "no"
    r = httpx.get(f"{stack}/api/phone/reachable", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"reachable": False}
    # The house failing is an error the gate reads as "do not know", never as "no".
    PHONE_REACH[0] = "error"
    assert httpx.get(f"{stack}/api/phone/reachable", headers=AUTH).status_code == 502


def test_revoking_a_device_key_hangs_up_its_open_socket_and_no_other(stack, tmp_path, monkeypatch):
    """A phone keeps /ws open for days: without the hang-up a revoked key would be "revoked
    at the next reconnect" (T-0569), the gap jarvis-core's token revoke closed for the house."""
    monkeypatch.setattr(device_keys, "ROOT", tmp_path)
    minted = httpx.post(f"{stack}/api/pair", json={"name": "Pixel", "kind": "phone"}, headers=AUTH).json()
    other = httpx.post(f"{stack}/api/pair", json={"name": "Tablet", "kind": "phone"}, headers=AUTH).json()
    url = stack.replace("http://", "ws://") + "/ws"

    async def run():
        async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {minted['key']}"}) as phone, \
                websockets.connect(url, additional_headers={"Authorization": f"Bearer {other['key']}"}) as tablet:
            await phone.recv()  # gateway.ready
            await tablet.recv()
            r = await asyncio.to_thread(httpx.delete, f"{stack}/api/devices/keys/{minted['device']['id']}",
                                        headers=AUTH)
            assert r.status_code == 200
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await asyncio.wait_for(phone.recv(), 5)
            await tablet.send(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "session.list"}))
            return closed.value.rcvd.code, json.loads(await asyncio.wait_for(tablet.recv(), 5))["id"]
    assert asyncio.run(run()) == (4401, 7)


def test_a_turn_device_is_the_paired_device_whose_socket_sent_the_prompt(stack, tmp_path, monkeypatch):
    """T-1498: a timer set from a phone rings on that phone. The house is told which device a
    turn came from only by the adapter, which knows the key each socket opened with; a phone
    cannot name another device, because nothing it sends is read for this."""
    monkeypatch.setattr(device_keys, "ROOT", tmp_path)
    phone = httpx.post(f"{stack}/api/pair", json={"name": "Pixel", "kind": "phone", "house_token": "tok_pixel"},
                       headers=AUTH).json()
    url = stack.replace("http://", "ws://") + "/ws"

    async def prompt(headers, session_id, extra=None):
        async with websockets.connect(url, additional_headers=headers) as ws:
            await ws.recv()  # gateway.ready
            params = {"session_id": session_id, "text": "ten minutes for the pasta", **(extra or {})}
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit", "params": params}))
            return json.loads(await ws.recv())["result"]["params"]

    def device_of(session_id):
        return httpx.get(f"{stack}/api/surface/current", headers=AUTH, params={"session": session_id}).json()["device"]

    # No surface report needed, and a device the frame names is not believed.
    asyncio.run(prompt({"Authorization": f"Bearer {phone['key']}"}, "s-dev", {"device": {"house_token": "tok_x"}}))
    assert device_of("s-dev") == {"id": phone["device"]["id"], "name": "Pixel", "kind": "phone",
                                  "house_token": "tok_pixel"}
    # The same conversation continued from the master key (the console) is no device's turn.
    asyncio.run(prompt(AUTH, "s-dev"))
    assert device_of("s-dev") is None
    assert device_of("never") is None
    # When each prompt came, so a tool asking under a conversation's durable key and its live id
    # takes the newest prompt's device; a session that sent none has no time.
    timed = httpx.get(f"{stack}/api/surface/current", headers=AUTH, params={"session": "s-dev"}).json()
    assert isinstance(timed["device_at"], float)
    assert httpx.get(f"{stack}/api/surface/current", headers=AUTH, params={"session": "never"}).json()["device_at"] is None


def test_a_secret_in_the_query_is_never_logged_and_a_private_line_is_dropped():
    # A browser cannot set a header on a WebSocket, so the console opens /ws?key=<the bearer
    # key>, and uvicorn printed the path whole: anyone reading `docker logs jarvis-adapter`
    # had the key (T-0773). The socket's line is uvicorn.error's, not uvicorn.access's.
    create_app(Settings(api_key=APP_KEY))

    def logged(name: str, msg: str, *args: object) -> str | None:
        record = logging.LogRecord(name, logging.INFO, __file__, 0, msg, args, None)
        kept = all(f.filter(record) for f in logging.getLogger(name).filters)
        return record.getMessage() if kept else None

    line = logged("uvicorn.error", '%s - "WebSocket %s" [accepted]', "10.0.0.5:5000", f"/ws?key={APP_KEY}")
    assert line is not None and APP_KEY not in line and "/ws?key=" in line
    line = logged("uvicorn.error", '%s - "WebSocket %s" 403', "10.0.0.5:5000", f"/ws?a=1&token={APP_KEY}")
    assert line is not None and APP_KEY not in line and "a=1" in line
    line = logged("uvicorn.access", '%s - "%s %s HTTP/%s" %d', "10.0.0.5:5000", "GET",
                  f"/api/status?key={APP_KEY}&x=2", "1.1", 200)
    assert line is not None and APP_KEY not in line and "x=2" in line
    assert logged("uvicorn.access", '%s - "%s %s HTTP/%s" %d', "10.0.0.5:5000", "POST",
                  "/api/private/chat", "1.1", 200) is None
    # Another uvicorn.error line with three arguments is not taken for a private one.
    assert logged("uvicorn.error", "Uvicorn running on %s://%s:%d", "http", "0.0.0.0", 8643)


def test_a_chosen_port_stays_ours_until_its_server_takes_it():
    """T-0821: the stack's servers started on ports chosen earlier and released. One taken in
    between (or handed out twice among the stack's seven) left uvicorn dead and the fixture
    polling for 60 s, then the test errored."""
    port = _free_port()
    with socket.socket() as other, pytest.raises(OSError):
        other.bind(("127.0.0.1", port))
    _serve(FastAPI(), port)
    assert httpx.get(f"http://127.0.0.1:{port}/nowhere").status_code == 404


def _app_over_upstream(upstream: FastAPI) -> str:
    """An adapter whose hermes serve is [upstream]; its base URL."""
    serve_port, adapter_port = _free_port(), _free_port()
    _serve(upstream, serve_port)
    _serve(create_app(Settings(api_key=APP_KEY, hermes_api_key=API_KEY, hermes_serve_token=SERVE_TOKEN,
                               hermes_serve_url=f"http://127.0.0.1:{serve_port}")), adapter_port)
    return f"ws://127.0.0.1:{adapter_port}/ws"


def _upstream(speaks_then_stays: bool = False, closes: bool = False) -> FastAPI:
    app = FastAPI()

    @app.websocket("/api/ws")
    async def ws(ws: WebSocket):
        await ws.accept()
        if closes:
            await ws.close(code=1011)
            return
        if speaks_then_stays:
            await ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready"}}))
        # Connected, then nothing: the 2026-11-12 drive's shape. It stays until the adapter
        # hangs up; a fixed sleep kept this server's shutdown waiting past the fixture's join.
        msg = await ws.receive()
        while msg["type"] != "websocket.disconnect":
            msg = await ws.receive()

    return app


def test_upstream_not_ready_closes_the_app_socket_with_1013_and_logs_it(monkeypatch, caplog):
    """T-1187: the adapter took the phone's socket, its upstream never said gateway.ready, and
    the phone sat on an open socket for the rest of the drive. Shorter than the phone's own
    10 s deadline, the adapter now says so first, with a code that means "try again"."""
    monkeypatch.setattr(app_mod, "UPSTREAM_READY_S", 0.5)
    caplog.set_level(logging.WARNING, logger="aura_adapter")
    url = _app_over_upstream(_upstream())

    async def run():
        async with websockets.connect(url, additional_headers=AUTH) as phone:
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await asyncio.wait_for(phone.recv(), 5)
            return closed.value.rcvd.code, closed.value.rcvd.reason
    assert asyncio.run(run()) == (1013, "upstream not ready")
    assert any("upstream not ready" in r.getMessage() for r in caplog.records)


def test_upstream_not_ready_also_covers_an_upstream_that_hangs_up_before_speaking(monkeypatch):
    monkeypatch.setattr(app_mod, "UPSTREAM_READY_S", 5)
    url = _app_over_upstream(_upstream(closes=True))

    async def run():
        async with websockets.connect(url, additional_headers=AUTH) as phone:
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await asyncio.wait_for(phone.recv(), 3)
            return closed.value.rcvd.code
    assert asyncio.run(run()) == 1013


def test_upstream_not_ready_leaves_a_socket_whose_upstream_spoke_alone(monkeypatch):
    monkeypatch.setattr(app_mod, "UPSTREAM_READY_S", 0.5)
    url = _app_over_upstream(_upstream(speaks_then_stays=True))

    async def run():
        async with websockets.connect(url, additional_headers=AUTH) as phone:
            first = json.loads(await asyncio.wait_for(phone.recv(), 5))
            await asyncio.sleep(1.5)  # well past the limit
            return first["params"]["type"], phone.state.name
    assert asyncio.run(run()) == ("gateway.ready", "OPEN")
