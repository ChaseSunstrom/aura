"""Env names say AURA_ (T-0959); a JARVIS_ name set outside the repo still counts.

The host's .env and GitLab's project variables were written with JARVIS_* names the repo
may not rename. Pinned here: the image's entrypoint and deploy/legacy-env.sh (the deploy
scripts' copy) adopt each JARVIS_X as AURA_X unless AURA_X is set, and every compose
interpolation of an AURA_ name falls back to its JARVIS_ spelling.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
LEGACY_ENV = ROOT / "deploy" / "legacy-env.sh"
COMPOSE_FILES = sorted(ROOT.glob("deploy/docker-compose*.yml")) + sorted(ROOT.glob("aura-core/docker-compose*.yml"))
#: Names born after the rename, each with who sets it. No host .env and no pipeline variable
#: ever spelled one JARVIS_, so there is nothing to adopt and a fallback would be a name
#: nobody can have set.
BORN_AURA = {"SRC": "scripts/install.sh sets it on every compose call that builds from the source (T-0864)"}

ODD = 'a$b"c `x` \\n'
ENV = {"PATH": "/usr/bin:/bin", "JARVIS_TAG": "t1", "JARVIS_API_KEY": ODD, "JARVIS_TOKEN": "old", "AURA_TOKEN": "new"}
PRINT = 'printf "%s|%s|%s" "$AURA_TAG" "$AURA_API_KEY" "$AURA_TOKEN"'


def _run(shell: str, script: str) -> str:
    return subprocess.run([shell, "-c", script], env=ENV, capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("shell", [s for s in ("sh", "dash", "bash") if shutil.which(s)])
def test_the_deploy_scripts_copy_adopts_legacy_names_in_any_posix_shell(shell):
    assert _run(shell, f". {LEGACY_ENV}; {PRINT}") == f"t1|{ODD}|new"


def test_the_entrypoint_adopts_legacy_names_under_its_own_strict_mode(tmp_path):
    text = ENTRYPOINT.read_text()
    block = text[text.index('_adopted=""'):text.index("unset _k _n _adopted")]
    script = tmp_path / "adopt.sh"
    script.write_text(block)
    assert _run("bash", f"set -euo pipefail; . {script}; {PRINT}") == f"t1|{ODD}|new"


def test_the_entrypoint_adopts_before_anything_reads_an_aura_name():
    lines = ENTRYPOINT.read_text().splitlines()
    start = lines.index('_adopted=""')
    early = [n + 1 for n, line in enumerate(lines[:start]) if "AURA_" in line and not line.lstrip().startswith("#")]
    later = [n for n, line in enumerate(lines[start:]) if "AURA_" in line and not line.lstrip().startswith("#")]
    assert early == [] and later


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_compose_interpolation_falls_back_to_the_legacy_name(path):
    bare = [m.group(0) for m in re.finditer(r"\$\{AURA_([A-Z0-9_]+)[^}]*\}?", path.read_text())
            if m.group(1) not in BORN_AURA
            and not m.group(0).startswith(f"${{AURA_{m.group(1)}:-${{JARVIS_{m.group(1)}")]
    assert bare == []


def test_a_name_excused_from_the_fallback_is_one_a_compose_file_uses_and_no_legacy_name():
    """The list above cannot be where a renamed name hides: each row is in use, and none has
    a JARVIS_ spelling anywhere in the compose files or the two adoption blocks."""
    text = "".join(path.read_text() for path in COMPOSE_FILES)
    adopters = ENTRYPOINT.read_text() + LEGACY_ENV.read_text()
    for name in BORN_AURA:
        assert f"${{AURA_{name}" in text, name
        assert f"JARVIS_{name}" not in text and f"JARVIS_{name}" not in adopters, name
