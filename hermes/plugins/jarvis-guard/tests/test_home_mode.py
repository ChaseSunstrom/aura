"""The gateway holds its home folder at the mode the stack sets for it (T-1497).

The compose file sets ``HERMES_HOME_MODE`` to 0711 so the adapter, another uid on a read-only
mount, can reach ``state/kill-switch.json``, the settings overrides and the persona. Hermes
applies that mode when a process starts, and then undoes it: writing its credential store
tightens the store's parent, which is the home, to 0700 whatever the setting says. Measured on
a clean install, that write came twelve seconds after the gateway started, the adapter then
answered "kill-switch file unreadable", and the console drew a stopped assistant on a stack
where nothing was stopped.

These tests hold the keeper that puts the mode back: one look at the folder, the thread that
looks every second, and the plugin starting it.

Not covered: that Hermes still tightens the folder (its code, read in the image when this was
written), a status request that lands inside the second between the tightening and the next
look, and what the adapter answers: its reader is unchanged and still reads a switch it cannot
reach as engaged. The whole path, status to the core at rest, is the clean-install proof
(``scripts/verify/install-clean.sh``).
"""
from __future__ import annotations

import logging
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGINS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGINS_DIR))
from _plugin_loader import FakeCtx, load_plugin  # noqa: E402
from _shared import _homemode  # noqa: E402

SET, TIGHT = 0o711, 0o700


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home at the set mode, with the setting the compose file gives the gateway."""
    folder = tmp_path / "home"
    folder.mkdir()
    folder.chmod(SET)
    monkeypatch.setenv("HERMES_HOME", str(folder))
    monkeypatch.setenv("HERMES_HOME_MODE", "0711")
    yield folder
    # The keeper is the process's: left running it would go on holding a folder the next test
    # in this run has pointed somewhere else.
    _homemode.stop()


def _keepers() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == _homemode.THREAD_NAME and t.is_alive()]


# --- one look ----------------------------------------------------------------------------------

def test_a_home_tightened_after_start_is_set_back(home):
    home.chmod(TIGHT)  # what Hermes' credential store write does to its parent
    assert _homemode.hold() is True
    assert _mode(home) == SET


def test_a_home_at_its_set_mode_is_left_alone(home):
    assert _homemode.hold() is False
    assert _mode(home) == SET


def test_any_other_mode_is_set_back_too(home):
    # Not only the tightening seen: the setting is the stack's word on this folder.
    home.chmod(0o755)
    assert _homemode.hold() is True
    assert _mode(home) == SET


def test_with_no_setting_nothing_is_held(home, monkeypatch):
    # A desk install has no adapter beside it and Hermes' own default (owner only) stands.
    monkeypatch.delenv("HERMES_HOME_MODE")
    home.chmod(TIGHT)
    assert _homemode.hold() is False
    assert _mode(home) == TIGHT
    assert _homemode.start() is False
    assert _keepers() == []


@pytest.mark.parametrize("value", ["rwx", "9999", "", "   ", "0", "-1", "77777", "0x1c9"])
def test_a_setting_that_is_not_a_mode_holds_nothing(home, monkeypatch, value):
    monkeypatch.setenv("HERMES_HOME_MODE", value)
    home.chmod(TIGHT)
    assert _homemode.hold() is False
    assert _mode(home) == TIGHT
    assert _homemode.start() is False


def test_the_setting_is_read_as_hermes_reads_it(home, monkeypatch):
    # Hermes strips the value and reads it as octal, with or without the leading zero.
    for value in ("711", " 0711 "):
        monkeypatch.setenv("HERMES_HOME_MODE", value)
        home.chmod(TIGHT)
        assert _homemode.hold() is True
        assert _mode(home) == SET


def test_a_folder_that_is_not_ours_raises_nothing_and_is_said_once(home, monkeypatch, caplog):
    def refuse(path, mode):
        raise PermissionError(1, "Operation not permitted")

    home.chmod(TIGHT)
    monkeypatch.setattr(_homemode.os, "chmod", refuse)
    with caplog.at_level(logging.WARNING, logger=_homemode.__name__):
        assert _homemode.hold() is False
        assert _homemode.hold() is False
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_a_home_that_is_not_there_raises_nothing(home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(home / "gone"))
    assert _homemode.hold() is False


# --- the keeper --------------------------------------------------------------------------------

def test_the_keeper_sets_a_tightened_home_back_within_three_seconds(home):
    assert _homemode.start(interval=0.05) is True
    home.chmod(TIGHT)
    by = time.monotonic() + 3
    while _mode(home) != SET and time.monotonic() < by:
        time.sleep(0.02)
    assert _mode(home) == SET
    # And again: the store is written more than once in a process's life.
    home.chmod(TIGHT)
    by = time.monotonic() + 3
    while _mode(home) != SET and time.monotonic() < by:
        time.sleep(0.02)
    assert _mode(home) == SET


def test_the_keeper_sets_the_mode_as_it_starts(home):
    # The gateway's own start already applies it; a process that starts later must not wait.
    home.chmod(TIGHT)
    assert _homemode.start(interval=30) is True
    by = time.monotonic() + 3
    while _mode(home) != SET and time.monotonic() < by:
        time.sleep(0.02)
    assert _mode(home) == SET


def test_starting_twice_keeps_one_keeper(home):
    assert _homemode.start(interval=0.05) is True
    assert _homemode.start(interval=0.05) is False
    assert len(_keepers()) == 1


def test_the_keeper_says_once_that_it_is_on(home, caplog):
    # The only way to see from a log that a running gateway holds the folder: it is silent
    # for as long as nothing tightens it.
    with caplog.at_level(logging.INFO, logger=_homemode.__name__):
        assert _homemode.start(interval=30) is True
        assert _homemode.start(interval=30) is False
    on = [r.getMessage() for r in caplog.records if "holding the home folder" in r.getMessage()]
    assert on == [f"holding the home folder {home} at its set mode 0711"]


def test_the_keeper_never_holds_a_process_open(home):
    assert _homemode.start(interval=0.05) is True
    assert all(t.daemon for t in _keepers())


def test_the_keeper_looks_once_a_second():
    # The window in which a status request can still find the folder shut. Longer and a console
    # that asks every few seconds can draw a stopped assistant; much shorter buys nothing.
    assert 0.5 <= _homemode.INTERVAL <= 2


def test_a_stopped_keeper_can_be_started_again(home):
    assert _homemode.start(interval=0.05) is True
    _homemode.stop()
    assert _keepers() == []
    assert _homemode.start(interval=0.05) is True
    assert len(_keepers()) == 1


# --- the plugin starts it ----------------------------------------------------------------------

def test_the_guard_starts_the_keeper_when_it_registers(home):
    home.chmod(TIGHT)
    guard = load_plugin("jarvis-guard")
    guard.register(FakeCtx())
    guard.register(FakeCtx())  # Hermes loads a plugin once; a second load must not add a keeper
    assert len(_keepers()) == 1
    by = time.monotonic() + 3
    while _mode(home) != SET and time.monotonic() < by:
        time.sleep(0.02)
    assert _mode(home) == SET


def test_a_keeper_that_cannot_start_does_not_stop_the_guard_loading(home, monkeypatch):
    # A governor that fails to load because a helper thread could not start leaves the gateway
    # ungoverned: the worse failure by far.
    class NoThreads:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(_homemode.threading, "Thread", NoThreads)
    guard = load_plugin("jarvis-guard")
    ctx = FakeCtx()
    guard.register(ctx)
    assert _homemode.start() is False
    assert os.environ["HERMES_HOME"] == str(home)
