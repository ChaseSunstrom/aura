"""`scripts/media/fetch-video.sh`, driven with fake tools on PATH.

The brief asks the scripts library to be *"parameterized, tested, idempotent
executables — real scripts with argument parsing, error handling and exit codes,
not snippets pasted into a shell"*, and each one ships with a test.

Nothing here touches the network. `yt-dlp`, `ffprobe`, `ffmpeg` and `curl` are
shell stubs, which is what makes the *verification* path testable at all: the
interesting cases are downloads that succeeded and produced something that is not
a video, and those are hard to obtain on purpose from the real internet and
trivial to stage here.

The exit codes are the contract — 1 usage, 2 a tool is missing, 3 the download
failed, 4 not what was asked for, 5 publishing failed — because a caller that
cannot tell "no video stream" from "network down" retries the wrong one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "media" / "fetch-video.sh"

#: A 64-char lowercase hex digest, the shape the store returns. Synthetic.
FAKE_ID = "a1" * 32

#: A yt-dlp stub that produces what the script then inspects.
_YTDLP_OK = (
    'prev=""; out=""\n'
    'for a in "$@"; do case "$prev" in --output) out="$a";; esac; prev="$a"; done\n'
    'dir="$(dirname "$out")"\n'
    'printf "fake video bytes" > "$dir/video.mp4"\n'
    'printf "A Real Video" > "$dir/title.txt"\n'
)


def stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    p.chmod(0o755)


@pytest.fixture
def tools(tmp_path: Path) -> Path:
    """A PATH where every tool exists and everything succeeds."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "yt-dlp", _YTDLP_OK)
    stub(bin_dir, "ffprobe", (
        'case "$*" in\n'
        '  *codec_type*) echo video ;;\n'
        '  *duration*)   echo 12.5 ;;\n'
        'esac\n'
    ))
    # ffmpeg writes the extracted frame the script requires to be non-empty.
    stub(bin_dir, "ffmpeg", 'for a in "$@"; do case "$a" in *frame.jpg) printf jpeg > "$a";; esac; done')
    stub(bin_dir, "curl", f"echo '{{\"id\":\"{FAKE_ID}\",\"size\":16}}'")
    return bin_dir


def run(tools: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    environ = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "AURA_API_KEY": "k",
        "AURA_ADAPTER_BASE": "http://adapter.test",
        "AURA_PUBLIC_URL": "http://jarvis.tail:8643",
        **env,
    }
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, env=environ, timeout=30
    )


class TestArguments:
    def test_nothing_asked_for_is_a_usage_error(self, tools: Path) -> None:
        r = run(tools)
        assert r.returncode == 1 and "one of --url or --search" in r.stderr

    def test_both_at_once_is_a_usage_error(self, tools: Path) -> None:
        r = run(tools, "--url", "u", "--search", "s")
        assert r.returncode == 1 and "mutually exclusive" in r.stderr

    def test_an_unknown_flag_is_refused_rather_than_ignored(self, tools: Path) -> None:
        r = run(tools, "--url", "u", "--depth", "3")
        assert r.returncode == 1 and "unknown argument: --depth" in r.stderr


class TestPreflight:
    def test_a_missing_tool_names_the_merge_request_and_never_installs(self, tools: Path) -> None:
        """Part 5's rule, enforced by the script that would otherwise want to break it."""
        (tools / "yt-dlp").unlink()
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 2
        assert "merge request" in r.stderr
        assert "do not install it at runtime" in r.stderr

    def test_no_key_is_refused_before_anything_is_downloaded(self, tools: Path) -> None:
        r = run(tools, "--url", "http://x/y", AURA_API_KEY="")
        assert r.returncode != 0 and "AURA_API_KEY" in r.stderr


class TestVerification:
    """A download can succeed and produce something that is not a video.

    These are the cases the frame-extraction check is kept for, and every one of
    them leaves a file that exists.
    """

    def test_a_download_that_produces_no_file_is_a_download_failure(self, tools: Path) -> None:
        stub(tools, "yt-dlp", "exit 0")
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 3 and "produced no file" in r.stderr

    def test_yt_dlp_failing_is_reported_as_such(self, tools: Path) -> None:
        stub(tools, "yt-dlp", "exit 1")
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 3 and "could not download" in r.stderr

    def test_an_html_error_page_has_no_video_stream(self, tools: Path) -> None:
        stub(tools, "ffprobe", 'case "$*" in *codec_type*) echo "" ;; *duration*) echo 0 ;; esac')
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 4 and "no video stream" in r.stderr

    def test_an_audio_only_download_is_refused(self, tools: Path) -> None:
        stub(tools, "ffprobe", 'case "$*" in *codec_type*) echo audio ;; *duration*) echo 30 ;; esac')
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 4 and "no video stream" in r.stderr

    def test_a_one_frame_stub_is_not_a_video(self, tools: Path) -> None:
        stub(tools, "ffprobe", 'case "$*" in *codec_type*) echo video ;; *duration*) echo 0.2 ;; esac')
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 4 and "not a video" in r.stderr

    def test_a_stream_that_does_not_decode_is_refused(self, tools: Path) -> None:
        """A container can hold a video stream with no decodable frame."""
        stub(tools, "ffmpeg", "exit 1")
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 4 and "no decodable frame" in r.stderr


class TestPublish:
    def test_the_happy_path_prints_one_json_line_with_the_id(self, tools: Path) -> None:
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 0, r.stderr
        assert len(r.stdout.strip().splitlines()) == 1, "one line, so a caller reads a field"
        out = json.loads(r.stdout)
        assert out["ok"] is True
        assert out["id"] == FAKE_ID
        assert out["url"] == f"http://jarvis.tail:8643/api/artifacts/{FAKE_ID}"
        assert out["title"] == "A Real Video"
        assert out["duration_s"] == 12.5

    def test_a_search_is_bounded_to_one_result(self, tools: Path) -> None:
        """A fetch, not a browse: a caller that wanted to choose passes a URL."""
        r = run(tools, "--search", "a funny compilation")
        assert r.returncode == 0
        assert "ytsearch1:" in r.stderr

    def test_a_store_that_returns_no_id_is_a_publish_failure(self, tools: Path) -> None:
        stub(tools, "curl", "echo '{\"error\":\"nope\"}'")
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 5 and "did not return an id" in r.stderr

    def test_curl_failing_is_a_publish_failure(self, tools: Path) -> None:
        stub(tools, "curl", "exit 22")
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 5 and "publishing to the artifact store failed" in r.stderr

    def test_a_title_with_a_quote_does_not_break_the_json(self, tools: Path) -> None:
        stub(tools, "yt-dlp", (
            'prev=""; out=""\n'
            'for a in "$@"; do case "$prev" in --output) out="$a";; esac; prev="$a"; done\n'
            'dir="$(dirname "$out")"; printf x > "$dir/video.mp4"\n'
            'printf \'He said "hi"\' > "$dir/title.txt"\n'
        ))
        r = run(tools, "--url", "http://x/y")
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["title"] == 'He said "hi"'
