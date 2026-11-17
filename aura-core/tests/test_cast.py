"""Chromecast / Google TV as media_player entities (integrations/cast).

No network and no pychromecast: ``jarvis.data["cast"]["discover"]`` replaces
the mDNS browse with a function that returns fake casts shaped like
pychromecast's ``Chromecast`` (``cast_info``, ``status``, ``media_controller``,
``set_volume``, ``quit_app``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.core import Jarvis  # noqa: E402
from aura.integrations import cast as cast_integration  # noqa: E402
from aura.integrations import voice as voice_integration  # noqa: E402
from aura.integrations.domains import async_setup as domains_setup  # noqa: E402


class FakeMediaController:
    def __init__(self, log: list) -> None:
        self.log = log
        self.status = SimpleNamespace(player_state="UNKNOWN")

    def play_media(self, url: str, content_type: str) -> None:
        self.log.append(("play_media", url, content_type))
        self.status.player_state = "PLAYING"

    def play(self) -> None:
        self.log.append(("play",))
        self.status.player_state = "PLAYING"

    def pause(self) -> None:
        self.log.append(("pause",))
        self.status.player_state = "PAUSED"

    def stop(self) -> None:
        self.log.append(("stop",))
        self.status.player_state = "IDLE"


class FakeCast:
    def __init__(self, name: str = "Living Room TV", uuid: str = "abc-123") -> None:
        self.log: list = []
        self.uuid = uuid
        self.cast_info = SimpleNamespace(
            friendly_name=name, model_name="Google TV", manufacturer="Google", host="10.0.0.40"
        )
        self.status = SimpleNamespace(volume_level=0.3)
        self.media_controller = FakeMediaController(self.log)
        self.waited = False

    def wait(self, timeout: float | None = None) -> None:
        self.waited = True

    def set_volume(self, volume: float) -> float:
        self.log.append(("set_volume", volume))
        self.status.volume_level = volume
        return volume

    def quit_app(self) -> None:
        self.log.append(("quit_app",))
        self.media_controller.status.player_state = "UNKNOWN"

    def disconnect(self, timeout: float | None = None) -> None:
        self.log.append(("disconnect",))


async def make_jarvis(tmp_path: Path, casts: list[FakeCast], config: dict | None = None):
    jarvis = Jarvis(tmp_path)
    await jarvis.areas.load()
    await jarvis.devices.load()
    await jarvis.entities.load()
    await domains_setup(jarvis, None)
    seen: dict = {}

    def discover(hosts, timeout):
        seen["hosts"] = hosts
        return casts

    jarvis.data["cast"] = {"discover": discover}
    assert await cast_integration.async_setup(jarvis, config or {}) is True
    return jarvis, seen


async def shutdown(jarvis: Jarvis) -> None:
    jarvis.is_running = True
    await jarvis.async_stop()


def test_cast_is_opt_in():
    """Only a `cast:` key loads it — the browse and per-cast threads cost."""
    from aura.integrations import CORE_INTEGRATIONS

    assert "cast" not in CORE_INTEGRATIONS


async def test_a_bare_cast_key_browses_mdns(tmp_path):
    jarvis = Jarvis(tmp_path)
    seen = []
    jarvis.data["cast"] = {"discover": lambda hosts, timeout: seen.append(hosts) or []}
    # `cast:` with nothing under it reaches async_setup as None.
    assert await cast_integration.async_setup(jarvis, None) is True
    assert seen == [None]


async def test_a_discovered_cast_is_a_media_player(tmp_path):
    tv = FakeCast()
    jarvis, seen = await make_jarvis(tmp_path, [tv], {"hosts": ["10.0.0.40"]})
    try:
        assert seen["hosts"] == ["10.0.0.40"]
        assert tv.waited
        state = jarvis.states.get("media_player.living_room_tv")
        assert state is not None
        assert state.attributes["volume_level"] == pytest.approx(0.3)
    finally:
        await shutdown(jarvis)


async def test_playback_services_reach_the_cast(tmp_path):
    tv = FakeCast()
    jarvis, _ = await make_jarvis(tmp_path, [tv])
    eid = "media_player.living_room_tv"
    try:
        await jarvis.services.async_call(
            "media_player", "play_media",
            {"entity_id": eid, "media_type": "video",
             "media_id": "http://jellyfin.test/Videos/1/stream"},
            blocking=True,
        )
        assert tv.log[-1] == ("play_media", "http://jellyfin.test/Videos/1/stream", "video/mp4")
        assert jarvis.states.get(eid).state == "playing"

        await jarvis.services.async_call(
            "media_player", "volume_set", {"entity_id": eid, "volume_level": 0.6}, blocking=True
        )
        assert tv.log[-1] == ("set_volume", 0.6)
        assert jarvis.states.get(eid).attributes["volume_level"] == pytest.approx(0.6)

        await jarvis.services.async_call(
            "media_player", "media_pause", {"entity_id": eid}, blocking=True
        )
        assert tv.log[-1] == ("pause",)
        assert jarvis.states.get(eid).state == "paused"

        await jarvis.services.async_call(
            "media_player", "media_stop", {"entity_id": eid}, blocking=True
        )
        assert tv.log[-1] == ("stop",)

        await jarvis.services.async_call(
            "media_player", "turn_off", {"entity_id": eid}, blocking=True
        )
        assert tv.log[-1] == ("quit_app",)
        assert jarvis.states.get(eid).state == "off"
    finally:
        await shutdown(jarvis)


async def test_content_type_follows_the_url_then_the_media_type(tmp_path):
    tv = FakeCast()
    jarvis, _ = await make_jarvis(tmp_path, [tv])
    try:
        for media_type, url, expected in (
            ("music", "http://h/song.flac", "audio/flac"),
            ("music", "http://h/stream", "audio/mpeg"),
            ("audio/ogg", "http://h/stream", "audio/ogg"),
        ):
            await jarvis.services.async_call(
                "media_player", "play_media",
                {"entity_id": "media_player.living_room_tv", "media_type": media_type,
                 "media_id": url},
                blocking=True,
            )
            assert tv.log[-1] == ("play_media", url, expected)
    finally:
        await shutdown(jarvis)


async def test_cast_tts_speaks_through_voice_say(tmp_path):
    """voice.say hands a relative /api/tts_proxy URL; the cast needs it absolute."""

    class Tts:
        async def synthesize(self, text, voice=None):
            return (b"\0\0" * 2205, 22050, 2, 1)

    tv = FakeCast()
    jarvis, _ = await make_jarvis(tmp_path, [tv], {"base_url": "http://10.0.0.2:8080/"})
    jarvis.data["voice_tts_client"] = Tts()
    assert await voice_integration.async_setup(jarvis, {"wake": False}) is True
    try:
        result = await jarvis.services.async_call(
            "voice", "say",
            {"text": "Dinner is ready.", "entity_id": "media_player.living_room_tv"},
            blocking=True, return_response=True,
        )
        assert result["failed"] == {}
        verb, url, mime = tv.log[-1]
        assert verb == "play_media" and mime == "audio/wav"
        assert url == "http://10.0.0.2:8080" + result["url"]
    finally:
        await shutdown(jarvis)


async def test_a_relative_url_without_base_url_is_refused(tmp_path):
    tv = FakeCast()
    jarvis, _ = await make_jarvis(tmp_path, [tv])
    try:
        result = await jarvis.services.async_call(
            "media_player", "play_media",
            {"entity_id": "media_player.living_room_tv", "media_type": "music",
             "media_id": "/api/tts_proxy/x.wav"},
            blocking=True, return_response=True,
        )
        assert "media_player.living_room_tv" in (result or {}).get("failed", {})
        assert tv.log == []
    finally:
        await shutdown(jarvis)
