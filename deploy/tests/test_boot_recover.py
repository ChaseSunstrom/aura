"""deploy/boot-recover.sh brings back what dockerd could not after a reboot.

2026-09-05: the LXC rebooted, dockerd started four seconds before tailscaled had assigned
the tailnet address every port is bound to, and every publishing container was left
Exited(255). A plain `docker compose up -d` then started them without programming the
port forwarding. These tests drive the script with shimmed `docker` and `ip` commands so
the two decisions it makes — wait for the RIGHT address, recreate ONLY what is down — are
pinned without touching a real daemon or a real crontab.

Not covered: the cron line actually firing at boot (that is cron's), and tailscaled
itself (the script gives up after its wait and says so).
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "boot-recover.sh"
DEPLOY = ROOT / "scripts" / "deploy.sh"


def _shims(tmp_path: Path, *, addr_present: bool, defined: list[str], running: list[str],
           down: list[str], runner: str | None = None, search_resolves: bool = True) -> Path:
    """Fake `docker` and `ip` on PATH; every compose call is appended to calls.log."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.log"
    calls.touch()  # the give-up path never reaches docker, and "no calls" must be readable
    # `runner`: None → no such container; "ok" → resolves; "broken" → cannot resolve;
    # "unreadable" → the container is there but its config yields no url.
    runner_present = "exit 0" if runner else "exit 1"
    runner_url = "echo ''" if runner == "unreadable" else "echo gitlab.example.ts.net"
    runner_resolves = "exit 0" if runner == "ok" else "exit 1"
    (bindir / "ip").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # `ip -4 addr show` — the address is either on an interface or it is not.
        {"echo '    inet 100.64.0.51/32 scope global tailscale0'" if addr_present else "true"}
        """), encoding="utf-8")
    (bindir / "docker").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> {calls}
        case "$*" in
          "compose config --services") printf '%s\\n' {" ".join(defined)} ;;
          "compose ps --services --status exited --status created --status dead") printf '%s\\n' {" ".join(down)} ;;
          "compose ps --services") printf '%s\\n' {" ".join(running)} ;;
          compose\\ up\\ *) exit 0 ;;
          compose\\ ps*) echo "svc=Up" ;;
          "inspect gitlab-runner") {runner_present} ;;
          exec\\ gitlab-runner\\ sh\\ *) {runner_url} ;;
          exec\\ gitlab-runner\\ getent\\ *) {runner_resolves} ;;
          "restart gitlab-runner") exit 0 ;;
          exec\\ jarvis-searxng\\ *) {"exit 0" if search_resolves else "exit 1"} ;;
          "restart jarvis-searxng") exit 0 ;;
        esac
        """), encoding="utf-8")
    for f in bindir.iterdir():
        f.chmod(0o755)
    return calls


def _run(tmp_path: Path, env_addr: str | None, wait_s: int = 0) -> subprocess.CompletedProcess:
    home = tmp_path / "stack"
    home.mkdir(exist_ok=True)
    if env_addr is not None:
        (home / ".env").write_text(f"TAILSCALE_IP={env_addr}\nJARVIS_TAG=abc\n", encoding="utf-8")
    env = dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}",
               AURA_REMOTE_DIR=str(home), BOOT_RECOVER_WAIT_S=str(wait_s))
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                          timeout=60)


def test_both_scripts_parse() -> None:
    for s in (SCRIPT, DEPLOY):
        subprocess.run(["bash", "-n", str(s)], check=True)


def test_recreates_only_the_services_that_are_down(tmp_path: Path) -> None:
    calls = _shims(tmp_path, addr_present=True,
                   defined=["jarvis", "jarvis-adapter", "jarvis-web", "searxng"],
                   running=["searxng", "jarvis-extract"],
                   down=["jarvis", "jarvis-adapter", "jarvis-web"])
    r = _run(tmp_path, "100.64.0.51")
    assert r.returncode == 0, r.stderr
    up = [c for c in calls.read_text().splitlines() if c.startswith("compose up")]
    assert up == ["compose up -d --force-recreate jarvis jarvis-adapter jarvis-web"], up
    log = (tmp_path / "stack" / "logs" / "boot-recover.log").read_text()
    assert "100.64.0.51 present" in log and "recreating: jarvis jarvis-adapter jarvis-web" in log


def test_does_nothing_when_everything_runs(tmp_path: Path) -> None:
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis", "searxng"],
                   running=["jarvis", "searxng"], down=[])
    r = _run(tmp_path, "100.64.0.51")
    assert r.returncode == 0
    assert not [c for c in calls.read_text().splitlines() if c.startswith("compose up")]
    assert "nothing to do" in (tmp_path / "stack" / "logs" / "boot-recover.log").read_text()


def test_a_defined_service_with_no_container_at_all_is_recreated(tmp_path: Path) -> None:
    # `docker compose ps` lists only services with a container; one that never got one
    # after a reboot must still be brought up, or the stack comes back three-quarters.
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis", "jarvis-web"],
                   running=["jarvis"], down=[])
    _run(tmp_path, "100.64.0.51")
    assert "compose up -d --force-recreate jarvis-web" in calls.read_text()


def test_gives_up_loudly_when_the_address_never_comes(tmp_path: Path) -> None:
    calls = _shims(tmp_path, addr_present=False, defined=["jarvis"], running=[], down=["jarvis"])
    r = _run(tmp_path, "100.64.0.51", wait_s=0)
    assert r.returncode == 2
    assert "gave up" in (tmp_path / "stack" / "logs" / "boot-recover.log").read_text()
    # Never a compose up against a stack whose ports cannot bind: that is the failure
    # this script exists to avoid repeating.
    assert "compose up" not in calls.read_text()


def test_waits_for_the_address_the_env_names_not_any_address(tmp_path: Path) -> None:
    # tailscale0 carries 100.64.0.51; .env names a different address; must not proceed.
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis"], running=[], down=["jarvis"])
    r = _run(tmp_path, "100.64.0.53", wait_s=0)
    assert r.returncode == 2 and "compose up" not in calls.read_text()


def test_deploy_installs_exactly_one_reboot_line() -> None:
    """The deploy step replaces its own crontab line rather than appending: `grep -v` the
    marker out of the existing crontab, add one line, write it back."""
    text = DEPLOY.read_text(encoding="utf-8")
    step = next(line for line in text.splitlines() if "jarvis-boot-recover" in line and "crontab -" in line)
    assert "grep -v 'jarvis-boot-recover'" in step, "old copies must be dropped before the new line"
    assert "@reboot" in step and "boot-recover.sh" in step
    assert "| crontab -" in step


def test_a_runner_that_cannot_resolve_gitlab_is_restarted(tmp_path: Path) -> None:
    """The reboot race one layer along, and the one nothing reports.

    Docker copies the host resolver into a container when it starts, so a runner
    that came up before tailscaled inherits the LAN nameserver and can never
    reach the GitLab host on the tailnet. Jobs tagged `jarvis` then queue for
    ever while untagged jobs run, so the pipeline only looks slow.
    """
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis"], running=["jarvis"],
                   down=[], runner="broken")
    r = _run(tmp_path, "100.64.0.51")
    assert r.returncode == 0
    assert "restart gitlab-runner" in calls.read_text()
    log = (tmp_path / "stack" / "logs" / "boot-recover.log").read_text()
    assert "cannot resolve gitlab.example.ts.net" in log and "restarted" in log


def test_a_healthy_runner_is_left_alone(tmp_path: Path) -> None:
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis"], running=["jarvis"],
                   down=[], runner="ok")
    _run(tmp_path, "100.64.0.51")
    assert "restart gitlab-runner" not in calls.read_text()


def test_no_runner_container_is_not_an_error(tmp_path: Path) -> None:
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis"], running=["jarvis"],
                   down=[], runner=None)
    r = _run(tmp_path, "100.64.0.51")
    assert r.returncode == 0 and "restart gitlab-runner" not in calls.read_text()


def test_a_runner_whose_url_cannot_be_read_is_left_alone(tmp_path: Path) -> None:
    """Restarting a container on a guess is worse than leaving it alone."""
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis"], running=["jarvis"],
                   down=[], runner="unreadable")
    _run(tmp_path, "100.64.0.51")
    assert "restart gitlab-runner" not in calls.read_text()
    assert "could not be read" in (tmp_path / "stack" / "logs" / "boot-recover.log").read_text()


def test_a_search_container_that_cannot_resolve_is_restarted(tmp_path: Path) -> None:
    """2026-10-07: SearXNG came up after a reboot unable to resolve anything, and web
    search returned nothing for a day while every container reported healthy."""
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis"], running=["jarvis"],
                   down=[], search_resolves=False)
    r = _run(tmp_path, "100.64.0.51")
    assert r.returncode == 0 and "restart jarvis-searxng" in calls.read_text()
    assert "cannot resolve public names" in (tmp_path / "stack" / "logs" / "boot-recover.log").read_text()


def test_a_search_container_that_resolves_is_left_alone(tmp_path: Path) -> None:
    calls = _shims(tmp_path, addr_present=True, defined=["jarvis"], running=["jarvis"], down=[])
    _run(tmp_path, "100.64.0.51")
    assert "restart jarvis-searxng" not in calls.read_text()
