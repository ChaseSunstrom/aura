#!/usr/bin/env python3
"""Drill: can the coding sandbox reach this host, or the LAN, or the internet?

``aura-core/tests/test_code_sandbox.py`` proves the *argv* — that
``container_argv`` puts ``--network=none`` in the command line and never puts
``--privileged``, ``--network=host`` or a docker socket mount in it. That is the
right place for those assertions and it needs no Docker to run. What it cannot
tell you is whether ``--network=none`` still means what it meant when the test
was written: the flag could be honoured, silently ignored by a newer daemon, or
undone by a site-wide daemon default, and the unit test would pass through all
three.

So this runs the container. It takes the argv from ``container_argv`` itself
rather than assembling its own, or it would be proving a command line nobody
uses.

**The positive control is the point.** "Nothing was reachable" and "the probe
was broken" produce identical output, and only one of them is a security
property. So every destination is probed twice: once under the default
environment (``network=none``) where all of them must fail, and once under
``network=egress`` where the ones that are genuinely up must succeed. A run
where the egress pass reaches nothing either is reported as INCONCLUSIVE, not
as a pass — that is a broken probe, and it is the failure mode this whole file
exists to avoid.

The probe is a raw TCP connect written in Python, with no ``curl``, ``nc`` or
DNS in it. A missing tool and a blocked network look the same to a shell
one-liner, and ``python:3.12-bookworm`` ships no ``curl``.

Run:  python3 scripts/verify/sandbox-isolation.py
Needs: Docker, and the ``python:3.12-bookworm`` image. Skips (exit 0, loudly)
when Docker is absent, so it can live in a suite that runs on a laptop.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aura-core"))

from aura.integrations.code.sandbox import (  # noqa: E402
    NETWORK_EGRESS,
    NETWORK_NONE,
    container_argv,
    environment_from_dict,
)

#: A raw TCP connect, in the container, with a short timeout. Written as one
#: program so the container is started once per pass rather than once per
#: destination — twelve `docker run`s of a 1 GB image is a minute of nothing.
PROBE = r"""
import json, socket, sys
out = {}
for name, host, port in json.loads(sys.argv[1]):
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((host, port))
        out[name] = "reached"
    except Exception as exc:
        out[name] = f"{type(exc).__name__}"
    finally:
        s.close()
print(json.dumps(out))
"""


def _host_addresses() -> list[tuple[str, str, int]]:
    """The destinations that matter, resolved on the host before we start.

    Resolution happens here, not in the container: a sandbox with no network
    has no DNS either, and a probe that failed at the name lookup would be
    reported as "unreachable" without ever having tried to open a socket.
    """
    targets: list[tuple[str, str, int]] = []

    # The host itself, by the address a bridged container reaches it on.
    gateway = _docker_gateway()
    if gateway:
        for port in (22, 8080):
            if _listening(gateway, port):
                targets.append((f"host:{port} via docker gateway", gateway, port))
                break

    # The host by its LAN address, which is a different route to the same box.
    lan = _primary_lan_address()
    if lan:
        for port in (22, 8080):
            if _listening(lan, port):
                targets.append((f"host:{port} on the LAN", lan, port))
                break

    # The LAN's gateway: another machine entirely, and the one that would let a
    # compromised sandbox reach the rest of the house.
    router = _default_route()
    if router:
        targets.append((f"LAN router {router}:80", router, 80))
        targets.append((f"LAN router {router}:53", router, 53))

    # And off the LAN altogether.
    targets.append(("public internet 1.1.1.1:53", "1.1.1.1", 53))
    return targets


def _docker_gateway() -> str:
    out = _run(["docker", "network", "inspect", "bridge", "--format",
                "{{range .IPAM.Config}}{{.Gateway}}{{end}}"])
    return (out or "").strip()


def _primary_lan_address() -> str:
    """This host's own LAN address, found by asking the routing table."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.1.1", 1))  # no packet is sent by a UDP connect
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def _default_route() -> str:
    out = _run(["ip", "route", "show", "default"]) or ""
    parts = out.split()
    return parts[2] if len(parts) > 2 and parts[1] == "via" else ""


def _listening(host: str, port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(2)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _run(argv: list[str]) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _probe(network: str, targets: list[tuple[str, str, int]]) -> dict[str, str]:
    """One container, one pass over every destination."""
    env = environment_from_dict({"name": "isolation-drill", "network": network})
    assert env.network == network, f"the environment refused network={network!r}"
    argv = container_argv(
        env, ROOT,
        f"python3 -c {_shell_quote(PROBE)} {_shell_quote(json.dumps(targets))}",
        writable=False,
    )
    print(f"    argv: {' '.join(argv[:9])} … ({len(argv)} args)")
    print(f"    network flag: {[a for a in argv if a.startswith('--network')] or 'ABSENT'}")
    done = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    if done.returncode != 0:
        raise SystemExit(f"the {network} container did not run: {done.stderr.strip()[:400]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def main() -> int:
    if not shutil.which("docker"):
        print("SKIP: no docker on this machine; the argv assertions in "
              "aura-core/tests/test_code_sandbox.py still ran")
        return 0

    targets = _host_addresses()
    print(f"Destinations ({len(targets)}), resolved on the host:")
    for name, host, port in targets:
        print(f"  - {name:38} -> {host}:{port}")

    print(f"\n[1/2] default environment (network={NETWORK_NONE}) — nothing may be reached")
    sealed = _probe(NETWORK_NONE, targets)
    for name, result in sealed.items():
        print(f"    {name:38} {result}")

    print(f"\n[2/2] opt-in environment (network={NETWORK_EGRESS}) — the control")
    opened = _probe(NETWORK_EGRESS, targets)
    for name, result in opened.items():
        print(f"    {name:38} {result}")

    reached_when_sealed = [n for n, r in sealed.items() if r == "reached"]
    reached_when_open = [n for n, r in opened.items() if r == "reached"]

    print()
    if reached_when_sealed:
        print("FAIL: the sealed sandbox reached " + ", ".join(reached_when_sealed))
        return 1
    if not reached_when_open:
        # Not a pass. The probe reaching nothing in either pass is what a
        # broken probe looks like, and it must never be read as isolation.
        print("INCONCLUSIVE: nothing was reachable even with egress on, so this "
              "run proves the probe works, not that the sandbox is sealed. "
              "Check that the destinations above are actually up.")
        return 2
    print(f"PASS: {len(sealed)} destinations unreachable under --network=none; "
          f"{len(reached_when_open)} of them reachable once egress is asked for, "
          "so the probe was working and the isolation is what stopped it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
