#!/usr/bin/env python3
"""Ask one model on the house's LLM server for a tool call, and check that one comes back.

The AI container's models were judged by one thing that matters to Jarvis: given a tool, does
the model call it, through the parser vLLM was started with (qwen3_xml)? A server that answers
/v1/models, or answers in prose, is not enough: Hermes would then talk instead of act. This
sends a single weather question with a get_weather tool and passes when the reply carries a
tool call to it with a city argument. A model not yet loaded is loaded by llama-swap first, so
the first call may take minutes (the timeout allows ten).

    python3 scripts/verify/models-tool-call.py qwen3.8-27b [--url http://127.0.0.1:9000/v1]

The key is LLM_API_KEY from the environment, else from AURA_ENV_FILE (default /opt/jarvis-host/.env).

Not covered: answer quality, or whether Hermes' own prompt gets the same behaviour; the
deploy smoke runs real Hermes turns.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    },
}


def _key_from(env_file: str) -> str:
    """LLM_API_KEY= from a dotenv file, or "" when it cannot be read."""
    try:
        for line in open(env_file, encoding="utf-8"):
            if line.startswith("LLM_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("model")
    ap.add_argument("--url", default="http://127.0.0.1:9000/v1")
    args = ap.parse_args()
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": "What's the weather in Paris right now? Use the tool."}],
        "tools": [TOOL],
        "max_tokens": 1024,
    }
    headers = {"Content-Type": "application/json"}
    # llama-swap answers only the inference key since T-0284: the environment's, else the host's
    # .env (where the deploy keeps it), so a check run from a bare shell is not a false 401.
    key = os.environ.get("LLM_API_KEY") or _key_from(os.environ.get("AURA_ENV_FILE", "/opt/jarvis-host/.env"))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{args.url}/chat/completions", data=json.dumps(body).encode(), headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            reply = json.load(r)
    except Exception as exc:  # noqa: BLE001 - any failure is the answer
        print(f"FAIL {args.model}: {type(exc).__name__}: {exc}")
        return 1
    took = time.monotonic() - started
    msg = reply["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    hit = [c for c in calls if c["function"]["name"] == "get_weather"]
    if not hit:
        print(f"FAIL {args.model} ({took:.0f} s): no get_weather call; content={str(msg.get('content'))[:200]!r}")
        return 1
    city = json.loads(hit[0]["function"]["arguments"] or "{}").get("city", "")
    ok = "paris" in city.lower()
    print(f"{'ok  ' if ok else 'FAIL'} {args.model} ({took:.0f} s): get_weather(city={city!r})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
