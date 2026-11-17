"""Put ``aura-schema`` on the path before pytest collects a plugin package.

Each plugin's ``tests/`` is a package, so pytest imports ``plugins/<name>/__init__.py``
*before* the test module runs — before the test's own ``sys.path`` line and before
``_plugin_loader`` can add anything. Every plugin now starts with
``from aura_schema import from_env`` (V2 D17), which would therefore fail at
collection on a checkout where the package is not pip-installed (CI and the images
install it; a developer's ``.venv`` may not). The sibling tree goes on the path here,
after any installed copy, which then wins.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "aura-schema"
if _SCHEMA_DIR.is_dir() and str(_SCHEMA_DIR) not in sys.path:
    sys.path.append(str(_SCHEMA_DIR))

# The installed Hermes, when this checkout has one beside it. Three plugin
# behaviours are only *provable* against the real runtime rather than against a
# fake: that `agent.redact` masks the credential shapes this deployment issues,
# that `register_redaction_patterns` accepts the two extra ones jarvis-guard
# adds, and that `RedactingFormatter` therefore masks them in log lines too. The
# plugins themselves never import Hermes at module scope (every import is lazy,
# inside a function), so the suite still runs green on a checkout with no Hermes
# — those tests skip instead, which is why this appends rather than requires.
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
#: The install the Hermes-dependent tests of this run import, or None when there is none.
HERMES_INSTALL: Path | None = None
for _candidate in (Path(os.environ.get("HERMES_INSTALL_DIR", "")), _HERMES_HOME / "hermes-agent"):
    if _candidate.name and (_candidate / "agent" / "redact.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.append(str(_candidate))
        HERMES_INSTALL = _candidate
        break

_SHA = re.compile(r"[0-9a-f]{40}")
_CI_FILE = Path(__file__).resolve().parents[1] / ".gitlab-ci.yml"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def hermes_release(install: Path) -> str | None:
    """The release tag a Hermes checkout stands on, read from its git files; None when it is not a
    git checkout or its commit is no release.

    Read, not asked of git: the pipeline's slim images have no git, and git refuses a checkout
    another user owns. Three files say it: ``HEAD`` (a commit, or a branch looked up loose and
    then packed), ``packed-refs`` (a tag's line, and for an annotated tag the peeled commit on
    the line after it) and ``refs/tags/*`` (a lightweight tag is its commit). Not covered: an
    annotated tag that is not packed (its commit is inside the tag object, which is not read),
    and a release installed without its git folder; both read as unknown, never as a guess."""
    git = install / ".git"
    packed = _read(git / "packed-refs").splitlines()
    head = _read(git / "HEAD").strip()
    if head.startswith("ref: "):
        ref = head[5:].strip()
        head = _read(git / ref).strip() or next((ln.split()[0] for ln in packed if ln.endswith(" " + ref)), "")
    if not _SHA.fullmatch(head):
        return None
    tags = set()
    for at, line in enumerate(packed):
        sha, _, ref = line.partition(" ")
        if not ref.startswith("refs/tags/"):
            continue
        peeled = packed[at + 1][1:].strip() if at + 1 < len(packed) and packed[at + 1].startswith("^") else sha
        if peeled == head:
            tags.add(ref[len("refs/tags/"):])
    try:
        tags.update(p.name for p in (git / "refs" / "tags").iterdir() if _read(p).strip() == head)
    except OSError:
        pass
    return max(tags) if tags else None


def pinned_release() -> str | None:
    """``HERMES_TAG`` as the pipeline has it: the release the image is built on and `test:hermes` runs in."""
    found = re.search(r'^\s+HERMES_TAG:\s*"([^"]+)"', _read(_CI_FILE), re.M)
    return found.group(1) if found else None


def release_line(install: Path | None, pinned: str | None, image_tag: str | None = None) -> str | None:
    """What a run says when the Hermes it imported is not the pinned release; None when there is
    nothing to say (no install, no pin, or the same release).

    ``image_tag`` is the job's own ``HERMES_TAG``: the pipeline's Hermes image carries no git
    folder, so its release reads as unknown, and a job running in the pinned image is on the
    pinned release by construction. A release that *was* read and differs is always said."""
    if install is None or pinned is None:
        return None
    found = hermes_release(install)
    if found == pinned or (found is None and image_tag == pinned):
        return None
    try:
        where = "~/" + str(install.relative_to(Path.home()))
    except ValueError:
        where = str(install)
    return (f"Hermes found at {where} is {found or 'an unknown release'}; production is built on {pinned} "
            f"(HERMES_TAG). The tests that need Hermes ran against the found one: only the pipeline's "
            f"test:hermes proves them on {pinned}.")


def pytest_terminal_summary(terminalreporter) -> None:
    """At the end, not in the header: `make test` runs with -q, which prints no header."""
    line = release_line(HERMES_INSTALL, pinned_release(), os.environ.get("HERMES_TAG"))
    if line:
        terminalreporter.write_line(line, yellow=True)


@pytest.fixture(autouse=True)
def _environ_restored():
    """Importing the installed Hermes (``tui_gateway.server``) runs ``load_hermes_dotenv``,
    which writes ``~/.hermes/.env`` into ``os.environ`` with override. monkeypatch cannot
    undo a write it did not make, so every later test saw the developer's settings:
    ``test_the_house_suffix…`` failed after the artifact delivery tests on a box whose
    ``.env`` still named the tailnet (2026-10-29). CI has no ``~/.hermes`` and never saw it.
    Not covered: the test that triggers the import still runs with that environment."""
    saved = dict(os.environ)
    yield
    if dict(os.environ) != saved:
        os.environ.clear()
        os.environ.update(saved)
