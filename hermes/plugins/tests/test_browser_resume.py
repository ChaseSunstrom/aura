"""T-1545: a browser goal keeps its sign-in across turns.

Hermes stops the headless browser at the end of every turn (cleanup_task_resources), and it
used to stop it before Chromium wrote its cookies (about every 30 s): a sign-in made late in a
turn was gone on the next one. _browser_flush closes an owned persistent-profile Chromium over
CDP first, so it writes them. The live proof is scripts/verify/browser_resume_live.py.

Not covered here: Chromium itself (the live proof covers it), and tabs, which never survive a
turn end (the headless-browser skill reopens the last URL).
"""
from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import _browser_flush  # noqa: E402


@dataclass
class _Browser:
    pid: int
    cdp_url: str = "ws://127.0.0.1:9/devtools/browser/x"
    owned: bool = True
    ephemeral: bool = False
    foreign: bool = False


class _Proc:
    def __init__(self, exits: bool = True) -> None:
        self.waited: list[float] = []
        self.exits = exits

    def wait(self, timeout: float) -> int:
        self.waited.append(timeout)
        if not self.exits:
            raise TimeoutError("still running")
        return 0

    def poll(self):
        return 0 if self.exits else None


class _Socket:
    def __init__(self, sent: list) -> None:
        self.sent = sent

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def recv(self, timeout: float) -> str:
        raise ConnectionError("Chromium closed the socket as it exited")


@pytest.fixture
def launcher(monkeypatch):
    calls: list = []
    module = types.ModuleType("tools.browser_launcher")
    module._browsers = {}

    def _terminate(pid, start_time, proc):
        calls.append(("terminate", pid))

    module._terminate = _terminate
    tools = types.ModuleType("tools")
    tools.browser_launcher = module
    monkeypatch.setitem(sys.modules, "tools", tools)
    monkeypatch.setitem(sys.modules, "tools.browser_launcher", module)
    sent: list = []
    connected: list = []

    def connect(url, **kwargs):
        connected.append(url)
        calls.append(("close", url))
        return _Socket(sent)

    monkeypatch.setattr(_browser_flush, "_connect", connect)
    assert _browser_flush.install() is True
    return module, calls, sent, connected


def test_browser_resume_a_persistent_profile_is_closed_over_cdp_before_it_is_stopped(launcher):
    module, calls, sent, _ = launcher
    module._browsers["profile:/data/p"] = _Browser(pid=41)
    proc = _Proc()
    module._terminate(41, 7, proc)
    # It exited cleanly, so Hermes's SIGTERM is skipped: the pid is gone and would only log a
    # "PID was recycled" warning at every turn end.
    assert calls == [("close", "ws://127.0.0.1:9/devtools/browser/x")]
    assert sent == [{"id": 1, "method": "Browser.close"}]
    assert proc.waited and max(proc.waited) <= _browser_flush.CLOSE_WAIT_S


def test_browser_resume_a_browser_that_ignores_the_close_is_stopped_as_before(launcher):
    module, calls, _, _ = launcher
    module._browsers["profile:/data/p"] = _Browser(pid=41)
    module._terminate(41, 7, _Proc(exits=False))
    assert calls == [("close", "ws://127.0.0.1:9/devtools/browser/x"), ("terminate", 41)]


@pytest.mark.parametrize("browser", [
    _Browser(pid=41, ephemeral=True),
    _Browser(pid=41, owned=False),
    _Browser(pid=41, foreign=True),
    _Browser(pid=41, cdp_url="http://127.0.0.1:9"),
    _Browser(pid=99),
])
def test_browser_resume_a_throwaway_borrowed_or_unknown_browser_is_stopped_as_before(launcher, browser):
    module, calls, sent, _ = launcher
    module._browsers["k"] = browser
    module._terminate(41, 7, None)
    assert calls == [("terminate", 41)]
    assert sent == []


def test_browser_resume_a_close_that_fails_still_stops_the_browser(launcher, monkeypatch):
    module, calls, _, _ = launcher
    module._browsers["profile:/data/p"] = _Browser(pid=41)

    def refuse(url, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(_browser_flush, "_connect", refuse)
    module._terminate(41, 7, None)
    assert calls == [("terminate", 41)]


def test_browser_resume_install_wraps_once_and_tolerates_a_missing_launcher(launcher, monkeypatch):
    module, calls, _, _ = launcher
    wrapped = module._terminate
    assert _browser_flush.install() is True
    assert module._terminate is wrapped
    monkeypatch.setitem(sys.modules, "tools.browser_launcher", None)
    assert _browser_flush.install() is False
