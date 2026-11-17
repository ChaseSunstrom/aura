"""edit-image.sh, with magick stubbed on PATH.

The stub is told what to do through environment variables, so every exit code the
header promises is driven on a host with no ImageMagick; the one test that needs the
real tool skips with the reason when it is absent. Not covered: what magick makes of a
geometry it does not understand, which is its exit 1 and the script's exit 4 either way.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "edit-image.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def tools(tmp_path: Path) -> Path:
    """magick writes its last argument; `magick identify` reports a size unless told the file is bad."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "magick", '''
if [ "$1" = identify ]; then
  [ -z "${STUB_BAD_IMAGE:-}" ] || exit 1
  echo "${STUB_DIMS:-640 480}"; exit 0
fi
echo "$@" > "${STUB_LOG:-/dev/null}"
out="${@: -1}"
if [ -n "${STUB_FAIL:-}" ]; then printf 'partial' > "$out"; exit 1; fi
if [ -n "${STUB_EMPTY:-}" ]; then : > "$out"; else printf 'PNG' > "$out"; fi
''')
    return bin_dir


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    (root / "in.png").write_bytes(b"in")
    return root


def run(tools: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", **(env or {})}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=full, timeout=30)


def roots(work: Path, **more: str) -> dict[str, str]:
    return {"AURA_DATA_ROOTS": str(work), **more}


def test_success_is_one_line_of_json_and_the_operations_run_in_order(tools, work, tmp_path):
    log = tmp_path / "magick.log"
    r = run(tools, "--in", str(work / "in.png"), "--out", str(work / "small" / "out.jpg"),
            "--resize", "50%", "--crop", "100x100+10+10", "--rotate", "90", "--quality", "85", "--strip",
            env=roots(work, STUB_LOG=str(log), STUB_DIMS="320 240"))
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1, "stdout is one JSON line and nothing else"
    body = json.loads(lines[0])
    assert body["ok"] is True and body["width"] == 320 and body["height"] == 240
    assert body["path"] == str(work / "small" / "out.jpg") and body["bytes"] > 0
    sent = log.read_text().split()
    assert sent[0] == str(work / "in.png") and sent[-1] == str(work / "small" / "out.jpg")
    assert sent.index("-crop") < sent.index("-resize") < sent.index("-rotate"), "crop, then scale, then turn"
    assert "+repage" in sent and "-strip" in sent and "-quality" in sent


def test_a_plain_conversion_passes_no_operations(tools, work, tmp_path):
    log = tmp_path / "magick.log"
    r = run(tools, "--in", str(work / "in.png"), "--out", str(work / "out.webp"), env=roots(work, STUB_LOG=str(log)))
    assert r.returncode == 0, r.stderr
    assert log.read_text().split() == [str(work / "in.png"), str(work / "out.webp")]


@pytest.mark.parametrize("args, why", [
    ((), "required"),
    (("--nonsense",), "unknown argument"),
    (("--in", "in.png", "--out", "out"), "needs an extension"),
    (("--in", "in.png", "--out", "out.png", "--quality", "0"), "1-100"),
    (("--in", "in.png", "--out", "out.png", "--quality", "101"), "1-100"),
    (("--in", "in.png", "--out", "in.png"), "same file"),
])
def test_usage_is_exit_1(tools, work, args, why):
    r = run(tools, *[str(work / a) if a in ("in.png", "out.png", "out") else a for a in args], env=roots(work))
    assert r.returncode == 1, r.stderr
    assert why in r.stderr


def test_a_missing_tool_is_exit_2_and_says_not_to_install_it(tools, work):
    (tools / "magick").unlink()
    r = run(tools, "--in", str(work / "in.png"), "--out", str(work / "out.png"),
            env={**roots(work), "PATH": f"{tools}:/usr/bin:/bin"})
    assert r.returncode == 2 and "do not install it at runtime" in r.stderr


def test_a_path_outside_the_shared_dirs_is_exit_3(tools, work, tmp_path):
    r = run(tools, "--in", str(work / "in.png"), "--out", str(work / "out.png"), env={"AURA_DATA_ROOTS": ""})
    assert r.returncode == 3 and "/opt/data/work:/opt/data/files" in r.stderr, r.stderr
    r = run(tools, "--in", str(work / "in.png"), "--out", str(tmp_path / "out.png"), env=roots(work))
    assert r.returncode == 3 and "--out is outside" in r.stderr
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"x")
    (work / "link.png").symlink_to(outside)
    r = run(tools, "--in", str(work / "link.png"), "--out", str(work / "out.png"), env=roots(work))
    assert r.returncode == 3 and "--in is outside" in r.stderr
    r = run(tools, "--in", str(work / "missing.png"), "--out", str(work / "out.png"), env=roots(work))
    assert r.returncode == 3 and "does not exist" in r.stderr


def test_a_magick_failure_is_exit_4_and_leaves_no_file(tools, work):
    r = run(tools, "--in", str(work / "in.png"), "--out", str(work / "out.png"), env=roots(work, STUB_FAIL="1"))
    assert r.returncode == 4 and "magick could not edit" in r.stderr
    assert not (work / "out.png").exists()


@pytest.mark.parametrize("env, why", [
    ({"STUB_EMPTY": "1"}, "empty file"),
    ({"STUB_BAD_IMAGE": "1"}, "read back"),
])
def test_an_output_magick_cannot_read_back_is_exit_5(tools, work, env, why):
    r = run(tools, "--in", str(work / "in.png"), "--out", str(work / "out.png"), env=roots(work, **env))
    assert r.returncode == 5, r.stderr
    assert why in r.stderr and not (work / "out.png").exists()


@pytest.mark.skipif(shutil.which("magick") is None, reason="magick is not on this host; the image has it (deploy/Dockerfile)")
def test_the_real_magick_resizes_a_generated_image(work):
    subprocess.run(["magick", "-size", "64x48", "xc:red", str(work / "red.png")], check=True, timeout=60)
    r = subprocess.run(["bash", str(SCRIPT), "--in", str(work / "red.png"), "--out", str(work / "red.jpg"), "--resize", "50%", "--strip"],
                       capture_output=True, text=True, env={**os.environ, "AURA_DATA_ROOTS": str(work)}, timeout=60)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert (body["width"], body["height"]) == (32, 24)


def test_the_image_ships_it_on_path_and_the_skill_calls_it_by_name():
    root = SCRIPT.parents[2]
    dockerfile = (root / "deploy" / "Dockerfile").read_text()
    assert "COPY scripts/image /opt/jarvis/bin/image" in dockerfile
    assert "/opt/jarvis/bin/*/*.sh" in dockerfile
    assert "!scripts/image/" in (root / ".dockerignore").read_text()
    skill = (root / "hermes" / "skills" / "image-edit" / "SKILL.md").read_text()
    assert "edit-image.sh --in" in skill and "scripts/image/edit-image.sh" not in skill
