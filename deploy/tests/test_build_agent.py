"""The desktop agent's build script limits and retries its two long steps (T-1145).

A stand-in interpreter plays pip and PyInstaller, so nothing is installed or frozen here.
Not covered: Wine itself, and time lost outside the script (the image pull, the runner).
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "aura-desktop-app" / "tools" / "build-agent.sh"

# $MODE says how the stand-in behaves; $MARK is a file that remembers the first call.
STAND_IN = """#!/usr/bin/env bash
echo "$2" >> "$CALLS"
case "$2:$MODE" in
  pip:stall-once) [ -e "$MARK" ] || { : > "$MARK"; sleep 30; } ;;
  pip:stall-always) sleep 30 ;;
  pip:broken) exit 3 ;;
  PyInstaller:*)
    while [ $# -gt 0 ]; do [ "$1" = --distpath ] && dist=$2; shift; done
    mkdir -p "$dist" && : > "$dist/jarvis-desktop-agent" ;;
esac
"""


def build(tmp_path: Path, mode: str) -> subprocess.CompletedProcess:
    fake = tmp_path / "python"
    fake.write_text(STAND_IN)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": os.environ["PATH"], "PYTHON": str(fake), "TRIPLE": "x86_64-unknown-linux-gnu",
           "OUT_DIR": str(tmp_path / "out"), "PIP_LIMIT_S": "1", "FREEZE_LIMIT_S": "20",
           "TIMES_FILE": str(tmp_path / "out" / "build-times.txt"),
           "MODE": mode, "MARK": str(tmp_path / "mark"), "CALLS": str(tmp_path / "calls"),
           "TMPDIR": str(tmp_path)}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120)


def calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls").read_text().split()


def times(tmp_path: Path) -> str:
    return (tmp_path / "out" / "build-times.txt").read_text()


def test_a_step_that_stalls_once_is_killed_tried_again_and_the_build_finishes(tmp_path):
    ran = build(tmp_path, "stall-once")
    assert ran.returncode == 0, ran.stderr
    assert calls(tmp_path) == ["pip", "pip", "PyInstaller"]
    kept = times(tmp_path)
    assert "pip install, try 1: exit 124 after" in kept
    assert re.search(r"pip install, try 2: exit 0 after \d+ s \(limit 1 s\)", kept)
    assert re.search(r"PyInstaller, try 1: exit 0 after \d+ s", kept)
    assert "build-agent: pip install, try 1: exit 124" in ran.stdout  # and in the job's log
    assert (tmp_path / "out" / "jarvis-desktop-agent-x86_64-unknown-linux-gnu").is_file()


def test_a_step_that_always_stalls_fails_by_name_after_two_tries(tmp_path):
    ran = build(tmp_path, "stall-always")
    assert ran.returncode == 1
    assert calls(tmp_path) == ["pip", "pip"]
    assert "pip install stalled twice at its 1 s limit" in times(tmp_path)


def test_a_step_that_fails_by_itself_is_not_tried_again(tmp_path):
    ran = build(tmp_path, "broken")
    assert ran.returncode == 3 and calls(tmp_path) == ["pip"]
    assert "pip install, try 1: exit 3" in times(tmp_path)


def test_the_jobs_clock_covers_both_tries_of_both_steps_and_keeps_the_times():
    limits = [int(n) for n in re.findall(r"_LIMIT_S:-(\d+)\}", SCRIPT.read_text())]
    assert len(limits) == 2
    job = yaml.safe_load((REPO / ".gitlab" / "ci" / "desktop-dist.yml").read_text())["desktop:agent:windows"]
    minutes = int(re.fullmatch(r"(\d+)m", job["timeout"])[1])
    kill_after = int(re.search(r"--kill-after=(\d+)", SCRIPT.read_text())[1])
    # Both tries of both steps, each with its kill grace, and five minutes for the image and the copy.
    assert minutes * 60 >= 2 * (sum(limits) + 2 * kill_after) + 300
    # A run that fails is the one whose times are wanted.
    assert job["artifacts"]["when"] == "always" and "TIMES_FILE=" in "".join(job["script"])
