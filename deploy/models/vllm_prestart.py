#!/usr/bin/python3
"""The `vllm` llama-swap runs: frees the cards, then hands over to the real one (T-0411).

Installed as /usr/local/sbin/vllm, ahead of /usr/local/bin/vllm on PATH, so every model yaml's
`vllm serve …` passes through it without a yaml changing. From 2026-10-27 22:30Z to
2026-10-29 00:07Z each start died ~15 s in at vLLM's free-memory check and nothing freed the
memory, while Docker reported healthy. Before a `serve` this:

1. SIGKILLs every engine group whose API server is gone: a `VLLM::*` process whose process
   group leader no longer exists keeps ~20 GiB a card and serves nobody. A group with a live
   server is never touched.
2. Asks the voice to release its cards (ADR-0039 rule 3); a voice that is down never blocks.
3. Leaves a watcher behind: on /health 200 it clears the failure mark and warms the voice
   again; if vllm exits first it writes the mark, which fails the container's healthcheck so
   health_guard tells the owner, and the manager retries Hermes's model three times on its own
   (manager.Healer, T-0412).

Then it execs the real vllm with the same pid, so llama-swap's stop and wait are unchanged.
Also installed as /usr/local/sbin/llama-server (T-0511): a GPU llama-server start needs the same
free cards. A CPU-only one names /usr/local/bin/llama-server in its yaml and skips this.
Not covered: memory held by something outside this container; a start that fails for another
reason (the mark is still written; the cause is in llama-swap's upstream log). A stop llama-swap
asked for mid-load (a swap, a TTL unload, /unload, the manager refusing a load) writes no mark:
the watcher cannot see the exit status, so it polls llama-swap's /running every second and
skips the mark when it saw a model "stopping" (T-0601). A stop that slips between two polls still
reads as a crash; the next start that answers /health clears it. A failed start leaves the voice released: its next line reloads it lazily.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BIN = "/usr/local/bin"
VOICE = "http://jarvis-cosyvoice:8890"
SWAP = "http://127.0.0.1:8000"  # llama-swap, as the healthcheck reaches it
MARK = Path("/tmp/jarvis-models-start-failed")


def _log(msg: str) -> None:
    print(f"vllm-prestart: {msg}", file=sys.stderr, flush=True)


def orphaned_engine_groups(proc: Path = Path("/proc")) -> set[int]:
    groups: set[int] = set()
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            if not (d / "cmdline").read_bytes().startswith(b"VLLM::"):
                continue
            pgid = int((d / "stat").read_text().rsplit(")", 1)[1].split()[2])
        except (OSError, IndexError, ValueError):
            continue  # exited while we looked
        # pgrp 0 is a leader outside this PID namespace: killpg(0) would hit our own group.
        if pgid > 1 and not (proc / str(pgid)).exists():
            groups.add(pgid)
    return groups


def _post(url: str, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=b"", method="POST"), timeout=timeout):
            return True
    except (OSError, ValueError) as exc:
        _log(f"{url}: {exc}")
        return False


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _port(args: list[str]) -> str:
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--port="):
            return a.split("=", 1)[1]
    return "8000"


def prepare(args: list[str]) -> None:
    for pgid in orphaned_engine_groups():
        _log(f"killing engine group {pgid}: its API server is gone and it still holds the cards")
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
    deadline = time.monotonic() + 15
    while orphaned_engine_groups() and time.monotonic() < deadline:
        time.sleep(0.5)
    _post(f"{VOICE}/admin/release")
    # ponytail: the watcher stays a zombie under vllm until vllm exits; one per start.
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--watch", _port(args), str(os.getpid())],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _swap_stopping() -> bool:
    """Whether llama-swap is stopping a model right now. A stop it asked for (swap, TTL unload,
    /unload, the manager refusing a load) puts the model in state "stopping" until the process is
    gone; a crash never does. Any model counts: only starting models have a watcher, so a
    stopping one is almost surely ours. An unreachable llama-swap is "no", the safe side for a
    crash."""
    req = urllib.request.Request(SWAP + "/running")
    if os.environ.get("LLM_API_KEY"):
        req.add_header("Authorization", "Bearer " + os.environ["LLM_API_KEY"])
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return any(m.get("state") == "stopping" for m in json.loads(r.read()).get("running", []))
    except (OSError, ValueError, AttributeError):
        return False


def watch(port: int, pid: int) -> None:
    health = f"http://127.0.0.1:{port}/health"
    asked = False
    # ponytail: a stop that finishes between two 1 s polls reads as a crash (a false alarm that the
    # next good start clears); the exit status is out of reach for a detached watcher.
    while _alive(pid):
        asked = asked or _swap_stopping()
        try:
            with urllib.request.urlopen(health, timeout=5):
                pass
        except (OSError, ValueError):
            time.sleep(1)
            continue
        MARK.unlink(missing_ok=True)
        _post(f"{VOICE}/admin/warm")
        return
    if asked or _swap_stopping():
        return
    MARK.write_text(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} the model on port {port} "
                    f"(pid {pid}) exited before /health answered; see /logs/stream/upstream\n")


def main(argv: list[str], name: str = "vllm") -> None:
    if argv[:1] == ["--watch"]:
        watch(int(argv[1]), int(argv[2]))
        return
    # llama-server has no subcommand: a start is any run llama-swap gives a port.
    if argv[:1] == ["serve"] or (name == "llama-server" and _port(argv) != "8000"):
        prepare(argv)
    real = f"{BIN}/{name}"
    os.execv(real, [real, *argv])


if __name__ == "__main__":
    main(sys.argv[1:], os.path.basename(sys.argv[0]))
