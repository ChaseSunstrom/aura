"""process-audio.sh, with sox stubbed on PATH.

The stub is told what to do through environment variables, so every exit code the
header promises is driven on a host with no sox; the one test that needs the real tool
skips with the reason when it is absent. Not covered: what sox makes of a format it
was built without, which is its exit 2 and the script's exit 4 either way.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "process-audio.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def tools(tmp_path: Path) -> Path:
    """sox writes its second argument; `sox --i` answers a duration, rate and channel count."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "sox", '''
if [ "$1" = --i ]; then
  case "$2" in -D) echo "${STUB_DURATION:-3.25}" ;; -r) echo 16000 ;; -c) echo 1 ;; esac; exit 0
fi
echo "$@" > "${STUB_LOG:-/dev/null}"
out="$2"
if [ -n "${STUB_FAIL:-}" ]; then printf 'partial' > "$out"; exit 2; fi
if [ -n "${STUB_EMPTY:-}" ]; then : > "$out"; else printf 'RIFF' > "$out"; fi
''')
    return bin_dir


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    (root / "in.wav").write_bytes(b"in")
    return root


def run(tools: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", **(env or {})}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=full, timeout=30)


def roots(work: Path, **more: str) -> dict[str, str]:
    return {"AURA_DATA_ROOTS": str(work), **more}


def test_success_is_one_line_of_json_and_the_effects_are_in_sox_order(tools, work, tmp_path):
    log = tmp_path / "sox.log"
    r = run(tools, "--in", str(work / "in.wav"), "--out", str(work / "voice" / "out.flac"),
            "--start", "5", "--duration", "30", "--channels", "1", "--rate", "16000", "--normalise",
            env=roots(work, STUB_LOG=str(log)))
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1, "stdout is one JSON line and nothing else"
    body = json.loads(lines[0])
    assert body["ok"] is True and body["duration_s"] == 3.25 and body["rate"] == 16000 and body["channels"] == 1
    assert body["path"] == str(work / "voice" / "out.flac") and body["bytes"] > 0
    sent = log.read_text().split()
    assert sent[:2] == [str(work / "in.wav"), str(work / "voice" / "out.flac")]
    assert sent[2:] == ["trim", "5", "30", "channels", "1", "rate", "-h", "16000", "gain", "-n"], \
        "trim first so the normalise measures what is left"


def test_the_american_spelling_and_a_trim_from_the_start(tools, work, tmp_path):
    log = tmp_path / "sox.log"
    r = run(tools, "--in", str(work / "in.wav"), "--out", str(work / "out.mp3"), "--normalize", "--duration", "10",
            env=roots(work, STUB_LOG=str(log)))
    assert r.returncode == 0, r.stderr
    assert log.read_text().split()[2:] == ["trim", "0", "10", "gain", "-n"]


@pytest.mark.parametrize("args, why", [
    ((), "required"),
    (("--nonsense",), "unknown argument"),
    (("--in", "in.wav", "--out", "out"), "needs an extension"),
    (("--in", "in.wav", "--out", "out.wav", "--rate", "fast"), "sample rate"),
    (("--in", "in.wav", "--out", "out.wav", "--channels", "0"), "1-9"),
    (("--in", "in.wav", "--out", "in.wav"), "same file"),
])
def test_usage_is_exit_1(tools, work, args, why):
    r = run(tools, *[str(work / a) if a in ("in.wav", "out.wav", "out") else a for a in args], env=roots(work))
    assert r.returncode == 1, r.stderr
    assert why in r.stderr


def test_a_missing_tool_is_exit_2_and_says_not_to_install_it(tools, work):
    (tools / "sox").unlink()
    r = run(tools, "--in", str(work / "in.wav"), "--out", str(work / "out.wav"),
            env={**roots(work), "PATH": f"{tools}:/usr/bin:/bin"})
    assert r.returncode == 2 and "do not install it at runtime" in r.stderr


def test_a_path_outside_the_shared_dirs_is_exit_3(tools, work, tmp_path):
    r = run(tools, "--in", str(work / "in.wav"), "--out", str(work / "out.wav"), env={"AURA_DATA_ROOTS": ""})
    assert r.returncode == 3 and "/opt/data/work:/opt/data/files" in r.stderr, r.stderr
    r = run(tools, "--in", str(work / "in.wav"), "--out", str(tmp_path / "out.wav"), env=roots(work))
    assert r.returncode == 3 and "--out is outside" in r.stderr
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"x")
    (work / "link.wav").symlink_to(outside)
    r = run(tools, "--in", str(work / "link.wav"), "--out", str(work / "out.wav"), env=roots(work))
    assert r.returncode == 3 and "--in is outside" in r.stderr
    r = run(tools, "--in", str(work / "missing.wav"), "--out", str(work / "out.wav"), env=roots(work))
    assert r.returncode == 3 and "does not exist" in r.stderr


def test_a_sox_failure_is_exit_4_and_leaves_no_file(tools, work):
    r = run(tools, "--in", str(work / "in.wav"), "--out", str(work / "out.wav"), env=roots(work, STUB_FAIL="1"))
    assert r.returncode == 4 and "sox could not process" in r.stderr
    assert not (work / "out.wav").exists()


@pytest.mark.parametrize("env, why", [
    ({"STUB_EMPTY": "1"}, "empty file"),
    ({"STUB_DURATION": "0"}, "read back"),
])
def test_an_output_sox_cannot_read_back_is_exit_5(tools, work, env, why):
    r = run(tools, "--in", str(work / "in.wav"), "--out", str(work / "out.wav"), env=roots(work, **env))
    assert r.returncode == 5, r.stderr
    assert why in r.stderr and not (work / "out.wav").exists()


@pytest.mark.skipif(shutil.which("sox") is None, reason="sox is not on this host; the image has it (deploy/Dockerfile)")
def test_the_real_sox_resamples_a_synthesised_tone_to_16k_mono(work):
    subprocess.run(["sox", "-n", "-r", "44100", "-c", "2", str(work / "tone.wav"), "synth", "1", "sine", "440"],
                   check=True, timeout=60)
    r = subprocess.run(["bash", str(SCRIPT), "--in", str(work / "tone.wav"), "--out", str(work / "tone16.wav"),
                        "--rate", "16000", "--channels", "1", "--normalise"],
                       capture_output=True, text=True, env={**os.environ, "AURA_DATA_ROOTS": str(work)}, timeout=60)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["rate"] == 16000 and body["channels"] == 1 and 0.9 < body["duration_s"] < 1.1


def test_the_image_ships_it_on_path_and_the_skill_calls_it_by_name():
    root = SCRIPT.parents[2]
    dockerfile = (root / "deploy" / "Dockerfile").read_text()
    assert "COPY scripts/audio /opt/jarvis/bin/audio" in dockerfile
    assert "/opt/jarvis/bin/*/*.sh" in dockerfile
    assert "!scripts/audio/" in (root / ".dockerignore").read_text()
    skill = (root / "hermes" / "skills" / "audio-process" / "SKILL.md").read_text()
    assert "process-audio.sh --in" in skill and "scripts/audio/process-audio.sh" not in skill
