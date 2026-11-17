"""The self-extension checkout has a tree to come from, and its gate has its checkers.

``provision_selfext_checkout`` in deploy/entrypoint.sh makes the checkout from a tree baked
at ``$SEED/repo``, and until 2026-09-18 nothing baked one: every deployed image logged "the
image carries no source tree" on every start, the selfext_* tools stayed hidden, and the
four selfext benchmark tasks ran against tools the model could not see. The tree cannot come
from the build context — .dockerignore keeps it to the seed assets and carries no .git — so
the job that builds the jarvis image writes a ``git archive`` of the commit to
deploy/seed-repo.tar first and the Dockerfile ADDs it. These tests pin every link of that:
the job writes the tar before the build, the context admits it and nothing wider, the
Dockerfile unpacks it where the entrypoint looks, and git never commits it.

The second half is the gate itself. ``selfext_verify`` runs ruff, pytest, mypy and vulture
inside the checkout with the ``python3`` on PATH, which in the container is Hermes' own venv,
and the base image ships none of them: ``python3 -m ruff --version`` on production answered
``No module named ruff``, so the loop's self-check failed before reading a line. The image
installs them at the versions the pipeline pins, and this holds the two equal — a self-check
on another checker version than the merge request's gate is what let !2 be pronounced ready
and then fail py-mypy.

Not covered: that the ADD actually unpacks, or that the checkers actually run in the built
image. That is the next pipeline's build:jarvis (the build fails on a missing tar, and each
install line ends in a --version) and the integration smoke.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "Dockerfile"
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
CI = ROOT / ".gitlab-ci.yml"

#: Where the build job and `make build` write the archive, relative to the context root.
SEED_TAR = "deploy/seed-repo.tar"
#: The one command both write it with. `--prefix=repo/` is what makes the ADD land at
#: $SEED/repo, the path the entrypoint copies from.
ARCHIVE = f"git archive --format=tar --prefix=repo/ -o {SEED_TAR} HEAD"


def test_the_dockerfile_unpacks_the_seed_tar_where_the_entrypoint_looks() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    adds = re.findall(r"^ADD\s+(\S+)\s+(\S+)", text, re.M)
    assert (SEED_TAR, "/opt/jarvis/seed/") in adds, adds
    # The tar's `repo/` prefix under that destination is $SEED/repo, which is what the
    # entrypoint passes to provision_selfext_checkout; a renamed prefix or SEED would leave
    # the tree in the image and the tools hidden, with the same log line as before.
    entry = ENTRYPOINT.read_text(encoding="utf-8")
    assert re.search(r"^SEED=/opt/jarvis/seed$", entry, re.M)
    assert re.search(r'^provision_selfext_checkout\s+"[^"]+"\s+"\$SEED/repo"$', entry, re.M)
    # After the chown/chmod pass over /opt/jarvis, not before it: the tree changes with every
    # commit, and an ADD above that RUN would push 74 MB through it on every build.
    assert text.index("-exec chown root:root {} +") < text.index(f"ADD {SEED_TAR}")


def test_the_build_job_writes_the_seed_tar_before_the_jarvis_build() -> None:
    doc = yaml.safe_load(CI.read_text(encoding="utf-8"))
    script = doc[".buildx"]["script"]
    archive = [i for i, step in enumerate(script) if ARCHIVE in step]
    build = [i for i, step in enumerate(script) if "docker buildx build" in step]
    assert archive and build and archive[0] < build[0], script
    # Only the jarvis image: build:adapter and build:extract share the root context and
    # would otherwise carry the tar into contexts that never read it.
    assert '"$SVC" != jarvis' in script[archive[0]], script[archive[0]]
    assert doc["build:jarvis"]["variables"]["SVC"] == "jarvis"
    # The local build bakes the same thing, so `make build` and the pipeline agree on what
    # a checkout is: HEAD, not the working tree.
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    build_target = makefile[makefile.index("\nbuild:"):]
    assert build_target.index(ARCHIVE) < build_target.index("docker build -f deploy/Dockerfile")


def _context_admits(path: str) -> bool:
    """Whether the build context carries *path*: last matching .dockerignore line wins, a
    `!` line re-admits, a directory pattern covers everything under it."""
    # ponytail: fnmatch, not BuildKit's matcher — right for this file's patterns (`*`, dirs,
    # one file); swap in a real matcher if `**` or character classes ever appear.
    admitted = True
    for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern = line.lstrip("!").rstrip("/")
        if fnmatch.fnmatch(path, pattern) or path.startswith(pattern + "/"):
            admitted = line.startswith("!")
    return admitted


def test_the_build_context_admits_the_seed_tar_and_not_the_repository() -> None:
    assert _context_admits(SEED_TAR)
    # The tree reaches the image as the tar, not by widening the context: the whole point
    # of the archive is that .git and the working tree stay out.
    assert not _context_admits(".git/HEAD")
    assert not _context_admits("deploy/tests/test_selfext_seed.py")


def test_the_seed_tar_is_never_committed() -> None:
    """74 MB that changes with every commit: one `git add -A` after `make build` would land
    it in history for good."""
    proc = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", SEED_TAR])
    assert proc.returncode == 0, f"{SEED_TAR} is not ignored"


def _pins(text: str) -> dict[str, set[str]]:
    """Every `name==version` in *text*, quoted (the Dockerfile) or bare (the pipeline)."""
    out: dict[str, set[str]] = {}
    for name, version in re.findall(r"\b([A-Za-z][A-Za-z0-9_-]*)==([0-9][^\s\"']*)", text):
        out.setdefault(name, set()).add(version)
    return out


def test_the_image_installs_every_checker_selfext_verify_runs_at_the_pipelines_pin() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    image = _pins(dockerfile)
    # What the loop actually runs, read from the loop: `python3 -m <module>` in
    # _gate_commands, plus mypy through scripts/mypy-plugins.sh. A checker added to the
    # gate without an install line fails here, not on the first selfext_verify in production.
    tools = (ROOT / "hermes" / "plugins" / "aura-selfext" / "tools.py").read_text(encoding="utf-8")
    modules = set(re.findall(r'"-m",\s*"(\w+)"', tools))
    assert {"ruff", "pytest", "vulture"} <= modules, modules
    assert "exec mypy" in (ROOT / "scripts" / "mypy-plugins.sh").read_text(encoding="utf-8")
    modules.add("mypy")
    missing = modules - set(image)
    assert not missing, f"selfext_verify runs {sorted(missing)}; the image does not install them"
    # Equal to the pipeline's pins, or the loop passes its own work and CI rejects it (!2).
    ci = _pins(CI.read_text(encoding="utf-8")
               + (ROOT / ".gitlab" / "ci" / "quality-python.yml").read_text(encoding="utf-8"))
    for name in ("ruff", "mypy", "vulture"):
        assert ci[name] == image[name], f"{name}: image {image[name]} vs pipeline {ci[name]}"
    # Installed is not proven; each one prints its version in the build, like jq and yt-dlp.
    for proof in ("-m ruff --version", "mypy --version", "vulture --version", "-m pytest --version"):
        assert proof in dockerfile, f"the build must prove `{proof}`, not only install it"
