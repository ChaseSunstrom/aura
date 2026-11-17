"""The plugins moved from jarvis-<x> to aura-<x> (T-1517): an upgraded volume loads each once.

These run the entrypoint's own ``migrate_renamed`` (cut out of ``deploy/entrypoint.sh`` with
the helpers it calls) over a volume laid out the way production's was before the rename.
Not covered: the rest of the entrypoint's start (the copy, ensure_plugins_enabled), which
other suites in this folder drive; and Hermes itself loading the result, which the deploy's
own check (one guard registered) shows.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"

OLD_CONFIG = """\
terminal:
  backend: jarvis-bwrap
web:
  search_backend: searxng
  extract_backend: "jarvis-extract"   # ours
plugins:
  enabled:
    - jarvis-guard
    - jarvis-bwrap
    - jarvis-hud
    - jarvis-homegrown
    - web-searxng
toolsets:
  - jarvis-goals
extract:
  url: http://jarvis-extract:8896
"""


def _script() -> str:
    """set -euo pipefail, log, write_atomic and py_atomic, then the rename's two functions and
    the call, exactly as entrypoint.sh has them."""
    text = ENTRYPOINT.read_text(encoding="utf-8")
    log = next(line for line in text.splitlines() if line.startswith("log() {"))
    atomic = text[text.index("read -r -d '' WRITE_ATOMIC_PY"):text.index("render() {")]
    start = text.index("rename_plugin_config() {")
    end = text.index("\nmigrate_renamed\n", start) + len("\nmigrate_renamed\n")
    return "set -euo pipefail\n" + log + "\n" + atomic + text[start:end]


def _volume(tmp_path: Path) -> tuple[Path, Path]:
    seed, home = tmp_path / "seed", tmp_path / "home"
    for name in ("aura-guard", "aura-bwrap", "aura-hud", "aura-goals", "aura-extract", "smart-home"):
        (seed / "plugins" / name).mkdir(parents=True)
        (seed / "plugins" / name / "plugin.yaml").write_text(f"name: {name}\n")
    (seed / "skills" / "aura-voice").mkdir(parents=True)
    for name in ("jarvis-guard", "jarvis-bwrap", "jarvis-hud", "jarvis-homegrown", "smart-home"):
        (home / "plugins" / name).mkdir(parents=True)
    (home / "skills" / "jarvis-voice").mkdir(parents=True)
    (home / "skills" / "homelab" / "jarvis-voice").mkdir(parents=True)
    (home / "skills" / "my-notes").mkdir(parents=True)
    (home / "plugin-data" / "jarvis-hud").mkdir(parents=True)
    (home / "config.yaml").write_text(OLD_CONFIG, encoding="utf-8")
    return seed, home


def _run(seed: Path, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", _script()], env={"PATH": "/usr/bin:/bin", "SEED": str(seed),
                          "HERMES_HOME": str(home)}, capture_output=True, text=True, check=True)


def test_plugin_rename_leaves_one_guard_and_the_users_own_plugins(tmp_path):
    """Before this, the copy put aura-guard beside the volume's jarvis-guard and both loaded:
    two pre_tool_call gates. Exactly one guard folder and one enabled guard name remain."""
    seed, home = _volume(tmp_path)
    out = _run(seed, home)
    plugins = sorted(p.name for p in (home / "plugins").iterdir())
    assert plugins == ["jarvis-homegrown", "smart-home"], plugins  # the copy adds aura-* after
    config = (home / "config.yaml").read_text(encoding="utf-8")
    enabled = config.split("plugins:\n  enabled:\n", 1)[1].split("toolsets:", 1)[0]
    assert [line.strip() for line in enabled.splitlines()] == [
        "- aura-guard", "- aura-bwrap", "- aura-hud", "- jarvis-homegrown", "- web-searxng"]
    assert "removing 'jarvis-guard' (now aura-guard)" in out.stderr


def test_plugin_rename_moves_the_backends_and_keeps_the_service_url_and_state(tmp_path):
    seed, home = _volume(tmp_path)
    _run(seed, home)
    config = (home / "config.yaml").read_text(encoding="utf-8")
    assert "  backend: aura-bwrap\n" in config
    assert '  extract_backend: "aura-extract"   # ours\n' in config
    assert "  - aura-goals\n" in config
    assert "url: http://jarvis-extract:8896" in config  # the compose service keeps its name
    assert (home / "plugin-data" / "jarvis-hud").is_dir()  # state is not a renamed folder


def test_plugin_rename_drops_the_old_skill_at_the_top_and_under_a_category(tmp_path):
    seed, home = _volume(tmp_path)
    _run(seed, home)
    assert not (home / "skills" / "jarvis-voice").exists()
    assert not (home / "skills" / "homelab" / "jarvis-voice").exists()
    assert (home / "skills" / "my-notes").is_dir()


def test_plugin_rename_is_idempotent_and_quiet_the_second_time(tmp_path):
    seed, home = _volume(tmp_path)
    _run(seed, home)
    once = (home / "config.yaml").read_text(encoding="utf-8")
    again = _run(seed, home)
    assert (home / "config.yaml").read_text(encoding="utf-8") == once
    assert again.stderr == ""


def test_plugin_rename_leaves_a_volume_with_no_config_alone(tmp_path):
    seed, home = _volume(tmp_path)
    (home / "config.yaml").unlink()
    _run(seed, home)
    assert not (home / "config.yaml").exists()


def test_every_shipped_plugin_and_skill_folder_is_aura_named():
    """The rule the migration rests on: no folder the image ships is still jarvis-named."""
    left = [p.name for p in (ROOT / "hermes" / "plugins").iterdir() if p.name.startswith("jarvis-")]
    left += [p.name for p in (ROOT / "hermes" / "skills").iterdir() if p.name.startswith("jarvis-")]
    assert left == []
