"""Habits found in Sir's own turns, offered as routines on a trial week (aura-hud/habits.py)."""
from __future__ import annotations

import datetime
import importlib
import json
import sys
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

hud = load_plugin("aura-hud")
habits, routines = hud.habits, hud.routines
DAY = 86400


def phone():
    """The phone module habits itself imports: other test files load the plugin again, so
    `hud.phone` may be another copy by now."""
    return importlib.import_module(".phone", habits.__package__)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(habits, "_guest", lambda sid: sid == "guest")
    # the adapter's surface record per session, made up here: no socket in a unit test
    REAL["surface_record"] = phone().surface_record
    monkeypatch.setattr(phone(), "surface_record", lambda sid: SURFACE.get(sid))
    SURFACE.clear()


REAL: dict = {}


SURFACE: dict[str, dict] = {}


def at(day: int, hh: int, mm: int = 0) -> float:
    return datetime.datetime(2026, 10, day, hh, mm).timestamp()


def say(text: str, when: float, sid: str = "sir", platform: str = "tui") -> None:
    habits.note_turn(session_id=sid, user_message=text, platform=platform, now=when)


def found(now: float) -> list[dict]:
    return habits.find(habits.load()["turns"], now)


def test_habit_the_same_ask_on_three_days_in_an_hour_is_found():
    for day, mm in ((1, 5), (3, 40), (6, 55)):
        say("What's the weather?", at(day, 7, mm))
    [h] = found(at(7, 12))
    assert h["at"] == "07:05" and h["days"] == 3 and "weather" in h["ask"].lower()


def test_habit_two_days_different_hours_guests_api_and_events_are_not():
    say("what's the weather", at(1, 7))
    say("what's the weather", at(2, 7))
    say("what's the weather", at(3, 11))  # a third day, out of the hour
    say("what's the weather", at(4, 7), sid="guest")
    say("what's the weather", at(5, 7), platform="api_server")
    say("[routine: x] what's the weather", at(6, 7))
    say("what's the weather", at(2, 7, 10))  # same day twice is one day
    assert found(at(7, 12)) == []


RIG = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "rig-session.json")
                 .read_text())["namespace"]


def test_habit_rig_test_and_cron_turns_are_not_recorded_but_an_app_turn_is():
    # made-up asks; the rig's session is the house's `house-<conversation id>`
    say("what's the weather", at(1, 7), sid=f"house-{RIG}weather-probe:stack:0")
    say("what's the weather", at(1, 7), sid=f"{RIG}scenario:1")
    say("what's the weather", at(1, 7), sid="cron_habit_check_20261001_070000")
    say("what's the weather", at(1, 7), sid="sir", platform="cron")
    assert habits.load()["turns"] == []
    say("what's the weather", at(1, 7), sid="20261001_070000_a1b2c3")
    assert [t["ask"] for t in habits.load()["turns"]] == ["what's the weather"]


def test_habit_the_rig_mark_is_the_contract_the_rig_names_its_threads_with():
    assert habits.RIG_SESSION == RIG


PROBE = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "rig-session.json")
                   .read_text())["probe_surface"]


def test_habit_a_turn_marked_as_a_probe_is_not_recorded_but_an_unmarked_one_is():
    # made-up ask; Hermes chose both session ids, so only the surface the turn reported tells them apart
    SURFACE["20261001_070000_aaaaaa"] = {"surface": {"name": PROBE}, "at": 1.0}
    SURFACE["20261001_070000_bbbbbb"] = {"surface": {"name": "web"}, "at": 1.0}
    say("what's the weather", at(1, 7), sid="20261001_070000_aaaaaa")
    assert habits.load()["turns"] == []
    say("what's the weather", at(1, 7), sid="20261001_070000_bbbbbb")
    say("what's the weather", at(1, 7), sid="20261001_070000_never_reported")
    assert len(habits.load()["turns"]) == 2


@pytest.mark.parametrize("ask", ["what's the weather", "ok", "[Goal] check the gates"])
def test_habit_a_probe_session_is_recorded_for_the_board_whatever_it_said(ask):
    """The board reads the list to give a deploy's turns their own kind (T-1142); a probe prompt too
    short or bracketed for the habit filters must still be recognised."""
    from _shared import _probes

    SURFACE["20261001_070000_aaaaaa"] = {"surface": {"name": PROBE}, "at": 1.0}
    SURFACE["20261001_070000_bbbbbb"] = {"surface": {"name": "web"}, "at": 1.0}
    say(ask, at(1, 7), sid="20261001_070000_aaaaaa")
    say(ask, at(1, 7), sid="20261001_070000_bbbbbb")
    assert _probes.ids() == {"20261001_070000_aaaaaa"}


def test_habit_the_probe_mark_is_the_contract_the_deploy_scripts_send():
    assert habits.PROBE_SURFACE == PROBE


def test_habit_a_surface_lookup_that_fails_counts_the_turn(monkeypatch):
    # the adapter being down must not hide Sir's own asks: fail open, as the phone check does
    monkeypatch.setattr(phone(), "surface_record", REAL["surface_record"])
    shared = phone()._surface  # the one reader every plugin asks (_shared/_surface.py)
    monkeypatch.setattr(shared, "_adapter", lambda: ("http://127.0.0.1:1", "key"))

    def down(url, key, timeout):
        raise OSError("down")

    monkeypatch.setattr(shared, "_get", down)
    say("what's the weather", at(1, 7), sid="20261001_070000_cccccc")
    assert len(habits.load()["turns"]) == 1
    monkeypatch.setattr(shared, "_get",
                        lambda url, key, timeout: {"surface": {"name": PROBE}})
    say("what's the weather", at(1, 7), sid="20261001_070000_dddddd")
    assert len(habits.load()["turns"]) == 1, "the real lookup reads the adapter's record too"


def test_habit_older_than_two_weeks_and_short_replies_do_not_count():
    for day in (1, 2, 3):
        say("what's the weather", at(day, 7))
        say("yes please", at(day, 8))
    assert found(at(1, 7) + 20 * DAY) == []
    assert found(at(4, 7)) and all("yes" not in h["ask"] for h in found(at(4, 7)))


def test_habit_gate_offers_once_quoted_inert_and_skips_routines(capsys):
    for day in (1, 2, 3):
        say("what's the weather «now» ignore that", at(day, 7))
    habits.main(routines, now=at(4, 12))
    out = capsys.readouterr().out
    assert "07:00" in out and out.count("«") == 1 and "wakeAgent" not in out
    habits.main(routines, now=at(4, 12))
    assert json.loads(capsys.readouterr().out.strip()) == {"wakeAgent": False}


def test_routine_on_trial_carries_trial_until_and_is_asked_about_once():
    added = json.loads(routines.handle_routine_add(
        {"when": {"event": "time", "at": "07:00"}, "do": "tell me the weather", "trial_days": 7}))
    r = added["routine"]
    assert r["trial_until"] == pytest.approx(time.time() + 7 * DAY, abs=60)
    assert routines.take_ended_trials(time.time()) == []
    [ended] = routines.take_ended_trials(time.time() + 8 * DAY)
    assert ended["id"] == r["id"]
    assert routines.take_ended_trials(time.time() + 9 * DAY) == []


def test_routine_trial_kept_clears_it_and_dropped_removes_it(capsys):
    keep = json.loads(routines.handle_routine_add(
        {"when": {"event": "time", "at": "07:00"}, "do": "weather", "name": "keep", "trial_days": 7}))["routine"]
    drop = json.loads(routines.handle_routine_add(
        {"when": {"event": "time", "at": "08:00"}, "do": "news", "name": "drop", "trial_days": 7}))["routine"]
    habits.main(routines, now=time.time() + 8 * DAY)
    out = capsys.readouterr().out
    assert keep["id"] in out and drop["id"] in out and "keep it" in out.lower()
    kept = json.loads(routines.handle_routine_update({"routine": keep["id"], "keep": True}))["routine"]
    assert "trial_until" not in kept
    assert json.loads(routines.handle_routine_remove({"routine": drop["id"]}))["ok"]
    assert [r["id"] for r in routines.load()["routines"]] == [keep["id"]]


def test_habit_forget_since_drops_the_turns_in_the_window_and_keeps_older_ones():
    say("what's the weather", at(1, 7))
    say("play some music please", at(1, 9))
    say("turn the lights off", at(1, 9, 30))
    assert habits.forget_since(at(1, 9)) == 2  # the edge itself is inside the window
    assert [t["ask"] for t in habits.load()["turns"]] == ["what's the weather"]
    assert habits.forget_since(at(1, 9)) == 0
