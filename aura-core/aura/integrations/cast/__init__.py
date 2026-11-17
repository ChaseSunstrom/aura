"""Cast integration — Chromecast, Google TV and Nest speakers as media players.

    cast:
      hosts: [192.168.1.40]          # optional; reached directly, beside the mDNS browse
      base_url: http://192.168.1.2:8080   # how a cast reaches this house's /api URLs
      discovery_timeout: 10
      scan_interval: 10

Discovery runs once at setup and only when a ``cast:`` block exists: the
browse and every connected cast cost pychromecast a socket thread each, so a
house without casts pays nothing. Each cast becomes ``media_player.<name>``
with ``play_media`` (a URL), ``volume_set``, ``media_play``/``media_pause``/
``media_stop`` and ``turn_off`` (quits the running app). ``voice.say`` with
an ``entity_id`` is the cast-TTS path: it hands over a relative
``/api/tts_proxy/…`` URL, which ``base_url`` makes absolute — a cast cannot
resolve a path, and without ``base_url`` such a call fails instead of
playing nothing.

Not covered: a cast that appears after setup (restart the house to pick it
up), groups, and the Jellyfin/YouTube receiver apps — a Jellyfin stream is
played as a plain URL on the default media receiver.

Tests replace discovery with ``jarvis.data["cast"]["discover"]``, a function
``(hosts, timeout) -> [Chromecast-like]``; pychromecast is imported lazily so
the suite runs without it.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import TYPE_CHECKING, Any

from ...const import STATE_IDLE, STATE_OFF, STATE_PAUSED, STATE_PLAYING
from ...entity import Entity, EntityPlatform

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "cast"

DEFAULT_SCAN_INTERVAL = 10.0
DEFAULT_DISCOVERY_TIMEOUT = 10.0

#: pychromecast's player_state → media_player state. Anything else (UNKNOWN,
#: no app running) reads as off, which is what a person sees on the screen.
_PLAYER_STATES = {
    "PLAYING": STATE_PLAYING,
    "BUFFERING": STATE_PLAYING,
    "PAUSED": STATE_PAUSED,
    "IDLE": STATE_IDLE,
}

#: Extensions pinned ahead of `mimetypes`, whose answer for these differs by
#: Python version and host (`audio/x-wav` on 3.11) — the receiver wants these.
_EXTENSIONS = {".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg"}

_VIDEO_TYPES = frozenset({"video", "movie", "episode", "tvshow", "channel"})


def content_type(media_type: str, url: str) -> str:
    """The MIME type the default receiver needs; it refuses a bare 'music'."""
    if "/" in str(media_type):
        return str(media_type)
    path = url.split("?", 1)[0]
    guessed = _EXTENSIONS.get(path[path.rfind("."):].lower()) or mimetypes.guess_type(path)[0]
    if guessed:
        return guessed
    # Jellyfin stream URLs carry no extension; the media type is all there is.
    return "video/mp4" if str(media_type).lower() in _VIDEO_TYPES else "audio/mpeg"


def _discover(hosts: list[str] | None, timeout: float) -> list[Any]:
    """Blocking mDNS browse (or direct connect to ``hosts``) via pychromecast."""
    import pychromecast  # lazy: a house with no cast block never loads it

    casts, browser = pychromecast.get_chromecasts(known_hosts=hosts, timeout=timeout)
    # The browse thread is not needed once the list is in; each cast keeps
    # its own socket thread for status.
    browser.stop_discovery()
    return list(casts)


class CastMediaPlayer(Entity):
    """One cast device. Status is read from pychromecast's pushed copy."""

    def __init__(self, cast: Any, base_url: str | None) -> None:
        self._cast = cast
        self._base_url = base_url.rstrip("/") if base_url else None
        info = cast.cast_info
        self._attr_name = info.friendly_name
        self._attr_unique_id = f"cast_{cast.uuid}"
        self._attr_should_poll = True
        self._media: tuple[str, str] | None = None
        self._off = False
        self.apply_status()

    @property
    def device_info(self) -> dict[str, Any]:
        info = self._cast.cast_info
        return {
            "identifiers": [self._attr_unique_id],
            "name": info.friendly_name,
            "manufacturer": getattr(info, "manufacturer", None) or "Google",
            "model": getattr(info, "model_name", None) or "Chromecast",
        }

    def apply_status(self) -> None:
        status = getattr(self._cast.media_controller, "status", None)
        player = getattr(status, "player_state", None) or "UNKNOWN"
        self._attr_state = STATE_OFF if self._off else _PLAYER_STATES.get(player, STATE_OFF)
        attributes: dict[str, Any] = {}
        volume = getattr(self._cast.status, "volume_level", None)
        if volume is not None:
            attributes["volume_level"] = float(volume)
        if self._media is not None and self._attr_state != STATE_OFF:
            attributes["media_content_type"], attributes["media_content_id"] = self._media
        self._attr_extra_attributes = attributes

    async def async_update(self) -> None:
        self.apply_status()

    async def _run(self, func: Any, *args: Any) -> None:
        # pychromecast calls send on a socket and may wait for an answer.
        await asyncio.to_thread(func, *args)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        url = str(media_id)
        if url.startswith("/"):
            if not self._base_url:
                raise ValueError(
                    f"{url} is relative and the cast block has no base_url to make it reachable"
                )
            url = self._base_url + url
        await self._run(
            self._cast.media_controller.play_media, url, content_type(media_type, url)
        )
        self._off = False
        self._media = (str(media_type), url)
        self.apply_status()

    async def async_media_play(self, **kwargs: Any) -> None:
        await self._run(self._cast.media_controller.play)
        self.apply_status()

    async def async_media_pause(self, **kwargs: Any) -> None:
        await self._run(self._cast.media_controller.pause)
        self.apply_status()

    async def async_media_stop(self, **kwargs: Any) -> None:
        await self._run(self._cast.media_controller.stop)
        self.apply_status()

    async def async_volume_set(self, volume_level: float, **kwargs: Any) -> None:
        await self._run(self._cast.set_volume, max(0.0, min(1.0, float(volume_level))))
        self.apply_status()

    async def async_turn_on(self, **kwargs: Any) -> None:
        # Nothing to launch without media; clear the sticky off so status shows.
        self._off = False
        self.apply_status()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run(self._cast.quit_app)
        self._off = True
        self._media = None
        self.apply_status()


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    # Called only when a `cast:` key exists (it is not a core integration);
    # a bare `cast:` arrives as None and means "browse mDNS".
    block = config if isinstance(config, dict) else {}
    hosts = [str(h) for h in block.get("hosts") or []] or None
    timeout = float(block.get("discovery_timeout", DEFAULT_DISCOVERY_TIMEOUT))

    store = jarvis.data.setdefault(DOMAIN, {})
    discover = store.get("discover", _discover)
    try:
        casts = await asyncio.to_thread(discover, hosts, timeout)
    except ImportError:
        _LOGGER.error("cast: pychromecast is not installed")
        return True
    except Exception:
        _LOGGER.exception("cast: discovery failed")
        return True

    entities: list[CastMediaPlayer] = []
    for cast in casts:
        try:
            await asyncio.to_thread(cast.wait, timeout)
        except Exception as exc:
            _LOGGER.error("cast: %s did not connect: %s", cast.cast_info.friendly_name, exc)
            continue
        jarvis.register_shutdown(lambda cast=cast: asyncio.to_thread(cast.disconnect, 5))
        entities.append(CastMediaPlayer(cast, block.get("base_url")))

    if entities:
        platform = EntityPlatform(
            jarvis, "media_player", DOMAIN,
            float(block.get("scan_interval", DEFAULT_SCAN_INTERVAL)),
        )
        await platform.async_add_entities(entities)
    store["entities"] = entities
    _LOGGER.info("cast: %d device(s) ready", len(entities))
    return True
