"""Which speech client `voice:` builds, and what it fills in when asked for little.

The switch is one string compared against a table, and it decides whether the
house speaks through wyoming-piper on a socket or through an OpenAI-shaped
service over HTTP. Getting it wrong is silent: the wrong client still answers,
just in the wrong voice or not at all, so the table is worth pinning.
"""

from __future__ import annotations

from types import SimpleNamespace

from aura.integrations.voice import (
    DATA_TTS_LENGTH_SCALE,
    HTTP_TTS_ENGINES,
    _build_clients,
)
from aura.voice.openai_tts import OpenAiTtsClient
from aura.voice.wyoming import WyomingTtsClient


def build(**tts):
    fake = SimpleNamespace(data={})
    _stt, speech, _wake = _build_clients(fake, {"tts": dict(tts)})
    return speech, fake.data


def test_no_engine_is_piper_because_that_is_what_the_house_still_speaks():
    speech, _data = build(host="127.0.0.1", port=10200, voice="en_GB-alan-medium")
    assert isinstance(speech, WyomingTtsClient)
    assert speech.port == 10200


def test_an_unknown_engine_falls_back_to_wyoming_rather_than_going_mute():
    """A typo in `engine:` must cost the operator a voice they did not pick, not silence."""
    speech, _data = build(engine="cosyvoyce", voice="jarvis")
    assert isinstance(speech, WyomingTtsClient)


def test_cosyvoice_builds_the_http_client_with_its_own_defaults():
    """The voice is one Kokoro has: "jarvis" was a CosyVoice clip, and asking for it is a 404."""
    speech, _data = build(engine="cosyvoice")
    assert isinstance(speech, OpenAiTtsClient)
    assert speech.url == "http://127.0.0.1:8890/v1"
    assert speech.voice == "af_heart"
    assert speech.model == "kokoro"


def test_kokoro_keeps_the_defaults_it_had_before_the_table_existed():
    speech, _data = build(engine="openai")
    assert isinstance(speech, OpenAiTtsClient)
    assert speech.url == "http://127.0.0.1:8880/v1"
    assert speech.voice == "bm_george"
    assert speech.model == "kokoro"


def test_configuration_beats_the_table():
    speech, _data = build(
        engine="cosyvoice", url="http://ai:8890/v1", voice="spare", model="kokoro-rl"
    )
    assert (speech.url, speech.voice, speech.model) == ("http://ai:8890/v1", "spare", "kokoro-rl")


def test_the_brief_reaches_the_client():
    speech, _data = build(engine="cosyvoice", instructions="Calm and level.")
    assert speech.instructions == "Calm and level."


def test_an_engine_name_is_matched_regardless_of_case():
    speech, _data = build(engine="CosyVoice")
    assert isinstance(speech, OpenAiTtsClient)


def test_length_scale_is_dropped_for_every_http_engine():
    """It is Piper's start-up flag. Reported anyway, Settings › Voice showed a
    pace number no engine was obeying."""
    for engine in HTTP_TTS_ENGINES:
        _speech, data = build(engine=engine, length_scale=0.9)
        assert data[DATA_TTS_LENGTH_SCALE] is None, engine


def test_length_scale_survives_on_piper_because_piper_is_the_one_that_takes_it():
    _speech, data = build(host="127.0.0.1", length_scale=0.9)
    assert data[DATA_TTS_LENGTH_SCALE] == 0.9
