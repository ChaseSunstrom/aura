"""The relay's two lines per turn: what makes a phone's "no answer at 17:42" findable here."""
from __future__ import annotations

import json
import logging

from aura_adapter.turn_log import TurnLog


def _prompt(sid: str, rid: int, text: str = "hi") -> str:
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": "prompt.submit",
                       "params": {"session_id": sid, "text": text}})


def _complete(sid: str, status: str = "complete") -> str:
    return json.dumps({"jsonrpc": "2.0", "method": "event", "params": {
        "type": "message.complete", "session_id": sid, "payload": {"text": "ok", "status": status}}})


def _lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "aura_adapter.turns"]


def test_turn_log_start_and_end_carry_the_session_surface_and_elapsed(caplog):
    caplog.set_level(logging.INFO, logger="aura_adapter.turns")
    turns = TurnLog(lambda sid: "android" if sid == "s1" else None)
    turns.sent(_prompt("s1", 7))
    turns.received('{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta"}}')
    turns.received(_complete("s1"))
    start, end = _lines(caplog)
    assert start == "turn start session=s1 id=7 surface=android"
    assert end.startswith("turn end session=s1 id=7 outcome=complete elapsed_ms=")


def test_turn_log_names_an_error_and_a_socket_that_closed_mid_turn(caplog):
    caplog.set_level(logging.INFO, logger="aura_adapter.turns")
    turns = TurnLog()
    turns.sent(_prompt("a", 1))
    turns.received(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": 4002, "message": "busy"}}))
    turns.sent(_prompt("b", 2))
    turns.close()
    lines = _lines(caplog)
    assert "outcome=error 4002 busy" in lines[1]
    assert "session=b id=2 outcome=socket-closed" in lines[3]


def test_turn_log_marks_a_local_answer_and_ignores_everything_else(caplog):
    caplog.set_level(logging.INFO, logger="aura_adapter.turns")
    turns = TurnLog()
    turns.sent(_prompt("s", 3, "what time is it"), local=True)
    turns.sent('{"method":"session.create","params":{}}')
    turns.sent("not json prompt.submit")
    turns.received(_complete("s"))  # the local answer was never relayed: nothing to close
    turns.received("not json")
    assert _lines(caplog) == ["turn start session=s id=3 surface=- answered-locally"]


def test_hermes_first_streamed_text_past_its_deadline_is_one_breach_per_turn(caplog):
    """T-0589: the first token is timed from prompt.submit to the turn's first message.delta;
    later deltas of the same turn are not first tokens."""
    from aura_adapter.turn_log import Deadlines
    caplog.set_level(logging.WARNING, logger="aura_adapter.turns")
    deadlines = Deadlines({"first_token_ms": -1.0})
    turns = TurnLog(deadlines=deadlines)
    delta = '{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta","session_id":"s","payload":{"text":"a"}}}'
    turns.sent(_prompt("s", 1))
    turns.received(delta)
    turns.received(delta)
    turns.received(_complete("s"))
    turns.received(delta)
    assert deadlines.breaches == {"first_token_ms": 1}
    assert [r.getMessage().split(" ms=")[0] for r in caplog.records] == ["voice deadline breached hop=first_token"]


def test_status_says_when_a_hop_last_breached_and_how_many_fell_in_the_last_hour():
    """T-1431: a deploy's smoke turns on a cold model breach the first-token deadline, and the
    bare count then read "3" until the next deploy with nothing to say they were hours old.
    The count since start stays; beside it are the breaches of the last hour and the seconds
    since the last one. Not covered: which turns they were (the warning lines carry the session)."""
    from aura_adapter.turn_log import Deadlines
    now = [1000.0]
    deadlines = Deadlines({"first_token_ms": 6000.0, "transcribe_ms": 2500.0}, clock=lambda: now[0])
    quiet = deadlines.status()
    assert quiet["first_token"] == {"deadline_ms": 6000, "breaches": 0, "last_hour": 0, "last_breach_s": None}

    for ms in (29875, 11822, 8904):  # the three warm-up turns of 2026-11-15, 30 s apart
        assert deadlines.check("first_token_ms", ms)
        now[0] += 30
    assert not deadlines.check("first_token_ms", 5999)
    fresh = deadlines.status()
    assert fresh["first_token"] == {"deadline_ms": 6000, "breaches": 3, "last_hour": 3, "last_breach_s": 30}
    assert fresh["transcribe"]["last_hour"] == 0 and fresh["transcribe"]["last_breach_s"] is None

    now[0] += 3550  # the first two are now more than an hour old, the third is not
    assert deadlines.status()["first_token"] == {
        "deadline_ms": 6000, "breaches": 3, "last_hour": 1, "last_breach_s": 3580}
    now[0] += 60
    assert deadlines.status()["first_token"] == {
        "deadline_ms": 6000, "breaches": 3, "last_hour": 0, "last_breach_s": 3640}


def test_breach_times_are_bounded_so_a_stuck_card_cannot_grow_the_adapter():
    """A card that is down breaches on every turn for days. Only the newest times are kept, so
    `last_hour` stops at the bound while `breaches` keeps the true count."""
    from aura_adapter import turn_log
    deadlines = turn_log.Deadlines({"transcribe_ms": 1.0}, clock=lambda: 5.0)
    for _ in range(turn_log.BREACH_TIMES_KEPT + 10):
        deadlines.check("transcribe_ms", 2)
    hop = deadlines.status()["transcribe"]
    assert hop["breaches"] == turn_log.BREACH_TIMES_KEPT + 10
    assert hop["last_hour"] == turn_log.BREACH_TIMES_KEPT


def test_busy_counts_open_turns_across_connections_and_remembers_the_car():
    from aura_adapter import turn_log
    turn_log._OPEN.clear()
    turn_log._car_at = None
    assert turn_log.busy() == {"turns": 0, "car": False}
    house, car = TurnLog(), TurnLog(lambda sid: "car")
    house.sent(_prompt("s1", 1))
    car.sent(_prompt("s1", 2))  # the same session id on another socket is another turn
    assert turn_log.busy() == {"turns": 2, "car": True}
    house.received(_complete("s1"))
    car.close()  # a socket that went away ends its turns
    assert turn_log.busy()["turns"] == 0
    # The drive outlasts the turn, for ten minutes.
    at = turn_log._car_at
    assert turn_log.busy(now=at + 599)["car"] is True
    assert turn_log.busy(now=at + 601)["car"] is False
    # A prompt the adapter answered itself never opened a turn.
    house.sent(_prompt("s2", 3), local=True)
    assert turn_log.busy()["turns"] == 0
