"""scripts/deploy-here.sh removes its copy of the tree however it ends.

The hand deploy it replaced left a ~1 GB deploy.XXXX directory per run (20 GB by
2026-10-09). Here git and docker are stubs: the archive is tiny and the image build fails,
which is the likeliest way a real run dies after the copy exists.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GIT = """#!/bin/sh
case "$*" in
  *rev-parse*) echo deadbeef ;;
  *archive*) d=$(mktemp -d); mkdir -p "$d/deploy" "$d/scripts/hermes-probes"; printf '  HERMES_TAG: "v0"\n' > "$d/.gitlab-ci.yml"; tar -C "$d" -cf - . ;;
esac
"""
DOCKER = """#!/bin/sh
case "$1" in
  inspect) echo reg/jarvis:old1234 ;;
  buildx) exit 1 ;;
esac
"""


def test_a_failed_build_leaves_no_copy_of_the_tree(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("git", GIT), ("docker", DOCKER)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    host, tmp = tmp_path / "host", tmp_path / "tmp"
    host.mkdir()
    tmp.mkdir()
    env_file = host / ("." + "env")
    env_file.write_text("AURA_REGISTRY=reg\nJARVIS_TAG=old1234\n")
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "TMPDIR": str(tmp),
           "AURA_HOST_DIR": str(host)}
    run = subprocess.run(["bash", str(ROOT / "scripts/deploy-here.sh")], env=env,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode != 0, run.stderr
    assert "build jarvis:deadbeef" in run.stderr
    assert not list(tmp.glob("deploy.*")), "the temp copy outlived a failed deploy"
    # The legacy key the host's .env still has: the deploy rewrites whichever key is there (T-0959).
    assert env_file.read_text().endswith("JARVIS_TAG=old1234\n"), "a failed build moved the tag"


def test_every_image_builds_from_a_folder_the_tree_has():
    """Each service's Dockerfile and build context is a path in this tree.

    T-1517 moved jarvis-web/ to aura-web/ and the script kept `ctx=jarvis-web`: the stubbed
    build above never reads the context, so only the real deploy found it ("path jarvis-web
    not found"). Not covered: whether the Dockerfile builds from that context.
    """
    script = (ROOT / "scripts/deploy-here.sh").read_text()
    rows = re.findall(r"^\s+([\w-]+)\)\s+df=(\S+) ctx=([^\s;]+)", script, re.M)
    assert {svc for svc, _, _ in rows} >= {"jarvis", "jarvis-adapter", "jarvis-web", "jarvis-extract"}
    for svc, df, ctx in rows:
        assert (ROOT / df).is_file(), f"{svc}: no Dockerfile at {df}"
        assert (ROOT / ctx).is_dir(), f"{svc}: no build context folder {ctx}"


def test_the_voice_and_model_images_build_as_latest_only_when_named(tmp_path):
    """They run as :latest, so a deploy that does not name them must leave the running one.

    Naming jarvis-models (T-0462: its seed's captureBuffer: 0) builds it as :latest; the app
    images are retagged from the running tag as before, and jarvis-cosyvoice is not touched.
    Not covered: the compose up that follows (the stubbed build fails, ending the run there).
    """
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "docker.log"
    docker = DOCKER.replace('case "$1" in', f'echo "$*" >> {calls}\ncase "$1" in')
    for name, body in (("git", GIT), ("docker", docker)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    host, tmp = tmp_path / "host", tmp_path / "tmp"
    host.mkdir()
    tmp.mkdir()
    (host / ("." + "env")).write_text("AURA_REGISTRY=reg\nJARVIS_TAG=old1234\n")
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "TMPDIR": str(tmp),
           "AURA_HOST_DIR": str(host)}
    run = subprocess.run(["bash", str(ROOT / "scripts/deploy-here.sh"), "HEAD", "jarvis-models"],
                         env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode != 0, run.stderr
    log = calls.read_text()
    assert "build jarvis-models:latest" in run.stderr
    assert "-t reg/jarvis-models:latest" in log and "-f deploy/models/Dockerfile" in log
    for svc in ("jarvis", "jarvis-adapter", "jarvis-web", "jarvis-extract"):
        assert f"tag reg/{svc}:old1234 reg/{svc}:deadbeef" in log
    assert "jarvis-cosyvoice" not in log
