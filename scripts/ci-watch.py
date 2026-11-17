#!/usr/bin/env python3
"""Watch a pushed head's pipeline from this host, without a token.

The pipeline's page needs a token this checkout does not read. Three things Docker shows on the
deploy host say most of it: the runner daemon's own result lines (counted, never printed: each
carries the project's path), the job containers the runner has started, and the image tag the
deployed stack runs, which the deploy sets to the commit's first eight characters. A failed job
is told by three numbers read out of its line: its exit code, the seconds it ran and its
pipeline's number (``exit 22 after 18 s (pipeline 910)``).

    make ci-watch                               # until this host deploys a new tag, or 80 minutes
    python3 scripts/ci-watch.py --sha "$(git rev-parse HEAD)"   # only that commit's tag counts
    python3 scripts/ci-watch.py --once          # one look, one line

It ends 0 when the tag moved, the stack is healthy and no job container is left (the deploy job
smokes the stack after it moves the tag); 1 when a job failed on this host's runner and no job
container has run for ``--quiet`` minutes since; 2 at ``--minutes``. A line is printed when what
it sees changes, and one at the end.

What it does not see. The second runner's jobs run on another machine: a failure there shows
here only as a pipeline that goes quiet with no failed job, and the watch then ends at its
limit. A failed job the pipeline allows is counted and does not end the watch. Without ``--sha``
any new tag ends it, including a deploy somebody else started. It asks Docker only: no process
is listed and nothing is matched against a command line, so the wait cannot find itself.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

#: One docker command (without the word ``docker``) to its exit code and output.
Docker = Callable[[list[str]], tuple[int, str]]
RUNNER = "gitlab-runner"
STACK = ("jarvis", "jarvis-adapter")
_HEALTH = "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}"


def _docker(argv: list[str]) -> tuple[int, str]:
    """The runner's log comes on both streams, so they are read as one. A missing Docker or a
    command that hangs is an answer (1, ""), not a crash: the watch says "unknown" and goes on."""
    try:
        out = subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return out.returncode, (out.stdout or "") + (out.stderr or "")


def since_stamp(ago: str | None, now: float) -> str:
    """The moment results are counted from, as a fixed time. Docker reads ``45m`` as "45 minutes
    before this call": asked again every half minute, the window would slide and the first
    jobs of the pipeline would fall out of the count while the watch runs."""
    units = {"s": 1, "m": 60, "h": 3600}
    back = 0.0
    if ago:
        if ago[-1:] not in units or not ago[:-1].replace(".", "", 1).isdigit():
            raise ValueError(f"--since takes a number and s, m or h (45m), not {ago!r}")
        back = float(ago[:-1]) * units[ago[-1]]
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - back))


def tag_of(image: str) -> str:
    """``host:5050/path/name:tag`` to ``tag``; a registry's port is not a tag."""
    last = image.strip().rsplit("/", 1)[-1]
    return last.rsplit(":", 1)[1] if ":" in last else ""


#: The three numbers a failed job's line carries, each matched as digits and nothing else: a line
#: shaped unlike the ones seen gives no number, never a piece of its text.
_EXIT = re.compile(r"(?:^|\s)exit_code=(\d+)(?=\s|$)|Job failed: exit code (\d+)(?=\s|$)")
_SECONDS = re.compile(r"(?:^|\s)duration_s=(\d+)(?:\.\d+)?(?=\s|$)")
_PIPELINE = re.compile(r"(?:^|\s)pipeline_id=(\d+)(?=\s|$)")
#: The daemon colours each field's name; with the escapes left in, no ``name=`` is found.
_COLOUR = re.compile(r"\x1b\[[0-9;]*m")
#: What an exit code usually is. Another program can exit with the same number, so each says "usually".
EXIT_NOTES = {22: "usually curl -f answered an HTTP error (an upload or a download refused)",
              137: "usually killed: out of memory, or stopped",
              143: "usually ended by a signal"}
#: Failed jobs told one by one; past this the rest are a count (a pipeline that fails everywhere).
_TOLD = 6


def how_it_ended(line: str) -> str:
    """One failed job's end, from its line in the runner's log: ``exit 22 after 18 s (pipeline 910)``.

    The diagnosis these numbers allow without the job's trace: a job that exits 22 after eighteen
    seconds died at a transfer in its first step, not at a test. Not covered: the job's name and
    the step that failed (both are in the trace, which needs a token); the exit code of a job the
    runner stopped itself (a time limit has none)."""
    line = _COLOUR.sub("", line)
    code, secs, pipeline = _EXIT.search(line), _SECONDS.search(line), _PIPELINE.search(line)
    if not (code or secs or pipeline):
        return "no detail"
    said = f"exit {code.group(1) or code.group(2)}" if code else "no exit code"
    said += f" after {secs.group(1)} s" if secs else ""
    return said + (f" (pipeline {pipeline.group(1)})" if pipeline else "")


@dataclass(frozen=True)
class Look:
    succeeded: int
    failed: int
    jobs: int
    tag: str
    healthy: bool
    ended: tuple[str, ...] = ()

    def how(self) -> str:
        """How the failed jobs ended, oldest first, and once what a known exit code usually is."""
        if not self.ended:
            return ""
        told = ", ".join(self.ended[:_TOLD]) + (f", +{len(self.ended) - _TOLD} more" if len(self.ended) > _TOLD else "")
        notes = [f"exit {code} is {note}" for code, note in EXIT_NOTES.items()
                 if any(end.split(" after ")[0].split(" (")[0] == f"exit {code}" for end in self.ended)]
        return f"; failed: {told}" + "".join(f"; {note}" for note in notes)

    def line(self) -> str:
        return (f"{self.succeeded} succeeded, {self.failed} failed, {self.jobs} job container(s) running, "
                f"tag {self.tag or 'unknown'}{self.how()}")


def look(docker: Docker, since: str) -> Look:
    """What this host shows now. Only counts, the tag and the failed jobs' numbers leave this function."""
    _code, log = docker(["logs", "--since", since, RUNNER])
    lines = log.splitlines()
    code, image = docker(["inspect", STACK[0], "--format", "{{.Config.Image}}"])
    healthy = True
    for name in STACK:
        ok, state = docker(["inspect", name, "--format", _HEALTH])
        words = state.split()
        healthy = healthy and ok == 0 and words[:1] == ["running"] and (len(words) == 1 or words[1] == "healthy")
    _ok, names = docker(["ps", "--format", "{{.Names}}"])
    ended = tuple(how_it_ended(ln) for ln in lines if "Job failed" in ln)
    return Look(succeeded=sum("Job succeeded" in ln for ln in lines), failed=len(ended),
                jobs=sum(name.startswith("runner-") for name in names.split()),
                tag=tag_of(image) if code == 0 else "", healthy=healthy, ended=ended)


def watch(docker: Docker, clock: Callable[[], float], sleep: Callable[[float], None], say: Callable[[str], None], *,
          minutes: float, interval: float, quiet_minutes: float, from_tag: str | None, sha: str | None,
          since: str) -> int:
    began, base, last, quiet_since = clock(), from_tag, "", None
    while True:
        now = look(docker, since)
        base = base if base is not None else now.tag
        if now.line() != last:
            last = now.line()
            say(last)
        moved = now.tag == sha[:8] if sha else bool(now.tag) and now.tag != base
        if moved and now.healthy and now.jobs == 0:
            say(f"deployed {now.tag}: the stack is healthy; {now.succeeded} succeeded, {now.failed} failed on this "
                "host's runner")
            return 0
        quiet_since = None if now.jobs else quiet_since if quiet_since is not None else clock()
        if now.failed and quiet_since is not None and clock() - quiet_since >= quiet_minutes * 60:
            say(f"no deploy: {now.failed} failed on this host's runner and no job has run here for "
                f"{quiet_minutes:g} minutes; the stack runs tag {now.tag or 'unknown'}{now.how()}")
            return 1
        if clock() - began >= minutes * 60:
            why = "the tag moved but the stack is not healthy" if moved and not now.healthy else \
                "the tag moved and a job is still running" if moved else "no new tag"
            say(f"stopped after {minutes:g} minutes: {why}; {now.line()}")
            return 2
        sleep(interval)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--sha", help="the pushed commit: only its tag (its first eight characters) is the deploy")
    ap.add_argument("--from-tag", help="the tag the stack ran before the push, for a watch started late")
    ap.add_argument("--since", help="count the runner's results from this long before the start (45m, 2h); "
                    "default: from the start (--once: the last hour)")
    ap.add_argument("--minutes", type=float, default=80, help="give up after this long (default 80)")
    ap.add_argument("--quiet", type=float, default=10, help="minutes with no job container that end the watch "
                    "after a failed job (default 10)")
    ap.add_argument("--interval", type=float, default=30, help="seconds between looks (default 30)")
    ap.add_argument("--once", action="store_true", help="one look, one line")
    args = ap.parse_args(argv)
    try:
        since = since_stamp(args.since or ("60m" if args.once else None), time.time())
    except ValueError as exc:
        ap.error(str(exc))

    def say(line: str) -> None:
        # Out now: sent to a file or a pipe, a held line would show nothing until the end (T-1445).
        print(f"{time.strftime('%H:%M', time.gmtime())} {line}" if not args.once else line, flush=True)

    if args.once:
        say(look(_docker, since).line())
        return 0
    return watch(_docker, time.monotonic, time.sleep, say, minutes=args.minutes, interval=args.interval,
                 quiet_minutes=args.quiet, from_tag=args.from_tag, sha=args.sha, since=since)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
