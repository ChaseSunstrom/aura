"""``media_fetch``: the fetch script run in the gateway, with the script faked.

The shell has no network (ADR-0045), so the one place ``fetch-video.sh`` can
download and publish is this process. These pin that the tool calls it there,
maps its exit codes, and publishes nothing on failure. Not covered: the script
itself (``scripts/media/fetch-video.sh`` has no suite; yt-dlp needs a network).
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

tools = load_plugin("jarvis-artifacts").tools

ID1 = "c" * 64


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    # `adapter.key` (AURA_ADAPTER_KEY) is what `_key()` reads; the script is
    # handed it as AURA_API_KEY, the name it reads.
    monkeypatch.setenv("AURA_ADAPTER_KEY", "k")
    monkeypatch.setenv("AURA_PUBLIC_URL", "http://jarvis.tail:8643")
    monkeypatch.setattr(tools, "_fetch_script", lambda: "/usr/local/bin/fetch-video.sh")


def fake_script(monkeypatch, *, code: int = 0, stdout: str = "", stderr: str = "", seen: dict | None = None):
    def run(argv, **kwargs):
        if seen is not None:
            seen.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(tools.subprocess, "run", run)


def test_it_runs_the_script_and_returns_the_artifact_id(monkeypatch) -> None:
    seen: dict = {}
    line = json.dumps({"ok": True, "id": ID1, "url": f"http://jarvis.tail:8643/api/artifacts/{ID1}",
                       "title": "Clip", "duration_s": 12.5, "quality": 480})
    fake_script(monkeypatch, stdout=f"fetch-video: downloading\n{line}\n", seen=seen)
    out = json.loads(tools.handle_media_fetch({"url": "https://example.com/v", "quality": 480,
                                               "title": "Clip", "ttl": "never"}))
    assert out["ok"] is True and out["id"] == ID1
    assert seen["argv"] == ["/usr/local/bin/fetch-video.sh", "--url", "https://example.com/v",
                            "--quality", "480", "--title", "Clip", "--ttl", "never"]
    assert seen["timeout"] == tools.MEDIA_FETCH_TIMEOUT > tools._TIMEOUT, "a download is not one round trip"
    assert seen["env"]["AURA_API_KEY"] == "k", "the script publishes with the store's key"
    assert seen["env"]["AURA_PUBLIC_URL"] == "http://jarvis.tail:8643"


def test_a_search_is_the_other_way_in(monkeypatch) -> None:
    seen: dict = {}
    fake_script(monkeypatch, stdout=json.dumps({"ok": True, "id": ID1}), seen=seen)
    tools.handle_media_fetch({"search": "funny meme compilation 2015"})
    assert seen["argv"][1:3] == ["--search", "funny meme compilation 2015"]
    assert "--quality" in seen["argv"] and seen["argv"][seen["argv"].index("--quality") + 1] == "720"


def test_a_script_exit_3_reports_the_download_failed_and_publishes_nothing(monkeypatch) -> None:
    calls = {"n": 0}
    monkeypatch.setattr(tools, "_call", lambda *a, **k: calls.update(n=calls["n"] + 1) or {})
    fake_script(monkeypatch, code=3, stderr="fetch-video: yt-dlp could not download https://x\n")
    out = json.loads(tools.handle_media_fetch({"url": "https://x"}))
    assert out["ok"] is False and out["exit_code"] == 3
    assert "download failed" in out["error"] and "could not download" in out["error"]
    assert "id" not in out and calls["n"] == 0, "nothing was published"


@pytest.mark.parametrize("params", [{}, {"url": "https://a", "search": "b"}, {"url": "-x"},
                                    {"url": "https://a", "quality": "hd"}])
def test_bad_arguments_never_reach_the_script(monkeypatch, params) -> None:
    ran = {"n": 0}
    monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: ran.update(n=ran["n"] + 1))
    out = json.loads(tools.handle_media_fetch(params))
    assert out["ok"] is False and ran["n"] == 0


def test_a_download_past_the_cap_is_stopped_and_said(monkeypatch) -> None:
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(tools.subprocess, "run", run)
    out = json.loads(tools.handle_media_fetch({"url": "https://slow"}))
    assert out["ok"] is False and "nothing was published" in out["error"]


def test_it_never_shells_out_through_the_terminal_tool(monkeypatch) -> None:
    """The sandbox has no network: the script must run in this process, not be dispatched."""
    seen: dict = {}
    fake_script(monkeypatch, stdout=json.dumps({"ok": True, "id": ID1}), seen=seen)
    tools.handle_media_fetch({"url": "https://example.com/v"})
    assert seen["argv"][0].endswith("fetch-video.sh"), "subprocess.run is handed the script itself"
    source = inspect.getsource(tools.handle_media_fetch)
    assert "dispatch(" not in source and "registry" not in source and '"terminal"' not in source
