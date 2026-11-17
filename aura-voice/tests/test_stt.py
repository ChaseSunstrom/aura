"""The GPU transcription endpoint, with no torch and no weights."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aura_voice.app import create_app
from aura_voice.stt import PROMPT, Transcriber, Unavailable, initial_prompt, keep, words

from test_speech import FakeEngine


class FakeEars:
    def __init__(self, fail: Exception | None = None) -> None:
        self.heard: list[bytes] = []
        self.fail = fail
        self.released = 0
        self.names: list[str] = []
        self.paths: list[str] = []

    def transcribe(self, path: str, language: str = "en", names: str = "") -> str:
        if self.fail is not None:
            raise self.fail
        self.heard.append(Path(path).read_bytes())
        self.paths.append(path)
        self.names.append(names)
        return "Turn on the lights."

    def release(self) -> None:
        self.released += 1


def url(audio: bytes) -> str:
    return "data:audio/wav;base64," + base64.b64encode(audio).decode()


def test_the_words_come_back_from_the_clip_it_was_sent() -> None:
    ears = FakeEars()
    r = TestClient(create_app(kokoro=FakeEngine(), warm=False, transcriber=ears)).post(
        "/v1/audio/transcriptions", json={"data_url": url(b"RIFF....")})
    assert r.status_code == 200
    assert r.json() == {"transcript": "Turn on the lights.", "engine": "whisper-gpu"}
    assert ears.heard == [b"RIFF...."]


def test_the_clip_is_held_in_memory_and_gone_after_its_words() -> None:
    """T-0462: the container's /tmp is its disk layer; a voice turn, private or not, is
    written only to RAM (/dev/shm) and deleted when its words are back. Does not cover
    swap, should the host page RAM out."""
    ears = FakeEars()
    TestClient(create_app(kokoro=FakeEngine(), warm=False, transcriber=ears)).post(
        "/v1/audio/transcriptions", json={"data_url": url(b"RIFF....")})
    (path,) = ears.paths
    assert path.startswith("/dev/shm/")
    assert not Path(path).exists()


def test_the_house_names_the_adapter_sends_reach_whisper() -> None:
    ears = FakeEars()
    c = TestClient(create_app(kokoro=FakeEngine(), warm=False, transcriber=ears))
    c.post("/v1/audio/transcriptions", json={"data_url": url(b"x"), "names": "Isa, Den lamp"})
    c.post("/v1/audio/transcriptions", json={"data_url": url(b"x")})
    assert ears.names == ["Isa, Den lamp", ""]


def test_the_house_names_come_before_the_examples_and_are_capped() -> None:
    """Whisper keeps only a prompt's last ~223 tokens, so the examples that teach it to
    spell "Jarvis" go last and survive any list; a huge list cannot crowd them out."""
    assert initial_prompt("") == PROMPT
    assert initial_prompt("  Isa,\n Den   lamp ") == f"Isa, Den lamp. {PROMPT}"
    long = initial_prompt(", ".join(f"Lamp {i}" for i in range(500)))
    assert long.endswith(PROMPT) and len(long) < 800


def test_transcribe_primes_whisper_with_the_names() -> None:
    class Model:
        def transcribe(self, path: str, **kw: object) -> dict:
            self.kw = kw
            return {"segments": []}

    t = Transcriber(Path("/nonexistent"), "small")
    t._model = model = Model()
    t.transcribe("clip", "en", "Isa")
    assert model.kw["initial_prompt"] == f"Isa. {PROMPT}"


def test_no_card_or_no_weights_is_a_503_the_adapter_falls_back_on() -> None:
    c = TestClient(create_app(kokoro=FakeEngine(), warm=False, transcriber=FakeEars(Unavailable("no weights"))))
    assert c.post("/v1/audio/transcriptions", json={"data_url": url(b"x")}).status_code == 503


def test_a_body_with_no_audio_is_a_400() -> None:
    c = TestClient(create_app(kokoro=FakeEngine(), warm=False, transcriber=FakeEars()))
    assert c.post("/v1/audio/transcriptions", json={"data_url": "nope"}).status_code == 400


def test_release_gives_back_the_whisper_model_too() -> None:
    ears = FakeEars()
    TestClient(create_app(kokoro=FakeEngine(), warm=False, transcriber=ears)).post("/admin/release")
    assert ears.released == 1


def test_missing_weights_are_unavailable_not_a_download(tmp_path: Path) -> None:
    with pytest.raises(Unavailable):
        Transcriber(tmp_path, "small").transcribe(str(tmp_path / "clip"))


def test_silence_whisper_is_unsure_of_is_dropped() -> None:
    assert not keep({"no_speech_prob": 0.9, "avg_logprob": -1.5})
    assert keep({"no_speech_prob": 0.9, "avg_logprob": -0.3})
    assert keep({"no_speech_prob": 0.1, "avg_logprob": -1.5})
    result = {"segments": [{"text": " Thank you.", "no_speech_prob": 0.95, "avg_logprob": -1.4},
                           {"text": " Text Isa.", "no_speech_prob": 0.01, "avg_logprob": -0.2}]}
    assert words(result) == "Text Isa."
