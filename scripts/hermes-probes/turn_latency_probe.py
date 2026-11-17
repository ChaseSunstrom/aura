"""End of input -> the reply's first sentence -> its first PCM byte, p50/p95 per stage (T-0605).

    python scripts/hermes-probes/turn_latency_probe.py --base http://<host>:8643 --key $AURA_API_KEY \
        [--text "In one short sentence, what is a kilowatt hour?"] [--n 10] [--no-first-clause] [--typed]

What the owner waits for after he stops talking, minus the microphone: `voice_latency_probe.py`
covers transcription; this starts where it ends. Per turn it submits the text over the adapter's
/ws (an approval.request is DENIED: a probe that needs a tool is the wrong question), then
  first_token_ms     submit -> the first message.delta (the model's time to first token)
  first_sentence_ms  submit -> the first sentence is complete (the text the apps speak first;
                     the same cut as the console's takeSentences, 20 characters at least)
  first_pcm_ms       that sentence's speak-pcm request -> its first PCM byte
  to_first_audio_ms  submit -> the first PCM byte: the sum, the number the 1.2 s target is about
Each turn says it is spoken (voice: true, as the apps' mic, phone and car do, T-0644), so the
adapter runs it at VOICE_REASONING_EFFORT; --typed sends it as a typed turn for the comparison.
Prints one JSON line {"n", "first_clause", "voice", "stages": {name: {"p50", "p95"}}}. Run it twice,
with and without --no-first-clause, to measure the first-clause cut on its own. Speaks nothing aloud:
the PCM is read and dropped. Does not cover the speaker gate or transcription, or a turn that
runs tools (it denies them), and the model's own variance: use n >= 10.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _probe import adapter_ws_url, connect, post  # noqa: E402

_END = re.compile(r"[.!?]+(?=\s|$)|\n+")


def first_sentence(text: str, min_chars: int = 20) -> str | None:
    """The first complete sentence of streamed `text`, or None while it is still arriving.

    The cut `jarvis-web/src/lib/hermes/speech.ts` `takeSentences` makes: a sentence end that is
    followed by space or the end, at least `min_chars` long. A trailing '.' with nothing after it
    yet counts, as the console counts it (the next delta may continue a decimal; so does its).
    """
    for m in _END.finditer(text):
        candidate = text[: m.end()].strip()
        if len(candidate) >= min_chars:
            return candidate
    return None


def pct(xs: list[float], p: float) -> float | None:
    xs = sorted(xs)
    return round(xs[max(0, math.ceil(p * len(xs)) - 1)], 1) if xs else None


def summarize(samples: dict[str, list[float]]) -> dict[str, dict[str, float | None]]:
    return {k: {"p50": pct(v, 0.5), "p95": pct(v, 0.95)} for k, v in samples.items()}


def first_pcm_ms(base: str, key: str, sentence: str, first_clause: bool) -> float:
    body = json.dumps({"text": sentence, "private": True, **({"first_clause": True} if first_clause else {})}).encode()
    t0 = time.monotonic()
    with post(base.rstrip("/") + "/api/audio/speak-pcm", body,
              {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}) as r:
        r.read(2)  # the first bytes; the rest is read and dropped below
        first = (time.monotonic() - t0) * 1000
        r.read()
    return first


async def turn(base: str, key: str, text: str, first_clause: bool, timeout: float,
               voice: bool = True) -> dict[str, float] | None:
    out: dict[str, float] = {}
    reply = ""
    async with connect(adapter_ws_url(base, key)) as rpc:
        sid = await rpc.create_session()
        t0 = time.monotonic()
        await rpc.call("prompt.submit", {"session_id": sid, "text": text, "surface": {"kind": "console"},
                                         **({"voice": True} if voice else {})})
        async for p in rpc.events(timeout):
            t, pl = p.get("type"), p.get("payload", {})
            if t == "approval.request":
                await rpc.call("approval.respond", {"session_id": sid, "request_id": pl.get("request_id"), "choice": "deny"})
            elif t in ("message.delta", "message.complete"):
                reply = (pl.get("text") or "") if t == "message.complete" else reply + (pl.get("text") or "")
                if reply and "first_token_ms" not in out:
                    out["first_token_ms"] = (time.monotonic() - t0) * 1000
                sentence = first_sentence(reply) or (reply.strip() if t == "message.complete" else None)
                if sentence:
                    out["first_sentence_ms"] = (time.monotonic() - t0) * 1000
                    break
        else:
            return None
    if "first_sentence_ms" not in out:
        return None
    out["first_pcm_ms"] = await asyncio.to_thread(first_pcm_ms, base, key, sentence, first_clause)
    out["to_first_audio_ms"] = out["first_sentence_ms"] + out["first_pcm_ms"]
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", default=os.environ.get("AURA_API_KEY", ""))
    ap.add_argument("--text", default="In one short sentence, what is a kilowatt hour?")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--no-first-clause", action="store_true")
    ap.add_argument("--typed", action="store_true", help="submit as a typed turn (no voice flag)")
    a = ap.parse_args()
    if not 1 <= a.n <= 100:
        sys.exit("--n is 1..100")
    samples: dict[str, list[float]] = {}
    for _ in range(a.n):
        got = await turn(a.base, a.key, a.text, not a.no_first_clause, a.timeout, not a.typed)
        for k, v in (got or {}).items():
            samples.setdefault(k, []).append(v)
    print(json.dumps({"n": a.n, "first_clause": not a.no_first_clause, "voice": not a.typed,
                      "stages": summarize(samples)}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
