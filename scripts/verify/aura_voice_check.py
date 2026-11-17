#!/usr/bin/env python3
"""Does AURA's voice answer fast? Time `af_heart` against a running jarvis-cosyvoice.

    python3 scripts/verify/aura_voice_check.py [BASE_URL]      (env AURA_VOICE_URL, else
                                                                http://127.0.0.1:8890)

One warm-up request first (it pays Kokoro's lazy load), then one timed request: it
POSTs `/v1/audio/speech` with voice `af_heart`, streams the PCM and prints the time to
the first byte and the total. Exit 0 under 250 ms to the first byte, 1 at or over it
(or on a failed request), 2 when the server lists no `af_heart` (an image without
Kokoro, or a build before it). Stdlib only, so it runs on the host with no venv.

What it does not prove: that the voice sounds right (listen), or the number on a
machine that is busy with something else: the first byte is a GPU figure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

VOICE = "af_heart"
SENTENCE = "Good morning. The house is quiet and everything is where you left it."
BUDGET_MS = 250.0
DEFAULT_URL = "http://127.0.0.1:8890"
TIMEOUT_S = 60


def has_voice(base: str, voice: str = VOICE) -> bool:
    with urllib.request.urlopen(f"{base}/health", timeout=TIMEOUT_S) as answer:
        return voice in json.load(answer).get("voices", [])


def speak(base: str, text: str = SENTENCE) -> tuple[float, float, int]:
    """(ms to the first byte, ms in total, bytes) of one streamed PCM answer."""
    body = json.dumps({"input": text, "voice": VOICE, "response_format": "pcm"}).encode()
    request = urllib.request.Request(
        f"{base}/v1/audio/speech", data=body, headers={"Content-Type": "application/json"})
    began = time.perf_counter()
    first = None
    size = 0
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:
        while chunk := answer.read(4096):
            if first is None:
                first = time.perf_counter()
            size += len(chunk)
    ended = time.perf_counter()
    if first is None:
        raise RuntimeError("the response had no body")
    return (first - began) * 1000, (ended - began) * 1000, size


def main(argv: list[str]) -> int:
    base = (argv[0] if argv else os.environ.get("AURA_VOICE_URL", DEFAULT_URL)).rstrip("/")
    try:
        if not has_voice(base):
            print(f"{base} lists no {VOICE} voice: is Kokoro in this image?", file=sys.stderr)
            return 2
        speak(base)  # warm-up: the load is not the latency being checked
        first_ms, total_ms, size = speak(base)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as err:
        print(f"{base}: {err}", file=sys.stderr)
        return 1
    print(f"first byte {first_ms:.0f} ms, total {total_ms:.0f} ms, {size} bytes ({VOICE})")
    if first_ms >= BUDGET_MS:
        print(f"FAIL: first byte {first_ms:.0f} ms >= {BUDGET_MS:.0f} ms", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
