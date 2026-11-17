"""deploy/n8n-env.sh <path>: fresh secrets for n8n, once, never printed, never rewritten (T-0630).

T-0628's installer calls this exact interface. Not covered: openssl missing (falls back to
python3 -c secrets; a host with neither fails loudly rather than writing weak values).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "n8n-env.sh"
KEYS = ("SANDBOX_API_KEYS", "SANDBOX_N8N_API_KEY", "N8N_INSTANCE_AI_SANDBOX_API_KEY",
        "SANDBOX_API_RUNNER_REGISTRATION_TOKEN", "SANDBOX_API_RUNNER_API_KEY", "N8N_ENCRYPTION_KEY")
ALIASES = {"N8N_SANDBOX_SERVICE_API_KEY": "N8N_INSTANCE_AI_SANDBOX_API_KEY",
           "SANDBOX_RUNNER_API_KEYS": "SANDBOX_API_RUNNER_API_KEY",
           "SANDBOX_RUNNER_REGISTRATION_TOKEN": "SANDBOX_API_RUNNER_REGISTRATION_TOKEN"}


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), str(path)], capture_output=True, text=True, check=True)


def _read(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def test_a_missing_file_is_made_0600_with_fresh_distinct_secrets(tmp_path: Path) -> None:
    target = tmp_path / "n8n.env"
    result = _run(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    values = _read(target)
    for key in KEYS:
        assert len(values[key]) >= 32, key
    assert len({values[k] for k in KEYS}) == len(KEYS)
    for alias, source in ALIASES.items():
        assert values[alias] == values[source]
    assert not any(v in result.stdout + result.stderr for v in values.values())


def test_only_missing_keys_are_added_and_values_never_change(tmp_path: Path) -> None:
    target = tmp_path / "n8n.env"
    target.write_text("N8N_ENCRYPTION_KEY=owner-chose-this\nEXTRA=keep # no newline at end")
    os.chmod(target, 0o644)
    _run(target)
    first = _read(target)
    assert first["N8N_ENCRYPTION_KEY"] == "owner-chose-this"
    assert first["EXTRA"] == "keep # no newline at end"
    assert all(k in first for k in KEYS)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    before = target.read_text()
    _run(target)
    assert target.read_text() == before


def test_an_existing_source_key_feeds_a_missing_alias(tmp_path: Path) -> None:
    target = tmp_path / "n8n.env"
    target.write_text("SANDBOX_API_RUNNER_API_KEY=abc\n")
    _run(target)
    assert _read(target)["SANDBOX_RUNNER_API_KEYS"] == "abc"
