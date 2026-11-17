"""The kill switch as `GET /api/status` reports it, against the shared contract.

The adapter is a separate image that does not ship `hermes/plugins`, so it
re-implements the read rather than importing `_killswitch`. That is a clone by
design, and `tests/contracts/kill-switch.json` is what stops it drifting: the
truth table below is the same one the gateway side runs, so a change to either
reader that disagrees with the other fails here.

The unreadable and unparseable rows are the ones worth reading twice. Both
readers answer ENGAGED for them, because the alternative — a HUD that shows a
released switch because it could not parse the file — tells the owner the
assistant is live at exactly the moment it is stopped.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_adapter.status import read_kill_switch

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "contracts" / "kill-switch.json")
    .read_text(encoding="utf-8")
)


def _write(tmp_path: Path, body: str | None) -> str:
    target = tmp_path / "kill-switch.json"
    if body is not None:
        target.write_text(body, encoding="utf-8")
    return str(target)


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_the_adapter_answers_the_contract(case, tmp_path):
    assert read_kill_switch(_write(tmp_path, case["file"]))["engaged"] is case["engaged"]


def test_the_gateway_side_answers_the_same_table(tmp_path, monkeypatch):
    """The other half of the clone, run against the identical rows.

    Imported here rather than in the plugin suite because this is the test whose
    job is the *agreement*; the plugin's own tests cover its behaviour.
    """
    import sys

    plugins = Path(__file__).resolve().parents[2] / "hermes" / "plugins"
    sys.path.insert(0, str(plugins))
    from _shared import _killswitch

    for case in CONTRACT["cases"]:
        home = tmp_path / case["name"].replace(" ", "-").replace(",", "")
        (home / "state").mkdir(parents=True)
        if case["file"] is not None:
            (home / "state" / "kill-switch.json").write_text(case["file"], encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(home))
        assert _killswitch.engaged() is case["engaged"], case["name"]


def test_the_contract_names_the_path_both_sides_use(tmp_path, monkeypatch):
    """A contract that agreed on the format but not on the location would pass
    every row above and still have the HUD reading a file nobody writes."""
    import sys

    plugins = Path(__file__).resolve().parents[2] / "hermes" / "plugins"
    sys.path.insert(0, str(plugins))
    from _shared import _killswitch

    monkeypatch.setenv("HERMES_HOME", "/opt/data")
    assert str(_killswitch.state_path()) == CONTRACT["container_path"]
    assert _killswitch.STATE_REL == CONTRACT["path"]
    assert _killswitch.SCHEMA == CONTRACT["schema"]

    from aura_adapter import status

    assert status.KILL_SWITCH_PATH == CONTRACT["container_path"]


def test_an_engaged_switch_carries_who_and_why_to_the_hud(tmp_path):
    """The HUD draws the reason. "Kill switch" alone does not tell the owner
    whether they pulled it an hour ago or somebody else just did."""
    path = _write(tmp_path, json.dumps({
        "schema": "jarvis.killswitch.v1", "engaged": True,
        "actor": "owner", "reason": "boiler leak", "at": "2026-09-03T17:38:33Z",
    }))
    state = read_kill_switch(path)
    assert state == {"engaged": True, "actor": "owner", "reason": "boiler leak",
                     "at": "2026-09-03T17:38:33Z"}


def test_a_directory_where_the_file_should_be_reads_as_engaged(tmp_path):
    """The OSError branch, which a missing-file test does not reach."""
    (tmp_path / "kill-switch.json").mkdir()
    assert read_kill_switch(str(tmp_path / "kill-switch.json"))["engaged"] is True
