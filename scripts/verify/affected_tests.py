#!/usr/bin/env python3
"""Runs the test files a change touched, each suite from the directory ``make test`` runs it in.

The project's ``sampo check --affected`` command (``sampo check affected``): Sampo hands it
every changed test path at once. One pytest at the root cannot run them, because each package's
suite imports from its own directory (139 failed and 575 errors on a set ``make test`` passes,
2026-11-09). A path the change deleted or renamed away also failed the whole run (T-0812).
The Kotlin mirrors under ``android-app/tools`` are scripts judged by their exit code, as
``make test-android`` runs them.

    .venv/bin/python scripts/verify/affected_tests.py <test paths...>

Does not cover: the tests of a changed source file that its change did not touch; the full
gate (``make test``) still decides.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: The suites ``make test`` runs from their own directory; every other test runs from the root.
OWN_DIR = ("aura-core", "aura-desktop", "aura-adapter", "aura-schema", "hermes", "evals", "aura-voice")
FLAGS = ["-q", "--timeout=120", "--timeout-method=signal"]


def plan(paths: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """(pytest files by the directory to run them from, mirror scripts), for the paths that exist."""
    groups: dict[str, list[str]] = {}
    mirrors: list[str] = []
    for p in paths:
        if not (ROOT / p).is_file() or Path(p).name == "__init__.py":
            continue
        top = Path(p).parts[0]
        if p.startswith("android-app/tools/"):
            mirrors.append(p)
        elif top in OWN_DIR:
            groups.setdefault(top, []).append(str(Path(p).relative_to(top)))
        else:
            groups.setdefault(".", []).append(p)
    return groups, mirrors


def main(paths: list[str]) -> int:
    groups, mirrors = plan(paths)
    failed = False
    for cwd, files in groups.items():
        print(f"--- {cwd}: {len(files)} file(s)", flush=True)
        # 5: nothing collected (a file of helpers named like a test) is not a failure.
        failed |= subprocess.call([sys.executable, "-m", "pytest", *FLAGS, *files], cwd=ROOT / cwd) not in (0, 5)
    for script in mirrors:
        print(f"--- {script}", flush=True)
        failed |= subprocess.call([sys.executable, script], cwd=ROOT) != 0
    return int(failed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
