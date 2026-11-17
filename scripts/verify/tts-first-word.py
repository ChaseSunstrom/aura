#!/usr/bin/env python3
"""Time to the first word on the voice shim: streamed against whole-line (ADR-0039 addendum).

Three lines of growing length, each asked for as a whole MP3 and as streamed
PCM, straight from the shim (`COSYVOICE_URL`, default the deployed one on
loopback). Prints first-byte and whole-line seconds for both, and the audio's
own length for the streamed one so the real-time factor can be read off.

    python3 scripts/verify/tts-first-word.py [--voice jarvis] [--url http://127.0.0.1:8890]
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

LINES = [
    "Ready.",
    "Bitcoin is down about two percent over the last day, sitting near seventy-seven thousand dollars.",
    "The freshest headlines: Bitcoin falls on a PPI overshoot, ETFs shed one hundred and sixty-seven "
    "million, and sell-side risk sits at rare lows. The throughline is a short-term pullback driven by "
    "the bond-yield spike rather than any crypto headline.",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8890")
    parser.add_argument("--voice", default="jarvis")
    args = parser.parse_args()
    for text in LINES:
        for fmt in ("mp3", "pcm"):
            began = time.monotonic()
            first = None
            total = 0
            body = {"model": "kokoro", "input": text, "voice": args.voice, "response_format": fmt}
            try:
                with httpx.stream("POST", f"{args.url}/v1/audio/speech", json=body, timeout=120) as r:
                    if r.status_code != 200:
                        print(f"{fmt:3} chars={len(text):3} the voice answered {r.status_code}: {r.read()[:120]!r}")
                        return 1
                    for chunk in r.iter_bytes():
                        if first is None:
                            first = time.monotonic() - began
                        total += len(chunk)
            except httpx.HTTPError as exc:
                print(f"the voice is unreachable at {args.url}: {exc.__class__.__name__}")
                return 1
            whole = time.monotonic() - began
            audio = f" audio={total / 2 / 24000:4.1f}s" if fmt == "pcm" else ""
            print(f"{fmt:3} chars={len(text):3} first_byte={first or 0:5.2f}s whole={whole:5.2f}s{audio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
