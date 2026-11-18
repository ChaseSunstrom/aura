"""The private-import probe passed on the Hermes the image is built from, with today's imports.

The plugins reach into Hermes' private modules (``tools.browser_tool``, ``tools.environments``),
and no pin or lockfile covers that: the v2026.9.24 bump passed /health and broke at the first
goal, and the v0.23 bump moved the browser's session API out from under aura-activity (T-1519).
``scripts/verify/hermes-imports.py IMAGE --record`` runs every such import in the image and
writes ``deploy/hermes-imports.lock.json``. This test fails when the Dockerfile's
``HERMES_TAG`` or the set of private imports moved since the last recorded pass, so a bump or
a new private import cannot reach CI's build without the probe having run on it.

Not covered: an import that resolves while the symbol's behaviour changed (the deploy smoke
decides that); the probe itself needs Docker, so CI checks the record, not the image.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "deploy" / "hermes-imports.lock.json"

_spec = importlib.util.spec_from_file_location("hermes_imports", ROOT / "scripts" / "verify" / "hermes-imports.py")
assert _spec and _spec.loader
hermes_imports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hermes_imports)


def _pinned_tag() -> str:
    found = re.search(r"^ARG HERMES_TAG=(\S+)$", (ROOT / "deploy" / "Dockerfile").read_text(), re.M)
    assert found, "deploy/Dockerfile names no HERMES_TAG"
    return found.group(1)


def test_hermes_imports_lock_names_the_tag_the_image_is_built_from():
    lock = json.loads(LOCK.read_text())
    tag = _pinned_tag()
    assert lock["image"] == f"nousresearch/hermes-agent:{tag}", (
        f"re-run: python3 scripts/verify/hermes-imports.py nousresearch/hermes-agent:{tag} --record")
    ci = re.search(r'^  HERMES_TAG: "(\S+)"$', (ROOT / ".gitlab-ci.yml").read_text(), re.M)
    assert ci and ci.group(1) == tag, "CI builds the tag the Dockerfile names"


def test_hermes_imports_lock_covers_every_private_import_the_plugins_make_and_all_resolved():
    lock = json.loads(LOCK.read_text())
    lines = sorted({s for alts in hermes_imports.statements().values() for s in alts})
    assert lock["imports"] == lines, (
        "a plugin's private Hermes imports changed since the probe ran: "
        "python3 scripts/verify/hermes-imports.py <image> --record")
    assert lock["broken"] == [], f"the probe recorded broken imports: {lock['broken']}"
