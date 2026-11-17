"""Routines, people and places (aura-hud/routines.py).

The owner, 2026-09-27: "Every time you connect to my car, start my playlist on
shuffle", "when I get to my girlfriends house, send her a text letting her know
im here", "it should be able to learn who my girlfriend is".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-hud")
routines = plugin.routines
# The real doors, kept before the autouse fixture below swaps the class methods for fakes.
REAL_ADAPTER_DOOR = routines._AdapterErase.forget_window
REAL_DESKTOP_DOOR = routines._DesktopErase.forget_window


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def no_real_stores(monkeypatch):
    """`forget_recent` reaches the adapter and the house's desktops by default; no test may.
    The default doors answer "nothing there" unless a test passes its own."""
    monkeypatch.setattr(routines._AdapterErase, "forget_window", lambda self, minutes: {"artifacts": 0})
    monkeypatch.setattr(routines._DesktopErase, "forget_window", lambda self, minutes: {"offline": 0, "answers": []})


def call(handler, args, **kw):
    return json.loads(handler(args, **kw))


def test_a_relation_is_learned_and_moves_to_the_person_named_last():
    call(routines.handle_person_remember, {"name": "Cameron", "relations": ["my girlfriend"], "phone": "+1 555 0100"})
    found = call(routines.handle_people_find, {"query": "my girlfriend"})["people"]
    assert [p["name"] for p in found] == ["Cameron"] and found[0]["phone"] == "+1 555 0100"
    # A new girlfriend is not a second one; a friend can be many people.
    call(routines.handle_person_remember, {"name": "Alex", "relations": ["girlfriend", "friend"]})
    call(routines.handle_person_remember, {"name": "Sam", "relations": ["friend"]})
    assert [p["name"] for p in call(routines.handle_people_find, {"query": "girlfriend"})["people"]] == ["Alex"]
    assert {p["name"] for p in call(routines.handle_people_find, {"query": "friend"})["people"]} == {"Alex", "Sam"}
    assert call(routines.handle_person_remember, {"relation": "boss"})["ok"] is False


def test_a_place_is_remembered_from_the_car_or_an_address():
    here = call(routines.handle_place_remember, {"label": "Home", "at": "41.7374,-40.5811"})
    assert here["ok"] and here["place"]["radius_m"] == 150
    looked_up = call(
        routines.handle_place_remember, {"label": "Cameron's house", "address": "12 Oak St, Elmford", "person": "Cameron"},
        geocode=lambda q: {"places": [{"lat": 42.03, "lon": -40.62, "address": "12 Oak St, Elmford, Vale"}]},
    )
    assert looked_up["place"]["lat"] == 42.03 and looked_up["place"]["address"].startswith("12 Oak")
    missing = call(routines.handle_place_remember, {"label": "Nowhere", "address": "zzz"}, geocode=lambda q: {"places": []})
    assert missing["ok"] is False
    assert call(routines.handle_place_remember, {"label": "Work"})["ok"] is False
    # Remembering "Home" again moves it, it does not add a second one.
    call(routines.handle_place_remember, {"label": "home", "at": "41.8,-40.6"})
    assert [p["label"] for p in call(routines.handle_place_list, {})["places"]] == ["Home", "Cameron's house"]


def test_routines_the_owner_asked_for_are_saved_read_back_and_synced():
    sent = []

    def emit(event, payload):
        sent.append((event, payload))
        return True

    car = call(routines.handle_routine_add, {"when": {"event": "car_connected"}, "do": "Start my Rocky 3 playlist on shuffle"}, emit=emit)
    assert car["ok"] and car["said"] == "Every time the phone connects to the car: Start my Rocky 3 playlist on shuffle"
    assert car["routine"]["cooldown_min"] == 10 and sent[-1][0] == routines.SYNC_EVENT

    # An arrival needs the place first; a missing one says how to fix it.
    no_place = call(routines.handle_routine_add, {"when": {"event": "arrive", "place": "Cameron's house"}, "do": "Text Cameron I'm here"})
    assert no_place["ok"] is False and "place_remember" in no_place["error"]
    call(routines.handle_place_remember, {"label": "Cameron's house", "at": "42.03,-40.62", "person": "Cameron"})
    arrive = call(routines.handle_routine_add, {
        "when": {"event": "arrive", "place": "her house (Cameron)", "after": "17:00"},
        "do": "Text Cameron at +15550100 that I'm here",
        "allow_without_asking": [{"action": "send_sms", "to": "+15550100"}, {"action": "run_shell", "to": "x"}],
    }, emit=emit)
    assert arrive["ok"] and arrive["routine"]["when"]["place_label"] == "Cameron's house"
    # Only a text or a call can be asked for unasked, and the phone is still the one to allow it.
    assert arrive["routine"]["allow_without_asking"] == [{"action": "send_sms", "to": "+15550100"}]
    assert "asks once" in arrive["note"]
    assert "(only between 17:00 and 23:59)" in arrive["said"]

    snap = sent[-1][1]
    assert len(snap["routines"]) == 2 and snap["places"][0]["label"] == "Cameron's house"
    assert set(snap["places"][0]) == {"id", "label", "lat", "lon", "radius_m"}

    # A place a routine watches cannot be forgotten from under it.
    assert call(routines.handle_place_forget, {"label": "Cameron's house"})["ok"] is False


def test_a_routine_is_paused_changed_spent_and_removed():
    call(routines.handle_routine_add, {"when": {"event": "time", "at": "7:30", "days": ["Monday", "tue"]}, "do": "Read my calendar", "name": "Morning"})
    assert call(routines.handle_routine_add, {"when": {"event": "time", "at": "25:00"}, "do": "x"})["ok"] is False
    paused = call(routines.handle_routine_update, {"routine": "morning", "enabled": False})
    assert paused["routine"]["enabled"] is False and paused["routine"]["when"]["days"] == ["mon", "tue"]
    assert call(routines.handle_routine_add, {"when": {"event": "arrive_at", "place": "x"}, "do": "y"})["ok"] is False
    call(routines.handle_place_remember, {"label": "Home", "at": "41.7,-40.6"})
    spent = call(routines.handle_routine_add, {"when": {"event": "arrive", "place": "home"}, "do": "Remind me to call Mom", "once": True})
    assert spent["said"].startswith("Next time you arrive at Home")
    after = json.loads(routines.handle_routine_done(spent["routine"]["id"]))
    assert [r["name"] for r in after["routines"]] == ["Morning"]
    assert call(routines.handle_routine_remove, {"routine": "Morning"})["ok"]
    assert call(routines.handle_routine_list, {})["routines"] == []


def test_the_phone_pulls_its_copy_with_the_hud_command():
    call(routines.handle_routine_add, {"when": {"event": "car_disconnected"}, "do": "Tell me where I parked"})
    out = json.loads(plugin._command("@sid-1 routines"))
    assert out["routines"][0]["when"]["event"] == "car_disconnected"


def test_the_console_views_and_switches_routines_through_the_command():
    call(routines.handle_person_remember, {"name": "Cameron", "relations": ["girlfriend"]})
    call(routines.handle_place_remember, {"label": "Home", "at": "41.7,-40.6"})
    added = call(routines.handle_routine_add, {"when": {"event": "arrive", "place": "home"}, "do": "Text Cameron I got home"})
    rid = added["routine"]["id"]
    view = json.loads(plugin._command("@sid-1 routines view"))
    assert view["routines"][0]["said"].startswith("Every time you arrive at Home")
    assert view["people"][0]["relations"] == ["girlfriend"]
    paused = json.loads(plugin._command(f"@sid-1 routines enable {rid} off"))
    assert paused["routines"][0]["enabled"] is False
    # The place is in use; forgetting it says so rather than orphaning the routine.
    assert json.loads(plugin._command("@sid-1 routines forget-place Home"))["ok"] is False
    after = json.loads(plugin._command(f"@sid-1 routines remove {rid}"))
    assert after["routines"] == []
    assert json.loads(plugin._command("@sid-1 routines forget-place Home"))["places"] == []
    assert json.loads(plugin._command("@sid-1 routines forget-person Cameron"))["people"] == []
    assert json.loads(plugin._command("@sid-1 routines nonsense"))["ok"] is False


def test_the_console_adds_and_edits_a_routine_exactly_as_the_tool_would(monkeypatch):
    """T-0549: the console's builder writes through `routines add|update <json>`, the tool's
    own handlers, and the session's phone gets the same sync event the tool sends. What the
    tool takes only by voice (a text sent unasked, a house-run action, a trial) is refused,
    not dropped, so a hand-written command cannot widen what the form may do."""
    sent = []
    monkeypatch.setattr(plugin.hud.STORE, "share", lambda key, event, payload: sent.append((event, payload)) or True)
    call(routines.handle_place_remember, {"label": "Home", "at": "41.7,-40.6"})
    args = {"when": {"event": "arrive", "place": "home", "days": ["mon"]}, "do": "Text Cameron I got home",
            "name": "Home text", "cooldown_minutes": 45, "once": False}
    added = json.loads(plugin._command("@sid-1 routines add " + json.dumps(args)))
    row = added["routines"][0]
    assert sent and sent[-1][0] == routines.SYNC_EVENT and sent[-1][1] == routines.snapshot()
    # The same row the tool writes for the same arguments, id and clock aside.
    by_tool = call(routines.handle_routine_add, args)["routine"]
    assert {k: v for k, v in row.items() if k not in ("id", "created", "said", "runs_in")} == \
        {k: v for k, v in by_tool.items() if k not in ("id", "created")}
    assert row["said"].startswith("Every time you arrive at Home on mon")

    sent.clear()
    edit = {"routine": row["id"], "do": "Text Cameron I'm home", "when": {"event": "time", "at": "18:00"},
            "cooldown_minutes": 5, "once": True, "enabled": False}
    changed = json.loads(plugin._command("@sid-1 routines update " + json.dumps(edit)))
    mine = next(r for r in changed["routines"] if r["id"] == row["id"])
    assert (mine["do"], mine["when"], mine["cooldown_min"], mine["once"], mine["enabled"]) == \
        ("Text Cameron I'm home", {"event": "time", "at": "18:00"}, 5, True, False)
    assert sent and sent[-1][0] == routines.SYNC_EVENT

    # The tool's own refusals come back as they are, and nothing is written.
    before = routines.load()
    bad = json.loads(plugin._command("@sid-1 routines add " + json.dumps({"when": {"event": "time", "at": "25:00"}, "do": "x"})))
    assert bad["ok"] is False and "HH:MM" in bad["error"]
    for key, value in (("allow_without_asking", [{"action": "send_sms", "to": "+15550100"}]),
                       ("house_action", {"service": "light.turn_on", "entity_ids": ["light.porch"]}),
                       ("trial_days", 7)):
        refused = json.loads(plugin._command("@sid-1 routines add " + json.dumps({**args, key: value})))
        assert refused["ok"] is False and key in refused["error"]
    assert json.loads(plugin._command(f"@sid-1 routines update {json.dumps({'routine': row['id'], 'keep': True})}"))["ok"] is False
    for raw in ("not json", "[1, 2]", ""):
        assert json.loads(plugin._command("@sid-1 routines add " + raw))["ok"] is False
    assert json.loads(plugin._command("@sid-1 routines update " + json.dumps({"routine": "nothing like it"})))["ok"] is False
    assert routines.load() == before
    # Hand-written over the socket, not shaped by the form: refused as an answer, never raised
    # out of the /hud command (the tool path has Hermes to catch a raise; this one has nothing).
    hostile = ["[" * 100_000 + "]" * 100_000]
    hostile += [json.dumps({**args, k: v}) for k, v in (
        ("when", []), ("when", {"event": 5}), ("when", {"event": "time", "at": 730}),
        ("when", {"event": "time", "at": "07:30", "days": "mon"}), ("do", 5), ("do", None),
        ("name", ["x"]), ("cooldown_minutes", "soon"), ("cooldown_minutes", [1]), ("once", {}),
        # The review of T-0549: JSON's Infinity makes int() raise OverflowError, and a number
        # where a list of days goes raised TypeError, out of the /hud command.
        ("cooldown_minutes", float("inf")), ("when", {"event": "time", "at": "07:30", "days": 5}),
        ("when", {"event": "leave", "away_min": float("inf")}), ("when", {"event": "pc_idle", "idle_min": float("inf")}))]
    hostile += [json.dumps({"routine": row["id"], k: v}) for k, v in (
        ("routine", 5), ("when", "arrive"), ("do", ["x"]), ("cooldown_minutes", "x"), ("enabled", []))]
    for raw in hostile:
        sub = "update" if raw.startswith('{"routine"') else "add"
        assert isinstance(json.loads(plugin._command(f"@sid-1 routines {sub} {raw}")), dict), raw[:80]


def test_a_playbook_runs_when_he_says_its_phrase():
    # The owner, 2026-10-06: "have playbooks". One he asks for by name, kept with the
    # routines, and put in front of the model every session so the phrase is enough.
    out = call(routines.handle_routine_add, {
        "name": "Movie night", "when": {"event": "asked", "phrase": "movie night"},
        "do": "Dim the living room to 20%, start Jellyfin on the TV, and set do-not-disturb",
    })
    assert out["ok"] is True
    assert out["said"].startswith('When you say "movie night":')
    assert call(routines.handle_routine_add, {"when": {"event": "asked"}, "do": "x"})["ok"] is False
    section = routines.playbook_section()
    assert '"movie night"' in section and "Dim the living room" in section
    call(routines.handle_routine_update, {"routine": "Movie night", "enabled": False})
    assert routines.playbook_section() == ""


def test_no_playbooks_no_prompt_line():
    call(routines.handle_routine_add, {"when": {"event": "car_connected"}, "do": "Play Rocky on shuffle"})
    assert routines.playbook_section() == ""


def test_a_name_heard_differently_each_time_still_finds_the_place_and_the_person():
    # 2026-10-07 in the car: saved as "Issa's house" (person "Issa"), asked for as
    # "Isis House", and the contact is "Isa". Exact words matched none of them, so the
    # routine was never made.
    call(routines.handle_place_remember, {"label": "Issa's house", "at": "41.6005,-40.6105", "person": "Issa"})
    call(routines.handle_place_remember, {"label": "Home", "at": "41.7374,-40.5811"})
    data = routines.load()
    for heard in ("Isis House", "Isa's house", "isas house", "Issa"):
        assert routines.find_place(data, heard)["label"] == "Issa's house", heard
    assert routines.find_place(data, "the office") is None
    assert routines.find_place(data, "Hope") is None  # one letter from "Home" is too far for a short word
    call(routines.handle_person_remember, {"name": "Issa", "relations": ["girlfriend"]})
    assert [p["name"] for p in call(routines.handle_people_find, {"query": "Isa"})["people"]] == ["Issa"]
    added = call(routines.handle_routine_add, {"name": "tell Ren I'm here", "when": {"event": "arrive", "place": "Isis House"},
                                               "do": "Text Ren that I'm here"})
    assert added["ok"] and added["routine"]["when"]["place_label"] == "Issa's house"


def test_a_birthday_is_remembered_and_found_when_it_comes_up():
    import datetime
    ok = json.loads(routines.handle_person_remember({"name": "Isa", "relations": ["girlfriend"], "birthday": "1998-10-10"}))
    assert ok["ok"] and ok["person"]["birthday"] == "10-10"
    assert json.loads(routines.handle_person_remember({"name": "Bob", "birthday": "13-40"}))["ok"] is False
    routines.handle_person_remember({"name": "Mom", "relations": ["mom"], "birthday": "01-02"})
    data = routines.load()
    soon = routines.upcoming_birthdays(data, 7, datetime.date(2026, 10, 8))
    assert [(b["name"], b["days_until"]) for b in soon] == [("Isa", 2)]
    # Past this year's: next year's, and a year boundary counts.
    assert routines.upcoming_birthdays(data, 90, datetime.date(2026, 12, 30))[0]["name"] == "Mom"
    assert routines.birthday_of("02-29") == "02-29"
    leap = {"people": [{"name": "Leap", "birthday": "02-29"}]}
    assert routines.upcoming_birthdays(leap, 0, datetime.date(2027, 2, 28))[0]["days_until"] == 0
    listed = json.loads(routines.handle_people_find({"birthdays_within_days": 400}))
    assert {b["name"] for b in listed["birthdays"]} == {"Isa", "Mom"}


def test_the_ledger_keeps_due_dates_lists_them_soonest_first_and_rolls_recurring_ones():
    import datetime
    ledger = plugin.ledger
    add = lambda **a: json.loads(ledger.handle_ledger_add(a))
    assert add(title="Car registration", due="2026-10-20", kind="renewal")["ok"]
    refill = add(title="Lipitor refill", due="2026-10-09", kind="refill", every_days=30)
    assert refill["ok"]
    assert add(title="TV warranty", due="2027-03-01", kind="warranty")["ok"]
    assert add(title="x", due="next friday")["ok"] is False
    assert add(title="x", due="2026-10-10", kind="taxes")["ok"] is False
    today = datetime.date(2026, 10, 8)
    soon = json.loads(ledger.handle_ledger_list({"within_days": 14}, today=today))["due"]
    assert [(i["title"], i["days_until"]) for i in soon] == [("Lipitor refill", 1), ("Car registration", 12)]
    done = json.loads(ledger.handle_ledger_done({"title": "lipitor"}))
    assert done["item"]["due"] == "2026-11-08" and not done["item"]["done"]
    json.loads(ledger.handle_ledger_done({"title": "registration"}))
    left = json.loads(ledger.handle_ledger_list({"within_days": 400}, today=today))["due"]
    assert [i["title"] for i in left] == ["Lipitor refill", "TV warranty"]
    overdue = json.loads(ledger.handle_ledger_list({"within_days": 0}, today=datetime.date(2026, 12, 1)))["due"]
    assert overdue[0]["days_until"] < 0


def test_a_reply_and_a_later_time_are_triggers():
    """The owner, 2026-10-08: "send a text / add something to my calendar when it got a
    response ... and can't queue things for later like that"."""
    import datetime

    call(routines.handle_person_remember, {"name": "Grandma Lori", "relations": ["grandma"], "phone": "+1 555 0142"})
    reply = call(routines.handle_routine_add, {
        "when": {"event": "message", "from": "grandma"}, "do": "Add the time she gives to my calendar", "once": True})
    assert reply["ok"] and reply["routine"]["when"] == {"event": "message", "from": "Grandma Lori", "from_phone": "+1 555 0142"}
    assert reply["said"] == "Next time Grandma Lori messages you: Add the time she gives to my calendar"
    nobody = call(routines.handle_routine_add, {"when": {"event": "message", "from": "Zed"}, "do": "x"})
    assert nobody["ok"] is False and "people_remember" in nobody["error"]
    assert call(routines.handle_routine_add, {"when": {"event": "message"}, "do": "x"})["ok"] is False

    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    later = call(routines.handle_routine_add, {"when": {"event": "time", "at": "09:00", "date": tomorrow}, "do": "Remind me to call the bank"})
    # A dated time is one-off even when `once` was not said.
    assert later["ok"] and later["routine"]["once"] is True and later["routine"]["when"]["date"] == tomorrow
    assert later["said"] == f"When it is 09:00 on {tomorrow}: Remind me to call the bank"
    assert call(routines.handle_routine_add, {"when": {"event": "time", "at": "09:00", "date": "2020-01-01"}, "do": "x"})["ok"] is False
    assert call(routines.handle_routine_add, {"when": {"event": "time", "at": "09:00", "date": "soon"}, "do": "x"})["ok"] is False


def test_pc_routines_are_their_own_events_and_never_the_phones():
    """T-0142: routines that run on the PC itself (aura_desktop's watcher). Their own
    events, so the phone -- which fires every `time` routine it is sent -- and the house's
    compiler (only `time`) both leave them alone."""
    sit = call(routines.handle_routine_add, {"when": {"event": "pc_active"}, "do": "Say good morning and read my calendar"})
    assert sit["ok"] and sit["routine"]["when"] == {"event": "pc_active", "idle_min": 5}
    assert sit["said"] == "Every time you sit down at the PC (after 5 minutes away): Say good morning and read my calendar"
    away = call(routines.handle_routine_add, {"when": {"event": "away_from_pc", "idle_min": 15}, "do": "Lock it"})
    assert away["ok"] and away["routine"]["when"] == {"event": "pc_idle", "idle_min": 15}
    assert away["said"] == "Every time you step away from the PC for 15 minutes: Lock it"
    at = call(routines.handle_routine_add, {"when": {"event": "pc_time", "at": "08:30", "days": ["mon"]}, "do": "Open the standup notes"})
    assert at["ok"] and at["routine"]["when"] == {"event": "pc_time", "at": "08:30", "days": ["mon"]}
    assert at["said"] == "Every time it is 08:30 on the PC on mon: Open the standup notes"
    # The idle threshold is minutes the desktop's probe can see: 1 to 240.
    assert call(routines.handle_routine_add, {"when": {"event": "pc_idle", "idle_min": 0}, "do": "x"})["ok"] is False
    assert call(routines.handle_routine_add, {"when": {"event": "pc_idle", "idle_min": 999}, "do": "x"})["ok"] is False
    assert call(routines.handle_routine_add, {"when": {"event": "pc_time"}, "do": "x"})["ok"] is False
    # The phone's one-time grant is not offered for a routine the phone never runs.
    granted = call(routines.handle_routine_add, {"when": {"event": "pc_active"}, "do": "Text Sam I'm in",
                                                 "allow_without_asking": [{"action": "send_sms", "to": "Sam"}]})
    assert granted["ok"] is False and "phone" in granted["error"]
    # The console's list says where each runs.
    assert sit["runs_in"] == "pc"
    listed = {r["id"]: r["runs_in"] for r in call(routines.handle_routine_list, {})["routines"]}
    assert listed[sit["routine"]["id"]] == "pc" and listed[at["routine"]["id"]] == "pc"
    # The contract says each runs on the PC, on the model path.
    contract = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts" / "routine-triggers.json").read_text())
    for event in ("pc_active", "pc_idle", "pc_time"):
        assert event in routines.EVENTS
        assert contract["triggers"][event]["core"] is None and contract["triggers"][event]["runs_on"] == "pc"


def test_the_view_carries_the_newest_phones_place_watch_only_when_a_place_routine_needs_it():
    """T-0170: the phone's report is phone-wide, so the view carries one (the newest across
    phones) and the console draws it beside every place routine, ageing it by `at`."""
    class House:
        asked = 0

        def armed(self):
            House.asked += 1
            return [{"device_id": "old", "at": 100.0, "state": "armed"},
                    {"device_id": "new", "at": 200.0, "state": "not_armed", "reason": "location_off"}]

    call(routines.handle_routine_add, {"when": {"event": "car_disconnected"}, "do": "Tell me where I parked"})
    assert routines.view(house=House())["place_watch"] is None and House.asked == 0
    call(routines.handle_place_remember, {"label": "Home", "at": "41.7,-40.6"})
    call(routines.handle_routine_add, {"when": {"event": "arrive", "place": "home"}, "do": "Say welcome home"})
    assert routines.view(house=House())["place_watch"]["device_id"] == "new"

    class Down:
        def armed(self):
            raise OSError("the house is down")

    assert routines.view(house=Down())["place_watch"] is None


def test_an_edit_says_no_to_empty_values_and_to_a_bare_write():
    """T-0592 (the review of T-0549): an empty do/when answered ok and changed nothing, and a
    bare `routines add` was called an unknown command."""
    row = call(routines.handle_routine_add, {"when": {"event": "car_connected"}, "do": "Say the weather"})["routine"]
    for empty in ({"do": ""}, {"when": {}}):
        out = call(routines.handle_routine_update, {"routine": row["id"], **empty})
        assert out["ok"] is False, empty
    for bare in ("routines add", "routines update"):
        out = json.loads(plugin._command(f"@sid-1 {bare}"))
        assert out["ok"] is False and "one JSON object" in out["error"]


def test_editing_what_a_routine_does_drops_its_unasked_text_grant():
    """A grant to text one person without asking belongs to the instruction it was given for;
    a new instruction or trigger asks again (the review of T-0549)."""
    row = call(routines.handle_routine_add, {
        "when": {"event": "car_connected"}, "do": "Text Ren I'm on my way",
        "allow_without_asking": [{"action": "send_sms", "to": "+15550100"}]})
    assert row["ok"] is True, row
    rid = row["routine"]["id"]
    assert next(r for r in routines.load()["routines"] if r["id"] == rid).get("allow_without_asking")
    out = call(routines.handle_routine_update, {"routine": rid, "do": "Text Sam I'm on my way"})
    assert out["ok"] is True and "asks again" in out.get("said", "") + out.get("note", "")
    stored = next(r for r in routines.load()["routines"] if r["id"] == rid)
    assert not stored.get("allow_without_asking")


def test_the_writes_that_sync_the_phone_have_one_list():
    assert set(routines.WRITES) >= {"add", "update", "remove", "enable"}


class FakeHouse:
    """The house's erase door as a made-up object: answers `body`, or raises `error`."""

    def __init__(self, body=None, error=None):
        self.body, self.error, self.asked = body, error, []

    def forget_window(self, minutes):
        self.asked.append(minutes)
        if self.error:
            raise self.error
        return self.body

    def delete(self, automation_id):
        return True


class _Refused(Exception):
    def __init__(self, status):
        self.response = type("R", (), {"status_code": status})()


def _forget_in_a_made_up_window(monkeypatch, house):
    """One person saved two minutes ago on a made-up clock, then `forget_recent` for ten minutes."""
    now = 1_800_000_000
    monkeypatch.setattr(routines.time, "time", lambda: now - 120)
    call(routines.handle_person_remember, {"name": "Bob Example", "relations": ["friend"]})
    return call(routines.handle_forget_recent, {"minutes": 10}, house=house, now=now)


def test_forget_recent_names_the_house_stores_when_the_house_refuses(monkeypatch):
    house = FakeHouse(error=_Refused(403))
    out = _forget_in_a_made_up_window(monkeypatch, house)
    # The plugin's own erase still happened; only the house is named as kept, with the status.
    assert out["ok"] and {k: v for k, v in out["erased"].items() if not k.startswith(("adapter", "desktop", "hermes"))} == {
        "habit_turns": 0, "list_items": 0, "routines": 0, "places": 0, "people": 1}
    assert house.asked == [10]
    assert set(routines.HOUSE_NOT_ERASED.values()) <= set(out["not_erased"])
    assert out["house_error"] == "the house refused (status 403)"
    assert "house_recorder_rows" not in out["erased"] and "house_memory_notes" not in out["erased"]


def test_forget_recent_names_the_house_stores_when_the_house_is_unreachable(monkeypatch):
    out = _forget_in_a_made_up_window(monkeypatch, FakeHouse(error=OSError("connection refused at 10.0.0.1")))
    assert out["ok"] and set(routines.HOUSE_NOT_ERASED.values()) <= set(out["not_erased"])
    assert out["house_error"] == "the house could not be reached"
    assert "10.0.0.1" not in json.dumps(out)  # a fixed hint, never the failure's own text


def test_forget_recent_names_only_the_store_the_house_did_not_confirm(monkeypatch):
    out = _forget_in_a_made_up_window(monkeypatch, FakeHouse({"minutes": 10, "recorder": {"states": 2, "events": 0}, "memory": None}))
    assert out["erased"]["house_recorder_rows"] == 2 and "house_memory_notes" not in out["erased"]
    assert routines.HOUSE_NOT_ERASED["memory"] in out["not_erased"]
    assert routines.HOUSE_NOT_ERASED["recorder"] not in out["not_erased"]


def test_forget_recent_without_a_configured_house_never_claims_the_house_erased(monkeypatch):
    from _shared import _house

    monkeypatch.setattr(_house, "configured", lambda: False)
    out = _forget_in_a_made_up_window(monkeypatch, None)
    assert out["ok"] and set(routines.HOUSE_NOT_ERASED.values()) <= set(out["not_erased"])


def test_forget_recent_erases_what_was_saved_in_the_window_and_says_what_it_cannot(monkeypatch):
    """T-1395. A made-up clock: Ann, Home and the car routine are an hour old, the rest two minutes."""
    habits = plugin.habits
    clock = {"t": 1_800_000_000}
    monkeypatch.setattr(routines.time, "time", lambda: clock["t"])
    old = clock["t"] - 3600
    monkeypatch.setattr(habits, "_guest", lambda sid: False)
    monkeypatch.setattr(habits, "_probe", lambda sid: False)
    say = lambda text, when: habits.note_turn(session_id="s", user_message=text, platform="tui", now=when)  # noqa: E731

    clock["t"] = old
    say("what is the weather", old)
    call(routines.handle_person_remember, {"name": "Ann Example", "relations": ["friend"]})
    call(routines.handle_place_remember, {"label": "Home", "at": "41.7,-40.5"})
    call(routines.handle_routine_add, {"when": {"event": "car_connected"}, "do": "Start the old playlist"})
    clock["t"] = old + 3600 - 120
    say("remember my new friend", clock["t"])
    call(routines.handle_person_remember, {"name": "Bob Example", "relations": ["friend"]})
    call(routines.handle_person_remember, {"name": "Ann Example", "notes": "changed in the window"})
    call(routines.handle_place_remember, {"label": "Gym", "at": "41.8,-40.6"})
    call(routines.handle_place_remember, {"label": "Cafe", "at": "41.9,-40.7"})
    call(routines.handle_routine_add, {"when": {"event": "arrive", "place": "Gym"}, "do": "Start the gym playlist"})
    # A routine saved earlier that uses a place made in the window keeps that place.
    data = routines.load()
    cafe = next(p for p in data["places"] if p["label"] == "Cafe")
    data["routines"].append({"id": "oldcafe1", "name": "cafe", "do": "say hi", "when": {"event": "arrive", "place": cafe["id"]}, "created": old})
    data["people"].append({"name": "No Stamp", "relations": []})
    routines._save(data)
    # A list item from the hour before stays; the two added in the window go with the window.
    clock["t"] = old
    plugin.lists.handle_lists({"action": "add", "list": "shopping", "item": "oat milk"})
    clock["t"] = old + 3600 - 120
    plugin.lists.handle_lists({"action": "add", "list": "shopping", "item": "tent pegs"})
    plugin.lists.handle_lists({"action": "add", "list": "camping", "item": "stove"})
    clock["t"] = old + 3600

    sent = []
    house = FakeHouse({"minutes": 10, "recorder": {"states": 3, "events": 2}, "memory": {"count": 1}})
    out = call(routines.handle_forget_recent, {"minutes": 10}, emit=lambda e, p: sent.append(e) or True, house=house)
    assert out["ok"] and out["erased"] == {"habit_turns": 1, "list_items": 2, "routines": 1, "places": 1, "people": 1,
                                           "house_recorder_rows": 5, "house_memory_notes": 1,
                                           "adapter_artifacts": 0, "desktop_screenshots": 0,
                                           "hermes_screen_pictures": 0}
    assert house.asked == [10]
    assert len(out["kept"]) == 1 and "Cafe" in out["kept"][0]
    # Every store confirmed erased, so none is named; the rest still is.
    assert out["not_erased"] == list(routines.NOT_ERASED) and any("history" in line for line in out["not_erased"])
    left = routines.load()
    assert {p["name"] for p in left["people"]} == {"Ann Example", "No Stamp"}
    assert {p["label"] for p in left["places"]} == {"Home", "Cafe"}
    assert {r["do"] for r in left["routines"]} == {"Start the old playlist", "say hi"}
    assert [t["ask"] for t in habits.load()["turns"]] == ["what is the weather"]
    assert [x["name"] for x in plugin.lists.load()] == ["shopping"]
    assert [i["text"] for i in plugin.lists.load()[0]["items"]] == ["oat milk"]
    assert routines.SYNC_EVENT in sent  # the phone stops watching the erased routine

    for bad in (0, 1441, "soon", None, -5):
        assert call(routines.handle_forget_recent, {"minutes": bad})["ok"] is False
    assert routines.load() == left


class FakeAdapter:
    """The adapter's erase door: answers `body`, or raises `error`."""

    def __init__(self, body=None, error=None):
        self.body, self.error, self.asked = body, error, []

    def forget_window(self, minutes):
        self.asked.append(minutes)
        if self.error:
            raise self.error
        return self.body


class FakeDesktops:
    """The house's desktops: `answers` are the replies of the machines that were asked."""

    def __init__(self, answers=(), offline=0, error=None):
        self.out, self.error, self.asked = {"offline": offline, "answers": list(answers)}, error, []

    def forget_window(self, minutes):
        self.asked.append(minutes)
        if self.error:
            raise self.error
        return self.out


def _forget(adapter=None, desktops=None, minutes=10, now=1_800_000_000):
    return call(routines.handle_forget_recent, {"minutes": minutes}, house=FakeHouse({"recorder": {"states": 0}, "memory": {"count": 0}}),
                now=now, adapter=adapter or FakeAdapter({"artifacts": 0}), desktops=desktops or FakeDesktops())


def test_forget_recent_counts_the_adapters_artifacts_and_the_desktops_screenshots():
    adapter = FakeAdapter({"minutes": 10, "artifacts": 4})
    desktops = FakeDesktops([{"status": "ok", "result": {"erased": 2, "undated": 1}},
                             {"status": "ok", "result": {"erased": 3, "undated": 0}}])
    out = _forget(adapter, desktops)
    assert adapter.asked == [10] and desktops.asked == [10]
    assert out["erased"]["adapter_artifacts"] == 4 and out["erased"]["desktop_screenshots"] == 5
    assert routines.ADAPTER_NOT_ERASED not in out["not_erased"] and routines.DESKTOP_NOT_ERASED not in out["not_erased"]
    assert "adapter_error" not in out and "desktop_error" not in out


@pytest.mark.parametrize("error, why", [(_Refused(401), "the adapter refused (status 401)"), (_Refused(503), "the adapter refused (status 503)"),
                                        (OSError("refused at 10.0.0.2:8643"), "the adapter could not be reached")])
def test_forget_recent_names_the_adapters_files_when_the_adapter_did_not_confirm(error, why):
    out = _forget(FakeAdapter(error=error))
    assert out["ok"] and routines.ADAPTER_NOT_ERASED in out["not_erased"] and "adapter_artifacts" not in out["erased"]
    assert out["adapter_error"] == why and "10.0.0.2" not in json.dumps(out)
    assert routines.DESKTOP_NOT_ERASED not in out["not_erased"]  # the other store is judged by its own answer


@pytest.mark.parametrize("body", [{}, {"artifacts": "3"}, {"artifacts": True}, {"artifacts": -1}, [3]])
def test_forget_recent_does_not_take_a_count_the_adapter_never_gave(body):
    out = _forget(FakeAdapter(body))
    assert routines.ADAPTER_NOT_ERASED in out["not_erased"] and "adapter_artifacts" not in out["erased"]


def test_forget_recent_leaves_a_desktop_named_that_is_offline_or_said_no():
    out = _forget(desktops=FakeDesktops([{"status": "ok", "result": {"erased": 2}}], offline=1))
    assert out["erased"]["desktop_screenshots"] == 2 and routines.DESKTOP_NOT_ERASED in out["not_erased"]
    assert out["desktop_error"] == "1 desktop is offline"
    for status, why in (("denied", "a desktop's owner declined"), ("approval_required", "a desktop is waiting for its owner's answer"),
                        ("unsupported", "a desktop's agent cannot do this"), ("error", "a desktop could not erase every screenshot")):
        out = _forget(desktops=FakeDesktops([{"status": status, "error": "/home/secret/shots failed"}]))
        assert routines.DESKTOP_NOT_ERASED in out["not_erased"] and out["desktop_error"] == why
        assert "secret" not in json.dumps(out) and "desktop_screenshots" not in out["erased"]


def test_forget_recent_names_the_desktops_when_the_house_cannot_list_them():
    out = _forget(desktops=FakeDesktops(error=OSError("no route to 10.0.0.3")))
    assert routines.DESKTOP_NOT_ERASED in out["not_erased"] and out["desktop_error"] == "the desktops could not be reached"
    assert "10.0.0.3" not in json.dumps(out)


def test_forget_recent_with_no_desktop_paired_has_nothing_to_erase_there():
    out = _forget(desktops=FakeDesktops())
    assert out["erased"]["desktop_screenshots"] == 0 and routines.DESKTOP_NOT_ERASED not in out["not_erased"]


def test_forget_recent_one_unconfirmed_desktop_keeps_the_store_named_beside_the_count():
    out = _forget(desktops=FakeDesktops([{"status": "ok", "result": {"erased": 1}}, {"status": "denied"}]))
    assert out["erased"]["desktop_screenshots"] == 1 and routines.DESKTOP_NOT_ERASED in out["not_erased"]


def test_forget_recent_erases_the_pictures_it_saved_itself_inside_the_window(home):
    folder = home / routines.SCREENS_DIR
    folder.mkdir(parents=True)
    now = 1_800_000_000
    ms = lambda age_min: int((now - age_min * 60) * 1000)  # noqa: E731
    fresh, edge, old = (folder / f"desk_1-{ms(2)}.jpg", folder / f"desk_2-{ms(9.9)}.jpg", folder / f"desk_1-{ms(11)}.jpg")
    other, linked = folder / "notes.txt", folder / f"desk_1-{ms(1)}.jpg.bak"
    for f in (fresh, edge, old, other, linked):
        f.write_bytes(b"x")
    target = home / "elsewhere.jpg"
    target.write_bytes(b"precious")
    (folder / f"desk_3-{ms(1)}.jpg").symlink_to(target)
    out = _forget(now=now)
    assert out["erased"]["hermes_screen_pictures"] == 2
    assert not fresh.exists() and not edge.exists() and old.exists() and other.exists() and linked.exists()
    assert target.read_bytes() == b"precious"


def test_forget_recent_reports_a_picture_it_could_not_remove_instead_of_a_count(home, monkeypatch):
    folder = home / routines.SCREENS_DIR
    folder.mkdir(parents=True)
    stuck = folder / f"desk_1-{1_800_000_000 * 1000 - 1000}.jpg"
    stuck.write_bytes(b"x")
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("/secret/path")))
    out = _forget(now=1_800_000_000)
    assert routines.SCREENS_NOT_ERASED in out["not_erased"] and "hermes_screen_pictures" not in out["erased"]
    assert "secret" not in json.dumps(out)


def test_forget_recent_with_no_pictures_folder_has_nothing_there(home):
    assert _forget()["erased"]["hermes_screen_pictures"] == 0


def test_the_folder_forget_recent_clears_is_the_one_the_desktop_tool_fills():
    source = (PLUGIN_DIR.parent / "aura-desktop" / "tools.py").read_text()
    assert f'SCREENS_DIR = "{routines.SCREENS_DIR}"' in source


def _live_phone():
    """The phone module `routines` itself imports: another test file may have loaded the plugin
    again, and then `plugin.phone` here is a stale copy that patching would miss."""
    import importlib

    return importlib.import_module(routines.__package__ + ".phone")


class _Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *a):
        return self.body


def test_the_adapters_door_posts_the_window_to_the_one_route_with_the_key(monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), auth=req.get_header("Authorization"),
                    body=json.loads(req.data), timeout=timeout)
        return _Reply(b'{"minutes": 7, "artifacts": 2}')

    monkeypatch.setattr(_live_phone(), "_adapter", lambda: ("http://adapter.test:8643", "made-up-key"))
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert REAL_ADAPTER_DOOR(routines._AdapterErase(), 7) == {"minutes": 7, "artifacts": 2}
    assert seen == {"url": "http://adapter.test:8643/api/artifacts/forget_window", "method": "POST",
                    "auth": "Bearer made-up-key", "body": {"minutes": 7}, "timeout": seen["timeout"]}
    assert 0 < seen["timeout"] <= 30  # bounded: a hung adapter cannot hold the tool


def test_the_adapters_door_without_a_key_does_not_call(monkeypatch):
    monkeypatch.setattr(_live_phone(), "_adapter", lambda: ("http://adapter.test:8643", ""))
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("called with no key"))
    with pytest.raises(OSError):
        REAL_ADAPTER_DOOR(routines._AdapterErase(), 7)


def test_the_desktop_door_asks_each_paired_desktop_online_and_counts_the_ones_offline(monkeypatch):
    from contextlib import nullcontext

    from _shared import _house

    sent = []
    machines = [{"device_id": "desk-a", "name": "A", "kind": "desktop", "online": True},
                {"device_id": "desk-b", "name": "B", "kind": "desktop", "online": False},
                {"device_id": "phone-1", "name": "P", "kind": "phone", "online": True},
                {"device_id": "desk-c", "name": "C", "kind": "desktop", "online": True}]

    def run_action(c, device_id, action, params, reason):
        sent.append((device_id, action, params))
        return {"ok": True, "response": {"status": "ok", "result": {"erased": 1}}}

    monkeypatch.setattr(_house, "configured", lambda: True)
    monkeypatch.setattr(_house, "client", lambda timeout=20.0: nullcontext(object()))
    monkeypatch.setattr(_house, "devices", lambda c: machines)
    monkeypatch.setattr(_house, "run_action", run_action)
    out = REAL_DESKTOP_DOOR(routines._DesktopErase(), 15)
    assert sent == [("desk-a", "forget_screenshots", {"minutes": 15}), ("desk-c", "forget_screenshots", {"minutes": 15})]
    assert out == {"offline": 1, "answers": [{"status": "ok", "result": {"erased": 1}}] * 2}


def test_the_desktop_door_with_the_house_unset_says_so_rather_than_no_desktops(monkeypatch):
    from _shared import _house

    monkeypatch.setattr(_house, "configured", lambda: False)
    with pytest.raises(RuntimeError):
        REAL_DESKTOP_DOOR(routines._DesktopErase(), 15)


# --------------------------------------------------------------------------- where the copy goes
@pytest.fixture
def screens(monkeypatch) -> list[tuple[str, str]]:
    """Four live sessions: the owner's desk console, phone and car, and a member's. The frames
    the store sends are kept as (session, event)."""
    frames: list[tuple[str, str]] = []

    def emitter(key: str, event: str, payload: dict) -> bool:
        frames.append((key, event))
        return True

    monkeypatch.setattr(plugin.hud, "STORE", plugin.hud.HudStore(
        emitter=emitter, live=lambda: ["desk", "phone", "car", "member"], owner=lambda sid: sid != "member"))
    return frames


def _copies(frames: list[tuple[str, str]]) -> list[str]:
    """The sessions sent the routines copy since the last look, one entry per frame."""
    got = sorted(key for key, event in frames if event == routines.SYNC_EVENT)
    del frames[:]
    return got


def test_the_routines_copy_reaches_every_one_of_the_owners_sessions_and_a_members_stays_put(screens, monkeypatch):
    """T-1473: the phone watches the triggers and listens on sessions of its own (its
    conversation, the car's voice, its console home). The copy went to the changing session
    only, so "text me when I leave work", said at the desk, armed nothing on the phone until
    the app next connected."""
    # forget_recent reaches the house and the adapter; here only its sender matters.
    monkeypatch.setattr(routines, "handle_forget_recent",
                        lambda args, emit=None, **_: json.dumps({"ok": emit(routines.SYNC_EVENT, {})}))
    ctx = plugin.FakeCtx()
    plugin.register(ctx)

    def tool(name: str, args: dict, session: str) -> dict:
        return json.loads(ctx.tools[name]["handler"](args, session_id=session))

    owners = ["car", "desk", "phone"]
    added = tool("routine_add", {"when": {"event": "car_connected"}, "do": "Start my playlist"}, "desk")
    assert added["ok"] and _copies(screens) == owners  # once each: the asker is not sent it twice
    rid = added["routine"]["id"]
    assert tool("routine_update", {"routine": rid, "do": "Start the news"}, "car")["ok"]
    assert _copies(screens) == owners
    assert tool("forget_recent", {"minutes": 10}, "phone")["ok"]
    assert _copies(screens) == owners
    # The console's Routines panel writes through the command.
    assert json.loads(plugin._command(f"@desk routines enable {rid} off"))["routines"][0]["enabled"] is False
    assert _copies(screens) == owners
    # Reading sends nothing.
    json.loads(plugin._command("@desk routines view"))
    assert _copies(screens) == []
    # A member's change is not handed to the owner's screens by this rule, nor the owner's to theirs.
    assert tool("routine_add", {"when": {"event": "car_connected"}, "do": "Say hello"}, "member")["ok"]
    assert _copies(screens) == ["member"]
    assert tool("routine_remove", {"routine": rid}, "desk")["ok"]
    assert _copies(screens) == owners

