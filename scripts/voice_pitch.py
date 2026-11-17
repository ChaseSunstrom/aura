"""After a deploy, the voice that speaks is the persona's, from the voice service on the GPU.

The owner heard "a girl speaking instead of jarvis" (2026-10), and the smoke's round trip
(step 6) passes for any voice that says the words. This asks the adapter for one line as
raw PCM (/api/audio/speak-pcm), measures its median pitch by autocorrelation, and asks the
voice service which device it runs on. Exits 1 on the house's fallback voice, on a device
other than cuda, on a line with no voiced speech, or on a pitch on the wrong side of the
ceiling for the voice the adapter chose (x-jarvis-voice): a female Kokoro voice (``af_``,
``bf_``) or Ada's and Friday's clips sit over it, every other voice under it (T-0861: AURA's
af_heart is female, JARVIS's bm_george male).

Env: AURA_BASE, AURA_API_KEY; SMOKE_VOICE_BASE (CosyVoice, default its host loopback
port); SMOKE_VOICE_MAX_HZ (default 165: male speech sits at about 85-155 Hz, female at
165-255); SMOKE_TIMEOUT.

Does not cover: a male voice that is not Jarvis's (another male reference clip passes);
an engine that does not stream (Piper through hermes serve: skipped, it answers MP3 and
this is stdlib only); the voice's quality. The pitch is an estimate, not a classifier.
Stdlib only, so the host's python3 runs it.
"""
from __future__ import annotations

import array
import json
import os
import statistics
import sys
import urllib.error
import urllib.request

LINE = "Good evening, Sir. All systems are running normally."
#: The persona clips that are women's voices; a Kokoro id says it itself (af_, bf_).
FEMALE_CLIPS = {"aura"}


def median_f0(pcm: bytes, rate: int) -> float:
    """The median pitch (Hz) of the voiced frames of 16-bit mono little-endian PCM; 0 if none."""
    s = array.array("h")
    s.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        s.byteswap()
    # ponytail: plain-Python autocorrelation, about a second a line at 8 kHz; numpy if lines grow.
    step = max(1, rate // 8000)
    x = [sum(s[i:i + step]) / step for i in range(0, len(s) - step + 1, step)]
    sr = rate / step
    n = int(sr * 0.04)
    lo, hi = int(sr / 400), int(sr / 60)
    frames = [x[i:i + n + hi + 1] for i in range(0, len(x) - n - hi, n // 2)]
    energy = [sum(v * v for v in f[:n]) for f in frames]
    if not frames or not max(energy):
        return 0.0
    f0s = []
    for f, e in zip(frames, energy):
        if e < max(energy) * 0.1:
            continue
        r = []
        for lag in range(lo, hi + 1):
            shifted = sum(v * v for v in f[lag:lag + n])
            r.append(sum(f[i] * f[i + lag] for i in range(n)) / ((e * shifted) ** 0.5 or 1.0))
        best = max(r)
        if best < 0.5:
            continue
        # The first peak near the best, not the best: twice the period correlates as well,
        # and taking it would read a 220 Hz voice as a 110 Hz one.
        peak = next(i for i in range(1, len(r) - 1)
                    if r[i] >= 0.9 * best and r[i] >= r[i - 1] and r[i] >= r[i + 1])
        f0s.append(sr / (lo + peak))
    return statistics.median(f0s) if f0s else 0.0


def main() -> int:
    base = os.environ.get("AURA_BASE", "http://127.0.0.1:8643").rstrip("/")
    voice = os.environ.get("SMOKE_VOICE_BASE", "http://127.0.0.1:8890").rstrip("/")
    ceiling = float(os.environ.get("SMOKE_VOICE_MAX_HZ") or 165)
    timeout = float(os.environ.get("SMOKE_TIMEOUT") or 900)
    req = urllib.request.Request(
        f"{base}/api/audio/speak-pcm", data=json.dumps({"text": LINE}).encode(),
        headers={"Authorization": f"Bearer {os.environ['AURA_API_KEY']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            pcm, rate = r.read(), int(r.headers.get("x-sample-rate") or 24000)
            engine = r.headers.get("x-jarvis-tts-engine") or "unknown"
            spoken = r.headers.get("x-jarvis-voice") or ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        if exc.code == 404 and "does not stream" in detail:
            print("SKIP: this stack's voice does not stream (not CosyVoice); pitch not measured")
            return 0
        print(f"FAIL: speak-pcm answered {exc.code}: {detail}")
        return 1
    if engine != "cosyvoice":
        print(f"FAIL: the {engine} voice spoke, not CosyVoice: the GPU voice is down or on the CPU")
        return 1
    try:
        with urllib.request.urlopen(f"{voice}/health", timeout=10) as r:
            device = str(json.load(r).get("device") or "")
    except (OSError, ValueError) as exc:
        print(f"FAIL: the voice's /health at {voice} did not answer ({exc.__class__.__name__}); set SMOKE_VOICE_BASE")
        return 1
    if device != "cuda":
        print(f"FAIL: CosyVoice runs on {device or 'an unknown device'}, not cuda: a line takes minutes")
        return 1
    f0 = median_f0(pcm, rate)
    if not f0:
        print(f"FAIL: no voiced speech in the {len(pcm)}-byte line")
        return 1
    female = spoken[1:3] == "f_" or spoken in FEMALE_CLIPS
    if female != (f0 > ceiling):
        heard = "over" if f0 > ceiling else "under"
        print(f"FAIL: {spoken or 'the voice'} should be {'fe' if female else ''}male, but its median pitch "
              f"is {f0:.0f} Hz, {heard} the {ceiling:.0f} Hz line")
        return 1
    print(f"{spoken}: {'fe' if female else ''}male voice, median pitch {f0:.0f} Hz, on cuda")
    return 0


if __name__ == "__main__":
    sys.exit(main())
