#!/usr/bin/env python3
"""Each model service starts and answers with no network at all (T-0353).

JARVIS's premise is that nothing leaves the house without a setting that says so. On
2026-10-09 vLLM turned out to have been reporting usage to stats.vllm.ai and TEI asking the
Hugging Face hub about its model on every start (T-0343). This is the tripwire for the next
one: for each model service it reads the running container (`docker inspect`), starts a
throwaway copy of the same image with the same command, environment and model mounts,
read-only, under `--network none`, and asks it the question its clients ask (TEI's /health,
a Wyoming describe) from inside. A service that needs anything from outside to start or to
answer fails here.

What it does not cover: the GPU services (NOT_COVERED), because a second copy would
contend with production for the cards; what a service sends while serving real requests;
a service that is not running on this host (reported, not failed).

    python3 deploy/offline_selftest.py        # or: make offline-selftest
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

#: container -> how its clients ask it whether it is up.
SERVICES: dict[str, str] = {
    "jarvis-embeddings": "http",
    "jarvis-reranker": "http",
    "wyoming-whisper": "wyoming",
    "wyoming-piper": "wyoming",
    "wyoming-openwakeword": "wyoming",
}
NOT_COVERED: dict[str, str] = {
    "jarvis-models": "vLLM on both GPUs; its opt-outs are pinned in deploy/tests (T-0343)",
    "jarvis-cosyvoice": "on the TTS GPU beside production",
}
READY_S = 240

_WYOMING_DESCRIBE = """
import json, socket, sys
s = socket.create_connection(("127.0.0.1", {port}), timeout=5)
s.sendall(json.dumps({{"type": "describe"}}).encode() + b"\\n")
line = s.makefile("rb").readline()
sys.exit(0 if json.loads(line).get("type") == "info" else 1)
"""


def port_of(cmd: list[str]) -> int | None:
    """The port a service's command line serves on: `--port N` or `--uri tcp://host:N`."""
    for i, arg in enumerate(cmd[:-1]):
        if arg == "--port":
            return int(cmd[i + 1])
        if arg == "--uri" and cmd[i + 1].startswith("tcp://"):
            return int(cmd[i + 1].rsplit(":", 1)[1])
    return None


def run_argv(inspect: dict[str, Any], name: str) -> list[str]:
    """`docker run` for a sealed copy: same image, command and env; mounts read-only; no network."""
    cfg = inspect["Config"]
    # No --rm: an exited copy must keep its logs until check() has read why (it removes it).
    argv = ["docker", "run", "-d", "--name", name, "--network", "none"]
    for m in inspect.get("Mounts") or []:
        source = m["Name"] if m.get("Type") == "volume" else m["Source"]
        argv += ["-v", f"{source}:{m['Destination']}:ro"]
    for env in cfg.get("Env") or []:
        argv += ["-e", env]
    entry = list(cfg.get("Entrypoint") or [])
    if entry:
        argv += ["--entrypoint", entry[0]]
    return argv + [cfg["Image"], *entry[1:], *(cfg.get("Cmd") or [])]


def probe_argv(kind: str, port: int, name: str) -> list[str]:
    if kind == "http":
        return ["docker", "exec", name, "curl", "-fsS", "-m", "3", f"http://127.0.0.1:{port}/health"]
    return ["docker", "exec", name, "python3", "-c", _WYOMING_DESCRIBE.format(port=port)]


def check(service: str, kind: str) -> tuple[bool, str]:
    got = subprocess.run(["docker", "inspect", service], capture_output=True, text=True)
    if got.returncode != 0:
        return True, "not running on this host"
    inspect = json.loads(got.stdout)[0]
    port = port_of(inspect["Config"].get("Cmd") or [])
    if port is None:
        return False, "no port in its command"
    name = f"offline-{service}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    started = subprocess.run(run_argv(inspect, name), capture_output=True, text=True)
    if started.returncode != 0:
        return False, f"would not start: {started.stderr.strip()[:200]}"
    t0 = time.time()
    try:
        while time.time() - t0 < READY_S:
            if subprocess.run(probe_argv(kind, port, name), capture_output=True).returncode == 0:
                return True, f"answered offline in {time.time() - t0:.0f}s"
            state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name],
                                   capture_output=True, text=True).stdout.strip()
            if state != "true":
                logs = subprocess.run(["docker", "logs", "--tail", "3", name], capture_output=True, text=True)
                return False, f"exited offline: {(logs.stdout + logs.stderr).strip()[-300:]}"
            time.sleep(3)
        return False, f"no answer within {READY_S}s offline"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def main() -> int:
    failed = 0
    for service, kind in SERVICES.items():
        ok, why = check(service, kind)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {service}: {why}", flush=True)
    for service, why in NOT_COVERED.items():
        print(f"SKIP {service}: {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
