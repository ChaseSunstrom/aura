"""scripts/deploy-here.sh refuses a console over its size budget (T-0797).

T-0796: the three.js chunk went over hud-size's budget and shipped, because hud-size is
optional to the pipeline's later stages and every manual deploy went around CI. Here git,
docker and node are stubs: docker "runs" the built web image by printing a tar of its
build/client, node stands for hud-size.mjs and exits with HUD_SIZE_RC.

Not covered: the real hud-size.mjs's arithmetic (its own gate, `make gate-hud`); a real image.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GIT = """#!/bin/sh
case "$*" in
  *rev-parse*) echo deadbeef ;;
  *archive*) d=$(mktemp -d); mkdir -p "$d/deploy" "$d/scripts/hermes-probes" "$d/scripts/verify"; printf '  HERMES_TAG: "v0"\n' > "$d/.gitlab-ci.yml"; tar -C "$d" -cf - . ;;
esac
"""
DOCKER = """#!/bin/sh
case "$1" in
  inspect) echo reg/jarvis:old1234 ;;
  run) [ "${DOCKER_RUN_RC:-0}" = 0 ] || exit 1; d=$(mktemp -d); mkdir -p "$d/client/_app"; echo x > "$d/client/_app/three.js"; tar -C "$d" -cf - client ;;
esac
"""
NODE = """#!/bin/sh
echo "$*" >> "$NODE_LOG"
echo "FAIL web_client_largest_gzip  310.0 KB  _app/immutable/chunks/three.js"
exit "${HUD_SIZE_RC:-0}"
"""


def _deploy(tmp_path: Path, rc: int, *args: str, run_rc: int = 0, svc: str = "jarvis-web"):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("git", GIT), ("docker", DOCKER), ("node", NODE)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    host, tmp = tmp_path / "host", tmp_path / "tmp"
    host.mkdir()
    tmp.mkdir()
    (host / ("." + "env")).write_text("AURA_REGISTRY=reg\nJARVIS_TAG=old1234\n")
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "TMPDIR": str(tmp),
           "AURA_HOST_DIR": str(host), "HUD_SIZE_RC": str(rc), "DOCKER_RUN_RC": str(run_rc),
           "NODE_LOG": str(tmp_path / "node.log")}
    run = subprocess.run(["bash", str(ROOT / "scripts/deploy-here.sh"), *args, "HEAD", svc],
                         env=env, capture_output=True, text=True, timeout=60)
    assert not list(tmp.glob("deploy.*")), "the temp copy outlived the deploy"
    return run


def test_deploy_here_size_over_budget_stops_naming_the_chunk(tmp_path):
    run = _deploy(tmp_path, 1)
    assert run.returncode != 0, run.stderr
    assert "three.js" in run.stderr and "size budget" in run.stderr
    assert "--allow-over-budget" in run.stderr
    assert "sync deploy/" not in run.stderr, "went on to ship an over-budget console"


def test_deploy_here_size_flag_goes_on_and_logs_it(tmp_path):
    run = _deploy(tmp_path, 1, "--allow-over-budget")
    assert "going on" in run.stderr and "--allow-over-budget" in run.stderr
    assert "sync deploy/" in run.stderr


def test_deploy_here_size_within_budget_goes_on_quietly(tmp_path):
    run = _deploy(tmp_path, 0)
    assert "sync deploy/" in run.stderr
    assert "going on" not in run.stderr
    assert "hud-size.mjs" in (tmp_path / "node.log").read_text()


def test_deploy_here_size_a_console_it_cannot_read_out_of_the_image_is_refused(tmp_path):
    run = _deploy(tmp_path, 0, run_rc=1)
    assert run.returncode != 0, run.stderr
    assert "sync deploy/" not in run.stderr
    assert not (tmp_path / "node.log").exists()


def test_deploy_here_size_is_not_run_when_the_console_is_not_rebuilt(tmp_path):
    run = _deploy(tmp_path, 1, svc="jarvis-adapter")
    assert "size budget" not in run.stderr
    assert not (tmp_path / "node.log").exists()
