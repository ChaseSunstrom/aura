"""The `vllm` that jarvis-models starts frees the cards first (T-0411).

From 2026-10-27 22:30Z to 2026-10-29 00:07Z every start of the house model died ~15 s in, at
vLLM's free-memory check, and Docker said healthy throughout: Hermes answered nothing for a day.
These pin what the shim does before every start and how a start that dies shows up.
"""
from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path

import pytest

SHIM = Path(__file__).resolve().parents[1] / "models" / "vllm_prestart.py"


@pytest.fixture
def shim():
    spec = importlib.util.spec_from_file_location("vllm_prestart", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _proc(root: Path, pid: int, pgid: int, cmd: str) -> None:
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")
    (d / "stat").write_text(f"{pid} (x y) S 1 {pgid} {pgid} 0 -1\n")


def test_only_engine_groups_whose_server_is_gone_count_as_orphans(tmp_path, shim):
    _proc(tmp_path, 1, 1, "llama-swap -config base.yaml")
    _proc(tmp_path, 28, 28, "/usr/bin/python3 /usr/local/bin/vllm serve m --port 5801")
    _proc(tmp_path, 104, 28, "VLLM::EngineCore")
    _proc(tmp_path, 148, 28, "VLLM::Worker_TP0")
    # A server that died and left its engine and workers holding ~20 GiB a card.
    _proc(tmp_path, 204, 200, "VLLM::EngineCore")
    _proc(tmp_path, 248, 200, "VLLM::Worker_TP1")
    (tmp_path / "self").mkdir()
    assert shim.orphaned_engine_groups(tmp_path) == {200}


def test_a_group_led_from_outside_the_container_is_never_killed(tmp_path, shim):
    # A leader outside the PID namespace reads as pgrp 0, and killpg(0) is the shim's own group.
    _proc(tmp_path, 300, 0, "VLLM::EngineCore")
    _proc(tmp_path, 301, 1, "VLLM::Worker_TP0")
    assert shim.orphaned_engine_groups(tmp_path) == set()


class _Calls:
    def __init__(self, voice_up: bool = True):
        self.log: list[tuple] = []
        self.voice_up = voice_up

    def urlopen(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        self.log.append(("http", url))
        if "jarvis-cosyvoice" in url and not self.voice_up:
            raise urllib.error.URLError("Name or service not known")
        return _Resp()

    def install(self, shim, monkeypatch, orphans=frozenset()):
        left = set(orphans)
        monkeypatch.setattr(shim.urllib.request, "urlopen", self.urlopen)
        monkeypatch.setattr(shim, "orphaned_engine_groups", lambda proc=None: set(left))

        def killpg(pgid, sig):
            self.log.append(("kill", pgid, sig))
            left.discard(pgid)
        monkeypatch.setattr(shim.os, "killpg", killpg)
        monkeypatch.setattr(shim.subprocess, "Popen", lambda argv, **kw: self.log.append(("watch", argv[2:])))
        monkeypatch.setattr(shim.os, "execv", lambda path, argv: self.log.append(("exec", path, argv)))


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def test_a_serve_kills_orphans_releases_the_voice_then_execs_the_real_vllm(shim, monkeypatch):
    calls = _Calls()
    calls.install(shim, monkeypatch, orphans={200})
    shim.main(["serve", "m", "--port", "5801"])
    kinds = [c[0] for c in calls.log]
    assert kinds == ["kill", "http", "watch", "exec"], calls.log
    assert calls.log[0] == ("kill", 200, shim.signal.SIGKILL)
    assert calls.log[1] == ("http", "http://jarvis-cosyvoice:8890/admin/release")
    assert calls.log[2] == ("watch", ["--watch", "5801", str(shim.os.getpid())])
    assert calls.log[3] == ("exec", "/usr/local/bin/vllm", ["/usr/local/bin/vllm", "serve", "m", "--port", "5801"])


def test_an_unreachable_voice_never_blocks_the_start(shim, monkeypatch):
    calls = _Calls(voice_up=False)
    calls.install(shim, monkeypatch)
    shim.main(["serve", "m", "--port=5802"])
    assert calls.log[-1][0] == "exec"
    assert ("watch", ["--watch", "5802", str(shim.os.getpid())]) in calls.log


def test_anything_but_serve_goes_straight_to_the_real_vllm(shim, monkeypatch):
    calls = _Calls()
    calls.install(shim, monkeypatch, orphans={200})
    shim.main(["--version"])
    assert calls.log == [("exec", "/usr/local/bin/vllm", ["/usr/local/bin/vllm", "--version"])]


def test_a_llama_server_start_frees_the_cards_the_same_way(shim, monkeypatch):
    # T-0511: Ripple-65B needs both whole cards after qwen3.8's vLLM, so an orphaned engine
    # group or a loaded voice would fail its load the way T-0411's starts failed.
    calls = _Calls()
    calls.install(shim, monkeypatch, orphans={200})
    shim.main(["-m", "/models/gguf/r.gguf", "--port", "5803"], "llama-server")
    assert [c[0] for c in calls.log] == ["kill", "http", "watch", "exec"], calls.log
    assert calls.log[-1] == ("exec", "/usr/local/bin/llama-server",
                             ["/usr/local/bin/llama-server", "-m", "/models/gguf/r.gguf", "--port", "5803"])
    calls.log.clear()
    shim.main(["--version"], "llama-server")
    assert calls.log == [("exec", "/usr/local/bin/llama-server", ["/usr/local/bin/llama-server", "--version"])]


def test_a_healthy_start_clears_the_mark_and_warms_the_voice(tmp_path, shim, monkeypatch):
    mark = tmp_path / "failed"
    mark.write_text("old")
    monkeypatch.setattr(shim, "MARK", mark)
    polls: list[str] = []

    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        polls.append(url)
        if url.endswith("/health") and polls.count(url) < 2:
            raise urllib.error.URLError("refused")
        return _Resp()
    monkeypatch.setattr(shim.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(shim.time, "sleep", lambda s: None)
    monkeypatch.setattr(shim, "_alive", lambda pid: True)
    shim.watch(5801, 4242)
    assert not mark.exists()
    assert polls[-1] == "http://jarvis-cosyvoice:8890/admin/warm"


def test_a_start_that_dies_before_its_health_check_marks_the_container(tmp_path, shim, monkeypatch):
    mark = tmp_path / "failed"
    monkeypatch.setattr(shim, "MARK", mark)
    urls: list[str] = []

    def urlopen(req, timeout=None):
        urls.append(req if isinstance(req, str) else req.full_url)
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(shim.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(shim.time, "sleep", lambda s: None)
    alive = iter([True, True, False])
    monkeypatch.setattr(shim, "_alive", lambda pid: next(alive))
    shim.watch(5801, 4242)
    assert mark.exists() and "5801" in mark.read_text()
    assert not any(u.endswith("/admin/warm") for u in urls)


def _dies(shim, monkeypatch, tmp_path, running):
    """A watch whose model exits before /health; `running` is what llama-swap's /running says
    on each poll (the last state repeats)."""
    mark = tmp_path / "failed"
    monkeypatch.setattr(shim, "MARK", mark)
    polls = iter(running)
    last = [running[-1]]

    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/running"):
            last[0] = next(polls, last[0])
            resp = _Resp()
            resp.read = lambda: ('{"running": [{"model": "m", "state": "%s"}]}' % last[0]).encode()
            return resp
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(shim.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(shim.time, "sleep", lambda s: None)
    alive = iter([True, True, True, False])
    monkeypatch.setattr(shim, "_alive", lambda pid: next(alive))
    shim.watch(5801, 4242)
    return mark


def test_a_stop_llama_swap_asked_for_mid_load_does_not_mark_the_container(tmp_path, shim, monkeypatch):
    # a swap, a TTL unload or a refused load: "stopping" is seen while the process is still going
    assert not _dies(shim, monkeypatch, tmp_path, ["starting", "stopping", "starting"]).exists()


def test_a_stop_seen_only_after_the_exit_does_not_mark_the_container(tmp_path, shim, monkeypatch):
    assert not _dies(shim, monkeypatch, tmp_path, ["starting", "starting", "starting", "stopping"]).exists()


def test_a_crash_while_llama_swap_still_says_starting_marks_the_container(tmp_path, shim, monkeypatch):
    assert _dies(shim, monkeypatch, tmp_path, ["starting"]).exists()
