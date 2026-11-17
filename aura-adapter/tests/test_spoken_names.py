"""T-0587: names said the way the owner taught them, and a softer, slower voice in quiet hours.

The taught spellings are ``voice.pronunciations`` (settable, so "say Siobhan as shi-vawn"
reaches it by voice) and the hours ``voice.quiet_hours``; the adapter re-reads both on every
line, so a change applies to the next sentence.
"""
from __future__ import annotations

import array
import datetime

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from aura_adapter import app as app_mod
from aura_adapter.app import create_app
from aura_adapter.config import Settings
from aura_adapter.speech_text import taught_from
from aura_adapter.spoken import QUIET_GAIN, QUIET_SPEED, Softer, quiet_now, spoken
from test_adapter import APP_KEY, _free_port, _serve

AUTH = {"Authorization": f"Bearer {APP_KEY}"}
LOUD = array.array("h", [12000, -12000, 8000, -8000] * 50).tobytes()


def test_a_taught_name_is_said_the_owners_way_whatever_its_case():
    taught = taught_from("Siobhan=shi-vawn; Nguyen = win; half-a-pair; =x")
    assert taught == {"siobhan": "shi-vawn", "nguyen": "win"}
    assert spoken("**SIOBHAN**'s car is at Nguyen's.", taught) == "shi-vawn's car is at win's."
    # Whole words only: a longer word that starts with the name is left as written.
    assert spoken("Siobhanfest", taught) == "Siobhanfest"
    assert spoken("Siobhan", None) == "Siobhan"


def test_quiet_hours_wrap_midnight_and_an_empty_span_is_never():
    at = datetime.time
    assert quiet_now("23:00-07:00", at(23, 30)) and quiet_now("23:00-07:00", at(3, 0))
    assert not quiet_now("23:00-07:00", at(7, 0)) and not quiet_now("23:00-07:00", at(12, 0))
    assert quiet_now("13:00-14:00", at(13, 15)) and not quiet_now("13:00-14:00", at(14, 0))
    assert not quiet_now("", at(23, 30)) and not quiet_now("late", at(23, 30))


def test_softer_halves_every_sample_across_a_split_chunk():
    soft = Softer()
    out = soft.feed(LOUD[:3]) + soft.feed(LOUD[3:])
    assert array.array("h", out).tolist() == [int(v * QUIET_GAIN) for v in array.array("h", LOUD)]


def test_speak_pcm_says_taught_names_and_is_slower_and_softer_in_quiet_hours(monkeypatch):
    heard: list[dict] = []
    # The pre-warm makes its fixed lines in the background meanwhile: ours are found by their words.
    mine = lambda: [h for h in heard if h["input"].startswith("Goodnight")]  # noqa: E731
    shim = FastAPI()

    @shim.post("/v1/audio/speech")
    async def speech(request: Request):
        heard.append(await request.json())
        return Response(LOUD, media_type="audio/pcm", headers={"x-sample-rate": "24000"})

    port = _free_port()
    server = _serve(shim, port)
    monkeypatch.setenv("AURA_PRONUNCIATIONS", "Siobhan=shi-vawn")
    monkeypatch.setenv("AURA_QUIET_HOURS", "23:00-07:00")
    try:
        with TestClient(create_app(Settings(api_key=APP_KEY, tts_engine="cosyvoice", tts_url=f"http://127.0.0.1:{port}"))) as client:
            monkeypatch.setattr(app_mod, "_clock", lambda: datetime.time(23, 30))
            night = client.post("/api/audio/speak-pcm", headers=AUTH, json={"text": "Goodnight, Siobhan.", "voice": "bm_george"})
            assert night.status_code == 200, night.text
            assert mine()[-1]["input"] == "Goodnight, shi-vawn."
            assert mine()[-1]["speed"] == QUIET_SPEED
            assert night.content == Softer().feed(LOUD), "a quiet-hours line is about half as loud"

            monkeypatch.setattr(app_mod, "_clock", lambda: datetime.time(12, 0))
            day = client.post("/api/audio/speak-pcm", headers=AUTH, json={"text": "Goodnight, Siobhan.", "voice": "bm_george"})
            assert len(mine()) == 2 and "speed" not in mine()[-1], "the day's voice is the voice's own pace"
            assert day.content == LOUD, "not the night's line from the cache, nor softened"
    finally:
        server.should_exit = True
