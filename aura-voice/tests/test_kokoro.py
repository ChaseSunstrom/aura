"""Kokoro's stock voices, the voice service's one engine, with no torch and no weights.

A fake pipeline stands where `kokoro.KPipeline` does: it is called with a text, a
voice file path, a speed and a split pattern, and yields objects with `.audio`.
What is pinned is the routing, the shape of the stream and the level; whether
af_heart sounds right, and arrives in under 250 ms on the card, is
`scripts/verify/aura_voice_check.py` against the running service.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aura_voice import kokoro_engine
from aura_voice.app import create_app
from aura_voice.engine import EngineError, UnknownVoice
from aura_voice.kokoro_engine import (
    SAMPLE_RATE, SENTENCE_GAP_MS, SENTENCES, TARGET_PEAK, VOICES, KokoroEngine, pcm16,
)


class Result:
    def __init__(self, audio) -> None:
        self.audio = audio


class FakePipeline:
    """One waveform per sentence, `loud` at its peak; records every call."""

    def __init__(self, lang: str, loud: float = 0.3) -> None:
        self.lang = lang
        self.loud = loud
        self.calls: list[dict] = []
        self.made: list[str] = []

    def __call__(self, text, voice, speed, split_pattern):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "split": split_pattern})
        for sentence in re.split(split_pattern, text.strip()):
            self.made.append(sentence)
            wave = np.full(2400, self.loud, dtype="float32")
            wave[0] = -self.loud
            yield Result(wave)


@pytest.fixture
def weights(tmp_path):
    (tmp_path / "kokoro-v1_0.pth").write_bytes(b"x")
    return tmp_path


def kokoro(weights, **kw) -> tuple[KokoroEngine, dict[str, FakePipeline]]:
    made: dict[str, FakePipeline] = {}

    def factory(lang, model_dir, device):
        made[lang] = FakePipeline(lang, **kw)
        return made[lang]

    return KokoroEngine(model_dir=weights, pipeline_factory=factory), made


def test_the_stock_voices_the_house_needs_are_listed():
    assert {"af_heart", "af_bella", "af_nicole", "bm_george"} <= VOICES


def test_a_british_voice_gets_the_british_pipeline(weights):
    engine, made = kokoro(weights)
    b"".join(engine.stream("Good evening.", "bm_george"))
    assert list(made) == ["b"]


def test_wav_is_answered_whole_with_a_header(weights):
    engine, _ = kokoro(weights)
    answer = TestClient(create_app(kokoro=engine)).post(
        "/v1/audio/speech", json={"input": "One. Two.", "voice": "af_heart"})
    assert answer.status_code == 200
    assert answer.content[:4] == b"RIFF"
    assert answer.headers["content-type"] == "audio/wav"


def test_the_first_sentence_is_out_before_the_second_is_made(weights):
    engine, made = kokoro(weights)
    pieces = engine.stream("First one. Second one. Third.", "af_heart")
    next(pieces)
    assert made["a"].made == ["First one."]
    pieces.close()


def test_sentences_are_split_and_a_short_gap_sits_between_not_before(weights):
    engine, made = kokoro(weights)
    pieces = list(engine.stream("First one. Second one?", "af_heart", 1.0))
    gap = b"\x00\x00" * (SAMPLE_RATE * SENTENCE_GAP_MS // 1000)
    assert len(pieces) == 3 and pieces[1] == gap and pieces[0] != gap
    assert made["a"].calls[0]["split"] == SENTENCES
    assert re.split(SENTENCES, "Dr. Who? Yes.\nNext") == ["Dr.", "Who?", "Yes.", "Next"]


def test_speed_is_passed_through_and_defaults_to_one(weights):
    engine, made = kokoro(weights)
    TestClient(create_app(kokoro=engine)).post(
        "/v1/audio/speech", json={"input": "Hi.", "voice": "af_heart", "response_format": "pcm"})
    assert made["a"].calls[0]["speed"] == 1.0


@pytest.mark.parametrize("loud", [0.2, 0.5, 1.4])
def test_each_piece_is_peak_normalised_to_one_level(weights, loud):
    engine, _ = kokoro(weights, loud=loud)
    pcm, rate = engine.synthesize("Hello there.", "af_heart")
    samples = np.frombuffer(pcm, dtype="<i2")
    assert rate == 24000
    assert abs(int(np.abs(samples).max()) - TARGET_PEAK * 32767) < 2


def test_near_silence_is_not_amplified():
    quiet = np.full(100, 0.001, dtype="float32")
    assert np.frombuffer(pcm16(quiet), dtype="<i2").max() < 100


def test_an_unknown_name_is_a_404_that_lists_the_voices(weights):
    engine, made = kokoro(weights)
    answer = TestClient(create_app(kokoro=engine)).post(
        "/v1/audio/speech", json={"input": "Hi.", "voice": "nobody"})
    assert answer.status_code == 404
    assert "nobody" in answer.json()["detail"] and "bm_george" in answer.json()["detail"]
    assert made == {}


def test_an_unregistered_name_is_unknown_to_the_engine(weights):
    engine, _ = kokoro(weights)
    with pytest.raises(UnknownVoice):
        next(engine.stream("Hi.", "nobody"))


def test_health_lists_kokoros_voices_and_the_device_it_speaks_on(weights):
    """T-0901: the adapter sends a line to the house's Piper when this says "cpu"."""
    engine, _ = kokoro(weights)
    engine.device = "cuda"
    body = TestClient(create_app(kokoro=engine)).get("/health").json()
    assert "af_heart" in body["voices"] and "bm_george" in body["voices"]
    assert body["device"] == "cuda" and body["warm"] is False


def test_missing_weights_is_a_503_callers_retry(tmp_path):
    engine = KokoroEngine(model_dir=tmp_path, pipeline_factory=lambda *a: pytest.fail("loaded"))
    answer = TestClient(create_app(kokoro=engine)).post(
        "/v1/audio/speech", json={"input": "Hi.", "voice": "af_heart", "response_format": "pcm"})
    assert answer.status_code == 503


def test_it_loads_on_first_use_and_release_gives_it_back(weights):
    engine, made = kokoro(weights)
    assert made == {} and not engine.warm
    engine.synthesize("Hi.", "af_heart")
    first = made["a"]
    engine.synthesize("Hi again.", "af_heart")
    assert made["a"] is first
    engine.release()
    engine.synthesize("Back.", "af_heart")
    assert made["a"] is not first


def test_warm_up_loads_both_pipelines_and_warm_and_release_ride_the_admin_routes(weights):
    engine, made = kokoro(weights)
    http = TestClient(create_app(kokoro=engine, warm=False))
    http.post("/admin/warm")
    for _ in range(100):
        if engine.warm:
            break
        import time
        time.sleep(0.02)
    assert engine.warm and set(made) == {"a", "b"}
    assert http.get("/health").json()["warm"] is True
    http.post("/admin/release")
    assert not engine.warm


def test_a_pipeline_that_fails_ends_as_an_engine_error(weights):
    def boom(lang, model_dir, device):
        raise RuntimeError("no cuda kernel")

    engine = KokoroEngine(model_dir=weights, pipeline_factory=boom)
    with pytest.raises(EngineError, match="could not load Kokoro"):
        engine.synthesize("Hi.", "af_heart")


def _check_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "verify" / "aura_voice_check.py"
    spec = importlib.util.spec_from_file_location("aura_voice_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serve(voices: list[str], delay: float):
    """A stand-in server: /health lists `voices`, speech streams after `delay` seconds."""
    import json
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"voices": voices}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Length", "4800")
            self.end_headers()
            self.wfile.write(b"\x00" * 4800)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize(("voices", "delay", "code"), [
    (["af_heart"], 0.0, 0),
    (["af_heart"], 0.3, 1),
    (["jarvis"], 0.0, 2),
])
def test_the_check_script_exits_by_what_it_finds(voices, delay, code, capsys):
    server = _serve(voices, delay)
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        assert _check_script().main([url]) == code
    finally:
        server.shutdown()
    if code == 0:
        assert "first byte" in capsys.readouterr().out


def test_the_check_script_exits_one_when_nothing_answers():
    assert _check_script().main(["http://127.0.0.1:1"]) == 1


def test_the_device_follows_the_device_setting(monkeypatch):
    monkeypatch.setattr(kokoro_engine, "DEFAULT_DEVICE", "cpu")
    assert kokoro_engine._select_device() == "cpu"


def test_the_service_speaks_with_kokoro_alone(weights):
    """T-0955 (the owner: "you can remove it"): no CosyVoice engine is built or importable,
    Kokoro says every line, and a request that names no voice is said in AURA's."""
    from aura_voice import engine as module

    engine, made = kokoro(weights)
    http = TestClient(create_app(kokoro=engine, warm=False))
    answer = http.post("/v1/audio/speech", json={"input": "Hi.", "response_format": "pcm"})
    assert answer.status_code == 200 and made["a"].calls[0]["voice"].endswith("/voices/af_heart.pt")
    body = http.get("/health").json()
    assert (body["model"], body["default_voice"], body["status"]) == ("kokoro", "af_heart", "ok")
    assert [m["id"] for m in http.get("/v1/models").json()["data"]] == ["kokoro"]
    assert not [name for name in vars(module) if name.startswith("CosyVoice")]


def test_health_says_why_when_kokoros_weights_are_missing(tmp_path):
    body = TestClient(create_app(kokoro=KokoroEngine(model_dir=tmp_path), warm=False)).get("/health").json()
    assert body["status"] == "no-model" and str(tmp_path) in body["detail"]
