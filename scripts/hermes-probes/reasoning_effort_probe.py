"""Does a session's reasoning level reach the model? Reasoning size per turn at two levels (T-0723).

    python scripts/hermes-probes/reasoning_effort_probe.py --base http://<host>:8643 --key $AURA_API_KEY \
        [--n 12] [--levels low,medium] [--out research/reasoning-effort.jsonl]

Per turn: a fresh session over the adapter's /ws, ``config.set reasoning <level>`` on that session
(session-scoped, the call the adapter makes for a spoken turn), then one typed ``prompt.submit``.
It records the reasoning the HUD streamed (``reasoning.delta``, or ``reasoning.available`` when
nothing streamed), the reply, the time to the first reply token and to message.complete. Arms are
interleaved (low, medium, low, ...) over a fixed list of questions, so drift in the host's load or
the model's cache falls on both alike. Prints one JSON line per turn and a summary line per level.
An approval.request is denied: these questions need no tool. Speaks nothing.

Does not cover: tokens as vLLM counts them (characters are what the socket shows; the summary also
gives characters / 4 as a rough token figure), or a spoken turn's own path (the adapter's voice flag
is what sets the level there; this sets it directly, which is the same Hermes call).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _probe import adapter_ws_url, connect  # noqa: E402

#: Short questions that invite a little working: the level has room to show here.
QUESTIONS = (
    "In one short sentence, what is a kilowatt hour?",
    "A train leaves at 3:40 pm and the trip takes 2 hours 35 minutes. When does it arrive? One sentence.",
    "Which is larger, 2 to the power 10 or 10 cubed, and by how much? One sentence.",
)


async def turn(base: str, key: str, level: str, text: str, timeout: float) -> dict:
    row: dict = {"level": level, "question": text}
    reasoning, reply = "", ""
    async with connect(adapter_ws_url(base, key)) as rpc:
        sid = await rpc.create_session()
        row["session_id"] = sid
        r = await rpc.call("config.set", {"session_id": sid, "key": "reasoning", "value": level})
        row["config_set"] = r.get("result", r.get("error"))
        t0 = time.monotonic()
        await rpc.call("prompt.submit", {"session_id": sid, "text": text, "surface": {"kind": "console"}})
        async for p in rpc.events(timeout):
            t, pl = p.get("type"), p.get("payload", {})
            if t == "approval.request":
                await rpc.call("approval.respond", {"session_id": sid, "request_id": pl.get("request_id"), "choice": "deny"})
            elif t == "reasoning.delta":
                reasoning += pl.get("text") or ""
                row.setdefault("first_reasoning_ms", round((time.monotonic() - t0) * 1000))
            elif t == "reasoning.available" and not reasoning:
                reasoning = pl.get("text") or ""
            elif t == "message.delta":
                reply += pl.get("text") or ""
                row.setdefault("first_token_ms", round((time.monotonic() - t0) * 1000))
            elif t == "message.complete":
                reply = pl.get("text") or reply
                reasoning = reasoning or (pl.get("reasoning") or "")
                row["complete_ms"] = round((time.monotonic() - t0) * 1000)
                break
    # A turn that did not think still gets a `reasoning.available`: Hermes relays the reply's
    # own first 500 characters under that name (agent/turn_response_intake.py,
    # `_relay_thinking`). Counted, a `none` turn read as reasoning exactly as long as its reply
    # (T-0814). Nothing streamed and the text is the reply's: that is not reasoning.
    if "first_reasoning_ms" not in row and reasoning.strip() == reply.strip()[:500].strip():
        reasoning = ""
    row["reasoning_chars"] = len(reasoning)
    row["reply_chars"] = len(reply)
    row["reply"] = reply[:160]
    return row


def summary(rows: list[dict], level: str) -> dict:
    mine = [r for r in rows if r["level"] == level and "complete_ms" in r]
    chars = [r["reasoning_chars"] for r in mine]
    first = [r["first_token_ms"] for r in mine if "first_token_ms" in r]
    return {
        "level": level, "n": len(mine),
        "reasoning_chars_median": statistics.median(chars) if chars else None,
        "reasoning_chars_mean": round(statistics.fmean(chars)) if chars else None,
        "reasoning_chars_range": [min(chars), max(chars)] if chars else None,
        "approx_tokens_median": round(statistics.median(chars) / 4) if chars else None,
        "first_token_ms_median": statistics.median(first) if first else None,
        "complete_ms_median": statistics.median([r["complete_ms"] for r in mine]) if mine else None,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", default=os.environ.get("AURA_API_KEY", ""))
    ap.add_argument("--n", type=int, default=12, help="turns per level")
    ap.add_argument("--levels", default="low,medium")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    levels = a.levels.split(",")
    rows: list[dict] = []
    for i in range(a.n):
        for level in levels if i % 2 == 0 else levels[::-1]:
            row = await turn(a.base, a.key, level, QUESTIONS[i % len(QUESTIONS)], a.timeout)
            rows.append(row)
            print(json.dumps(row), flush=True)
            if a.out:
                with a.out.open("a") as f:
                    f.write(json.dumps(row) + "\n")
    for level in levels:
        print(json.dumps({"summary": summary(rows, level)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
