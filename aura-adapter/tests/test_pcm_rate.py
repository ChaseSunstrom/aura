"""The car's speech at the guidance channel's rate (T-0920)."""

from __future__ import annotations

import math
from array import array

from aura_adapter.pcm_rate import Resampler


def _tone(rate: int, seconds: float, hz: float = 440.0) -> list[int]:
    return [round(8000 * math.sin(2 * math.pi * hz * n / rate)) for n in range(int(rate * seconds))]


def test_rate_24k_to_16k_keeps_the_tone_in_two_thirds_of_the_bytes():
    src = array("h", _tone(24000, 0.5)).tobytes()
    out = array("h", Resampler(24000, 16000).feed(src))
    assert abs(len(out) - 8000) <= 1
    want = _tone(16000, 0.5)
    assert max(abs(a - b) for a, b in zip(out, want)) < 120  # linear interpolation of a 440 Hz tone


def test_rate_change_has_no_seam_where_a_streamed_piece_ends():
    src = array("h", _tone(24000, 0.3)).tobytes()
    whole = Resampler(24000, 16000).feed(src)
    r = Resampler(24000, 16000)
    # Odd sizes too: a piece can end in the middle of a sample.
    pieces = [src[i:i + 777] for i in range(0, len(src), 777)]
    assert b"".join(r.feed(p) for p in pieces) == whole


def test_rate_conversion_runs_both_ways():
    src = array("h", _tone(16000, 0.25)).tobytes()
    assert abs(len(Resampler(16000, 24000).feed(src)) // 2 - 6000) <= 2
    assert abs(len(Resampler(24000, 22050).feed(array("h", _tone(24000, 1.0)).tobytes())) // 2 - 22050) <= 2
