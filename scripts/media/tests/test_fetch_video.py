"""fetch-video.sh, with every tool it calls stubbed on PATH.

The media brief says each script in the library ships with a test; this one had
none. The stubs stand in for yt-dlp, ffprobe, ffmpeg and curl and are told what to
do through files in the temp dir, so each exit code the header promises is driven
without a network, a codec or an artifact store.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "fetch-video.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\nset -e\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def tools(tmp_path: Path) -> Path:
    """Stubs that behave: a download makes a file, ffprobe sees video, ffmpeg makes a frame, curl answers an id."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "yt-dlp", '''
out=""; title=""
while [ $# -gt 0 ]; do case "$1" in --output) out="$2"; shift 2;; --print-to-file) title="$3"; shift 3;; *) shift;; esac; done
[ -n "$STUB_NO_FILE" ] && exit 0
[ -n "$STUB_FAIL_DOWNLOAD" ] && { echo "ERROR: unavailable" >&2; exit 1; }
if [ -n "$STUB_EMPTY" ]; then : > "${out/\\%(ext)s/mp4}"; else printf 'not-really-video-bytes' > "${out/\\%(ext)s/mp4}"; fi
echo "A Test Video" > "$title"
''')
    _stub(bin_dir, "ffprobe", '''
case "$*" in
  *codec_type*) echo "${STUB_STREAM:-video}" ;;
  *duration*) echo "${STUB_DURATION:-212.4}" ;;
esac
''')
    _stub(bin_dir, "ffmpeg", '''
[ -n "$STUB_NO_FRAME" ] && exit 1
for a in "$@"; do case "$a" in *.jpg) printf 'JPEG' > "$a";; esac; done
''')
    _stub(bin_dir, "curl", '''
echo "$@" > "${STUB_CURL_LOG:-/dev/null}"
[ -n "$STUB_PUBLISH_FAIL" ] && exit 22
echo '{"id":"'"$(printf 'a%.0s' $(seq 1 64))"'","title":"A Test Video"}'
''')
    return bin_dir


def run(tools: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "AURA_API_KEY": "k" * 20, **(env or {})}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=full, timeout=30)


def test_success_is_one_line_of_json_with_the_artifact_id(tools, tmp_path):
    log = tmp_path / "curl.log"
    r = run(tools, "--url", "https://example.test/v", "--quality", "480", env={"STUB_CURL_LOG": str(log)})
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1, "stdout is one JSON line and nothing else"
    body = json.loads(lines[0])
    assert body["ok"] is True and body["id"] == "a" * 64 and body["quality"] == 480
    assert body["title"] == "A Test Video" and body["duration_s"] == 212.4
    assert body["url"].endswith("/api/artifacts/" + "a" * 64)
    sent = log.read_text()
    assert "x-artifact-mime: video/mp4" in sent and "x-artifact-source-url: https://example.test/v" in sent


def test_usage_is_exit_1(tools):
    assert run(tools).returncode == 1
    assert run(tools, "--nonsense").returncode == 1


def test_a_missing_tool_is_exit_2_and_says_not_to_install_it(tools):
    (tools / "yt-dlp").unlink()
    # The stubs and the system's own bin, where bash lives and yt-dlp does not.
    r = run(tools, "--url", "https://example.test/v", env={"PATH": f"{tools}:/usr/bin:/bin"})
    assert r.returncode == 2 and "do not install it at runtime" in r.stderr


def test_a_failed_download_is_exit_3(tools):
    assert run(tools, "--url", "https://example.test/v", env={"STUB_FAIL_DOWNLOAD": "1"}).returncode == 3
    assert run(tools, "--url", "https://example.test/v", env={"STUB_NO_FILE": "1"}).returncode == 3


@pytest.mark.parametrize("env, why", [
    ({"STUB_EMPTY": "1"}, "empty"),
    ({"STUB_STREAM": "audio"}, "no video stream"),
    ({"STUB_DURATION": "0.5"}, "not a video"),
    ({"STUB_NO_FRAME": "1"}, "no decodable frame"),
])
def test_a_file_that_is_not_a_video_is_exit_4(tools, env, why):
    r = run(tools, "--url", "https://example.test/v", env=env)
    assert r.returncode == 4, r.stderr
    assert why in r.stderr


def test_a_publish_failure_is_exit_5(tools):
    r = run(tools, "--url", "https://example.test/v", env={"STUB_PUBLISH_FAIL": "1"})
    assert r.returncode == 5 and "publishing" in r.stderr


def test_a_search_is_one_result(tools, tmp_path):
    log = tmp_path / "curl.log"
    r = run(tools, "--search", "funny meme compilation", env={"STUB_CURL_LOG": str(log)})
    assert r.returncode == 0, r.stderr
    assert "fetch-video: downloading ytsearch1:funny meme compilation" in r.stderr
    assert "x-artifact-source-url" not in log.read_text(), "a search has no source URL to record"


def test_the_image_ships_it_on_path_and_the_skill_calls_it_by_name():
    root = SCRIPT.parents[2]
    dockerfile = (root / "deploy" / "Dockerfile").read_text()
    assert "COPY scripts/media /opt/jarvis/bin/media" in dockerfile
    assert '/usr/local/bin/$(basename "$f")' in dockerfile
    assert "!scripts/media/" in (root / ".dockerignore").read_text()
    skill = (root / "hermes" / "skills" / "media-fetch" / "SKILL.md").read_text()
    # The skill names the `media_fetch` tool, which runs this script in the gateway: the
    # model's shell has no network (ADR-0045), so a skill that told it to run the script
    # would send it to a `curl` that reaches nothing. The script's flags are the tool's.
    assert "media_fetch" in skill and "fetch-video.sh" in skill
    assert "fetch-video.sh --url" not in skill and "scripts/media/fetch-video.sh" not in skill
