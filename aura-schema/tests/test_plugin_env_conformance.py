"""The Hermes plugins read their configuration from the schema, not the environment.

Every plugin does ``from aura_schema import from_env`` and reads attributes of
the validated object; the only environment variables still read directly are
Hermes' own plumbing (``HERMES_HOME``, ``HERMES_BIN``), which are not JARVIS
settings. These tests scan the plugin source so a new ``os.environ.get("X")`` —
the pattern that produced the 47-name sprawl of V2_PLAN §1.3 — fails here, and
every attribute a plugin reads off ``from_env()`` is a field the schema declares.
"""
from __future__ import annotations

import re
from pathlib import Path

from aura_schema import Settings

PLUGINS_DIR = Path(__file__).resolve().parents[2] / "hermes" / "plugins"

#: Hermes runtime plumbing the plugins may still read directly — where Hermes
#: keeps its home and which binary to shell out to are Hermes' variables, not
#: JARVIS settings, and a plugin copied into ``~/.hermes/plugins`` must resolve
#: them the way Hermes does. ``HERMES_HOME_MODE`` is the mode Hermes itself applies to its
#: home; the guard reads it to put that mode back after Hermes tightens the folder (T-1497).
HERMES_PLUMBING = {"HERMES_HOME", "HERMES_BIN", "HERMES_HOME_MODE"}

_ENV_READ_RE = re.compile(
    r"""(?:os\.environ\.get|os\.getenv)\(\s*["']([A-Z_][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z_][A-Z0-9_]*)["']\s*\]"""
)
_ATTR_READ_RE = re.compile(r"\bfrom_env\(\)\.([a-z_][a-z0-9_]*)|\bcfg\.([a-z_][a-z0-9_]*)")


def _sources() -> list[Path]:
    return [p for p in PLUGINS_DIR.rglob("*.py")
            if "__pycache__" not in p.parts and "tests" not in p.parts and p.name != "_plugin_loader.py"]


def test_plugins_read_no_setting_from_the_environment():
    direct: dict[str, list[str]] = {}
    for path in _sources():
        for a, b in _ENV_READ_RE.findall(path.read_text(encoding="utf-8")):
            direct.setdefault(a or b, []).append(path.relative_to(PLUGINS_DIR).as_posix())
    stray = {k: v for k, v in direct.items() if k not in HERMES_PLUMBING}
    assert not stray, f"plugins read settings from the environment instead of aura_schema: {stray}"


def test_no_plugin_keeps_a_private_env_helper():
    offenders = [p.relative_to(PLUGINS_DIR).as_posix() for p in _sources()
                 if re.search(r"^def _env\(", p.read_text(encoding="utf-8"), re.M)]
    assert not offenders, f"a plugin grew its own _env() helper again: {offenders}"


def test_every_attribute_a_plugin_reads_is_a_schema_field():
    fields = set(Settings.model_fields)
    unknown: dict[str, set[str]] = {}
    read_any = False
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if "from aura_schema import" not in text:
            continue
        for a, b in _ATTR_READ_RE.findall(text):
            name = a or b
            read_any = True
            if name not in fields:
                unknown.setdefault(path.relative_to(PLUGINS_DIR).as_posix(), set()).add(name)
    assert read_any, "no plugin reads from_env() — did the scan break?"
    assert not unknown, f"plugins read attributes the schema does not declare: {unknown}"
