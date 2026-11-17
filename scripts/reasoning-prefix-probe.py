"""T-0814 (bench/results/reasoning-effort.md): does a reasoning level that adds a template instruction split the prefix cache?

The same long prompt (a ~12k-token system message, then a short question) straight to the
model server, streamed, timing the first chunk: medium twice (warm), low twice, none twice.
A fresh random tag at the top of the system text makes the first medium cold too, so every
level starts cold and only the template's own change differs. Prints neither the key nor the
address.
"""
import json
import random
import subprocess
import time
import urllib.request
from pathlib import Path

env = dict(l.split("=", 1) for l in Path("/opt/jarvis-host/.env").read_text().splitlines() if "=" in l and not l.startswith("#"))
key = env.get("LLM_API_KEY", "").strip()
ports = subprocess.run(["docker", "port", "jarvis-models"], capture_output=True, text=True).stdout.splitlines()
base = "http://" + next((l.split(" -> ")[1] for l in ports if " -> " in l), "127.0.0.1:8080")

tag = f"run {random.randrange(10**9)}"
system = tag + "\n" + "\n".join(f"Rule {i}: the house keeps note {i} about room {i % 17} and device {i % 29}." for i in range(1200))
q = "In one short sentence, what is a kilowatt hour?"
tools = [{"type": "function", "function": {"name": "noop", "description": "does nothing", "parameters": {"type": "object", "properties": {}}}}]


def first_token(level):
    body = {"model": "qwen3.8-27b", "stream": True, "max_tokens": 600, "tools": tools,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": q}],
            "stream_options": {"include_usage": True}}
    if level:
        body["reasoning_effort"] = level
    req = urllib.request.Request(f"{base}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "content-type": "application/json"})
    t0 = time.monotonic()
    first = None
    prompt_tokens = None
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if chunk.get("usage"):
                prompt_tokens = chunk["usage"].get("prompt_tokens")
            for c in chunk.get("choices") or []:
                d = c.get("delta") or {}
                if first is None and (d.get("content") or d.get("reasoning_content") or d.get("reasoning")):
                    first = time.monotonic() - t0
    return first, prompt_tokens


for level in ["medium", "medium", "low", "low", "none", "none", "medium"]:
    ft, pt = first_token(level)
    print(json.dumps({"level": level, "first_token_s": round(ft, 2) if ft else None, "prompt_tokens": pt}), flush=True)
