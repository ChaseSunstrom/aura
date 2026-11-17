"""Nothing that ships a plugin.yaml may be missing from a list that names plugins.

Three lists name the shipped plugins, and each one had gone stale by the time Phase 4
finished adding to it:

* ``deploy/tests/ci-config.yaml`` — what CI's plugin-discovery job enables. A plugin
  absent from it is *discovered* by that job (the grep matches the name either way) but
  never loaded, so a plugin whose ``register()`` raises passes CI and fails in the
  gateway. It was missing eight before this test existed.
* ``deploy/config.template.yaml`` ``plugins.enabled`` — the manifest a reader trusts.
  The entrypoint appends anything it finds anyway, so a gap here is a documentation
  defect rather than a runtime one, which is exactly the kind that survives for months.

Both are checked against the filesystem, which is the only source that cannot drift:
a directory under ``hermes/plugins/`` with a ``plugin.yaml`` is a plugin, and
``_shared/`` deliberately has none.

Not covered: whether a plugin *works*. That is each plugin's own suite, and the real
Hermes load is the CI job this file keeps honest.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT / "hermes" / "plugins"
CI_CONFIG = ROOT / "deploy" / "tests" / "ci-config.yaml"
TEMPLATE = ROOT / "deploy" / "config.template.yaml"


def shipped_plugins() -> set[str]:
    return {path.parent.name for path in PLUGINS_DIR.glob("*/plugin.yaml")}


def _enabled(path: Path) -> set[str]:
    # The template carries ${ENV} placeholders that are not valid YAML values in a few
    # places, but plugins.enabled is a plain list of strings, so a targeted read of the
    # block beats a full parse that would need the environment.
    lines = path.read_text(encoding="utf-8").splitlines()
    out: set[str] = set()
    in_plugins = in_enabled = False
    for line in lines:
        if not line.startswith(" ") and line.strip():
            in_plugins = line.strip() == "plugins:"
            in_enabled = False
            continue
        if in_plugins and line.strip().rstrip(":") in ("enabled", "disabled"):
            in_enabled = line.strip() == "enabled:"
            continue
        if in_enabled and line.strip().startswith("- "):
            out.add(line.strip()[2:].strip())
    return out


def test_the_filesystem_actually_carries_plugins():
    """A scan that found nothing would make every test below pass vacuously."""
    assert len(shipped_plugins()) >= 10, shipped_plugins()


def test_ci_enables_every_shipped_plugin():
    missing = shipped_plugins() - _enabled(CI_CONFIG)
    assert not missing, (
        f"deploy/tests/ci-config.yaml does not enable {sorted(missing)}, so CI's "
        f"plugin-discovery job never loads them and a broken register() reaches the gateway")


def test_ci_enables_nothing_that_does_not_exist():
    stale = _enabled(CI_CONFIG) - shipped_plugins()
    assert not stale, f"ci-config.yaml enables plugins that are not in the tree: {sorted(stale)}"


def test_the_config_template_names_every_shipped_plugin():
    enabled = _enabled(TEMPLATE)
    missing = shipped_plugins() - enabled
    assert not missing, (
        f"deploy/config.template.yaml does not name {sorted(missing)} under plugins.enabled; "
        f"the entrypoint would append them, but the template is the manifest a reader trusts")


def test_shared_is_not_mistaken_for_a_plugin():
    assert "_shared" not in shipped_plugins()
    assert not (PLUGINS_DIR / "_shared" / "plugin.yaml").exists()


# --------------------------------------------------------------------------- #
# Names Hermes has already taken
# --------------------------------------------------------------------------- #
#: What Hermes itself bundles, recorded from an install rather than discovered here,
#: because CI's python image has no Hermes in it and a check that only runs on a dev
#: box is a check that notices nothing.
BUNDLED = ROOT / "tests" / "contracts" / "hermes-bundled-plugins.json"

#: Where a Hermes install keeps its own plugins, if this machine has one. Both are
#: real: the second is the container's, the first a local install.
_HERMES_PLUGIN_DIRS = (
    Path.home() / ".hermes" / "hermes-agent" / "plugins",
    Path("/opt/hermes/plugins"),
)


def _bundled() -> dict:
    return json.loads(BUNDLED.read_text(encoding="utf-8"))


def _installed_bundled_names() -> set[str] | None:
    for directory in _HERMES_PLUGIN_DIRS:
        if directory.is_dir():
            return {p.name for p in directory.iterdir()
                    if p.is_dir() and not p.name.startswith("_") and p.name != "__pycache__"}
    return None


def test_no_shipped_plugin_takes_a_name_hermes_already_bundles():
    """Two plugins answering to one name is not an error anyone would see.

    Hermes loads its own plugins and JARVIS' from different directories, so nothing at
    runtime objects to a collision; it just means a config key, a `hermes plugins list`
    row and a `disabled:` entry each mean two things. `disk-cleanup` is bundled and
    `disk-space` is ours, which is how close this already is.
    """
    clash = shipped_plugins() & set(_bundled()["plugins"])
    assert not clash, (
        f"these plugin names are also bundled with Hermes {_bundled()['hermes_version']}: "
        f"{sorted(clash)}. Rename ours; the bundled one is not ours to move")


def test_the_bundled_list_still_matches_the_hermes_on_this_machine():
    """The recorded list is a claim about a Hermes version, and versions move.

    Skipped where no Hermes is installed, which includes CI's python image — so this
    is the half that catches a bump on a dev box or in the Hermes image, while the
    test above is the half that runs everywhere. Recorded by hand rather than
    generated on purpose: a generator would re-record whatever the new image carried
    and the collision it introduced would land silently, which is the notification the
    file exists to produce.
    """
    installed = _installed_bundled_names()
    if installed is None:
        pytest.skip("no Hermes install on this machine; the recorded list is the check")
    recorded = set(_bundled()["plugins"])
    assert installed == recorded, (
        f"Hermes' bundled plugins have changed since {_bundled()['recorded']}: "
        f"added {sorted(installed - recorded)}, gone {sorted(recorded - installed)}. "
        f"Refresh tests/contracts/hermes-bundled-plugins.json and check the additions "
        f"against hermes/plugins/ for a name collision")
