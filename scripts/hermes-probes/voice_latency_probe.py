"""p50/p95 per stage of the transcribe path, from n real requests (T-0605).

    python scripts/hermes-probes/voice_latency_probe.py --base http://<host>:8643 \
        --core http://127.0.0.1:8080 --wav clip.wav [--n 20] [--key $AURA_API_KEY] [--core-token $AURA_TOKEN]

Read-only: each request is a *private* transcribe (no clip held for a claim, no line logged,
nothing sent to Hermes' disk) and --core adds direct calls to the speaker gate's verify, which
only embeds and compares. Nothing is approved, nothing is spoken, no prompt is submitted.
Stages are the adapter's Server-Timing (receive, decode, gate, gate_wait, transcribe) plus
`client` (wall clock here, so the network and the body's upload) and `core_verify` (the gate
on its own). Prints one JSON line: {"n", "stages": {name: {"p50", "p95"}}}.

Does not measure the model's first sentence or the speech after it: that is car_turn_probe.py.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _probe import post  # noqa: E402


def pct(xs: list[float], p: float) -> float | None:
    """Nearest rank, as /api/status does, so a probe and the status agree."""
    xs = sorted(xs)
    return round(xs[max(0, math.ceil(p * len(xs)) - 1)], 1) if xs else None


def summarize(samples: dict[str, list[float]]) -> dict[str, dict[str, float | None]]:
    return {k: {"p50": pct(v, 0.5), "p95": pct(v, 0.95)} for k, v in samples.items()}


def parse_timing(header: str) -> dict[str, float]:
    """`receive;dur=1.2, decode;dur=900.0` -> {"receive": 1.2, "decode": 900.0}."""
    out: dict[str, float] = {}
    for part in header.split(","):
        name, _, dur = part.strip().partition(";dur=")
        if name and dur:
            out[name] = float(dur)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--core", default="")
    ap.add_argument("--wav", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--key", default=os.environ.get("AURA_API_KEY", ""))
    ap.add_argument("--core-token", default=os.environ.get("AURA_TOKEN", ""))
    a = ap.parse_args()
    if not 1 <= a.n <= 200:
        sys.exit("--n is 1..200")
    wav = open(a.wav, "rb").read()
    body = json.dumps({"data_url": "data:audio/wav;base64," + base64.b64encode(wav).decode(),
                       "mime_type": "audio/wav", "surface": "console", "private": True}).encode()
    samples: dict[str, list[float]] = {}
    for _ in range(a.n):
        t0 = time.monotonic()
        with post(a.base.rstrip("/") + "/api/audio/transcribe", body,
                   {"Authorization": f"Bearer {a.key}", "Content-Type": "application/json"}) as r:
            r.read()
            timing = parse_timing(r.headers.get("Server-Timing", ""))
        samples.setdefault("client", []).append((time.monotonic() - t0) * 1000)
        for k, v in timing.items():
            samples.setdefault(k, []).append(v)
        if a.core:
            t0 = time.monotonic()
            with post(a.core.rstrip("/") + "/api/voice/speaker/verify", wav,
                       {"Authorization": f"Bearer {a.core_token}", "Content-Type": "audio/wav"}) as r:
                r.read()
            samples.setdefault("core_verify", []).append((time.monotonic() - t0) * 1000)
    print(json.dumps({"n": a.n, "stages": summarize(samples)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
