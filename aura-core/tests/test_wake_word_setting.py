"""The console's Wake word setting changes the word the house listens for (T-1479).

`voice.wake_word` used to be stored and applied to an attribute nothing read for
a run: the run's word was the pipeline's (never empty, and the shipped
configuration names one), the pipeline never handed it to the wake client, and
the client asked for the persona's word or the model it was built with at start.
After a restart the stored choice sat at the top of the voice block, where
set-up does not look. These tests pin each level by the decision that matters:
what the `detect` request asked the wake service for.

What they do not cover: a real openWakeWord, or a live voice. The fake service
answers `describe` and records `detect`; whether the real one then listens for
that name is its own contract (see `WyomingWakeClient.wake_name`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.api import common  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.settings import SettingsOverlay  # noqa: E402
from aura.voice.pipeline import PipelineRun  # noqa: E402
from aura.voice.wyoming import WyomingWakeClient  # noqa: E402

from test_voice import (  # noqa: E402
    FakeWyomingServer,
    _wake_info,
    queue_of,
    setup_voice,
    sine_pcm,
)


async def _asked_for(*served, name="unset", persona=None, model="hey_jarvis"):
    """The detect request (names, and how many describe round trips) for one call."""
    fake = await FakeWyomingServer(info=_wake_info(*served), detection="whatever").start()
    try:
        client = WyomingWakeClient("127.0.0.1", fake.port, model=model, persona_word=persona)
        if name == "unset":
            await client.detect([sine_pcm(20)] * 4)
        else:
            await client.detect([sine_pcm(20)] * 4, name=name)
        return (
            fake.events_of("detect")[0]["data"].get("names"),
            len(fake.events_of("describe")),
        )
    finally:
        await fake.stop()


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------
async def test_a_served_name_is_what_the_client_asks_for():
    names, _ = await _asked_for("hey_jarvis", "computer", name="computer")
    assert names == ["computer"]


async def test_a_name_the_service_does_not_serve_is_never_sent(caplog):
    """openWakeWord listens for its default word, silently, for a name it lacks."""
    with caplog.at_level("WARNING"):
        names, _ = await _asked_for("hey_jarvis", name="computer")
    assert names == ["hey_jarvis"]
    assert "computer" in caplog.text


async def test_a_name_is_not_sent_when_the_service_cannot_be_asked(caplog):
    # Nothing listening on the port: the describe fails, the start-up model is kept.
    client = WyomingWakeClient("127.0.0.1", 1, model="hey_jarvis")
    with caplog.at_level("WARNING"):
        assert await client.wake_name("computer") == "hey_jarvis"
    assert "computer" in caplog.text


async def aura():
    return "aura"


async def test_the_chosen_word_beats_the_persona_word_where_both_are_served():
    names, _ = await _asked_for("hey_jarvis", "aura", "computer", name="computer", persona=aura)
    assert names == ["computer"]


async def test_a_chosen_word_the_service_lacks_falls_back_to_the_persona_word(caplog):
    with caplog.at_level("WARNING"):
        names, _ = await _asked_for("hey_jarvis", "aura", name="computer", persona=aura)
    assert names == ["aura"]
    assert "computer" in caplog.text


async def test_choosing_the_start_up_word_beats_the_persona_and_costs_no_describe():
    names, describes = await _asked_for(
        "hey_jarvis", "aura", name="hey_jarvis", persona=aura
    )
    assert names == ["hey_jarvis"] and describes == 0


async def test_nothing_chosen_follows_the_persona_word_as_before():
    names, _ = await _asked_for("hey_jarvis", "aura", persona=aura)
    assert names == ["aura"]
    names, _ = await _asked_for("hey_jarvis", name=None, persona=aura)
    assert names == ["hey_jarvis"]


async def test_nothing_chosen_asks_what_it_asked_before():
    names, describes = await _asked_for("hey_jarvis", "computer")
    assert names == ["hey_jarvis"] and describes == 0
    names, describes = await _asked_for("hey_jarvis", "computer", name=None)
    assert names == ["hey_jarvis"] and describes == 0


async def test_nothing_chosen_with_the_persona_word_equal_to_the_model_costs_no_describe():
    async def same():
        return "hey_jarvis"

    names, describes = await _asked_for("hey_jarvis", persona=same)
    assert names == ["hey_jarvis"] and describes == 0


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
class RecordingWake:
    def __init__(self):
        self.names: list[str | None] = []

    async def detect(self, audio_iter, name=None):
        self.names.append(name)
        async for _chunk in audio_iter:
            pass
        return "hey_jarvis"


async def _names_a_run_hands_over(tmp_path, **run_kwargs):
    wake = RecordingWake()
    run = PipelineRun(
        Jarvis(tmp_path), wake=wake, start_stage="wake", end_stage="wake", **run_kwargs
    )

    async def events(_kind, _data):
        return None

    await run.execute(await queue_of(*[sine_pcm(20)] * 2), events)
    return wake.names


async def test_a_run_hands_the_chosen_word_to_the_wake_client(tmp_path):
    names = await _names_a_run_hands_over(
        tmp_path, wake_word="computer", wake_word_chosen="computer"
    )
    assert names == ["computer"]


async def test_a_run_with_nothing_chosen_hands_the_client_no_name(tmp_path):
    """A pipeline's own word is inert at the client, as it was before T-1479."""
    assert await _names_a_run_hands_over(tmp_path, wake_word="hey_jarvis") == [None]


# ---------------------------------------------------------------------------
# the manager
# ---------------------------------------------------------------------------
SHIPPED = {"pipelines": [{"name": "Jarvis", "wake_word": "hey_jarvis"}]}


def _run_word(jarvis) -> str | None:
    return jarvis.data["voice"].async_create_run().wake_word


async def test_with_nothing_chosen_the_run_word_is_the_pipelines(tmp_path):
    jarvis = await setup_voice(tmp_path, dict(SHIPPED))
    run = jarvis.data["voice"].async_create_run()
    assert jarvis.data["voice"].wake_word_chosen is None
    assert run.wake_word == "hey_jarvis"
    assert run.wake_word_chosen is None


async def test_a_word_chosen_in_the_console_beats_the_pipelines_word(tmp_path):
    jarvis = await setup_voice(tmp_path, dict(SHIPPED))
    result = await common.async_set_setting(
        jarvis, {"key": "voice.wake_word", "value": "computer"}
    )
    assert result["applied"] is True
    run = jarvis.data["voice"].async_create_run()
    assert run.wake_word == "computer" and run.wake_word_chosen == "computer"


async def test_a_house_that_never_chose_asks_for_its_configured_model(tmp_path):
    """`voice.wake.model: X` with a pipeline record still naming hey_jarvis.

    Every record defaults to hey_jarvis, so a run's own word is not the owner's
    word: sending it would swap the configured model for the built-in one.
    """
    fake = await FakeWyomingServer(info=_wake_info("hey_jarvis", "alexa")).start()
    try:
        jarvis = await setup_voice(
            tmp_path,
            {
                "wake": {"host": "127.0.0.1", "port": fake.port, "model": "alexa"},
                "pipelines": [{"name": "Jarvis", "wake_word": "hey_jarvis"}],
            },
        )

        async def events(_kind, _data):
            return None

        run = jarvis.data["voice"].async_create_run(start_stage="wake", end_stage="wake")
        await run.execute(await queue_of(*[sine_pcm(20)] * 4), events)
        assert fake.events_of("detect")[0]["data"]["names"] == ["alexa"]
    finally:
        await fake.stop()


async def test_resetting_the_word_gives_the_run_back_to_the_pipeline(tmp_path):
    jarvis = await setup_voice(tmp_path, dict(SHIPPED))
    await common.async_set_setting(jarvis, {"key": "voice.wake_word", "value": "computer"})
    await common.async_reset_setting(jarvis, {"key": "voice.wake_word"})
    assert jarvis.data["voice"].wake_word_chosen is None
    assert _run_word(jarvis) == "hey_jarvis"


# ---------------------------------------------------------------------------
# start-up
# ---------------------------------------------------------------------------
async def test_set_up_reads_the_chosen_word_from_the_voice_block(tmp_path):
    jarvis = await setup_voice(tmp_path, {**SHIPPED, "wake_word": "computer"})
    assert jarvis.data["voice"].wake_word_chosen == "computer"
    assert _run_word(jarvis) == "computer"


@pytest.mark.parametrize("stored", [None, "", "   ", 7, ["computer"]])
async def test_set_up_ignores_a_word_that_is_not_a_name(tmp_path, stored):
    jarvis = await setup_voice(tmp_path, {**SHIPPED, "wake_word": stored})
    assert jarvis.data["voice"].wake_word_chosen is None
    assert _run_word(jarvis) == "hey_jarvis"


async def test_a_stored_choice_survives_a_restart_through_the_overlay(tmp_path):
    """The path a restart takes: overlay merged over the file, then set-up."""
    overlay = SettingsOverlay(tmp_path)
    await overlay.async_set("voice.wake_word", "computer")

    raw = {"voice": {**SHIPPED, "wake": {"model": "hey_jarvis"}}}
    after_restart = SettingsOverlay(tmp_path)
    await after_restart.async_load()
    merged, unapplied = after_restart.apply(raw)
    assert unapplied == []
    jarvis = await setup_voice(tmp_path, merged["voice"])
    assert _run_word(jarvis) == "computer"
