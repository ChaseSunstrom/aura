"""The bubblewrap terminal backend reaches the deployed container whole (ADR-0045).

Five links, each of which fails on its own and would fail silently: the binary in
the image, the seccomp profile on the container, the profile allowing what bwrap
needs and nothing it does not, the template naming the backend, and the entrypoint
carrying the three leaves onto a volume rendered when the answer was ``local``.
Each is pinned here the way the media tools and the vision leaves are — against the
file, not a copy of it.

Not covered: that bwrap contains anything. That is
``scripts/verify/sandbox-drill.py`` (``scripts/egress-audit.sh --live`` §7) against
the deployed container, with its own positive control.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "Dockerfile"
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
SECCOMP = ROOT / "deploy" / "seccomp" / "jarvis.json"
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
TEMPLATE = ROOT / "deploy" / "config.template.yaml"

#: What bubblewrap calls from an unprivileged uid and Docker's default profile refuses
#: (probed 2026-09-18 on Docker 29.8: ``unshare -Ur true`` → "Operation not permitted").
BWRAP_NEEDS = {"clone", "clone3", "unshare", "setns", "mount", "umount2", "pivot_root"}
#: What must stay refused: each one Docker refuses by default and the kernel would
#: honour from an unprivileged container if seccomp let it through.
STILL_REFUSED = {"reboot", "kexec_load", "init_module", "finit_module", "syslog", "sethostname",
                 "setdomainname", "swapon", "swapoff", "ptrace"}


def _apt_line() -> str:
    lines = [line for line in DOCKERFILE.read_text(encoding="utf-8").split("\n") if "imagemagick" in line]
    assert len(lines) == 1, lines
    return lines[0]


def test_the_image_installs_bubblewrap_and_proves_it_runs() -> None:
    """Pinned on the media apt line, with a version print: a missing binary is a build
    failure, not the first shell command of a deployed gateway."""
    assert " bubblewrap" in _apt_line(), _apt_line()
    assert "bwrap --version" in DOCKERFILE.read_text(encoding="utf-8")


def test_the_gateway_container_runs_under_the_derived_seccomp_profile_and_never_unconfined() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    jarvis = compose["services"]["jarvis"]
    assert jarvis["security_opt"] == ["seccomp=./seccomp/jarvis.json"], jarvis.get("security_opt")
    for service in compose["services"].values():
        assert not any("unconfined" in opt for opt in service.get("security_opt", [])), service
    # The path is relative to the compose file, which scripts/deploy.sh copies whole.
    assert SECCOMP.is_file()
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert 'rsync -az --delete' in deploy and '"$HERE/deploy/"' in deploy


def test_the_profile_allows_what_bwrap_needs_and_only_that_beyond_dockers_default() -> None:
    profile = json.loads(SECCOMP.read_text(encoding="utf-8"))
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO", "the default must stay deny"
    unconditional = set()
    for rule in profile["syscalls"]:
        if rule["action"] == "SCMP_ACT_ALLOW" and not rule.get("includes") and not rule.get("args"):
            unconditional |= set(rule["names"])
    assert BWRAP_NEEDS <= unconditional, BWRAP_NEEDS - unconditional
    assert not (STILL_REFUSED & unconditional), STILL_REFUSED & unconditional
    # The rest of the CAP_SYS_ADMIN group stays gated: the profile opened seven calls, not the group.
    for rule in profile["syscalls"]:
        if rule.get("includes", {}).get("caps") == ["CAP_SYS_ADMIN"]:
            assert "bpf" in rule["names"] and "keyctl" not in rule["names"]
            assert not (BWRAP_NEEDS & set(rule["names"])), "a call is both gated and open"
            break
    else:
        raise AssertionError("the CAP_SYS_ADMIN group of Docker's default profile is gone")


def test_the_template_names_the_backend_the_plugin_and_the_reset() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert '  backend: "${AURA_TERMINAL_BACKEND}"' in text, "env-owned, quoted (an unset var renders empty)"
    assert "  container_persistent: false" in text and '  sandbox_dir: "/opt/data/sandboxes"' in text
    assert "    - jarvis-bwrap" in text
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "AURA_TERMINAL_BACKEND: ${AURA_TERMINAL_BACKEND:-${JARVIS_TERMINAL_BACKEND:-jarvis-bwrap}}" in compose, (
        "the compose file carries the default: the renderer writes an unset variable as \"\"")


def test_the_entrypoint_pins_the_backend_and_adds_the_two_leaves_to_an_old_volume() -> None:
    """``=terminal.backend``: pinned, because every deployed volume already has
    ``backend: local`` and an unpinned leaf never replaces one that is present. The
    other two are new leaves inside a block every volume has."""
    leaves = ENTRYPOINT.read_text(encoding="utf-8").split('CONFIG_LEAVES="', 1)[1].split('"', 1)[0].split()
    assert "=terminal.backend" in leaves and "terminal.backend" not in leaves
    assert "terminal.container_persistent" in leaves and "terminal.sandbox_dir" in leaves
