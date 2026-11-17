"""`python -m jarvis_desktop` still starts the agent after the package became `aura_desktop`.

A PC set up before T-0960 starts the agent by the old module name (the README's Windows task
ran ``pythonw -m jarvis_desktop run``), and the agent updates itself unattended. A wheel with
no ``jarvis_desktop`` in it makes the next logon after an update fail with "No module named
jarvis_desktop", and the agent stays down with nobody told (T-1435).

Does not cover: a wheel actually built and installed (no build backend offline here); the
package list is read from pyproject the way setuptools' finder matches it.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _shipped_packages() -> list[str]:
    """Top-level packages the wheel holds: directories with an ``__init__.py`` whose name matches
    one of ``[tool.setuptools.packages.find] include`` (setuptools matches with fnmatchcase)."""
    include = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"]["setuptools"]["packages"]["find"]["include"]
    return sorted(
        p.name for p in ROOT.iterdir()
        if (p / "__init__.py").is_file() and any(fnmatchcase(p.name, pattern) for pattern in include)
    )


def test_the_wheel_ships_the_old_module_name_as_an_entry_point_only():
    assert _shipped_packages() == ["aura_desktop", "jarvis_desktop"]
    # Only the entry point is kept. A module beside it would be a second copy of the agent's
    # code under a name nothing here imports, and would hide a stale import.
    old = sorted(p.name for p in (ROOT / "jarvis_desktop").iterdir() if p.name != "__pycache__")
    assert old == ["__init__.py", "__main__.py"]


def test_the_old_module_name_runs_the_same_agent():
    def version(module: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", module, "--version"], cwd=ROOT,
                              capture_output=True, text=True, timeout=60, check=False)

    old, new = version("jarvis_desktop"), version("aura_desktop")
    assert new.returncode == 0 and new.stdout.startswith("jarvis-desktop ")
    assert (old.returncode, old.stdout) == (new.returncode, new.stdout), old.stderr[-400:]
