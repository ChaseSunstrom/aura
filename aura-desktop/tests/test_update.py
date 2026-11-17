"""Self-update: version ordering, index parsing, the once-a-day gate, the CLI.

Nothing here opens a socket or spawns pip. Every function that would reach the
network takes an injectable ``opener``, and the one that would run pip takes an
injectable ``runner``, so the whole module is exercised offline — which is also
the point: an update check must never be able to fail the agent it runs in.
"""

from __future__ import annotations

import pytest

from aura_desktop import update
from aura_desktop.__main__ import _default_to_run, main
from aura_desktop.config import Config, load_config


# --- version ordering -------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        # The property CI's `0.1.<pipeline-iid>` scheme depends on: a string
        # compare would rank "0.1.9" above "0.1.123", a tuple compare does not.
        ("0.1.123", "0.1.0", True),
        ("0.1.123", "0.1.9", True),
        ("0.1.10", "0.1.9", True),
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.1.123", False),
        ("1.0.0", "0.9.99", True),
        # Padding: "0.1" and "0.1.0" are the same version.
        ("0.1", "0.1.0", False),
        ("0.1.0", "0.1", False),
        # A leading v and a build suffix are ignored, not mis-parsed.
        ("v0.2.0", "0.1.0", True),
        ("0.1.5rc1", "0.1.5", False),
    ],
)
def test_is_newer(candidate, current, expected):
    assert update.is_newer(candidate, current) is expected


def test_parse_version_keeps_only_the_numeric_release():
    assert update.parse_version("v1.2.3+local") == (1, 2, 3)
    assert update.parse_version("") == (0,)
    assert update.parse_version("garbage") == (0,)


# --- reading the simple index ----------------------------------------------


_INDEX_HTML = """<!DOCTYPE html>
<html><head><title>Links for jarvis-desktop</title></head><body>
<h1>Links for jarvis-desktop</h1>
<a href="https://gitlab.example/files/aa/jarvis_desktop-0.1.0-py3-none-any.whl#sha256=deadbeef">jarvis_desktop-0.1.0-py3-none-any.whl</a><br>
<a href="https://gitlab.example/files/bb/jarvis_desktop-0.1.0.tar.gz#sha256=cafef00d">jarvis_desktop-0.1.0.tar.gz</a><br>
<a href="https://gitlab.example/files/cc/jarvis_desktop-0.1.9-py3-none-any.whl#sha256=1234">jarvis_desktop-0.1.9-py3-none-any.whl</a><br>
<a href="https://gitlab.example/files/dd/jarvis_desktop-0.1.123-py3-none-any.whl#sha256=5678">jarvis_desktop-0.1.123-py3-none-any.whl</a><br>
</body></html>
"""


def _opener(payload: bytes | Exception, counter: list[int] | None = None):
    """A fake fetcher: returns fixed bytes, or raises, and can count its calls."""

    def opener(url: str, timeout: float) -> bytes:
        if counter is not None:
            counter.append(1)
        if isinstance(payload, Exception):
            raise payload
        return payload

    return opener


def test_parse_simple_index_reads_wheels_and_sdists_and_dedups():
    versions = update.parse_simple_index(_INDEX_HTML)
    # De-duplicated across href and text, and across wheel + sdist.
    assert versions == ["0.1.0", "0.1.9", "0.1.123"]


def test_fetch_versions_returns_empty_on_any_failure():
    assert update.fetch_versions(opener=_opener(OSError("no network"))) == []
    assert update.fetch_versions(opener=_opener(b"<html>nothing here</html>")) == []


def test_latest_available_picks_the_highest():
    assert update.latest_available(opener=_opener(_INDEX_HTML.encode())) == "0.1.123"


def test_latest_available_is_none_when_unreachable():
    assert update.latest_available(opener=_opener(TimeoutError())) is None


def test_available_update_only_returns_a_strictly_newer_version():
    html = _INDEX_HTML.encode()
    assert update.available_update("0.1.0", opener=_opener(html)) == "0.1.123"
    assert update.available_update("0.1.123", opener=_opener(html)) is None
    assert update.available_update("0.2.0", opener=_opener(html)) is None


# --- the pip command --------------------------------------------------------


def test_run_update_builds_the_right_pip_command_with_the_index_root():
    seen: list[list[str]] = []

    def runner(command: list[str]) -> int:
        seen.append(command)
        return 0

    code = update.run_update(runner=runner)
    assert code == 0
    (command,) = seen
    assert command[1:] == [
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--index-url",
        update.PYPI_INDEX_ROOT,
        update.PACKAGE_NAME,
    ]
    # The ROOT, not the per-package page: pip appends the name itself.
    assert not update.PYPI_INDEX_ROOT.endswith(f"/{update.PACKAGE_NAME}/")


# --- the once-a-day startup check -------------------------------------------


def test_maybe_notify_update_gates_to_once_per_day(tmp_path):
    config = Config(state_dir=tmp_path)
    calls: list[int] = []
    html = _INDEX_HTML.encode()

    first = update.maybe_notify_update(
        config, "0.1.0", now=1000.0, opener=_opener(html, calls)
    )
    assert first == "0.1.123"
    assert len(calls) == 1

    # A restart a minute later must not check again.
    again = update.maybe_notify_update(
        config, "0.1.0", now=1060.0, opener=_opener(html, calls)
    )
    assert again is None
    assert len(calls) == 1  # opener was not called a second time

    # A day and a bit later, it checks again.
    later = update.maybe_notify_update(
        config, "0.1.0", now=1000.0 + 25 * 3600, opener=_opener(html, calls)
    )
    assert later == "0.1.123"
    assert len(calls) == 2


def test_maybe_notify_update_never_raises(tmp_path):
    config = Config(state_dir=tmp_path)
    # A broken opener, a state dir that cannot be written — none of it escapes.
    assert update.maybe_notify_update(config, "0.1.0", now=1.0, opener=_opener(OSError())) is None


def test_maybe_notify_update_records_the_check_time(tmp_path):
    config = Config(state_dir=tmp_path)
    update.maybe_notify_update(config, "0.1.0", now=42.0, opener=_opener(_INDEX_HTML.encode()))
    state = update._read_state(tmp_path / update._STATE_FILE)
    assert state["last_check"] == 42.0
    assert state["latest_seen"] == "0.1.123"


# --- the CLI ----------------------------------------------------------------


def test_cli_check_update_reports_without_installing():
    out: list[str] = []
    code = update.cli_check_update("0.1.0", opener=_opener(_INDEX_HTML.encode()), out=out.append)
    assert code == 0
    assert "update available" in out[0]
    assert "0.1.123" in out[0]


def test_cli_check_update_says_when_up_to_date():
    out: list[str] = []
    code = update.cli_check_update("0.1.123", opener=_opener(_INDEX_HTML.encode()), out=out.append)
    assert code == 0
    assert "latest" in out[0]


def test_cli_check_update_returns_one_when_unreachable():
    out: list[str] = []
    code = update.cli_check_update("0.1.0", opener=_opener(OSError()), out=out.append)
    assert code == 1
    assert "could not reach" in out[0]


def test_cli_update_installs_when_a_newer_version_exists():
    out: list[str] = []
    ran: list[list[str]] = []

    def runner(command: list[str]) -> int:
        ran.append(command)
        return 0

    code = update.cli_update(
        "0.1.0", opener=_opener(_INDEX_HTML.encode()), runner=runner, out=out.append
    )
    assert code == 0
    assert ran  # pip was invoked
    assert any("updating" in line for line in out)


def test_cli_update_does_nothing_when_already_current():
    out: list[str] = []
    ran: list[list[str]] = []

    code = update.cli_update(
        "0.1.123",
        opener=_opener(_INDEX_HTML.encode()),
        runner=lambda c: ran.append(c) or 0,
        out=out.append,
    )
    assert code == 0
    assert not ran  # pip was NOT invoked
    assert any("already" in line for line in out)


def test_cli_update_reports_a_pip_failure():
    out: list[str] = []
    code = update.cli_update(
        "0.1.0",
        opener=_opener(_INDEX_HTML.encode()),
        runner=lambda c: 1,
        out=out.append,
    )
    assert code == 1
    assert any("not updated" in line for line in out)


# --- integration with main() and the flag parsing --------------------------


def test_default_to_run_leaves_update_flags_alone():
    # `run` must NOT be spliced in front of a flag that exits before any command.
    assert _default_to_run(["--update"]) == ["--update"]
    assert _default_to_run(["--check-update"]) == ["--check-update"]


def test_main_routes_check_update_without_a_config(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(update, "cli_check_update", lambda: called.append("check") or 0)
    monkeypatch.setattr(update, "cli_update", lambda: called.append("update") or 0)
    # No -c, no state dir, no server: the flag short-circuits all of that.
    assert main(["--check-update"]) == 0
    assert called == ["check"]


def test_main_routes_update(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(update, "cli_update", lambda: called.append("update") or 7)
    assert main(["--update"]) == 7
    assert called == ["update"]


# --- the config flag --------------------------------------------------------


def test_auto_update_check_defaults_on_and_can_be_switched_off(tmp_path):
    assert load_config(tmp_path / "nope.json", env={"AURA_STATE_DIR": str(tmp_path)}).auto_update_check is True

    path = tmp_path / "config.json"
    path.write_text('{"auto_update_check": false}')
    off = load_config(path, env={"AURA_STATE_DIR": str(tmp_path)})
    assert off.auto_update_check is False

    env_off = load_config(
        tmp_path / "nope.json",
        env={"AURA_STATE_DIR": str(tmp_path), "AURA_AUTO_UPDATE_CHECK": "0"},
    )
    assert env_off.auto_update_check is False


def test_an_agent_frozen_into_the_desktop_app_never_offers_pip(monkeypatch, tmp_path, capsys):
    # Inside the desktop app the agent is a PyInstaller binary: pip cannot
    # update it, and the app replaces it with itself (ADR-0051). The daily
    # notice would tell somebody to run a command that does nothing, and
    # `--update` would run pip against a Python that is not there.
    import sys

    from aura_desktop import update
    from aura_desktop.__main__ import main

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fetched = []
    config = type("C", (), {"state_dir": tmp_path, "auto_update_check": True})()
    assert update.maybe_notify_update(config, "0.1.0", opener=lambda *a: fetched.append(a)) is None
    assert fetched == []
    assert main(["--update"]) == 2
    assert main(["--check-update"]) == 2
    assert "desktop app" in capsys.readouterr().err
