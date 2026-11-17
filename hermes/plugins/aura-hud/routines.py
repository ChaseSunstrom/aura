"""Routines, and the people and places they are about.

The owner, 2026-09-27: "make it so jarvis can remember/do things/figure it out
itself, for example, I should be able to say (Every time you connect to my car,
start my playlist on shuffle) ... and something like (when I get to my
girlfriends house, send her a text letting her know im here), or (when I get
home text my girlfriend I got home safely) ... and it should be able to learn
who my girlfriend is".

A **routine** is a trigger and an instruction in the person's own words:

* the trigger is something only the phone sees -- the car linking or going
  (``car_connected`` / ``car_disconnected``), arriving at or leaving a
  remembered place (``arrive`` / ``leave``), or a time of day (``time``) -- with
  an optional window of hours and days, a cooldown, and ``once`` for "next time";
* the instruction is carried out by Jarvis as an ordinary turn when the trigger
  fires (the phone submits it, the car speaks the reply), so it can "figure it
  out": the playlist is found by name, the text is written then.

A **person** is a name with the relations it is known by ("girlfriend", "mom"),
a phone number and an address; a **place** is a label and a point with a
radius, optionally someone's ("Cameron's house"). Routines name places; the
model resolves "my girlfriend" to a person and "her house" to a place.

The phone keeps a copy (``hud.phone.routines`` after every change, sent to the changing
session and the owner's other live ones, and
``/hud @<sid> routines`` on each connect) and watches the triggers itself:
geofences on a GrapheneOS phone have no Play Services to lean on.

Does not cover what the phone decides: a routine that sends a text or places a
call only does so unasked when the person has allowed exactly that recipient on
the phone itself (``allow_without_asking`` is a request the phone shows once,
never a grant); every other confirm-tier step asks as usual.
"""

from __future__ import annotations

import datetime
import logging
import json
import os
import re
import threading
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Optional

from _shared._jsonstore import err as _err, ok as _ok, read_json, write_json

_LOGGER = logging.getLogger(__name__)

#: The phone's copy is refreshed with this event after every change.
SYNC_EVENT = "hud.phone.routines"

#: `asked` is a playbook: no phone trigger, he says its phrase and the model runs it
#: (`playbook_section` puts every one in the session prompt). The phone ignores it.
#: `message` is a text or chat notification from a remembered person, which the phone
#: reads off its own notifications (the owner, 2026-10-08: "when she replies, add it to
#: my calendar" offered to keep an eye out and had nothing to keep it with).
#: `pc_active` / `pc_idle` / `pc_time` run on the PC itself (aura_desktop's watcher, T-0142):
#: sitting down after `idle_min` away, stepping away for `idle_min`, a time of day there. Their
#: own events rather than `time` with a flag, because the phone fires every `time` routine it is
#: sent and the house's compiler takes `time` too; neither knows these, so neither runs them.
EVENTS = ("car_connected", "car_disconnected", "arrive", "leave", "time", "asked", "message", "pc_active", "pc_idle", "pc_time")
PC_EVENTS = ("pc_active", "pc_idle", "pc_time")
DEFAULT_IDLE_MIN = 5
MAX_IDLE_MIN = 240
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: Actions a routine may ask to run without the phone's per-run confirmation.
UNASKED_ACTIONS = ("send_sms", "place_call")

#: A visit is not a second visit: GPS wanders in and out of a small circle.
DEFAULT_COOLDOWN_MIN = {"arrive": 30, "leave": 30, "car_connected": 10, "car_disconnected": 10, "time": 0, "asked": 0, "message": 10,
                        "pc_active": 30, "pc_idle": 30, "pc_time": 0}
DEFAULT_RADIUS_M = 150
MAX_INSTRUCTION = 600
MAX_ITEMS = 200

#: Relations more than one person can hold at once; any other ("girlfriend",
#: "mom", "boss") moves to the person named last.
SHARED_RELATIONS = frozenset({
    "friend", "coworker", "colleague", "neighbour", "neighbor", "cousin", "sibling", "brother",
    "sister", "family", "kid", "child", "son", "daughter", "grandparent", "aunt", "uncle",
})

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_LOCK = threading.Lock()


def _path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-routines" / "memory.json"


def load() -> dict:
    data = read_json(_path())
    return {k: list(data.get(k) or []) for k in ("people", "places", "routines")}


def _save(data: dict) -> None:
    write_json(_path(), data)


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _key(text: Any) -> str:
    """Lower-case, and without the "my " a person says before a relation."""
    k = _norm(text).casefold()
    for lead in ("my ", "the "):
        if k.startswith(lead):
            k = k[len(lead):]
    return k


#: How alike two names must be to be one: speech-to-text wrote one woman's name "Issa",
#: "Isis" and "Isa" in three minutes (2026-10-07). 0.8 takes those and leaves "Hope"
#: apart from "Home" (0.75).
ALIKE = 0.8


def _alike(a: Any, b: Any) -> bool:
    """Whether two names sound like one, as speech-to-text spells them: possessives aside."""
    x, y = (re.sub(r"['’]s\b", "", _key(v)) for v in (a, b))
    return bool(x and y) and SequenceMatcher(None, x, y).ratio() >= ALIKE


# --- people ---------------------------------------------------------------------


def find_people(data: dict, query: str) -> list[dict]:
    """People whose name or relation matches [query] ("girlfriend", "cam", "Cameron")."""
    q = _key(query)
    if not q:
        return list(data["people"])
    exact = [p for p in data["people"] if q == _key(p["name"]) or q in p.get("relations", [])]
    if exact:
        return exact
    near = [p for p in data["people"] if q in _key(p["name"]) or any(q in r for r in p.get("relations", []))]
    return near or [p for p in data["people"] if _alike(q, p["name"]) or any(_alike(q, w) for w in _key(p["name"]).split())]


def handle_person_remember(args: dict, **_: Any) -> str:
    name = _norm(args.get("name"))
    if not name:
        return _err("name is required: the person's own name (ask if you do not know it)")
    raw = args.get("relations") or ([args["relation"]] if args.get("relation") else [])
    relations = sorted({_key(r) for r in raw if _key(r)})
    with _LOCK:
        data = load()
        person = next((p for p in data["people"] if _key(p["name"]) == _key(name)), None)
        if person is None:
            if len(data["people"]) >= MAX_ITEMS:
                return _err("too many people remembered; forget someone first")
            person = {"name": name, "relations": [], "created": int(time.time())}  # forget_recent reads it
            data["people"].append(person)
        # A relation belongs to one person at a time: a new girlfriend is not a second one.
        for other in data["people"]:
            if other is not person:
                other["relations"] = [r for r in other.get("relations", []) if r not in relations or r in SHARED_RELATIONS]
        person["relations"] = sorted(set(person.get("relations", [])) | set(relations))
        for field in ("phone", "address", "notes"):
            if args.get(field):
                person[field] = _norm(args[field])[:300]
        if args.get("birthday"):
            day = birthday_of(str(args["birthday"]))
            if day is None:
                return _err("birthday is MM-DD or YYYY-MM-DD (\"03-14\")")
            person["birthday"] = day
        person["updated"] = int(time.time())
        _save(data)
    said = f"I'll remember {name}" + (f" is your {' and '.join(relations)}" if relations else "")
    return _ok(person=person, said=said)


def handle_people_find(args: dict, **_: Any) -> str:
    within = args.get("birthdays_within_days")
    if within is not None:
        try:
            days = max(0, min(366, int(within)))
        except (TypeError, ValueError):
            return _err("birthdays_within_days is a number of days")
        return _ok(birthdays=upcoming_birthdays(load(), days, datetime.date.today()))
    return _ok(people=find_people(load(), str(args.get("query") or "")))


def birthday_of(raw: str) -> Optional[str]:
    """ "MM-DD" from "03-14" or "1990-03-14"; None for anything else, or a day no year has."""
    m = re.fullmatch(r"(?:\d{4}-)?(\d{1,2})-(\d{1,2})", raw.strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        datetime.date(2000, month, day)  # a leap year, so 02-29 is a real birthday
    except ValueError:
        return None
    return f"{month:02d}-{day:02d}"


def upcoming_birthdays(data: dict, days: int, today: datetime.date) -> list[dict]:
    """The people whose birthday falls in the next [days] days (0 is today), soonest first.
    A 29 February birthday is kept on 28 February in a year without one."""
    out = []
    for p in data["people"]:
        md = p.get("birthday")
        if not md:
            continue
        month, day = (int(x) for x in md.split("-"))
        for year in (today.year, today.year + 1):
            try:
                when = datetime.date(year, month, day)
            except ValueError:
                when = datetime.date(year, 2, 28)
            if when >= today:
                break
        until = (when - today).days
        if until <= days:
            out.append({"name": p["name"], "birthday": md, "days_until": until,
                        "relations": p.get("relations", []), "phone": p.get("phone")})
    return sorted(out, key=lambda b: b["days_until"])


def handle_person_forget(args: dict, **_: Any) -> str:
    q = _key(args.get("name"))
    with _LOCK:
        data = load()
        keep = [p for p in data["people"] if _key(p["name"]) != q]
        if len(keep) == len(data["people"]):
            return _err(f"nobody called {args.get('name')!r} is remembered")
        data["people"] = keep
        _save(data)
    return _ok(forgot=args.get("name"))


# --- places ---------------------------------------------------------------------


def find_place(data: dict, label: str) -> Optional[dict]:
    """A place by its exact label first, then by a label or owner the words contain."""
    q = _key(label)
    if not q:
        return None
    for p in data["places"]:
        if _key(p["label"]) == q:
            return p
    for p in data["places"]:
        if q in _key(p["label"]) or _key(p["label"]) in q:
            return p
    for p in data["places"]:
        if p.get("person") and _key(p["person"]) in q:
            return p
    # Last, a label that sounds like the words: the name is heard differently each time.
    return next((p for p in data["places"] if _alike(q, p["label"])), None)


def _point(raw: Any) -> Optional[tuple[float, float]]:
    if isinstance(raw, dict) and "lat" in raw and "lon" in raw:
        raw = f"{raw['lat']},{raw['lon']}"
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", str(raw or ""))
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def handle_place_remember(args: dict, geocode: Optional[Callable[[str], dict]] = None, **_: Any) -> str:
    label = _norm(args.get("label"))
    if not label:
        return _err("label is required, e.g. \"home\" or \"Cameron's house\"")
    point = _point(args.get("at"))
    if point is None and args.get("lat") is not None and args.get("lon") is not None:
        point = _point(f"{args['lat']},{args['lon']}")
    address = _norm(args.get("address"))
    if point is None and address:
        found = (geocode or _geocode)(address)
        hits = found.get("places") or []
        if not hits:
            return _err(f"could not find {address!r} ({found.get('error', 'no match')}); ask for a fuller address")
        point = (hits[0]["lat"], hits[0]["lon"])
        address = hits[0].get("address") or address
    if point is None:
        return _err("give `at` (\"lat,lon\": a car turn says where the car is, or use phone_action "
                    "get_location) or an `address`")
    radius = max(50, min(int(args.get("radius_m") or DEFAULT_RADIUS_M), 2000))
    with _LOCK:
        data = load()
        place = next((p for p in data["places"] if _key(p["label"]) == _key(label)), None)
        if place is None:
            if len(data["places"]) >= MAX_ITEMS:
                return _err("too many places remembered; forget one first")
            place = {"id": uuid.uuid4().hex[:8], "label": label, "created": int(time.time())}
            data["places"].append(place)
        place.update(lat=round(point[0], 6), lon=round(point[1], 6), radius_m=radius)
        if address:
            place["address"] = address[:300]
        if args.get("person"):
            place["person"] = _norm(args["person"])
        _save(data)
    return _ok(place=place)


def handle_place_list(args: dict, **_: Any) -> str:
    return _ok(places=load()["places"])


def handle_place_forget(args: dict, **_: Any) -> str:
    with _LOCK:
        data = load()
        place = find_place(data, str(args.get("label") or ""))
        if place is None:
            return _err(f"no place called {args.get('label')!r}")
        users = [r["name"] for r in data["routines"] if r.get("when", {}).get("place") == place["id"]]
        if users:
            return _err(f"{place['label']} is used by: {', '.join(users)}; remove those routines first")
        data["places"] = [p for p in data["places"] if p is not place]
        _save(data)
    return _ok(forgot=place["label"])


def _geocode(address: str) -> dict:
    from . import places as _places  # the plugin's own Photon client

    return _places.search(address, limit=1)


# --- routines -------------------------------------------------------------------

_EVENT_ALIASES = {
    "connect": "car_connected", "car": "car_connected", "drive_start": "car_connected",
    "disconnect": "car_disconnected", "drive_end": "car_disconnected", "park": "car_disconnected",
    "arrive_at": "arrive", "enter": "arrive", "arrival": "arrive",
    "exit": "leave", "leave_from": "leave", "depart": "leave",
    "sit_down": "pc_active", "at_pc": "pc_active", "pc_back": "pc_active", "computer_active": "pc_active",
    "away_from_pc": "pc_idle", "pc_away": "pc_idle", "step_away": "pc_idle", "computer_idle": "pc_idle",
    "pc_at": "pc_time",
}


def _when(raw: Any, data: dict) -> tuple[Optional[dict], str]:
    raw = raw if isinstance(raw, dict) else {}
    event = _key(raw.get("event"))
    event = _EVENT_ALIASES.get(event, event)
    if event not in EVENTS:
        return None, f"when.event must be one of {', '.join(EVENTS)}"
    when: dict[str, Any] = {"event": event}
    if event in ("arrive", "leave"):
        place = find_place(data, str(raw.get("place") or ""))
        if place is None:
            return None, (f"no remembered place called {raw.get('place')!r}. Remember it first with "
                          "place_remember (an address, or where the car is now)")
        when["place"] = place["id"]
        when["place_label"] = place["label"]
        if event == "leave" and raw.get("away_min") not in (None, ""):
            # Only the away-lock reads it (compiled.py): leave, then gone this long.
            try:
                away = int(raw["away_min"])
            except (TypeError, ValueError, OverflowError):
                away = 0
            if not 1 <= away <= 60:
                return None, "when.away_min is how many minutes gone counts, 1 to 60"
            when["away_min"] = away
    if event == "asked":
        phrase = _norm(raw.get("phrase"))
        if not phrase:
            return None, "when.phrase is the words he will say to run it (\"movie night\")"
        when["phrase"] = phrase[:80]
    if event == "message":
        found = find_people(data, str(raw.get("from") or "")) if _norm(raw.get("from")) else []
        if len(found) != 1:
            return None, (f"when.from must name one remembered person; {raw.get('from')!r} matched "
                          f"{len(found)}. Remember them first with people_remember")
        when["from"] = found[0]["name"]
        if found[0].get("phone"):
            when["from_phone"] = found[0]["phone"]
        if _norm(raw.get("contains")):
            when["contains"] = _norm(raw.get("contains"))[:80]
    if event in ("pc_active", "pc_idle"):
        try:
            idle = int(DEFAULT_IDLE_MIN if raw.get("idle_min") in (None, "") else raw["idle_min"])
        except (TypeError, ValueError, OverflowError):
            idle = 0
        if not 1 <= idle <= MAX_IDLE_MIN:
            return None, f"when.idle_min is how many minutes away counts, 1 to {MAX_IDLE_MIN}"
        when["idle_min"] = idle
    if event in ("time", "pc_time"):
        if not _HHMM.match(str(raw.get("at") or "")):
            return None, "when.at is the time of day, \"HH:MM\" (24-hour)"
        when["at"] = raw["at"]
        if raw.get("date"):
            # A date makes it one-off: "tomorrow at nine", "in two hours".
            try:
                day = datetime.date.fromisoformat(str(raw["date"]))
            except ValueError:
                return None, "when.date is \"YYYY-MM-DD\""
            if day < datetime.date.today():
                return None, f"when.date {day} has passed"
            when["date"] = day.isoformat()
    given = raw.get("days")
    days = [str(d)[:3].lower() for d in (given if isinstance(given, (list, tuple)) else []) if str(d)[:3].lower() in DAYS]
    if days:
        when["days"] = days
    for edge in ("after", "before"):
        if raw.get(edge):
            if not _HHMM.match(str(raw[edge])):
                return None, f"when.{edge} is \"HH:MM\""
            when[edge] = raw[edge]
    return when, ""


def describe(routine: dict) -> str:
    """The routine as one sentence, to read back to the person."""
    w = routine["when"]
    trigger = {
        "car_connected": "the phone connects to the car",
        "car_disconnected": "the car disconnects",
        "arrive": f"you arrive at {w.get('place_label')}",
        "leave": f"you leave {w.get('place_label')}",
        "time": f"it is {w.get('at')}" + (f" on {w['date']}" if w.get("date") else ""),
        "asked": 'you say "%s"' % w.get("phrase"),
        "message": f"{w.get('from')} messages you"
        + (f" about \"{w['contains']}\"" if w.get("contains") else ""),
        "pc_active": f"you sit down at the PC (after {w.get('idle_min')} minutes away)",
        "pc_idle": f"you step away from the PC for {w.get('idle_min')} minutes",
        "pc_time": f"it is {w.get('at')} on the PC" + (f" on {w['date']}" if w.get("date") else ""),
    }[w["event"]]
    if w.get("days"):
        trigger += " on " + ", ".join(w["days"])
    if w.get("after") or w.get("before"):
        trigger += f" (only between {w.get('after', '00:00')} and {w.get('before', '23:59')})"
    if w["event"] == "asked" or w.get("date"):
        return f"When {trigger}: {routine['do']}"
    return f"{'Next time' if routine.get('once') else 'Every time'} {trigger}: {routine['do']}"


#: The most playbooks put in a session's prompt; the rest are still in routine_list.
MAX_PLAYBOOKS_IN_PROMPT = 20


def playbook_section(session_info: Any = None) -> str:
    """Sir's playbooks, for every session's prompt: the phrase and what to do.

    Without this a playbook is a routine nothing fires: the model would have to think
    of `routine_list` when he says "movie night". Rendered when the session's prompt is
    built, so one added mid-session is known from the next session (and to
    `routine_list` at once). Empty when there are none, so no line is spent.
    """
    try:
        books = [r for r in load()["routines"] if r.get("enabled", True) and r["when"].get("event") == "asked"]
    except Exception:  # noqa: BLE001 - an unreadable store must not cost the prompt
        return ""
    if not books:
        return ""
    lines = ['- "%s" (%s): %s' % (r["when"]["phrase"], r.get("name") or r["id"], r["do"])
             for r in books[:MAX_PLAYBOOKS_IN_PROMPT]]
    return ("Sir's playbooks. When he says one of these phrases (or asks for it by name), carry out "
            "its steps now with your tools, then say in one sentence what you did:\n" + "\n".join(lines))


def _cooldown(raw: Any, event: str) -> int:
    try:
        minutes = int(raw) if raw is not None else DEFAULT_COOLDOWN_MIN[event]
    except (TypeError, ValueError, OverflowError):  # JSON's Infinity is a float int() refuses
        minutes = DEFAULT_COOLDOWN_MIN[event]
    return max(0, min(minutes, 24 * 60))


def handle_routine_add(args: dict, emit: Optional[Callable[[str, dict], bool]] = None,
                       house: Any = None, **_: Any) -> str:
    house = house or _HouseAutomations()
    instruction = _norm(args.get("do"))
    if not instruction:
        return _err("do is required: what to do, in the person's words (\"start my Rocky playlist on shuffle\")")
    if len(instruction) > MAX_INSTRUCTION:
        return _err(f"do is at most {MAX_INSTRUCTION} characters")
    with _LOCK:
        data = load()
        when, problem = _when(args.get("when"), data)
        if when is None:
            return _err(problem)
        if len(data["routines"]) >= MAX_ITEMS:
            return _err("too many routines; remove one first")
        unasked = []
        if args.get("allow_without_asking") and when["event"] in PC_EVENTS:
            # The grant is the phone's (it asks once, then lets one recipient through during
            # that routine's turn), and a PC routine never runs on the phone: it would ask
            # about a routine it never runs, and the yes would do nothing (review of T-0142).
            return _err("allow_without_asking is for the phone's routines; a routine on the PC asks "
                        "at the console each time instead")
        for item in args.get("allow_without_asking") or []:
            action = _key((item or {}).get("action"))
            to = _norm((item or {}).get("to"))
            if action in UNASKED_ACTIONS and to:
                unasked.append({"action": action, "to": to})
        routine = {
            "id": uuid.uuid4().hex[:8],
            "name": _norm(args.get("name"))[:80] or instruction[:60],
            "when": when,
            "do": instruction,
            "once": bool(args.get("once")) or bool(when.get("date")),
            "cooldown_min": _cooldown(args.get("cooldown_minutes"), when["event"]),
            "enabled": True,
            "allow_without_asking": unasked,
            "created": int(time.time()),
        }
        try:
            trial = max(0, min(int(args.get("trial_days") or 0), 30))
        except (TypeError, ValueError):
            return _err("trial_days is a number of days")
        if trial:
            # A habit Jarvis offered runs for a trial; the habit gate asks once when it ends.
            routine["trial_until"] = int(time.time()) + trial * 86400
        compiled_why = ""
        if args.get("house_action") is not None and trial:
            compiled_why = "a trial routine stays with me until it is kept"
        elif args.get("house_action") is not None:
            # The house runs it with no model turn when it can (compiled.py); the automation is
            # written first, so a routine is never saved pointing at an automation that is not there.
            compiled_why = _compile_into_house(routine, args["house_action"], house)
        data["routines"].append(routine)
        try:
            _save(data)
        except OSError:
            if routine.get("automation_id"):
                house.delete(routine["automation_id"])
            raise
    synced = _sync(emit)
    note = ""
    if unasked:
        note = ("The phone asks once before this routine may "
                + " / ".join(f"{u['action'].replace('_', ' ')} {u['to']}" for u in unasked)
                + " without asking each time.")
    runs_in = _runs_in(routine)
    if compiled_why:
        note = (note + " " if note else "") + f"It runs through me, not the house: {compiled_why}."
    return _ok(routine=routine, said=describe(routine), phone_synced=synced, note=note, runs_in=runs_in)


def handle_routine_list(args: dict, **_: Any) -> str:
    return _ok(routines=[_listed(r) for r in load()["routines"]])


def _runs_in(r: dict) -> str:
    """Where a routine runs: "house" when compiled (no model turn), "pc" when the PC's own
    watcher fires it (aura_desktop/routines.py), "model" for one the phone hands me."""
    if r.get("automation_id"):
        return "house"
    return "pc" if (r.get("when") or {}).get("event") in PC_EVENTS else "model"


def _listed(r: dict) -> dict:
    return {**r, "said": describe(r), "runs_in": _runs_in(r)}


def _find_routine(data: dict, ref: str) -> Optional[dict]:
    q = _key(ref)
    if not q:
        return None
    return next((r for r in data["routines"] if r["id"] == ref or _key(r["name"]) == q), None) or \
        next((r for r in data["routines"] if q in _key(r["name"] + " " + r["do"])), None)


def handle_routine_remove(args: dict, emit: Optional[Callable[[str, dict], bool]] = None,
                          house: Any = None, **_: Any) -> str:
    with _LOCK:
        data = load()
        routine = _find_routine(data, str(args.get("routine") or ""))
        if routine is None:
            return _err(f"no routine matches {args.get('routine')!r}; routine_list shows them")
        # The house copy goes first: dropping the row while the automation survives would leave
        # it firing with nothing in the list to remove it by.
        if routine.get("automation_id") and not (house or _HouseAutomations()).delete(routine["automation_id"]):
            return _err("the house did not remove this routine's automation; nothing changed, try again")
        data["routines"] = [r for r in data["routines"] if r is not routine]
        _save(data)
    _sync(emit)
    return _ok(removed=describe(routine))


#: Said in every forget_recent answer, so "forgotten" is never taken for "everywhere": the tool
#: reaches this plugin's two files, the house's two stores, the adapter's artifacts and the
#: desktops' screenshots, and nothing else.
NOT_ERASED = (
    "this conversation's history (only a whole session can be deleted)",
    "long-term memory notes",
    "the audit log (kept on purpose)",
    "backups already taken",
    "what the phone keeps, and a desktop screenshot saved under a name of its own",
    "a person or place saved earlier and changed in the window (the change stays)",
    "a list item added earlier and ticked, unticked or removed in the window (the change stays)",
)
#: One per store of T-1399, added only when that store did not confirm its own erase.
ADAPTER_NOT_ERASED = "the adapter's saved files (artifacts) made in the window"
DESKTOP_NOT_ERASED = "the screenshots a desktop saved in the window"
SCREENS_NOT_ERASED = "the desktop pictures this assistant kept for itself in the window"
#: Where the aura-desktop tool saves what it looked at; a test pins it to that tool's constant.
SCREENS_DIR = "desktop/screens"
#: A desktop answers only after its owner does, so the wait is long; at most this many are asked
#: so a house of many machines cannot hold the tool for minutes.
MAX_DESKTOPS_ASKED = 5
DESKTOP_WAIT_S = 60.0
ADAPTER_WAIT_S = 10
#: Added per store only when the house did not confirm that store erased (refused, unreachable,
#: or the store is not running), so a failed call never reads as a finished one.
HOUSE_NOT_ERASED = {
    "recorder": "the house's recorder history (states and events) in the window",
    "memory": "the house's memory notes made in the window",
}
MAX_FORGET_MIN = 1440


def _forget_in_house(house: Any, minutes: int) -> tuple[dict, list[str], str]:
    """Ask the house to erase its own rows for the window. Returns (counts, stores still not
    erased, why). Never raises: a refusal or an unreachable house leaves both stores named, and
    `why` is a fixed hint with the status, never the server's text or the address."""
    try:
        body = (house or _HouseAutomations()).forget_window(minutes)
    except Exception as exc:  # noqa: BLE001 - any failure leaves the house stores named as kept
        status = getattr(getattr(exc, "response", None), "status_code", None)
        _LOGGER.warning("the house did not erase its rows for the last %s minutes", minutes)
        why = f"the house refused (status {status})" if status else "the house could not be reached"
        return {}, list(HOUSE_NOT_ERASED.values()), why
    body = body if isinstance(body, dict) else {}
    counts: dict[str, int] = {}
    left: list[str] = []
    recorder, memory = body.get("recorder"), body.get("memory")
    if isinstance(recorder, dict):
        counts["house_recorder_rows"] = sum(int(v) for v in recorder.values() if isinstance(v, int) and not isinstance(v, bool))
    else:
        left.append(HOUSE_NOT_ERASED["recorder"])
    if isinstance(memory, dict) and isinstance(memory.get("count"), int):
        counts["house_memory_notes"] = memory["count"]
    else:
        left.append(HOUSE_NOT_ERASED["memory"])
    return counts, left, ("" if not left else "the house did not confirm every store")


class _AdapterErase:
    """The adapter's owner-only erase of the artifacts made in a window. The adapter stamps the
    window with its own clock; a refusal or a dead adapter raises, and the caller names the store."""

    def forget_window(self, minutes: int) -> dict:
        from urllib import request

        from . import phone

        base, key = phone._adapter()
        if not key:
            raise OSError("no adapter key is set")
        req = request.Request(f"{base}/api/artifacts/forget_window", data=json.dumps({"minutes": minutes}).encode(),
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
        with request.urlopen(req, timeout=ADAPTER_WAIT_S) as r:
            body = json.load(r)
        return body if isinstance(body, dict) else {}


class _DesktopErase:
    """Asks each paired desktop that is online to delete the screenshots it saved in a window.
    The action is Tier 3, so each machine's owner is asked first: a `denied` is an answer, kept
    in `answers`, not an error. Raises when the house cannot list the desktops."""

    def forget_window(self, minutes: int) -> dict:
        from _shared import _house

        if not _house.configured():
            raise RuntimeError("the house is not configured")
        with _house.client(timeout=DESKTOP_WAIT_S) as c:
            machines = [d for d in _house.devices(c) if str(d.get("kind") or "") == "desktop"]
            online = [d for d in machines if d.get("online")]
            answers: list[dict] = []
            for n, machine in enumerate(online):
                if n >= MAX_DESKTOPS_ASKED:
                    answers.append({"status": "error"})  # not asked: never counted as erased
                    continue
                try:
                    out = _house.run_action(c, str(machine["device_id"]), "forget_screenshots", {"minutes": minutes},
                                            f"forget the screenshots saved in the last {minutes} minutes, as the owner asked")
                except Exception:  # noqa: BLE001 - one machine failing must not hide the others' answers
                    answers.append({"status": "error"})
                    continue
                raw = out.get("response")
                answers.append(raw if isinstance(raw, dict) else {"status": "ok" if out.get("ok") else "error"})
        return {"offline": len(machines) - len(online), "answers": answers}


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _forget_in_adapter(adapter: Any, minutes: int) -> tuple[dict, list[str], str]:
    """The adapter's artifacts: (counts, stores still not erased, why). Never raises; `why` is a
    fixed hint with the status, never the adapter's text or its address."""
    try:
        body = (adapter or _AdapterErase()).forget_window(minutes)
    except Exception as exc:  # noqa: BLE001 - any failure leaves the store named as kept
        status = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        _LOGGER.warning("the adapter did not erase its artifacts for the last %s minutes", minutes)
        why = f"the adapter refused (status {status})" if status else "the adapter could not be reached"
        return {}, [ADAPTER_NOT_ERASED], why
    count = _count(body.get("artifacts")) if isinstance(body, dict) else None
    if count is None:
        return {}, [ADAPTER_NOT_ERASED], "the adapter did not confirm the erase"
    return {"adapter_artifacts": count}, [], ""


#: What a desktop's non-ok answer is called. Fixed words: the device's own error text can name a
#: path on that machine.
_DESKTOP_WHY = {
    "denied": "a desktop's owner declined",
    "approval_required": "a desktop is waiting for its owner's answer",
    "unsupported": "a desktop's agent cannot do this",
}


def _forget_on_desktops(desktops: Any, minutes: int) -> tuple[dict, list[str], str]:
    """The desktops' screenshots. A machine counts only on its own `ok`; one offline, declining
    or failing leaves the store named even when another confirmed. No desktop paired is a
    confirmed zero: there is nothing to keep."""
    try:
        body = (desktops or _DesktopErase()).forget_window(minutes)
        answers = [a for a in body.get("answers", []) if isinstance(a, dict)]
        offline = _count(body.get("offline")) or 0
    except Exception:  # noqa: BLE001 - the house could not list or reach them
        _LOGGER.warning("the desktops were not asked to erase their screenshots for the last %s minutes", minutes)
        return {}, [DESKTOP_NOT_ERASED], "the desktops could not be reached"
    erased, why = 0, []
    if offline:
        why.append(f"{offline} desktop is offline" if offline == 1 else f"{offline} desktops are offline")
    confirmed = 0
    for answer in answers:
        count = _count((answer.get("result") or {}).get("erased")) if isinstance(answer.get("result"), dict) else None
        if answer.get("status") == "ok" and count is not None:
            erased, confirmed = erased + count, confirmed + 1
        else:
            text = _DESKTOP_WHY.get(str(answer.get("status")), "a desktop could not erase every screenshot")
            if text not in why:
                why.append(text)
    if not why and not answers:
        return {"desktop_screenshots": 0}, [], ""
    counts = {"desktop_screenshots": erased} if confirmed else {}
    return counts, ([DESKTOP_NOT_ERASED] if why else []), "; ".join(why)


def _forget_local_screens(since: float) -> tuple[dict, list[str]]:
    """The pictures `desktop_screen` saved here, named `<device>-<epoch ms>.jpg`, from the window.
    A link is skipped (it could point anywhere); a name with no stamp is not ours to date."""
    folder = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / SCREENS_DIR
    erased = failed = 0
    try:
        found = sorted(folder.iterdir()) if folder.is_dir() else []
    except OSError:
        return {}, [SCREENS_NOT_ERASED]
    for path in found:
        stamped = re.fullmatch(r".+-(\d{13})\.jpg", path.name)
        if not stamped or int(stamped.group(1)) / 1000 < since or path.is_symlink() or not path.is_file():
            continue
        try:
            path.unlink()
            erased += 1
        except OSError:
            failed += 1
    return ({}, [SCREENS_NOT_ERASED]) if failed else ({"hermes_screen_pictures": erased}, [])


def handle_forget_recent(args: dict, emit: Optional[Callable[[str, dict], bool]] = None,
                         house: Any = None, now: Optional[float] = None, adapter: Any = None,
                         desktops: Any = None, **_: Any) -> str:
    """Erase the habit turns noted, the list items added, and the routines, people and places
    first saved, in the last `minutes` (a list left with no item goes with them). Goes through the same removers as the single forgets, so a routine's house
    automation goes first and a place a surviving routine uses stays. Also asks the house, the
    adapter and each online desktop to erase their own part of the window; a store that did not
    confirm is named in `not_erased`, never counted. Does NOT reach anything in NOT_ERASED; an
    entry with no `created` stamp (saved before the stamp existed) is kept. Each store is judged
    by its own clock, so a window is only as exact as the clocks agree."""
    try:
        minutes = int(args.get("minutes"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        minutes = 0
    if not 1 <= minutes <= MAX_FORGET_MIN:
        return _err(f"minutes is a whole number from 1 to {MAX_FORGET_MIN}")
    from . import habits, lists

    since = (time.time() if now is None else now) - minutes * 60
    data = load()

    def fresh(kind: str) -> list[dict]:
        return [x for x in data[kind] if isinstance(x.get("created"), (int, float)) and x["created"] >= since]

    erased = {"habit_turns": habits.forget_since(since), "list_items": lists.forget_since(since),
              "routines": 0, "places": 0, "people": 0}
    kept: list[str] = []
    # Routines before places: a place is free to go once the routine made with it has gone.
    for kind, remover, ref in (("routines", handle_routine_remove, "id"), ("places", handle_place_forget, "label"),
                               ("people", handle_person_forget, "name")):
        for item in fresh(kind):
            arg = {"routine": item["id"]} if kind == "routines" else {ref: item[ref]}
            out = json.loads(remover(arg, emit=emit, house=house))
            if out.get("ok"):
                erased[kind] += 1
            else:
                kept.append(f"{kind[:-1]} {item.get('name') or item.get('label')}: {out.get('error')}")
    # After the plugin's own: the house is asked last, so what it refuses never stops the local erase.
    counts, house_left, why = _forget_in_house(house, minutes)
    erased.update(counts)
    extra = {"house_error": why} if why else {}
    # Each store is asked and judged on its own: one that fails never stops or hides the others.
    counts, adapter_left, why = _forget_in_adapter(adapter, minutes)
    erased.update(counts)
    extra.update({"adapter_error": why} if why else {})
    counts, desktop_left, why = _forget_on_desktops(desktops, minutes)
    erased.update(counts)
    extra.update({"desktop_error": why} if why else {})
    counts, screens_left = _forget_local_screens(since)
    erased.update(counts)
    left = house_left + adapter_left + desktop_left + screens_left
    return _ok(minutes=minutes, erased=erased, kept=kept, not_erased=list(NOT_ERASED) + left, **extra)


def handle_routine_update(args: dict, emit: Optional[Callable[[str, dict], bool]] = None, **_: Any) -> str:
    with _LOCK:
        data = load()
        routine = _find_routine(data, str(args.get("routine") or ""))
        if routine is None:
            return _err(f"no routine matches {args.get('routine')!r}")
        if routine.get("automation_id") and any(k in args for k in ("enabled", "do", "when", "once", "cooldown_minutes")):
            # The house holds the running copy; changing only this row would leave it stale.
            return _err("this routine runs in the house: remove it and add it again to change it")
        if "enabled" in args:
            routine["enabled"] = bool(args["enabled"])
        # A present-but-empty do/when changed nothing yet answered ok (the review of T-0549).
        for key in ("do", "when"):
            if key in args and not args[key]:
                return _err(f"{key} cannot be empty; leave it out to keep it")
        before = (routine.get("do"), json.dumps(routine.get("when"), sort_keys=True))
        if args.get("do"):
            # Refused, as add refuses it, rather than cut short where nobody sees (review of T-0549).
            if len(_norm(args["do"])) > MAX_INSTRUCTION:
                return _err(f"do is at most {MAX_INSTRUCTION} characters")
            routine["do"] = _norm(args["do"])
        if args.get("when"):
            when, problem = _when(args["when"], data)
            if when is None:
                return _err(problem)
            routine["when"] = when
        if args.get("cooldown_minutes") is not None:
            routine["cooldown_min"] = _cooldown(args["cooldown_minutes"], routine["when"]["event"])
        if "once" in args:
            routine["once"] = bool(args["once"])
        if args.get("keep"):
            routine.pop("trial_until", None)
            routine.pop("trial_asked", None)
        note = ""
        # A grant to text or call one person unasked was given for this instruction and trigger;
        # a new one asks again rather than inheriting it (the review of T-0549).
        if routine.get("allow_without_asking") and before != (routine.get("do"), json.dumps(routine.get("when"), sort_keys=True)):
            routine["allow_without_asking"] = []
            note = "Its unasked texts and calls were dropped with the change; the phone asks again."
        _save(data)
    _sync(emit)
    return _ok(routine=routine, said=describe(routine), **({"note": note} if note else {}))


def handle_routine_done(routine_id: str) -> str:
    """The phone ran a `once` routine: it is spent. Returns the snapshot line for the command."""
    with _LOCK:
        data = load()
        before = len(data["routines"])
        data["routines"] = [r for r in data["routines"] if not (r["id"] == routine_id and r.get("once"))]
        if len(data["routines"]) != before:
            _save(data)
    return json.dumps(snapshot(), ensure_ascii=False)


def take_ended_trials(now: float) -> list[dict]:
    """Trial routines whose week is up and not yet asked about; each is asked about once."""
    with _LOCK:
        data = load()
        ended = [r for r in data["routines"] if r.get("trial_until", now + 1) <= now and not r.get("trial_asked")]
        for r in ended:
            r["trial_asked"] = True
        if ended:
            _save(data)
    return ended


def view(house: Any = None) -> dict:
    """Everything, for the console's Settings > Routines: each routine with its sentence, and
    whether the phone can fire the place ones (T-0170)."""
    data = load()
    return {
        "routines": [_listed(r) for r in data["routines"]],
        "people": data["people"],
        "places": data["places"],
        "place_watch": _place_watch(data, house),
    }


def _place_watch(data: dict, house: Any = None) -> Optional[dict]:
    """The newest phone's place-watch report, when a place routine needs one.

    None when there is no place routine, no report, or the house cannot be asked; the
    console then says there is no report (tests/contracts/routine-armed.json `render`).
    Phone-wide: every fact behind it (permission, location, the watch) is the phone's.
    """
    if not any((r.get("when") or {}).get("event") in ("arrive", "leave") for r in data["routines"]):
        return None
    try:
        reports = (house or _HouseAutomations()).armed()
    except Exception:  # noqa: BLE001 - the routines still draw, without the line
        _LOGGER.warning("could not read the phones' place-watch reports", exc_info=True)
        return None
    return max(reports, key=lambda r: r.get("at") or 0, default=None)


#: What the console's builder may set (T-0549): the tool's own arguments, less the ones only a
#: spoken ask may carry -- a text or call sent unasked (the phone's one-time consent follows a
#: conversation), a routine the house runs with nobody asked (ASK in the matrix), a habit's
#: trial and its keep (the habit gate's question). Anything else is refused, not dropped.
#: The routines sub-commands that change what the phone watches: the aura-hud command
#: emits the phone's sync after each (one list, not a copy in __init__).
WRITES: tuple[str, ...] = ("enable", "remove", "add", "update", "forget-person", "forget-place", "done")

CONSOLE_ARGS = {
    "add": ("when", "do", "name", "once", "cooldown_minutes"),
    "update": ("routine", "enabled", "do", "when", "once", "cooldown_minutes"),
}


def _console_write(sub: str, raw: str) -> dict:
    try:
        args = json.loads(raw)
    except (ValueError, RecursionError):  # RecursionError: a deeply nested array or object
        args = None
    if not isinstance(args, dict):
        return {"ok": False, "error": f"routines {sub} takes one JSON object"}
    extra = sorted(set(args) - set(CONSOLE_ARGS[sub]))
    if extra:
        return {"ok": False, "error": f"{', '.join(extra)}: not set from the console; ask Jarvis for it"}
    handler = handle_routine_add if sub == "add" else handle_routine_update
    return json.loads(handler(args))


def command(arg: str) -> str:
    """`/hud @<sid> routines [view | done <id> | enable <id> on|off | remove <id> |
    add <json> | update <json> | forget-person <name> | forget-place <label>]`: the phone's
    copy (no sub-command), the console's view, and the console's switches and builder. Each
    change answers the new view; a refusal answers `{ok: false, error}` and writes nothing."""
    sub, _, rest = arg.strip().partition(" ")
    rest = rest.strip()
    if sub == "":
        return json.dumps(snapshot(), ensure_ascii=False)
    if sub == "done" and rest:
        return handle_routine_done(rest)
    if sub == "view":
        return json.dumps(view(), ensure_ascii=False)
    if sub in CONSOLE_ARGS and rest:
        out = _console_write(sub, rest)
    elif sub == "enable" and rest:
        ref, _, on = rest.rpartition(" ")
        out = json.loads(handle_routine_update({"routine": ref or on, "enabled": on != "off"}))
    elif sub == "remove" and rest:
        out = json.loads(handle_routine_remove({"routine": rest}))
    elif sub == "forget-person" and rest:
        out = json.loads(handle_person_forget({"name": rest}))
    elif sub == "forget-place" and rest:
        out = json.loads(handle_place_forget({"label": rest}))
    elif sub in CONSOLE_ARGS:
        return json.dumps({"ok": False, "error": f"routines {sub} takes one JSON object"})
    else:
        return json.dumps({"ok": False, "error": f"unknown routines command {sub!r}"})
    if not out.get("ok"):
        return json.dumps(out, ensure_ascii=False)
    return json.dumps(view(), ensure_ascii=False)


def snapshot() -> dict:
    """What the phone keeps: the routines, the places they watch, the people they may text."""
    data = load()
    return {
        # A compiled routine runs in the house (compiled.py), never on the phone too.
        "routines": [r for r in data["routines"] if not r.get("automation_id")],
        # The places a house-run routine waits on (the away-lock, T-0332): the phone watches them
        # and reports their edges as routine_place events -- only these, so the house learns no
        # more of where the owner goes than a routine needs.
        "house_places": sorted({r["when"]["place"] for r in data["routines"]
                                if r.get("automation_id") and r.get("enabled", True)
                                and r["when"].get("event") in ("arrive", "leave") and r["when"].get("place")}),
        "places": [{k: p[k] for k in ("id", "label", "lat", "lon", "radius_m") if k in p} for p in data["places"]],
        "people": [{k: p[k] for k in ("name", "phone") if k in p} for p in data["people"]],
    }


def _sync(emit: Optional[Callable[[str, dict], bool]]) -> bool:
    if emit is None:
        return False
    try:
        return bool(emit(SYNC_EVENT, snapshot()))
    except Exception:  # the phone pulls again on its next connect
        return False


class _HouseAutomations:
    """jarvis-core's automation API, for compiled routines (T-0185). Lazy: tests pass a fake."""

    def entities(self) -> set[str]:
        from _shared import _house
        with _house.client() as c:
            return {s["entity_id"] for s in c.get("/api/states").raise_for_status().json()}

    def devices(self) -> dict[str, str]:
        """The paired devices, id -> kind: a compiled away-lock names one of them as the PC and
        listens to the phones only."""
        from _shared import _house
        with _house.client() as c:
            return {str(d["device_id"]): str(d.get("kind") or "") for d in _house.devices(c) if d.get("device_id")}

    def armed(self) -> list[dict]:
        """Each phone's newest place-watch report (jarvis-core keeps them past a disconnect)."""
        from _shared import _house
        if not _house.configured():
            return []
        with _house.client(timeout=3.0) as c:
            return [r for r in c.get("/api/config/companion/armed").raise_for_status().json() if isinstance(r, dict)]

    def create(self, automation: dict) -> str:
        from _shared import _house
        with _house.client() as c:
            body = c.post("/api/config/automation/create", json={"automation": automation}).raise_for_status().json()
        return str((body.get("automation") or {}).get("id") or "")

    def forget_window(self, minutes: int) -> dict:
        """The owner-only erase of the house's recorder rows and memory notes in the last
        `minutes`. Raises when the house is not set up or answers an error status; the caller
        turns that into 'still not erased'. The house stamps the window with its own clock."""
        from _shared import _house
        if not _house.configured():
            raise RuntimeError("the house is not configured")
        with _house.client() as c:
            body = c.post("/api/config/forget_window", json={"minutes": minutes}).raise_for_status().json()
        return body if isinstance(body, dict) else {}

    def delete(self, automation_id: str) -> bool:
        """True once the house no longer holds it (already gone counts)."""
        from _shared import _house
        try:
            with _house.client() as c:
                r = c.post("/api/config/automation/delete", json={"automation_id": automation_id})
            if r.status_code != 404:
                r.raise_for_status()
            return True
        except Exception:  # noqa: BLE001 - the caller keeps the routine so it can be retried
            _LOGGER.warning("could not delete the compiled routine's automation %s", automation_id)
            return False


def _compile_into_house(routine: dict, call: Any, house: Any) -> str:
    """Store a compiled routine's automation and mark the routine; answer why not, or ""."""
    from . import compiled
    try:
        devices = house.devices() if hasattr(house, "devices") else {}
        automation, why = compiled.compile_routine(routine, call, house.entities(), devices=devices)
        if automation is None:
            return why
        automation_id = house.create(automation)
    except Exception as exc:  # noqa: BLE001 - the house unreachable: the model path still works
        return f"the house could not take it ({type(exc).__name__}); it stays with me"
    if not automation_id:
        return "the house did not say which automation it made; it stays with me"
    routine["automation_id"] = automation_id
    routine["house_action"] = call
    return ""
