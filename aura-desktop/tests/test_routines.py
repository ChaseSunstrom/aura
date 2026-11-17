"""PC routines (T-0142): the phone's run rules, shared through tests/contracts/routine-rules.json,
and the watcher that fires the PC's own routines -- sitting down, stepping away, a time of day."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aura_desktop import routines
from aura_desktop.config import load_config

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "routine-rules.json").read_text())
ZONE = ZoneInfo(CONTRACT["zone"])


def at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=ZONE)


@pytest.mark.parametrize("case", CONTRACT["may_run"], ids=lambda c: c["why"])
def test_may_run_is_the_phones_rule(case):
    routine = {"enabled": case["enabled"], "when": case["when"], "cooldown_min": case["cooldown_min"]}
    now = at(case["now"])
    ago = case["last_fired_min_ago"]
    last = None if ago is None else now.timestamp() - ago * 60
    assert routines.may_run(routine, now, last) is case["runs"]


@pytest.mark.parametrize("case", CONTRACT["next_fire"], ids=lambda c: c["why"])
def test_next_fire_is_the_phones_rule(case):
    got = routines.next_fire({"when": case["when"]}, at(case["now"]))
    assert (got.replace(tzinfo=None).isoformat(timespec="minutes") if got else None) == case["next"]


@pytest.mark.parametrize("case", CONTRACT["prompt"], ids=lambda c: c["name"])
def test_the_prompt_is_the_phones(case):
    assert routines.prompt({"name": case["name"], "do": case["instruction"]}, case["what"]) == case["text"]


def snapshot(*items):
    return {"routines": list(items), "people": [], "places": []}


def routine(rid, event, **when):
    return {"id": rid, "name": rid, "do": f"do {rid}", "enabled": True, "cooldown_min": 0,
            "when": {"event": event, **when}}


class Fired:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, r, text):
        self.calls.append((r["id"], text))


def run(coro):
    return asyncio.run(coro)


def test_only_the_pcs_routines_are_kept():
    fired = Fired()
    w = routines.PcRoutines(fired)
    w.sync(snapshot(routine("a", "pc_active", idle_min=5), routine("t", "time", at="08:00"),
                    routine("c", "car_connected"), routine("p", "pc_time", at="08:30")))
    assert sorted(r["id"] for r in w.routines) == ["a", "p"]


def test_sitting_down_after_long_enough_away_fires_once_and_a_short_break_does_not():
    fired = Fired()
    w = routines.PcRoutines(fired)
    w.sync(snapshot(routine("back", "pc_active", idle_min=5)))
    now = at("2026-10-12T09:00")
    # Idle climbs to six minutes, then input: the stretch was long enough.
    for idle in (0, 120, 240, 360):
        run(w.sample(idle, now))
    run(w.sample(3, now))
    assert [c[0] for c in fired.calls] == ["back"]
    assert "Sir sat down at the PC" in fired.calls[0][1]
    # A two-minute break is not "sitting down".
    for idle in (60, 120):
        run(w.sample(idle, now))
    run(w.sample(2, now))
    assert len(fired.calls) == 1


def test_stepping_away_fires_once_per_stretch():
    fired = Fired()
    w = routines.PcRoutines(fired)
    w.sync(snapshot(routine("away", "pc_idle", idle_min=10)))
    now = at("2026-10-12T09:00")
    for idle in (300, 599, 600, 900, 1200):
        run(w.sample(idle, now))
    assert [c[0] for c in fired.calls] == ["away"]
    run(w.sample(1, now))  # back
    for idle in (300, 601):
        run(w.sample(idle, now))
    assert [c[0] for c in fired.calls] == ["away", "away"]


def test_a_routine_inside_its_cooldown_or_off_its_days_does_not_fire():
    fired = Fired()
    w = routines.PcRoutines(fired)
    cool = routine("away", "pc_idle", idle_min=1)
    cool["cooldown_min"] = 30
    tuesday_only = routine("tue", "pc_idle", idle_min=1, days=["tue"])
    w.sync(snapshot(cool, tuesday_only))
    monday = at("2026-10-12T09:00")
    run(w.sample(61, monday))
    run(w.sample(1, monday))
    run(w.sample(61, monday + dt.timedelta(minutes=10)))
    assert [c[0] for c in fired.calls] == ["away"]


def test_a_pc_time_routine_fires_when_due_and_a_one_off_is_spent():
    fired = Fired()
    spent: list[str] = []
    w = routines.PcRoutines(fired, on_spent=spent.append)
    daily = routine("standup", "pc_time", at="08:30")
    once = routine("bank", "pc_time", at="08:30", date="2026-10-12")
    once["once"] = True
    w.sync(snapshot(daily, once))
    run(w.tick(at("2026-10-12T08:29")))
    assert fired.calls == []
    run(w.tick(at("2026-10-12T08:30")))
    assert sorted(c[0] for c in fired.calls) == ["bank", "standup"]
    assert spent == ["bank"]
    run(w.tick(at("2026-10-12T08:31")))
    assert len(fired.calls) == 2


def test_without_an_adapter_link_the_pc_says_routines_are_unavailable(tmp_path):
    config = load_config(tmp_path / "none.json", env={"AURA_STATE_DIR": str(tmp_path)})
    assert routines.link_settings(config) is None
    assert "adapter" in routines.unavailable_reason(config)


def test_the_link_reads_its_adapter_and_key_file(tmp_path):
    key = tmp_path / "key"
    key.write_text("secret\n")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"adapter_url": "https://jarvis:8643", "adapter_key_file": str(key)}))
    config = load_config(cfg, env={"AURA_STATE_DIR": str(tmp_path)})
    assert routines.link_settings(config, env={}) == ("https://jarvis:8643", "secret")


def test_the_key_can_come_from_the_environment_as_the_console_reads_it(tmp_path):
    config = load_config(tmp_path / "none.json", env={"AURA_STATE_DIR": str(tmp_path), "AURA_ADAPTER_URL": "https://j:8643"})
    assert routines.link_settings(config, env={"AURA_ADAPTER_KEY": "k"}) == ("https://j:8643", "k")
    assert routines.link_settings(config, env={}) is None


def test_an_unreadable_key_file_says_so_without_the_key(tmp_path, caplog):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"adapter_url": "https://j:8643", "adapter_key_file": str(tmp_path / "missing")}))
    config = load_config(cfg, env={"AURA_STATE_DIR": str(tmp_path)})
    assert routines.link_settings(config, env={"AURA_ADAPTER_KEY": "never-logged"}) is None
    assert "missing" in caplog.text and "never-logged" not in caplog.text


def test_nobody_is_at_a_console_to_answer_a_routine():
    deny = routines._answer({"type": "approval.request", "payload": {"request_id": "r1", "title": "ha_call_service"}}, "s")
    assert deny[0][0] == "approval.respond" and deny[0][1] == {"session_id": "s", "request_id": "r1", "choice": "deny"}
    assert "refused" in deny[0][2]
    clarify = routines._answer({"type": "clarify.request", "payload": {"request_id": "q1"}}, "s")
    assert clarify[0][0] == "clarify.respond" and clarify[0][1]["request_id"] == "q1"
    assert routines._answer({"type": "message.complete", "payload": {"text": "Done."}}, "s") == [("", {}, "Done.")]
    failed = routines._answer({"type": "message.complete", "payload": {"status": "error"}}, "s")
    assert "did not finish" in failed[0][2]
    assert routines._answer({"type": "message.complete", "payload": {"status": "interrupted"}}, "s") == []


def test_what_the_watcher_fired_survives_a_new_socket():
    """One watcher for the agent's life: a reconnect while idle does not fire pc_idle again."""
    fired = Fired()
    w = routines.PcRoutines(fired)
    w.sync(snapshot(routine("away", "pc_idle", idle_min=1)))
    now = at("2026-10-12T09:00")
    run(w.sample(61, now))
    w.sync(snapshot(routine("away", "pc_idle", idle_min=1)))  # the copy pulled again on the new socket
    run(w.sample(90, now))
    assert len(fired.calls) == 1
