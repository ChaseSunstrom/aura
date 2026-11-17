"""convert-media.sh, with ffmpeg and ffprobe stubbed on PATH.

The stubs are told what to do through environment variables, so every exit code the
header promises is driven on a host with no ffmpeg at all; the one test that needs the
real tool skips with the reason when it is absent. Not covered: what ffmpeg does with a
codec it cannot write, which is its exit 1 and the script's exit 4 either way.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "convert-media.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def tools(tmp_path: Path) -> Path:
    """ffmpeg writes its last argument; ffprobe sees a video stream unless told otherwise."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "ffmpeg", '''
echo "$@" > "${STUB_LOG:-/dev/null}"
out="${@: -1}"
if [ -n "${STUB_FAIL:-}" ]; then printf 'partial' > "$out"; exit 1; fi
if [ -n "${STUB_EMPTY:-}" ]; then : > "$out"; else printf 'not-really-media' > "$out"; fi
''')
    _stub(bin_dir, "ffprobe", '''
if [ -n "${STUB_NO_STREAM_FOR:-}" ]; then case "$*" in *codec_type*"$STUB_NO_STREAM_FOR"*) exit 0 ;; esac; fi
case "$*" in
  *a:0*) echo audio ;;
  *codec_type*) echo "${STUB_STREAM-video}" ;;
  *duration*) echo "${STUB_DURATION:-12.5}" ;;
esac
''')
    return bin_dir


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    """A shared root with one input in it; the script is pointed at it through AURA_DATA_ROOTS."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "in.mp4").write_bytes(b"in")
    return root


def run(tools: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", **(env or {})}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=full, timeout=30)


def roots(work: Path, **more: str) -> dict[str, str]:
    return {"AURA_DATA_ROOTS": str(work), **more}


def test_success_is_one_line_of_json_and_ffmpeg_got_the_trim(tools, work, tmp_path):
    log = tmp_path / "ffmpeg.log"
    r = run(tools, "--in", str(work / "in.mp4"), "--out", str(work / "out" / "clip.mkv"),
            "--start", "00:00:05", "--duration", "10", "--height", "480",
            env=roots(work, STUB_LOG=str(log)))
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1, "stdout is one JSON line and nothing else"
    body = json.loads(lines[0])
    assert body["ok"] is True and body["stream"] == "video" and body["duration_s"] == 12.5
    assert body["path"] == str(work / "out" / "clip.mkv") and body["bytes"] > 0
    sent = log.read_text()
    assert "-ss 00:00:05" in sent and "-t 10" in sent and "scale=-2:480" in sent
    assert sent.split().index("-ss") < sent.split().index("-i"), "the seek is before the input: fast seek"
    assert "-c copy" not in sent


def test_audio_only_drops_the_video_and_an_audio_input_wants_no_video(tools, work, tmp_path):
    log = tmp_path / "ffmpeg.log"
    r = run(tools, "--in", str(work / "in.mp4"), "--out", str(work / "sound.mp3"), "--audio-only", "--copy",
            env=roots(work, STUB_LOG=str(log)))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["stream"] == "audio"
    assert "-vn" in log.read_text() and "-c copy" in log.read_text()
    # An mp3 in: ffprobe sees no video stream on the input, so none is demanded of the output.
    r = run(tools, "--in", str(work / "in.mp4"), "--out", str(work / "sound.wav"), env=roots(work, STUB_STREAM=""))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["stream"] == "audio"


@pytest.mark.parametrize("args, why", [
    ((), "required"),
    (("--nonsense",), "unknown argument"),
    (("--in", "in.mp4", "--out", "out.mp4", "--copy", "--height", "480"), "cannot --height"),
    (("--in", "in.mp4", "--out", "out.mp4", "--audio-only", "--height", "480"), "nothing to --height"),
    (("--in", "in.mp4", "--out", "out"), "needs an extension"),
    (("--in", "in.mp4", "--out", "in.mp4"), "same file"),
])
def test_usage_is_exit_1(tools, work, args, why):
    # File names in the table are relative to the shared root so the failure is the usage, not a path.
    r = run(tools, *[str(work / a) if a in ("in.mp4", "out.mp4", "out") else a for a in args], env=roots(work))
    assert r.returncode == 1, r.stderr
    assert why in r.stderr


def test_a_missing_tool_is_exit_2_and_says_not_to_install_it(tools, work, tmp_path):
    (tools / "ffmpeg").unlink()
    # The stubs and a copy of the system's own bin (where bash and realpath live) with ffmpeg
    # left out. The system's bin itself held no ffmpeg on the hosts this ran on until T-1453;
    # where it does (test:media, the image), the real tool ran and the script answered 4.
    system = tmp_path / "system-bin"
    system.mkdir()
    for folder in ("/usr/bin", "/bin"):
        for tool in Path(folder).iterdir():
            if tool.name != "ffmpeg" and not (system / tool.name).exists():
                (system / tool.name).symlink_to(tool)
    r = run(tools, "--in", str(work / "in.mp4"), "--out", str(work / "out.mp4"),
            env={**roots(work), "PATH": f"{tools}:{system}"})
    assert r.returncode == 2 and "do not install it at runtime" in r.stderr


def test_a_path_outside_the_shared_dirs_is_exit_3(tools, work, tmp_path):
    # The defaults: /opt/data/work and /opt/data/files, which a pytest tmp dir is not under.
    r = run(tools, "--in", str(work / "in.mp4"), "--out", str(work / "out.mp4"), env={"AURA_DATA_ROOTS": ""})
    assert r.returncode == 3 and "/opt/data/work:/opt/data/files" in r.stderr, r.stderr
    # An output elsewhere, even with a permitted input.
    r = run(tools, "--in", str(work / "in.mp4"), "--out", str(tmp_path / "out.mp4"), env=roots(work))
    assert r.returncode == 3 and "--out is outside" in r.stderr
    # A symlink inside pointing outside is resolved and refused.
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    (work / "link.mp4").symlink_to(outside)
    r = run(tools, "--in", str(work / "link.mp4"), "--out", str(work / "out.mp4"), env=roots(work))
    assert r.returncode == 3 and "--in is outside" in r.stderr
    # A permitted path that does not exist.
    r = run(tools, "--in", str(work / "missing.mp4"), "--out", str(work / "out.mp4"), env=roots(work))
    assert r.returncode == 3 and "does not exist" in r.stderr


def test_an_ffmpeg_failure_is_exit_4_and_leaves_no_file(tools, work):
    r = run(tools, "--in", str(work / "in.mp4"), "--out", str(work / "out.mp4"), env=roots(work, STUB_FAIL="1"))
    assert r.returncode == 4 and "ffmpeg could not convert" in r.stderr
    assert not (work / "out.mp4").exists(), "a half-written output is what a caller would publish"


@pytest.mark.parametrize("env, why", [
    ({"STUB_EMPTY": "1"}, "empty file"),
    ({"STUB_NO_STREAM_FOR": "out.mp4"}, "no video stream"),
])
def test_an_output_that_is_not_what_was_asked_is_exit_5(tools, work, env, why):
    r = run(tools, "--in", str(work / "in.mp4"), "--out", str(work / "out.mp4"), env=roots(work, **env))
    assert r.returncode == 5, r.stderr
    assert why in r.stderr and not (work / "out.mp4").exists()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are not both on this host; the image has them (deploy/Dockerfile)",
)
def test_the_real_ffmpeg_extracts_audio_from_a_synthesised_clip(work):
    clip = work / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10",
                    "-f", "lavfi", "-i", "sine=duration=1", "-shortest", str(clip)], check=True, timeout=60)
    r = subprocess.run(["bash", str(SCRIPT), "--in", str(clip), "--out", str(work / "clip.wav"), "--audio-only"],
                       capture_output=True, text=True, env={**os.environ, "AURA_DATA_ROOTS": str(work)}, timeout=60)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["stream"] == "audio" and 0.5 < body["duration_s"] < 1.5


def test_the_image_ships_it_on_path_and_the_skill_calls_it_by_name():
    root = SCRIPT.parents[2]
    dockerfile = (root / "deploy" / "Dockerfile").read_text()
    assert "COPY scripts/media /opt/jarvis/bin/media" in dockerfile
    assert "/opt/jarvis/bin/*/*.sh" in dockerfile, "every scripts/<area>/*.sh is linked into /usr/local/bin"
    skill = (root / "hermes" / "skills" / "media-convert" / "SKILL.md").read_text()
    assert "convert-media.sh --in" in skill and "scripts/media/convert-media.sh" not in skill
