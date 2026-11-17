"""The HTTP contract, with no torch and no weights.

Everything here is about the promises the callers rely on:
`aura-core/aura/voice/openai_tts.py`, Hermes's built-in `openai` TTS provider
and the adapter. They send OpenAI's body and expect a WAV, an MP3 or a PCM stream back.
"""

from __future__ import annotations

import io
import json
import threading
import time
import wave

import anyio
import pytest
from fastapi.testclient import TestClient

from aura_voice.app import MAX_SPEED, MIN_SPEED, create_app
from aura_voice.engine import EngineError, UnknownVoice, VoicePrint


class FakeEngine:
    """Records what it was asked for and answers a second of silence."""

    sample_rate = 24000

    def __init__(self, voices=("af_heart",), fail: Exception | None = None) -> None:
        self.voices = frozenset(voices)
        self.calls: list[dict] = []
        self.fail = fail

    def synthesize(self, text, voice, speed=1.0):
        self.calls.append({"text": text, "voice": voice, "speed": speed})
        if self.fail is not None:
            raise self.fail
        if voice not in self.voices:
            raise UnknownVoice(voice)
        return b"\x00\x00" * self.sample_rate, self.sample_rate


def client(engine: FakeEngine) -> TestClient:
    return TestClient(create_app(kokoro=engine))


def speak(engine: FakeEngine, **body):
    payload = {"model": "kokoro", "input": "The kitchen lights are on.", **body}
    return client(engine).post("/v1/audio/speech", json=payload)


def test_it_answers_a_wav_the_pipeline_can_parse():
    engine = FakeEngine()
    answer = speak(engine)
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(answer.content)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 24000
        # The frame count is the real one, not a streaming placeholder: the
        # workaround in openai_tts.py must never need to fire on this engine.
        assert handle.getnframes() == 24000
        assert len(handle.readframes(handle.getnframes())) == 48000


def test_a_callers_instructions_are_accepted_and_dropped():
    """OpenAI's body may carry `instructions`; Kokoro has no delivery direction."""
    engine = FakeEngine()
    assert speak(engine, instructions="Read this as a list.").status_code == 200
    assert "instructions" not in engine.calls[0]


def test_an_unknown_voice_names_the_ones_that_exist():
    engine = FakeEngine(voices=("af_heart", "bm_george"))
    answer = speak(engine, voice="nobody")
    assert answer.status_code == 404
    assert "af_heart, bm_george" in answer.json()["detail"]


def test_empty_input_is_refused_rather_than_synthesised():
    engine = FakeEngine()
    answer = speak(engine, input="   ")
    assert answer.status_code == 400
    assert not engine.calls


def test_an_unsupported_format_says_what_is_supported():
    answer = speak(FakeEngine(), response_format="opus")
    assert answer.status_code == 400
    assert "wav" in answer.json()["detail"] and "mp3" in answer.json()["detail"]


def test_mp3_is_served_for_hermes(monkeypatch):
    """Hermes's OpenAI-shaped TTS provider writes `.mp3` temp files and asks for
    that; without it every spoken line 400'd. Encoded by ffmpeg, faked here."""
    from aura_voice import app as app_module

    monkeypatch.setattr(app_module, "mp3_of", lambda pcm, rate: b"ID3" + bytes(len(pcm) // 100))
    answer = speak(FakeEngine(), response_format="mp3")
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/mpeg"
    assert answer.content.startswith(b"ID3")


def test_health_reports_the_device_and_whether_it_is_warm():
    """The HUD shows the engine and the device: a voice that fell back to the
    CPU is a slow voice nobody can explain otherwise."""
    body = client(FakeEngine()).get("/health").json()
    assert body["device"] == "cpu" and body["warm"] is False and body["warming"] is False


def test_a_request_during_the_warm_up_is_refused_at_once_not_queued():
    """The warm-up holds the engine's lock for minutes after a start; a request
    queued behind it would outlast every caller's timeout, the deploy's smoke
    included. A 503 with Retry-After is what the adapter falls back on."""
    from fastapi.testclient import TestClient

    from aura_voice.app import create_app

    app = create_app(kokoro=FakeEngine(), warm=False)
    app.state.warm_state["warming"] = True
    answer = TestClient(app).post("/v1/audio/speech", json={"model": "kokoro", "input": "Hello.", "voice": "af_heart"})
    assert answer.status_code == 503 and answer.headers["retry-after"] == "15"
    assert "warming" in answer.json()["detail"]


def test_the_release_and_warm_handshake_exists():
    """ADR-0039 §3: llama-swap asks for the card back before a load, and for
    the warm-up after. A fake engine without either is a no-op, not an error."""
    c = client(FakeEngine())
    assert c.post("/admin/release").json() == {"status": "released"}
    assert c.post("/admin/warm").json()["status"] == "idle"


def test_pcm_comes_back_headerless():
    answer = speak(FakeEngine(), response_format="pcm")
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/pcm"
    assert len(answer.content) == 48000


@pytest.mark.parametrize(
    "asked,used", [(0.1, MIN_SPEED), (9.0, MAX_SPEED), (1.2, 1.2), (0, 1.0)]
)
def test_speed_is_clamped_not_refused(asked, used):
    engine = FakeEngine()
    speak(engine, speed=asked)
    assert engine.calls[0]["speed"] == used


def test_a_model_that_will_not_load_is_503_so_callers_retry():
    engine = FakeEngine(fail=EngineError("no model at /models"))
    answer = speak(engine)
    assert answer.status_code == 503
    assert "no model" in answer.json()["detail"]


def test_health_is_ok_and_names_the_default_voice():
    """The compose healthcheck reads this; with weights in place it says ok."""
    body = client(FakeEngine()).get("/health").json()
    assert body["status"] == "ok"
    assert body["voices"] == ["af_heart"]
    assert body["default_voice"] == "af_heart"


def test_the_openai_model_list_names_kokoro():
    body = client(FakeEngine()).get("/v1/models").json()
    assert [row["id"] for row in body["data"]] == ["kokoro"]


def test_the_voice_list_names_the_engine():
    body = client(FakeEngine()).get("/v1/voices").json()
    assert body["voices"] == [{"id": "af_heart", "engine": "kokoro"}]


def test_a_voiceprint_needs_the_model_file_and_some_audio(tmp_path):
    """`/v1/speaker/embed` (the house's speaker gate, voice/neural.py): no campplus.onnx
    beside the model is a 503 that says so, and an empty body a 400."""
    c = TestClient(create_app(kokoro=FakeEngine(), voiceprint=VoicePrint(tmp_path)))
    assert c.post("/v1/speaker/embed", content=b"RIFF....").status_code == 503
    assert c.post("/v1/speaker/embed", content=b"").status_code == 400


def test_a_voiceprint_is_taken_from_the_voiced_frames():
    """The car's clips are seconds of silence and road noise around a short command,
    and CAM++ fingerprinted all of it: the owner's car turns scored 0.08-0.42 against
    his own car prints (2026-10-08). Measured on synthesised lines in one voice, keeping
    the frames above the clip's noise floor took a clip padded with 3 s of silence and
    heavy noise from 0.55 to 0.71, and a clean one from 0.92 to 0.88."""
    import numpy as np

    from aura_voice.engine import voiced

    rate = 16000
    rng = np.random.default_rng(0)
    t = np.arange(rate) / rate
    speech = (0.3 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype("float32")
    noise = (0.01 * rng.standard_normal(rate * 3)).astype("float32")
    clip = np.concatenate([noise[:rate], speech + noise[rate:2 * rate], noise[2 * rate:]])
    kept = voiced(clip, rate)
    assert 0.8 * rate <= len(kept) <= 1.1 * rate
    # Nothing stands out of plain noise: the clip is kept whole, never emptied.
    assert len(voiced(noise, rate)) == len(noise)
    # Under half a second of voice is too little to judge on: the whole clip is kept.
    short = np.concatenate([noise[:rate], speech[: rate // 4] + noise[rate:rate + rate // 4], noise[rate:2 * rate]])
    assert len(voiced(short, rate)) == len(short)



class StreamingEngine(FakeEngine):
    """Streams like the real engine: the lock is held from the first piece to the last."""

    def __init__(self, pieces: int | None = None) -> None:
        super().__init__()
        self.lock = threading.Lock()
        self.pieces = pieces

    def stream(self, text, voice, speed=1.0):
        with self.lock:
            made = 0
            while self.pieces is None or made < self.pieces:
                # A piece takes time to make, so a hang-up lands while one is being made.
                time.sleep(0.05)
                made += 1
                yield b"\x00\x00" * 2400


def _pcm_request(app, receive, send):
    """The ASGI call uvicorn makes; TestClient reads the whole body and never hangs up."""
    body = json.dumps({"model": "kokoro", "input": "Hello.", "response_format": "pcm"}).encode()
    asked = False

    async def first_the_body_then(message=None):
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/v1/audio/speech",
        "raw_path": b"/v1/audio/speech", "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("test", 1), "server": ("test", 80),
    }
    return app(scope, first_the_body_then, send)


def test_a_listener_who_vanishes_mid_stream_does_not_hold_the_engine():
    """The voice must not wait on a listener's network.

    On 2026-10-29 the adapter's network went away mid-sentence with no FIN, the
    send blocked on a window that never reopened, and the stream kept the
    synthesis lock until TCP gave up: the next reply waited 4.5 minutes. The
    sentence is made in full whether or not anyone is reading it.
    """
    engine = StreamingEngine(pieces=5)
    app = create_app(kokoro=engine)

    async def run() -> bool:
        heard = anyio.Event()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                heard.set()
                await anyio.sleep_forever()  # the peer's window never reopens

        async with anyio.create_task_group() as group:
            group.start_soon(_pcm_request, app, anyio.sleep_forever, send)
            await heard.wait()
            free = await anyio.to_thread.run_sync(engine.lock.acquire, True, 5)
            group.cancel_scope.cancel()
        return free

    assert anyio.run(run), "a listener who stopped reading still holds the engine"


def test_a_listener_who_hangs_up_stops_the_sentence_being_made():
    """A barge-in frees the engine for the next reply instead of finishing a sentence no one hears."""
    engine = StreamingEngine(pieces=None)  # endless: only a stop ends it
    app = create_app(kokoro=engine)

    async def run() -> None:
        heard = anyio.Event()

        async def receive():
            await heard.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                heard.set()

        with anyio.fail_after(10):
            await _pcm_request(app, receive, send)

    anyio.run(run)
    assert engine.lock.acquire(timeout=5), "the hung-up stream is still being made"
