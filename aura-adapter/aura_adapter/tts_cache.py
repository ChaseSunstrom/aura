"""Short lines Jarvis says word for word again and again, kept as the audio they became.

speak-pcm made every line fresh: "Pausing your music." cost 1.1-1.3 s to its first byte on
production each time it was said (2026-10-09), after the fast path (fast_intent) had answered
it in no time as text. A line of at most MAX_CHARS that streamed through whole is kept here,
keyed by voice and spoken text, and the next request for it is answered from memory; the
fast path's fixed lines are made once at start (:func:`prewarm_lines`).

Memory only, on purpose: what Jarvis said is never written down, and the cache dies with the
adapter. Does not cover a voice whose clip changes under the same name (the old audio is
served until the adapter restarts, which every deploy does), lines that vary (the time), or
the whole-line ``/api/audio/speak``.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime

from . import fast_intent

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class PhraseCache:
    """Least recently used out first, under a byte cap."""

    #: An acknowledgement, not a reply: the longest fast-path line is 105 characters.
    MAX_CHARS = 120
    #: About five and a half minutes of 24 kHz speech.
    MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, max_bytes: int = MAX_BYTES) -> None:
        self._lines: OrderedDict[tuple[str, str], tuple[str, bytes]] = OrderedDict()
        self._max = max_bytes
        self.size = 0

    def get(self, voice: str, text: str) -> tuple[str, bytes] | None:
        """(sample rate, PCM) for a line said before in this voice, or None."""
        hit = self._lines.get((voice, text))
        if hit is not None:
            self._lines.move_to_end((voice, text))
        return hit

    def put(self, voice: str, text: str, rate: str, pcm: bytes) -> bool:
        """Keep a whole line; False for one too long, empty or bigger than the cap."""
        if not pcm or len(text) > self.MAX_CHARS or len(pcm) > self._max:
            return False
        old = self._lines.pop((voice, text), None)
        if old is not None:
            self.size -= len(old[1])
        self._lines[(voice, text)] = (rate, pcm)
        self.size += len(pcm)
        while self.size > self._max:
            _, (_, gone) = self._lines.popitem(last=False)
            self.size -= len(gone)
        return True


def prewarm_lines() -> list[str]:
    """The fast path's fixed replies: whole, as the phone speaks a reply, and by sentence, as
    the console does. The time and the date vary, so they are not here."""
    now = datetime(2026, 1, 1)
    whole = [fast_intent.answer(kind, now, down=down, cold=cold)
             for kind in ("media:pause", "media:play", "media:next", "media:previous")
             for down, cold in ((False, False), (False, True), (True, False))]
    whole.append(fast_intent.UNREACHABLE_LINE)
    lines = whole + [s for line in whole for s in _SENTENCE.split(line)]
    return [line for line in dict.fromkeys(lines) if len(line) <= PhraseCache.MAX_CHARS]
