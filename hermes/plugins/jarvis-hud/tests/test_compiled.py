"""Routines that compile to a jarvis-core automation, and the ones that stay with the model (T-0185)."""
from __future__ import annotations

import json

import pytest
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

import importlib  # noqa: E402

compiled = importlib.import_module(load_plugin("jarvis-hud").__name__ + ".compiled")
FIX = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "compiled-routine.json")
                 .read_text(encoding="utf-8"))
TRIGGERS = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "routine-triggers.json")
                      .read_text(encoding="utf-8"))


def test_compiled_routine_contract_the_plugin_emits_the_shared_fixture():
    auto, why = compiled.compile_routine(FIX["routine"], FIX["call"], set(FIX["entities"]))
    assert why == "" and auto == FIX["automation"]


def test_compiled_routine_only_triggers_the_table_maps_to_the_house_compile():
    call, ents = FIX["call"], set(FIX["entities"])
    for event, row in TRIGGERS["triggers"].items():
        routine = {"id": "r1", "when": {"event": event, "at": "07:00"}}
        auto, _ = compiled.compile_routine(routine, call, ents)
        # A plain house switch compiles on time only; leave compiles only as the away-lock (below).
        assert (auto is not None) == (event == "time"), event
        if row["core"] is None:
            assert auto is None, event


def test_compiled_routine_a_text_call_search_or_unknown_entity_never_compiles():
    r = FIX["routine"]
    for call in ({"service": "notify.send", "entity_ids": ["notify.x"]},
                 {"service": "script.turn_on", "entity_ids": ["light.porch"]},  # wrong domain
                 {"service": "light.turn_on", "entity_ids": ["light.gone"]},
                 {"service": "light.turn_on", "entity_ids": ["light.porch"], "data": {"flash": "long"}},
                 None):
        auto, why = compiled.compile_routine(r, call, set(FIX["entities"]))
        assert auto is None and why, call


def test_compiled_routine_an_ask_tier_service_is_never_on_the_list():
    # Nothing that texts, calls, pays or unlocks: those stay with the model and its approvals.
    assert not any(s.split(".")[0] in {"notify", "lock", "alarm_control_panel", "shell_command",
                                       "rest_command", "persistent_notification", "script", "scene"}
                   for s in compiled.SERVICES)
    assert "cover.open_cover" not in compiled.SERVICES  # the cover may be the garage door


class FakeHouse:
    def __init__(self, entities=("light.porch",), fail=False, keep=False):
        self.ents, self.fail, self.keep = set(entities), fail, keep
        self.created: list = []
        self.deleted: list = []

    def entities(self):
        return self.ents

    def create(self, automation):
        if self.fail:
            raise OSError("house down")
        self.created.append(automation)
        return "auto-1"

    def delete(self, automation_id):
        if self.keep:
            return False
        self.deleted.append(automation_id)
        return True


@pytest.fixture
def routines(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return importlib.import_module(load_plugin("jarvis-hud").__name__ + ".routines")


def add(routines, house, **extra):
    args = {"when": {"event": "time", "at": "19:30"}, "do": "turn the porch light on", **extra}
    return json.loads(routines.handle_routine_add(args, house=house))


def test_compiled_routine_runs_in_the_house_and_never_reaches_the_phone(routines):
    house = FakeHouse()
    out = add(routines, house, house_action={"service": "light.turn_on", "entity_ids": ["light.porch"]})
    assert out["runs_in"] == "house" and house.created
    assert not routines.snapshot()["routines"]  # the phone never fires it too
    listed = json.loads(routines.handle_routine_list({}))["routines"]
    assert [r["runs_in"] for r in listed] == ["house"]
    assert [r["runs_in"] for r in json.loads(routines.command("view"))["routines"]] == ["house"]  # the console
    json.loads(routines.handle_routine_remove({"routine": listed[0]["id"]}, house=house))
    assert house.deleted == ["auto-1"]


def test_compiled_routine_list_marks_a_model_routine_and_ask_tier_stays_with_the_model(routines):
    house = FakeHouse()
    out = add(routines, house, house_action={"service": "lock.unlock", "entity_ids": ["lock.front"]})
    assert out["runs_in"] == "model" and "runs through me" in out["note"] and not house.created
    assert routines.snapshot()["routines"]  # the phone watches it as before


def test_compiled_routine_a_house_that_cannot_take_it_leaves_it_with_the_model(routines):
    out = add(routines, FakeHouse(fail=True), house_action={"service": "light.turn_on", "entity_ids": ["light.porch"]})
    assert out["runs_in"] == "model" and "could not take it" in out["note"]


def test_compiled_routine_cannot_be_edited_into_a_stale_copy(routines):
    add(routines, FakeHouse(), house_action={"service": "light.turn_on", "entity_ids": ["light.porch"]})
    rid = json.loads(routines.handle_routine_list({}))["routines"][0]["id"]
    out = json.loads(routines.handle_routine_update({"routine": rid, "when": {"event": "time", "at": "20:00"}}))
    assert out.get("ok") is False and "remove it and add it again" in out.get("error", "")


def test_compiled_routine_stays_listed_when_the_house_keeps_its_automation(routines):
    house = FakeHouse()
    rid = add(routines, house, house_action={"service": "light.turn_on", "entity_ids": ["light.porch"]})["routine"]["id"]
    house.keep = True
    assert "error" in json.loads(routines.handle_routine_remove({"routine": rid}, house=house))
    assert [r["id"] for r in json.loads(routines.handle_routine_list({}))["routines"]] == [rid]


def test_compiled_routine_add_with_a_house_action_asks_like_a_service_call():
    from _shared import _matrix
    assert _matrix.decide("routine_add", {"do": "x", "house_action": {"service": "light.turn_on"}}).verdict == _matrix.ASK
    assert _matrix.decide("routine_add", {"do": "x"}).verdict == _matrix.FREE


def test_compiled_routine_once_or_trial_stays_with_the_model(routines):
    call = {"service": "light.turn_on", "entity_ids": ["light.porch"]}
    house = FakeHouse()
    once = add(routines, house, house_action=call, once=True)  # the house would fire it daily
    trial = add(routines, house, house_action=call, trial_days=7)
    assert [once["runs_in"], trial["runs_in"]] == ["model", "model"] and not house.created
    assert "one-off" in once["note"] and "trial" in trial["note"]


# --- the away-lock: a leave routine with away_min and a lock_if_idle house action (T-0332) -----
AWAY = FIX["away_lock"]


def test_compiled_routine_contract_the_away_lock_is_the_shared_fixture():
    auto, why = compiled.compile_routine(AWAY["routine"], AWAY["call"], set(), devices=AWAY["devices"])
    assert why == "" and auto == AWAY["automation"]


def test_compiled_routine_the_away_lock_triggers_are_the_contracts():
    auto, _ = compiled.compile_routine(AWAY["routine"], AWAY["call"], set(), devices=AWAY["devices"])
    place = AWAY["routine"]["when"]["place"]
    for edge, got in (("leave", auto["trigger"][0]), ("arrive", auto["action"][1]["wait_for_trigger"][0])):
        row = json.loads(json.dumps(TRIGGERS["triggers"][edge]["core"]).replace("<when.place>", place)
                         .replace("<phone>", "phone-1"))
        assert got == row, edge


@pytest.mark.parametrize("away_min, idle_s", [(1, 120), (5, 270), (60, 3570)])
def test_compiled_routine_the_lock_waits_as_long_as_the_owner_is_gone_and_never_under_two_minutes(away_min, idle_s):
    routine = json.loads(json.dumps(AWAY["routine"]))
    routine["when"]["away_min"] = away_min
    auto, _ = compiled.compile_routine(routine, AWAY["call"], set(), devices=AWAY["devices"])
    assert auto["action"][1]["timeout"] == away_min * 60
    assert auto["action"][3]["data"]["params"] == {"idle_s": idle_s}


@pytest.mark.parametrize("change", [
    {"away_min": None}, {"away_min": 0}, {"away_min": 61}, {"away_min": "five"},  # no or a bad window
    {"action": "lock_screen"}, {"action": "sleep"}, {"action": "run_command"},  # not an action that cannot interrupt
    {"device_id": "pc-gone"},  # not a paired device
    {"service": "light.turn_off"},  # a leave routine compiles only as the away-lock
])
def test_compiled_routine_any_other_leave_routine_stays_with_the_model(change):
    routine = json.loads(json.dumps(AWAY["routine"]))
    call = dict(AWAY["call"])
    if "away_min" in change:
        if change["away_min"] is None:
            routine["when"].pop("away_min")
        else:
            routine["when"]["away_min"] = change["away_min"]
    for key in ("action", "device_id", "service"):
        if key in change:
            call[key] = change[key]
    auto, why = compiled.compile_routine(routine, call, {"light.porch"}, devices=AWAY["devices"])
    assert auto is None and why, change


class PairedHouse(FakeHouse):
    def devices(self):
        return {"pc-1": "desktop", "phone-1": "android"}


def test_compiled_routine_lock_my_pc_when_i_leave_runs_in_the_house_and_the_phone_watches_the_place(routines):
    routines.handle_place_remember({"label": "home", "at": "29.76,-95.36"})
    house = PairedHouse()
    out = json.loads(routines.handle_routine_add({
        "when": {"event": "leave", "place": "home", "away_min": 5}, "do": "lock my PC",
        "house_action": {"service": "device_control.run", "device_id": "pc-1", "action": "lock_if_idle"}}, house=house))
    assert out["runs_in"] == "house" and house.created[0]["mode"] == "restart"
    place = out["routine"]["when"]["place"]
    snap = routines.snapshot()
    assert snap["routines"] == [] and snap["house_places"] == [place]


@pytest.mark.parametrize("away_min", [0, 61, "soon"])
def test_compiled_routine_a_bad_away_min_is_refused_with_its_range(routines, away_min):
    routines.handle_place_remember({"label": "home", "at": "29.76,-95.36"})
    out = json.loads(routines.handle_routine_add({
        "when": {"event": "leave", "place": "home", "away_min": away_min}, "do": "lock my PC"}, house=PairedHouse()))
    assert out["ok"] is False and "1 to 60" in out["error"]


def test_compiled_routine_the_away_lock_listens_to_the_owners_phones_only():
    """Another paired device (a PC that is not the owner's to trust for this) cannot report the
    owner home to cancel the lock, or away to start it (security review of d6fdf59e)."""
    devices = {"pc-1": "desktop", "phone-1": "android", "phone-2": "Android", "tablet": "desktop"}
    auto, _ = compiled.compile_routine(AWAY["routine"], AWAY["call"], set(), devices=devices)
    senders = {t["event_data"]["device_id"] for t in auto["trigger"] + auto["action"][1]["wait_for_trigger"]}
    assert senders == {"phone-1", "phone-2"}
    auto, why = compiled.compile_routine(AWAY["routine"], AWAY["call"], set(), devices={"pc-1": "desktop"})
    assert auto is None and "phone" in why
