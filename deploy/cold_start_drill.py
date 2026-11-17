#!/usr/bin/env python3
"""A cold-start drill: stop a compose stack, start it, and time each service to healthy (T-0585).

    python3 deploy/cold_start_drill.py --dry -f aura-core/docker-compose.yml     # what it would do
    python3 deploy/cold_start_drill.py --yes -f aura-core/docker-compose.yml     # stop, start, time

Prints each service's seconds to healthy. Exits 1 when one is past its budget, never got
there, or started before a service it depends on was healthy; 2 when it would not run.
The ordering check has two halves: the compose file itself (a dependency that has a
healthcheck must be waited on with ``condition: service_healthy``, a one-shot with
``service_completed_successfully``), which a dry run already checks, and the times seen.

"Healthy" is Docker's healthcheck; a one-shot (restart "no", no healthcheck) is done on exit 0;
a service with neither must run ``--settle`` seconds without a restart. A real run takes the
stack down for as long as it takes to come back, needs ``--yes``, and refuses the assistant's
own stack (a service named ``jarvis``): that one changes only through a deploy.

Any restart during the drill fails that service, even if it then gets healthy: a start that
only works on the second try is what the drill is for. A long-running service with
``restart: "no"`` and no healthcheck would be taken for a one-shot; the house stack has none.

Not covered: whether a healthy service answers correctly, model warm-up after "healthy",
services behind a compose profile that is not switched on, and anything on another host.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

#: Seconds from the start of the drill, which includes compose's own time before the first
#: container starts (48 s on the first real run, 12 Nov 2026). The speech and embedding
#: services load a model first; the gateway took 136 s on that run, 90 s of it its own.
BUDGETS = {"wyoming-whisper": 300, "jarvis-embeddings": 300, "jarvis-reranker": 300, "jarvis-gateway": 240}
DEFAULT_BUDGET = 120


@dataclass
class Service:
    container: str
    deps: dict[str, str] = field(default_factory=dict)
    healthcheck: bool = False
    oneshot: bool = False


def read_compose(config: dict[str, Any]) -> dict[str, Service]:
    """The services of ``docker compose config --format json``.

    Compose lists there only what would start: a service behind a profile is present when
    COMPOSE_PROFILES switches it on (``make`` does, for two), and it is then stopped and
    started with the rest, so it is timed with the rest.
    """
    out: dict[str, Service] = {}
    for name, svc in (config.get("services") or {}).items():
        check = svc.get("healthcheck") or {}
        has_check = bool(check) and not check.get("disable")
        out[name] = Service(
            container=svc.get("container_name") or f"{config.get('name', '')}-{name}-1",
            deps={dep: (how or {}).get("condition", "service_started") for dep, how in (svc.get("depends_on") or {}).items()},
            healthcheck=has_check, oneshot=not has_check and svc.get("restart", "no") == "no")
    return out


def static_order(services: dict[str, Service]) -> list[str]:
    """What the compose file gets wrong about start order, before anything runs."""
    problems = []
    for name, svc in services.items():
        for dep, condition in svc.deps.items():
            on = services.get(dep)
            if on is None:
                problems.append(f"{name}: depends on {dep}, which is not a service here")
            elif on.healthcheck and condition != "service_healthy":
                problems.append(f"{name}: depends on {dep}, which has a healthcheck, without condition: service_healthy")
            elif on.oneshot and condition != "service_completed_successfully":
                problems.append(f"{name}: depends on {dep}, a one-shot, without condition: service_completed_successfully")
    return problems


def judge(services: dict[str, Service], times: dict[str, dict[str, Any]], budgets: dict[str, float],
          default: float) -> tuple[list[dict[str, Any]], list[str]]:
    """(a row per service, what failed). ``times``: started and ready in seconds from the drill's start."""
    rows, problems = [], []
    for name, svc in services.items():
        seen = times.get(name) or {"started": None, "ready": None, "state": "not seen"}
        budget = budgets.get(name, default)
        ready = seen["ready"]
        rows.append({"service": name, "started": seen["started"], "seconds": ready, "budget": budget, "state": seen["state"]})
        if ready is None:
            problems.append(f"{name}: never healthy ({seen['state']})")
            continue
        if ready > budget:
            problems.append(f"{name}: healthy after {ready:.1f} s, budget {budget:g} s")
        for dep in svc.deps:
            dep_seen = times.get(dep) or {}
            # The poll sees "ready" up to a second late, and compose starts the dependent at
            # once, so the comparison is with the last moment the dependency was seen NOT
            # ready: a start before that is certainly too early.
            floor = dep_seen.get("unready", dep_seen.get("ready"))
            if dep in services and floor is not None and seen["started"] is not None and seen["started"] < floor:
                what = "done" if services[dep].oneshot else "healthy"
                problems.append(f"{name}: started at {seen['started']:.1f} s, before {dep} was {what} (not before {floor:.1f} s)")
    return rows, problems


def table(rows: list[dict[str, Any]]) -> str:
    wide = max(len(r["service"]) for r in rows)
    def sec(v: float | None) -> str:
        return f"{('%.1f' % v) if v is not None else '-':>6} s"
    return "\n".join(
        f"{r['service']:<{wide}}  started {sec(r.get('started'))}  healthy {sec(r['seconds'])}  "
        f"budget {r['budget']:g} s  {r['state']}" for r in rows)


def watch(services: dict[str, Service], inspect: Callable[[str], dict[str, Any]], *, now: Callable[[], float],
          sleep: Callable[[float], None], give_up: float, settle: float) -> dict[str, dict[str, Any]]:
    """Poll each container until it is ready or has plainly failed, or ``give_up`` seconds pass.

    ``inspect(container)`` gives Status, Health (absent with no healthcheck), ExitCode,
    RestartCount and ``started`` (seconds from the drill's start).
    """
    out: dict[str, dict[str, Any]] = {name: {"started": None, "ready": None, "state": "not seen"} for name in services}
    final: set[str] = set()
    prev: float | None = None  # the poll before this one: what was not ready now was not ready then
    while True:
        t = now()
        for name, svc in services.items():
            if name in final:
                continue
            if out[name]["ready"] is None:
                out[name]["unready"] = prev
            try:
                s = inspect(svc.container)
            except Exception as exc:  # noqa: BLE001 - a container not created yet is just not seen yet
                out[name]["state"] = f"not seen ({type(exc).__name__})"
                continue
            row = out[name]
            row["started"] = s.get("started")
            status, health, restarts = s.get("Status"), s.get("Health"), s.get("RestartCount") or 0
            if restarts or status == "restarting":
                row["state"] = f"restarted {restarts} times"
                final.add(name)
            elif svc.oneshot:
                if status == "exited":
                    row["state"] = "done" if s.get("ExitCode") == 0 else f"exited {s.get('ExitCode')}"
                    row["ready"] = t if s.get("ExitCode") == 0 else None
                    final.add(name)
                else:
                    row["state"] = str(status)
            elif status == "exited":
                row["state"] = f"exited {s.get('ExitCode')}"
                final.add(name)
            elif svc.healthcheck:
                row["state"] = str(health)
                if health == "healthy":
                    row["ready"] = t
                if health in ("healthy", "unhealthy"):
                    final.add(name)
            else:
                # No healthcheck to ask: up and not restarted for a while is all there is to see.
                row["state"] = "running"
                if status == "running" and row["started"] is not None and t - row["started"] >= settle:
                    row["ready"], row["state"] = t, f"running {settle:g} s, no healthcheck"
                    final.add(name)
        if len(final) == len(services) or t >= give_up:
            return out
        prev = t
        sleep(1.0)


def read_inspect(info: dict[str, Any], began_wall: float) -> dict[str, Any]:
    """One ``docker inspect`` entry as ``watch`` wants it; raises until the container has started again."""
    state = info["State"]
    # Docker writes nanoseconds; fromisoformat takes six digits at most.
    stamp = re.sub(r"(\.\d{1,6})\d*", r"\1", state["StartedAt"]).replace("Z", "+00:00")
    started = datetime.fromisoformat(stamp).timestamp() - began_wall
    if started < 0:
        raise LookupError("not started again yet")
    return {"Status": state.get("Status"), "Health": (state.get("Health") or {}).get("Status"),
            "ExitCode": state.get("ExitCode"), "RestartCount": info.get("RestartCount"), "started": started}


def _compose(file: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", "-f", file, *args], capture_output=True, text=True)


def _run(file: str, services: dict[str, Service], give_up: float, settle: float) -> dict[str, dict[str, Any]]:
    """Stop the stack, start it, watch it. The only code here that touches Docker."""
    stopped = _compose(file, "stop")
    if stopped.returncode:
        raise RuntimeError(f"stop failed: {stopped.stderr.strip()[-300:]}")
    began_wall, began = time.time(), time.monotonic()
    # Its progress output is thrown away: an unread pipe would stall it once the pipe filled.
    up = subprocess.Popen(["docker", "compose", "-f", file, "up", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def inspect(container: str) -> dict[str, Any]:
        raw = subprocess.run(["docker", "inspect", container], capture_output=True, text=True, timeout=20)
        return read_inspect(json.loads(raw.stdout)[0], began_wall)

    code: int | None = None
    try:
        times = watch(services, inspect, now=lambda: time.monotonic() - began, sleep=time.sleep, give_up=give_up, settle=settle)
        try:
            code = up.wait(timeout=60)
        except subprocess.TimeoutExpired:
            # Still waiting on a dependency that never got healthy: the rows already say which.
            up.kill()
        return times
    finally:
        # Also on Ctrl-C or a crash in here: the stack may be half started, so say how to finish.
        if code != 0:
            print(f"drill: `up -d` {'did not finish' if code is None else f'exited {code}'}; "
                  f"to bring the stack back: docker compose -f {file} up -d", file=sys.stderr)


def main(argv: list[str], *, config: dict[str, Any] | None = None, run: Callable[..., dict[str, dict[str, Any]]] = _run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-f", "--file", required=True)
    ap.add_argument("--dry", action="store_true", help="read the file, check its start order, print the plan; touch nothing")
    ap.add_argument("--yes", action="store_true", help="really stop and start the stack")
    ap.add_argument("--budget", action="append", default=[], metavar="SERVICE=SECONDS")
    ap.add_argument("--default-budget", type=float, default=DEFAULT_BUDGET)
    ap.add_argument("--settle", type=float, default=10.0, help="seconds a service with no healthcheck must run unrestarted")
    args = ap.parse_args(argv)
    if config is None:
        try:
            read = _compose(args.file, "config", "--format", "json")
        except OSError as exc:
            print(f"drill: cannot run docker: {exc}", file=sys.stderr)
            return 2
        if read.returncode:
            print(f"drill: cannot read {args.file}: {read.stderr.strip()[-300:]}", file=sys.stderr)
            return 2
        config = json.loads(read.stdout)
    services = read_compose(config)
    budgets = {**BUDGETS, **{k: float(v) for k, v in (b.split("=", 1) for b in args.budget)}}
    order = static_order(services)
    if args.dry:
        print(f"dry run: would stop and start {len(services)} services of {args.file}")
        for name, svc in services.items():
            how = "healthcheck" if svc.healthcheck else "exit 0" if svc.oneshot else f"running {args.settle:g} s"
            print(f"  {name}: ready by {how}, budget {budgets.get(name, args.default_budget):g} s"
                  + (f", after {', '.join(svc.deps)}" if svc.deps else ""))
        for problem in order:
            print(f"FAIL: {problem}")
        return 1 if order else 0
    if "jarvis" in services:
        print("drill: this is the assistant's own stack; it changes only through a deploy", file=sys.stderr)
        return 2
    if not args.yes:
        print("drill: a real run stops the stack; pass --yes (or --dry to see the plan)", file=sys.stderr)
        return 2
    if order:
        # A file that is wrong about order is not worth an outage to confirm.
        for problem in order:
            print(f"FAIL: {problem}")
        return 1
    give_up = max(budgets.get(name, args.default_budget) for name in services) + 30
    try:
        seen = run(args.file, services, give_up, args.settle)
    except (RuntimeError, OSError) as exc:
        print(f"drill: {exc}", file=sys.stderr)
        return 2
    rows, problems = judge(services, seen, budgets, args.default_budget)
    print(table(rows))
    for problem in problems:
        print(f"FAIL: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
