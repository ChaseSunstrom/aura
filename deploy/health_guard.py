#!/usr/bin/env python3
"""Tell the owner when a production container crash-loops or turns unhealthy (T-0344).

The reranker crash-looped for about ten hours on 2026-10-09 (a port clash with jarvis-models)
and nothing said so. This runs from the host's user crontab every five minutes, beside
disk_guard.py, reads Docker's own state, and records a `notice` in the house's notifications
(jarvis-core `notifications.add`, which the console and the phone show) when an incident
starts and again when it clears. No container gets the Docker socket for this.

It also asks this host's CI runner whether it can reach GitLab (T-0424): on 2026-10-27 the
runner container stayed up while its url named the old tailnet host, and CI took no job for two
days without anyone hearing. Two failed checks in a row are one notice, and three good ones clear it (T-0601).

What it does not do: restart anything; watch containers outside the two production compose
projects (CI jobs, lanes and one-off runs are not the owner's business); notice a container that
was removed outright; notice CI going quiet for any reason other than this runner (GitLab's own
runner, a pipeline nobody started). When the house itself is down the notice cannot be recorded, so the
incident stays unannounced and is tried again on the next run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

#: Compose projects on this host that are production: the Hermes stack and the house.
PROJECTS = frozenset({"jarvis", "jarvis-core"})
#: Restarts between two checks that count as a crash loop even if each check sees "running".
RESTART_JUMP = 3
STATE = Path(os.environ.get("HEALTH_GUARD_STATE", str(Path.home() / ".local/state/jarvis-health-guard.json")))
HOST_ENV = Path(os.environ.get("AURA_HOST_ENV", "/opt/jarvis-host/.env"))
HOUSE = os.environ.get("HOUSE_URL", "http://127.0.0.1:8080")
#: This host's CI runner container; empty turns the check off.
RUNNER = os.environ.get("HEALTH_GUARD_RUNNER", "gitlab-runner")
CI_KEY = "ci:gitlab-runner"
#: Hysteresis for containers and the runner alike: a service that flaps (T-0601: GitLab gave 12
#: alternating notices) is neither down nor back until it has held for this many checks in a row.
RAISE_AFTER = 2
CLEAR_AFTER = 3


def problem(c: dict[str, Any], before: dict[str, Any] | None) -> str | None:
    """What is wrong with one container, in words, or None."""
    if c["state"] == "restarting":
        return "keeps restarting"
    if c["health"] == "unhealthy":
        return "is unhealthy"
    if c["state"] == "exited" and c["exit_code"] != 0 and c["restart_policy"] not in ("", "no"):
        return f"stopped with exit code {c['exit_code']}"
    if before is not None and c["restarts"] - before.get("restarts", c["restarts"]) >= RESTART_JUMP:
        return f"restarted {c['restarts'] - before['restarts']} times since the last check"
    return None


def decide(containers: list[dict[str, Any]], state: dict[str, Any]) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """The notices to send and the state to keep: one when an incident starts, one when it clears."""
    notices: list[tuple[str, str]] = []
    new: dict[str, Any] = {}
    for c in containers:
        if c["project"] not in PROJECTS:
            continue
        before = state.get(c["name"])
        why = problem(c, before)
        was = (before or {}).get("incident", "")
        bad = (before or {}).get("bad", 0) + 1 if why else 0
        good = (before or {}).get("good", 0) + 1 if not why else 0
        incident = was
        if why and not was and bad >= RAISE_AFTER:
            notices.append((c["name"], f"{c['name']} {why}"))
            incident = why
        elif not why and was and good >= CLEAR_AFTER:
            notices.append((c["name"], f"{c['name']} is running normally again"))
            incident = ""
        new[c["name"]] = {"restarts": c["restarts"], "incident": incident, "bad": bad, "good": good}
    return notices, new


def settle(notices: list[tuple[str, str]], new: dict[str, Any], old: dict[str, Any],
           sent: dict[str, bool]) -> dict[str, Any]:
    """The state to save: a container whose notice was not recorded keeps its old entry, so
    the next run sees the same change and tries again."""
    kept = dict(new)
    for name, _ in notices:
        if not sent.get(name):
            if name in old:
                kept[name] = old[name]
            else:
                # Not announced yet: keep the count, drop the incident, so the next bad check retries.
                kept[name] = {**new[name], "incident": ""}
    return kept


def runner_problem() -> str | None:
    """What is wrong with the CI runner, from its own `verify`, or None. Container state is not
    enough: the 09-05 and 10-27 outages both had the container up."""
    if not RUNNER:
        return None
    try:
        r = subprocess.run(["docker", "exec", RUNNER, "gitlab-runner", "verify"],
                           capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return "cannot reach GitLab"
    out = r.stdout + r.stderr
    if r.returncode != 0 and "Verifying runner" not in out:
        return "is not running"
    return None if "is valid" in out and "not valid" not in out else "cannot reach GitLab"


def ci_decide(why: str | None, before: dict[str, Any] | None) -> tuple[str | None, dict[str, Any]]:
    """The notice to send, if any, and the state to keep. Needs two failed checks in a row to
    raise and three good ones to clear, so a GitLab restart or a flap says nothing."""
    before = before or {}
    was = before.get("incident", "")
    strikes = before.get("strikes", 0) + 1 if why else 0
    good = 0 if why else before.get("good", 0) + 1
    if why and strikes >= RAISE_AFTER and not was:
        return (f"CI's runner on this host {why}: no deploy or image build will run until it is fixed",
                {"strikes": strikes, "incident": why, "good": 0})
    if not why and was and good >= CLEAR_AFTER:
        return "CI's runner on this host is taking jobs again", {"strikes": 0, "incident": "", "good": 0}
    return None, {"strikes": strikes, "incident": was, "good": good}


def containers() -> list[dict[str, Any]]:
    ids = subprocess.run(["docker", "ps", "-aq"], capture_output=True, text=True, check=True).stdout.split()
    if not ids:
        return []
    raw = json.loads(subprocess.run(["docker", "inspect", *ids], capture_output=True, text=True, check=True).stdout)
    return [{
        "name": r["Name"].lstrip("/"),
        "project": (r["Config"].get("Labels") or {}).get("com.docker.compose.project", ""),
        "state": r["State"]["Status"],
        "health": (r["State"].get("Health") or {}).get("Status", ""),
        "exit_code": r["State"].get("ExitCode", 0),
        "restart_policy": (r["HostConfig"].get("RestartPolicy") or {}).get("Name", ""),
        "restarts": r.get("RestartCount", 0),
    } for r in raw]


def notify(title: str, name: str, body: str | None = None, source: str = "health_guard") -> bool:
    body = json.dumps({"kind": "notice", "title": title, "source": source,
                       "body": body or f"Seen by the host's health check. `docker logs {name}` says why."}).encode()
    # The token read is inside the try: an unreadable .env is a notice not recorded (retried
    # next run), not a crash that fails the deploy calling --image-failed (T-0419 review).
    try:
        token = next((line.split("=", 1)[1].strip() for line in HOST_ENV.read_text().splitlines()
                      if line.startswith("HASS_TOKEN=")), "")
        req = urllib.request.Request(f"{HOUSE}/api/services/notifications/add", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status < 400
    except OSError as err:
        print(f"health_guard: could not record '{title}': {err}", file=sys.stderr)
        return False


def image_failed(svc: str, job_url: str) -> int:
    """One notice for an image this pipeline built but could not ship (T-0419). Exits 0 even
    when the house does not take it: production keeps the last image that passed either way,
    and a deploy must not fail over a notice."""
    title = f"The new {svc} image did not ship"
    if not notify(title, svc, source="deploy",
                  body=f"Its CI build or security scan failed, so production keeps the last {svc} "
                       f"image that passed. The job says why: {job_url}"):
        print(f"health_guard: {title} (not recorded)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--image-failed"] and len(args) == 3:
        return image_failed(args[1], args[2])
    old = json.loads(STATE.read_text()) if STATE.exists() else {}
    notices, new = decide(containers(), old)
    title, new[CI_KEY] = ci_decide(runner_problem(), old.get(CI_KEY))
    if title:
        notices.append((CI_KEY, title))
    sent = {name: notify(title, name, body=(
        "Seen by the host's health check. `docker exec gitlab-runner gitlab-runner verify` says why; "
        "docs/ci.md lists the causes seen so far.") if name == CI_KEY else None) for name, title in notices}
    for name, title in notices:
        print(f"health_guard: {title}{'' if sent[name] else ' (not recorded; retrying next run)'}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(settle(notices, new, old, sent), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
