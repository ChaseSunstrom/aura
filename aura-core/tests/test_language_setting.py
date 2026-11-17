"""The console's Speech language is what the recogniser is asked for (T-1484).

`voice.language` used to be stored and applied to an attribute nothing read for
a run: the run's language was the pipeline's (never empty, and the shipped
configuration names one), the run never handed it to the recogniser, and the
recogniser used the language it was built with at start, or none. These tests
pin each level by the decision that matters: the `language` of the `transcribe`
request a fake Wyoming recogniser received.

What they do not cover: a real recogniser, or a voice speaking another language
(a model that only knows English answers in English whatever it is asked); the
language replies are spoken in, which the voice decides; and what the adapter
does with the choice for the apps' speech, which its own tests hold (T-1487:
here only the route it reads).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.api import common  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import voice as voice_integration  # noqa: E402
from aura.voice.pipeline import PipelineRun  # noqa: E402

from test_voice import FakeWyomingServer, queue_of, setup_voice, sine_pcm  # noqa: E402

import pytest  # noqa: E402


def _shipped(port: int, **stt) -> dict:
    """The shipped file's shape: the voice block names a language, and so does its pipeline."""
    return {
        "language": "en",
        "stt": {"host": "127.0.0.1", "port": port, **stt},
        "tts": False,
        "wake": False,
        "pipelines": [{"name": "Jarvis", "language": "en"}],
    }


async def _events(_kind, _data):
    return None


async def _asked(jarvis, fake) -> tuple[str | None, str]:
    """One recognition run: the language the recogniser was asked for, and the run's own."""
    before = len(fake.events_of("transcribe"))
    run = jarvis.data["voice"].async_create_run(start_stage="stt", end_stage="stt")
    await run.execute(await queue_of(sine_pcm(20), sine_pcm(20)), _events)
    assert run.error is None
    return fake.events_of("transcribe")[before]["data"].get("language"), run.language


@pytest.fixture
async def fake():
    server = await FakeWyomingServer().start()
    yield server
    await server.stop()


# ---------------------------------------------------------------------------
# a house that never chose
# ---------------------------------------------------------------------------
async def test_a_house_that_never_chose_is_asked_for_no_language(tmp_path, fake):
    """The file's own `language: en` is not a choice: it was never sent, and is not now."""
    jarvis = await setup_voice(tmp_path, _shipped(fake.port))
    assert jarvis.data["voice"].language_chosen is None
    assert await _asked(jarvis, fake) == (None, "en")


async def test_a_house_that_never_chose_keeps_its_recognisers_own_language(tmp_path, fake):
    jarvis = await setup_voice(tmp_path, _shipped(fake.port, language="nl"))
    assert await _asked(jarvis, fake) == ("nl", "en")


# ---------------------------------------------------------------------------
# the setting, live
# ---------------------------------------------------------------------------
async def test_a_language_chosen_in_the_console_is_what_the_recogniser_is_asked_for(
    tmp_path, fake
):
    jarvis = await setup_voice(tmp_path, _shipped(fake.port))
    result = await common.async_set_setting(jarvis, {"key": "voice.language", "value": "de"})
    assert result["applied"] is True
    assert await _asked(jarvis, fake) == ("de", "de")


async def test_the_choice_beats_the_language_the_recogniser_started_with(tmp_path, fake):
    jarvis = await setup_voice(tmp_path, _shipped(fake.port, language="nl"))
    await common.async_set_setting(jarvis, {"key": "voice.language", "value": "de"})
    assert (await _asked(jarvis, fake))[0] == "de"


async def test_choosing_the_language_the_file_names_is_still_a_choice(tmp_path, fake):
    jarvis = await setup_voice(tmp_path, _shipped(fake.port))
    await common.async_set_setting(jarvis, {"key": "voice.language", "value": "en"})
    assert await _asked(jarvis, fake) == ("en", "en")


async def test_resetting_the_language_asks_what_was_asked_before(tmp_path, fake):
    """A reset hands the hook the file's value, which is not a choice."""
    jarvis = await setup_voice(tmp_path, _shipped(fake.port))
    await common.async_set_setting(jarvis, {"key": "voice.language", "value": "de"})
    await common.async_reset_setting(jarvis, {"key": "voice.language"})
    assert jarvis.data["voice"].language_chosen is None
    assert await _asked(jarvis, fake) == (None, "en")


# ---------------------------------------------------------------------------
# start-up
# ---------------------------------------------------------------------------
async def _restarted(tmp_path, raw: dict, provenance: dict[str, str] | None = None) -> Jarvis:
    """The path a restart takes: the stored overlay loaded, merged over the file, then set-up."""
    jarvis = Jarvis(tmp_path)
    await jarvis.settings.async_load()
    merged = await jarvis.async_install_config(raw, provenance)
    assert await voice_integration.async_setup(jarvis, merged["voice"]) is True
    return jarvis


async def test_a_stored_choice_survives_a_restart(tmp_path, fake):
    await Jarvis(tmp_path).settings.async_set("voice.language", "de")
    jarvis = await _restarted(tmp_path, {"voice": _shipped(fake.port)})
    assert jarvis.data["voice"].language_chosen == "de"
    assert await _asked(jarvis, fake) == ("de", "de")


async def test_a_restart_with_nothing_stored_chose_nothing(tmp_path, fake):
    jarvis = await _restarted(tmp_path, {"voice": _shipped(fake.port)})
    assert await _asked(jarvis, fake) == (None, "en")


async def test_a_stored_choice_a_package_overrides_is_not_sent(tmp_path, fake):
    """The console shows the package's value for such a key; the stored one did not land."""
    await Jarvis(tmp_path).settings.async_set("voice.language", "de")
    jarvis = await _restarted(
        tmp_path, {"voice": _shipped(fake.port)}, {"voice.language": "demo"}
    )
    assert jarvis.data["voice"].language_chosen is None
    assert await _asked(jarvis, fake) == (None, "en")


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
class OldRecogniser:
    """A client written before the choice existed: no `language` keyword."""

    async def transcribe(self, audio_iter, rate=16000):
        async for _chunk in audio_iter:
            pass
        return "hello"


class RecordingRecogniser:
    def __init__(self):
        self.languages: list[str | None] = []

    async def transcribe(self, audio_iter, rate=16000, language=None):
        self.languages.append(language)
        async for _chunk in audio_iter:
            pass
        return "hello"


async def _run_stt(tmp_path, stt, **run_kwargs) -> PipelineRun:
    run = PipelineRun(
        Jarvis(tmp_path), stt=stt, start_stage="stt", end_stage="stt", **run_kwargs
    )
    await run.execute(await queue_of(sine_pcm(20), sine_pcm(20)), _events)
    return run


async def test_a_run_hands_the_chosen_language_to_the_recogniser(tmp_path):
    stt = RecordingRecogniser()
    run = await _run_stt(tmp_path, stt, language="de", language_chosen="de")
    assert run.error is None
    assert stt.languages == ["de"]


async def test_a_run_with_nothing_chosen_passes_no_language(tmp_path):
    """A pipeline's own language is inert at the recogniser, as it was before T-1484."""
    run = await _run_stt(tmp_path, OldRecogniser(), language="en")
    assert run.error is None and run.stt_text == "hello"


# ---------------------------------------------------------------------------
# the apps' speech (T-1487)
# ---------------------------------------------------------------------------
async def test_the_house_answers_its_chosen_voice_language_and_null_when_none(tmp_path, fake):
    """The adapter reads this before it sends the apps' speech to the card's Whisper,
    which otherwise assumes English. The file's language is the house's default, not a
    choice, so it reads as none; a reset reads as none again."""
    from types import SimpleNamespace

    from aura.api import rest

    assert "/api/voice/language" in {getattr(r, "path", "") for r in rest.api_router.routes}
    jarvis = await setup_voice(tmp_path, _shipped(fake.port))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(jarvis=jarvis)))
    assert await rest.voice_language(request) == {"language": None}
    await common.async_set_setting(jarvis, {"key": "voice.language", "value": "de"})
    assert await rest.voice_language(request) == {"language": "de"}
    await common.async_reset_setting(jarvis, {"key": "voice.language"})
    assert await rest.voice_language(request) == {"language": None}
