"""An OpenAI-shaped `/v1/audio/speech` in front of Kokoro-82M, with the house's Whisper and voiceprints.

Why this shape and not a Wyoming shim. Three consumers have to follow the voice
wherever it goes: jarvis-core's pipeline, Hermes's `/api/audio/speak` (which is
what the adapter, the phone, the desktop, `scripts/smoke.sh` step 6 and the
`voice` benchmark category all reach through), and the console. jarvis-core
already has `OpenAiTtsClient`, and Hermes's built-in `openai` TTS provider
already honours `tts.openai.base_url`, so this endpoint reaches all of them with
configuration and no new protocol code.

Kokoro is the only voice (T-0955). CosyVoice 3 spoke from reference clips until
T-0901 unmounted them; the owner, 2026-11-10: "you don't need to use [it]
if kokoro is better, you can remove it". The service, its env names and its
container keep the cosyvoice name until T-0953 renames them.

Deliberately not covered: authentication. This binds to loopback beside the
other voice services and is reached over the host's own network namespace, the
same trust boundary wyoming-piper sits inside. An `Authorization` header is
accepted and ignored so an OpenAI client library can talk to it unmodified. Nor
is `instructions`: Kokoro has no delivery direction, so a caller's is dropped.
"""

from __future__ import annotations

import base64
import io
import logging
import wave
from typing import Any

import os
import queue
import subprocess
import tempfile
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .engine import EngineError, UnknownVoice, VoicePrint
from .kokoro_engine import KokoroEngine
from .stt import Transcriber, Unavailable

_LOGGER = logging.getLogger(__name__)

#: What `/v1/models` lists and `/health` names.
MODEL_ID = "kokoro"
#: The default pack's voice (AURA's): what a request that names none is said in.
DEFAULT_VOICE = "af_heart"

#: `response_format` values that mean something here. Kokoro emits float
#: samples: `pcm` is those as 16-bit, streamed as they are made; `wav` is the
#: same in one piece with an honest header; `mp3` is for Hermes, whose
#: OpenAI-shaped TTS provider writes `.mp3` temp files and asks for exactly
#: that -- with `wav` and `pcm` alone, pointing Hermes here 400'd on every
#: line.
FORMATS = {"wav", "pcm", "mp3"}

#: Load the model and speak one word at start, so the first real line is fast.
WARM_ON_BOOT = os.environ.get("COSYVOICE_WARM_ON_BOOT", "1").strip().lower() in ("1", "true", "yes")

#: OpenAI's own bounds. Outside them a duration control stops being a pace change
#: and starts being an artefact, so this clamps rather than errors.
MIN_SPEED = 0.5
MAX_SPEED = 2.0


class SpeechRequest(BaseModel):
    """The subset of OpenAI's speech request this service acts on."""

    model: str = MODEL_ID
    input: str = ""
    voice: str = DEFAULT_VOICE
    response_format: str = "wav"
    speed: float = 1.0


def wav_of(pcm: bytes, rate: int) -> bytes:
    """16-bit mono PCM wrapped in a RIFF header with an honest frame count.

    Written whole rather than streamed on purpose. A streaming service writes
    its header before the audio exists, leaving a placeholder length that
    `wave` then believes — the trap `aura-core/aura/voice/openai_tts.py`
    carries a workaround for. Answering in one piece means that workaround
    never has to fire on this engine.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(rate))
        handle.writeframes(pcm)
    return buffer.getvalue()


def mp3_of(pcm: bytes, rate: int) -> bytes:
    """16-bit mono PCM as MP3, through the ffmpeg the image carries."""
    try:
        done = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(int(rate)), "-ac", "1", "-i", "-",
             "-codec:a", "libmp3lame", "-q:a", "2", "-f", "mp3", "-"],
            input=pcm, capture_output=True, check=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise EngineError(f"mp3 encoding failed: {err}") from err
    return done.stdout


def create_app(
    *, kokoro: Any = None, warm: bool | None = None, transcriber: Any = None, voiceprint: Any = None
) -> FastAPI:
    """The service. The engine, the ears and the voiceprint are injectable so the HTTP contract
    tests need no torch."""
    speaker = kokoro if kokoro is not None else KokoroEngine()
    ears = transcriber if transcriber is not None else Transcriber()
    app = FastAPI(title="jarvis-cosyvoice", version="1")
    state: dict[str, Any] = {"warming": False, "warm_error": ""}
    # Exposed for the tests: the warm-up thread's flag, without a thread.
    app.state.warm_state = state

    def warm_in_background() -> None:
        # No `warm_up` on the engine means nothing to do, not a thread that fails
        # inside: that thread raced the reply on a loaded CI box (pipeline 322
        # read "warming" from a fake that has no warm-up at all).
        if state["warming"] or not hasattr(speaker, "warm_up") or getattr(speaker, "warm", False):
            return

        def run() -> None:
            state["warming"] = True
            state["warm_error"] = ""
            try:
                speaker.warm_up()
            except Exception as err:  # noqa: BLE001 - reported on /health, never fatal
                state["warm_error"] = str(err)
                _LOGGER.warning("warm-up failed: %s", err)
            finally:
                state["warming"] = False
            # The ears too: the first car request after a restart paid Whisper's load.
            if hasattr(ears, "warm"):
                try:
                    ears.warm()
                except Exception as err:  # noqa: BLE001 - the CPU path answers instead
                    _LOGGER.warning("whisper warm-up failed: %s", err)

        threading.Thread(target=run, name="voice-warm", daemon=True).start()

    @app.on_event("startup")
    async def _warm() -> None:  # pragma: no cover - the thread is exercised through /admin/warm
        if WARM_ON_BOOT if warm is None else warm:
            warm_in_background()

    @app.post("/admin/warm")
    async def admin_warm() -> dict[str, Any]:
        """Load and speak one word, in the background. ADR-0039 §3's second half."""
        warm_in_background()
        warm_now = getattr(speaker, "warm", False)
        return {"status": "warming" if state["warming"] else "warm" if warm_now else "idle"}

    @app.post("/admin/release")
    async def admin_release() -> dict[str, Any]:
        """Give the card back before a llama-swap load (ADR-0039 §3). The next
        line pays the load again, or `/admin/warm` pays it first."""
        if hasattr(speaker, "release"):
            await run_in_threadpool(speaker.release)
        await run_in_threadpool(ears.release)
        return {"status": "released"}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> dict[str, Any]:
        """Words from `{data_url, language}` -- the adapter's transcribe body as is.
        503 when there is no card or no weights: the adapter then asks Hermes (stt.py)."""
        try:
            body = await request.json()
            head, _, data = str(body.get("data_url") or "").partition(",")
            audio = base64.b64decode(data, validate=False) if head.startswith("data:") and ";base64" in head else b""
        except ValueError:
            audio = b""
        if not audio:
            raise HTTPException(status_code=400, detail="data_url must be a base64 data: URL")
        # RAM, not /tmp: the container's /tmp is its disk layer, and a voice turn -- private
        # ones above all (T-0462) -- must not reach a disk. Whisper wants a seekable file
        # (an MP4 from Safari is not readable from a pipe), so a tmpfs file it is.
        with tempfile.NamedTemporaryFile(suffix=".audio", dir="/dev/shm") as clip:
            clip.write(audio)
            clip.flush()
            try:
                text = await run_in_threadpool(ears.transcribe, clip.name, str(body.get("language") or "en"),
                                               str(body.get("names") or ""))
            except Unavailable as err:
                raise HTTPException(status_code=503, detail=str(err)) from err
        return {"transcript": text, "engine": "whisper-gpu"}

    @app.get("/health")
    async def health() -> JSONResponse:
        """Never 503 for want of a model.

        The compose healthcheck asks this, and a container that is up but has
        no weights mounted must report *why* rather than restart-looping past
        the operator before they can read the reason.
        """
        ready = speaker.available() if hasattr(speaker, "available") else True
        return JSONResponse(
            {
                "status": "ok" if ready else "no-model",
                "voices": sorted(speaker.voices),
                "default_voice": DEFAULT_VOICE,
                "model": MODEL_ID,
                "sample_rate": speaker.sample_rate,
                # The HUD shows the engine AND the device (the brief): a voice that quietly
                # fell back to the CPU is a slow voice nobody can explain otherwise, and the
                # adapter sends a line to the house's Piper when this says "cpu" (T-0901).
                "device": getattr(speaker, "device", "cpu"),
                "warm": bool(getattr(speaker, "warm", False)),
                "warming": bool(state["warming"]),
                "warm_error": state["warm_error"],
                "detail": "" if ready else
                f"no Kokoro weights at {getattr(speaker, 'model_dir', '')}: the image is meant to carry them",
            }
        )


    prints = voiceprint if voiceprint is not None else VoicePrint()

    @app.post("/v1/speaker/embed")
    async def speaker_embed(request: Request) -> dict[str, Any]:
        """A WAV in, its CAM++ voiceprint out: the house's speaker gate compares these."""
        body = await request.body()
        if not body:
            raise HTTPException(400, "no audio in the request body")
        if not prints.available():
            raise HTTPException(503, f"no campplus.onnx at {prints.path}")
        try:
            vector = await run_in_threadpool(prints.embed, body)
        except EngineError as err:
            raise HTTPException(400, str(err))
        except Exception as err:  # a malformed WAV is a bad request
            raise HTTPException(400, f"could not read the audio: {err}")
        return {"model": "campplus", "embedding": vector}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "house"}]}

    @app.get("/v1/voices")
    async def voices() -> dict[str, Any]:
        return {"voices": [{"id": name, "engine": MODEL_ID} for name in sorted(speaker.voices)]}

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest) -> Response:
        text = request.input.strip()
        if not text:
            raise HTTPException(400, "input is empty; there is nothing to say")
        want = request.response_format.lower()
        if want not in FORMATS:
            raise HTTPException(
                400, f"response_format {request.response_format!r} is not one of {sorted(FORMATS)}"
            )
        speed = max(MIN_SPEED, min(MAX_SPEED, float(request.speed or 1.0)))
        voice = request.voice or DEFAULT_VOICE
        if voice not in speaker.voices:
            raise HTTPException(404, f"no voice named {voice!r}. Registered: {', '.join(sorted(speaker.voices))}")
        if state["warming"]:
            # Said at once, never waited for: the warm-up holds the engine's
            # lock for the first seconds after a start, and a request that
            # queued behind it would sit there past every caller's timeout --
            # the deploy's smoke included. A 503 with a Retry-After is what the
            # adapter falls back on, and what a client retries.
            raise HTTPException(503, "the voice is still warming up", headers={"Retry-After": "15"})
        if want == "pcm" and hasattr(speaker, "stream"):
            # Streamed as it is made: the caller hears the start of the line
            # while the end of it does not exist yet. An error after the first
            # piece cannot become a status any more; it ends the body.
            pieces = speaker.stream(text, voice, speed)
            try:
                first = await run_in_threadpool(next, pieces, b"")
            except EngineError as err:
                raise HTTPException(503, str(err))

            # The rest is made on its own thread, whoever is reading. The engine
            # holds its lock from the first piece to the last, so a sentence made
            # only as fast as the listener reads held it as long as the listener
            # liked: a peer gone without a FIN blocked the send, and every reply
            # waited 4.5 minutes for TCP to give up (2026-10-29). A hang-up stops
            # it after the piece in hand. Costs the sentence's PCM in memory.
            made: queue.SimpleQueue = queue.SimpleQueue()
            stop = threading.Event()

            def make_the_rest() -> None:
                try:
                    for piece in pieces:
                        if stop.is_set():
                            break
                        made.put(piece)
                except Exception as err:  # ends the body, as it did when read in step
                    made.put(err)
                finally:
                    pieces.close()
                    made.put(None)

            threading.Thread(target=make_the_rest, name="voice-stream", daemon=True).start()

            async def body():
                try:
                    yield first
                    while (piece := await run_in_threadpool(made.get)) is not None:
                        if isinstance(piece, Exception):
                            raise piece
                        yield piece
                finally:
                    stop.set()

            return StreamingResponse(body(), media_type="audio/pcm", headers={"x-sample-rate": str(speaker.sample_rate)})
        try:
            pcm, rate = await run_in_threadpool(speaker.synthesize, text, voice, speed)
            if want == "mp3":
                return Response(await run_in_threadpool(mp3_of, pcm, rate), media_type="audio/mpeg")
        except UnknownVoice:
            known = ", ".join(sorted(speaker.voices)) or "none registered"
            raise HTTPException(404, f"no voice named {voice!r}. Registered: {known}")
        except EngineError as err:
            # 503, not 500: every one of these is "the model is not ready or
            # not there", which is a state the operator can fix, and the
            # callers retry a 503 rather than surfacing a crash to the person.
            raise HTTPException(503, str(err))
        if want == "pcm":
            return Response(pcm, media_type="audio/pcm")
        return Response(wav_of(pcm, rate), media_type="audio/wav")

    return app
