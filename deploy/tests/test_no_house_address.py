"""No tracked file names the house: its LAN (T-0809), its home domain, its tailnet (T-0841).

The LAN and the domain are the owner's, so they cannot be typed here either: both are read from
the host's env (TAILSCALE_IP and GITLAB_API_URL in /opt/jarvis-host/.env) and those two tests
skip where it is absent (CI, a fresh clone). A tailnet's name and addresses have a shape of their
own, so those two run everywhere: a real tailnet is tail<6 hex>.ts.net, and a real address is any
in 100.64.0.0/10 but the placeholders below. A failure names files, never the value.

Not covered: git history, which keeps the old text (rewriting main is a hard limit), and a
value written another way (hex, a reverse-DNS name, split across lines).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

HOST_ENV = Path("/opt/jarvis-host/.env")
ROOT = Path(__file__).resolve().parents[2]
#: Tailnet-range addresses docs and tests may use: 100.64.0.x, the range's top, and
#: 100.100.100.100, which is Tailscale's resolver on every tailnet, nobody's device.
PLACEHOLDER = re.compile(r"100\.64\.0\.\d{1,3}|100\.127\.255\.255|100\.100\.100\.100")
CGNAT = r"(^|[^0-9.])100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}"


def _host_env(key: str) -> str | None:
    try:
        text = HOST_ENV.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(rf"^{key}=(\S+)", text, re.M)
    return m.group(1) if m else None


def _files(pattern: str, *flags: str) -> list[str]:
    hits = subprocess.run(["git", "grep", "-l", *flags, pattern], cwd=ROOT, capture_output=True, text=True)
    return hits.stdout.split()


def test_no_tracked_file_names_the_house_lan():
    m = re.match(r"(\d+\.\d+\.\d+)\.\d+$", _host_env("TAILSCALE_IP") or "")
    if m is None:
        pytest.skip("the house's address is unknown here (no host env, as in CI)")
    hits = _files(r"(^|[^0-9])" + re.escape(m.group(1)) + r"\.[0-9]", "-E")
    assert hits == [], "these files name the house's LAN: write 10.20.30.x instead"


def test_no_tracked_file_names_the_home_domain():
    host = re.sub(r"^[a-z]+://", "", _host_env("GITLAB_API_URL") or "").split("/")[0].split(":")[0]
    if host.count(".") < 1:
        pytest.skip("the home domain is unknown here (no host env, as in CI)")
    domain = ".".join(host.split(".")[-2:])
    hits = _files(domain, "-F", "-i")
    assert hits == [], "these files name the home domain: write home.example, or read it from the env"


def test_no_tracked_file_names_a_real_tailnet():
    hits = _files(r"tail[0-9a-f]{6}\.ts\.net", "-E", "-i")
    assert hits == [], "these files name a tailnet: write example.ts.net"


def test_no_tracked_file_names_a_tailnet_address():
    out = subprocess.run(["git", "grep", "-oIE", CGNAT], cwd=ROOT, capture_output=True, text=True).stdout
    hits = sorted({line.split(":", 1)[0] for line in out.splitlines()
                   if not PLACEHOLDER.fullmatch(re.sub(r"^[^0-9]", "", line.split(":", 1)[1]))})
    assert hits == [], "these files name a tailnet address: write 100.64.0.x instead"
