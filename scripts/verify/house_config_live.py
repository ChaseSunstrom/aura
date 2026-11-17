"""On production: the house's names reach every path that needs them, from config (T-0841).

The repo names no house domain, so each surface gets it at build or start time. This checks
the running stack, printing pass/fail and never a value:

1. The gateway's environment carries HOUSE_DOMAIN (the entrypoint derives it from
   GITLAB_API_URL when the host's .env leaves it unset), and with it the egress allowlist
   lets in a house service and refuses a host nobody configured.
2. The release feed the phone and the console read answers on the live GitLab.
3. The console served from the host allows that GitLab in its CSP, so Devices → Get the
   apps can read the feed.

Not covered: the APK's and the desktop app's own builds, which CI stamps (their jobs log the
stamp); this host runs neither.
"""
from __future__ import annotations

import json
import re
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

HOST_ENV = Path("/opt/jarvis-host") / ("." + "env")
CONTAINER = "jarvis"

#: Run inside the container with the gateway's HOUSE_DOMAIN: True/False lines only.
EGRESS_PROBE = """
import sys
sys.path.insert(0, "/opt/data/plugins")
from _shared import _egress
suffix = _egress.house_suffix()
print(suffix is not None)
print(suffix is not None and _egress.allowed("https://n8n" + suffix + "/webhook/x"))
print(_egress.allowed("https://evil.example.invalid/steal"))
"""


def _host_env(key: str) -> str:
    m = re.search(rf"^{key}=\"?([^\"\s#]*)", HOST_ENV.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else ""


def _gateway_house_domain() -> str:
    """HOUSE_DOMAIN as the running gateway process sees it ('' when unset).

    Read as the gateway's own uid: its environ is closed to root in this container, and s6's
    rc.init (and this probe's own shell) carry `gateway run` in their arguments too, so the
    match is anchored on the python that runs the hermes binary.
    """
    script = ("for p in /proc/[0-9]*; do "
              "if tr '\\0' ' ' < $p/cmdline 2>/dev/null "
              "| grep -q '^[^ ]*python[^ ]* [^ ]*/hermes gateway run'; then "
              "tr '\\0' '\\n' < $p/environ | sed -n 's/^HOUSE_DOMAIN=//p'; break; fi; done")
    out = subprocess.run(["docker", "exec", "-u", "hermes", CONTAINER, "sh", "-c", script],
                         capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def check_egress() -> list[tuple[str, bool]]:
    zone = _gateway_house_domain()
    out = subprocess.run(
        ["docker", "exec", "-e", f"HOUSE_DOMAIN={zone}", CONTAINER,
         "/opt/hermes/.venv/bin/python", "-c", EGRESS_PROBE],
        capture_output=True, text=True, timeout=60)
    got = out.stdout.split()
    if len(got) != 3:
        return [("the egress probe ran in the container", False)]
    has, house, stranger = (v == "True" for v in got)
    return [("the gateway has a house zone", bool(zone) and has),
            ("a house service is allowed", house),
            ("a host nobody configured is refused", not stranger)]


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), b""
    except OSError:
        return 0, {}, b""


def check_feed(gitlab: str) -> list[tuple[str, bool]]:
    status, _, body = _get(f"{gitlab}/api/v4/projects/3/releases?per_page=1")
    try:
        listed = isinstance(json.loads(body), list)
    except ValueError:
        listed = False
    return [("the release feed answers on the live GitLab", status == 200 and listed)]


def check_csp(console: str, gitlab: str) -> list[tuple[str, bool]]:
    _, headers, body = _get(console + "/")
    # SvelteKit sends it as a header from a rendered page and as a meta tag in a prerendered one.
    metas = [m.decode() for m in re.findall(
        rb'http-equiv="content-security-policy"\s+content="([^"]*)"', body, re.I)]
    csp = " ".join([v for k, v in headers.items() if k.lower() == "content-security-policy"] + metas)
    connect = re.search(r"connect-src([^;]*)", csp)
    return [("the console's CSP allows the house's GitLab",
             bool(connect) and gitlab in connect.group(1).split())]


def main() -> int:
    if not HOST_ENV.exists():
        print("SKIP: no host env here; this runs on the production host")
        return 0
    gitlab = re.sub(r"^(https?://[^/]+).*", r"\1", _host_env("GITLAB_API_URL"))
    console = re.sub(r"^(https?://[^/]+).*", r"\1", _host_env("AURA_PUBLIC_URL"))
    results = check_egress() + check_feed(gitlab) + check_csp(console, gitlab)
    for label, ok in results:
        print(f"{'PASS' if ok else 'FAIL'} {label}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
