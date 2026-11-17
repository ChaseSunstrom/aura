#!/usr/bin/env python3
"""Synthesize a theme's earcons (T-0861): soft bell notes, no samples, so no licence to track.

    python3 scripts/themes/earcons.py aura      # writes themes/aura/assets/sound/aura-<name>.ogg

Each earcon is a few sine partials with an exponential decay, peak-normalised to -6 dBFS and
encoded with ffmpeg (libvorbis). Not covered: loudness against the voice; the surfaces duck.
"""
from __future__ import annotations

import math
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RATE = 48000
#: name -> the notes (semitones above A4) and their onsets in seconds.
SHAPES = {
    "wake": [(3, 0.0), (10, 0.09)],
    "done": [(10, 0.0), (3, 0.11)],
    "approval": [(3, 0.0), (7, 0.1), (12, 0.2)],
    "error": [(-2, 0.0), (-7, 0.14)],
}


def bell(semitones: int, length: float) -> list[float]:
    f = 440.0 * 2 ** (semitones / 12)
    out = []
    for i in range(int(RATE * length)):
        t = i / RATE
        env = math.exp(-t * 7.0) * min(1.0, t / 0.004)
        out.append(env * (math.sin(2 * math.pi * f * t) + 0.35 * math.sin(2 * math.pi * 2 * f * t)
                          + 0.12 * math.sin(2 * math.pi * 3.01 * f * t)))
    return out


def render(notes: list[tuple[int, float]]) -> list[float]:
    total = max(on for _, on in notes) + 0.6
    mix = [0.0] * int(RATE * total)
    for semis, on in notes:
        start = int(RATE * on)
        for i, v in enumerate(bell(semis, 0.6)):
            mix[start + i] += v
    peak = max(abs(v) for v in mix) or 1.0
    return [v / peak * 0.5 for v in mix]  # -6 dBFS


def main(argv: list[str]) -> int:
    theme = argv[0] if argv else "aura"
    out = ROOT / "themes" / theme / "assets" / "sound"
    out.mkdir(parents=True, exist_ok=True)
    for name, notes in SHAPES.items():
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            with wave.open(tmp.name, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(b"".join(struct.pack("<h", round(v * 32767)) for v in render(notes)))
            target = out / f"{theme}-{name}.ogg"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", tmp.name, "-c:a", "libvorbis",
                            "-q:a", "4", "-map_metadata", "-1", "-fflags", "+bitexact", str(target)], check=True)
            print(f"wrote {target.relative_to(ROOT)} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
