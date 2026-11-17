"""Speech made at one rate, sent at another: the car's guidance channel plays 16 kHz (T-0920).

CosyVoice speaks at 24 kHz and the phone resampled each line for the car itself, so a
third of the bytes it pulled over its mobile link were thrown away on arrival; the car's
cut-outs were that link running near real time (T-0917).

Linear interpolation over 16-bit mono little-endian PCM, carried across pieces so a
streamed line has no seam where one piece ends. Does not low-pass first: what the voice
puts above the new Nyquist folds back down, as it did in the phone's own resampler.
"""

from __future__ import annotations

import sys
from array import array

#: The rates a caller may ask for: the car's channels and the phone's players.
RATES = frozenset({8000, 16000, 22050, 24000, 44100, 48000})


class Resampler:
    """Pieces of `src` Hz PCM in, `dst` Hz PCM out. At most the line's last sample is lost."""

    def __init__(self, src: int, dst: int) -> None:
        self._step = src / dst
        self._pos = 0.0  # the next output sample, in input samples from `_held[0]`
        self._held = array("h")
        self._odd = b""  # a piece may end mid-sample (TtsPlayer's odd-sized read, T-0211)

    def feed(self, piece: bytes) -> bytes:
        data = self._odd + piece
        cut = len(data) - len(data) % 2
        self._odd = data[cut:]
        more = array("h", data[:cut])
        if sys.byteorder == "big":
            more.byteswap()
        held, pos, step = self._held, self._pos, self._step
        held.extend(more)
        out = array("h")
        last = len(held) - 1
        while pos < last:
            i = int(pos)
            a = held[i]
            out.append(round(a + (held[i + 1] - a) * (pos - i)))
            pos += step
        drop = min(int(pos), len(held))
        del held[:drop]
        self._pos = pos - drop
        if sys.byteorder == "big":
            out.byteswap()
        return out.tobytes()
