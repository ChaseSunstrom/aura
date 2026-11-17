"""scripts/deploy.sh stops before it changes anything when the bind address is missing.

Every published port binds TAILSCALE_IP (the LAN address since 10-27). boot-recover.sh waits
for that address at reboot, but a plain deploy recreated the containers without looking, so a
deploy while the address was down stopped the old stack and could not start the new one
("cannot assign requested address", the 2026-09-05 outage), and the rollback hit the same wall.

Not covered: running the check on the host (needs ssh and the host); an address that is
present but unroutable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"


def _preflight_line() -> str:
    return next(line for line in DEPLOY.read_text(encoding="utf-8").splitlines()
                if "bind_address_present" in line and line.lstrip().startswith("remote"))


def test_the_preflight_runs_before_the_deploy_changes_env_or_containers() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    check = text.index(_preflight_line())
    assert check < text.index('log "2. record the running tag"')
    assert check < text.index("sed -i -E 's/^(AURA|JARVIS)_TAG=")
    assert check < text.index("$COMPOSE pull")


def _run_check(tmp_path: Path, env_line: str, addr_out: str) -> int:
    """Run the shell function deploy.sh sends to the host, with `ip` faked."""
    text = DEPLOY.read_text(encoding="utf-8")
    start = text.index("bind_address_present() {")
    body = text[start:text.index("\n}\n", start) + 3]
    (tmp_path / ".env").write_text(env_line + "\n", encoding="utf-8")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ip").write_text(f"#!/bin/sh\nprintf '%s\\n' '{addr_out}'\n", encoding="utf-8")
    (fake / "ip").chmod(0o755)
    (fake / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake / "sleep").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    return subprocess.run(["bash", "-c", f"{body}\nbind_address_present"], cwd=tmp_path,
                          env=env, capture_output=True, timeout=30).returncode


def test_a_missing_address_fails_and_a_present_one_passes(tmp_path: Path) -> None:
    present = "    inet 10.20.30.59/24 brd 10.20.30.255 scope global eth0"
    for d in "abcd":
        (tmp_path / d).mkdir()
    assert _run_check(tmp_path / "a", "TAILSCALE_IP=10.20.30.59", present) == 0
    assert _run_check(tmp_path / "b", "TAILSCALE_IP=10.20.30.59", "    inet 10.0.0.2/24") != 0
    # A prefix of the right address is not the address.
    assert _run_check(tmp_path / "c", 'TAILSCALE_IP="10.20.30.5"', present) != 0
    # No address configured: compose binds nothing specific, so there is nothing to wait for.
    assert _run_check(tmp_path / "d", "OTHER=1", "") == 0
