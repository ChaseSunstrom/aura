"""vulture-allowlist-check.py fails on a name no scanned file mentions, passes otherwise."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "vulture-allowlist-check.py"


def _run(tmp_path: Path, allow: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def live_name(args_hint): ...\n")
    (tmp_path / "allow.py").write_text(allow)
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "allow.py"), str(tmp_path / "src")],
        capture_output=True,
        text=True,
    )


def test_allowlist_live_name_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, "# note\nargs_hint\nlive_name\n").returncode == 0


def test_allowlist_stale_name_fails(tmp_path: Path) -> None:
    r = _run(tmp_path, "args_hint\ngone_symbol\n")
    assert r.returncode == 1
    assert "gone_symbol" in r.stderr
