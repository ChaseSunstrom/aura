"""lock_if_idle (T-0332): the away-lock's action. It locks only a PC nobody has touched for idle_s
-- what a screensaver does -- so it can run unattended (Tier 1) while lock_screen, which can lock
someone out mid-sentence, stays Tier 3."""

from __future__ import annotations

import dataclasses

import pytest

from aura_desktop.actions import system
from aura_desktop.actions.builtins import TIER_TABLE, all_actions
from aura_desktop.policy import ActionTier


@pytest.fixture()
def locks(monkeypatch):
    """Every locker present; records what ran."""
    ran: list[list[str]] = []
    monkeypatch.setattr(system.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(system, "_run", lambda argv, timeout=8.0: (ran.append(argv), (0, "", ""))[1])
    return ran


def act():
    return next(a for a in all_actions() if a.id == "lock_if_idle")


def idle_for(ctx, seconds):
    return dataclasses.replace(ctx, idle=lambda: seconds)


def test_it_runs_unasked_and_lock_screen_still_asks_every_time():
    assert TIER_TABLE["lock_if_idle"] == ActionTier.AUTO == act().tier
    assert TIER_TABLE["lock_screen"] == ActionTier.CONFIRM


def test_an_idle_pc_is_locked(ctx, locks):
    out = act().run(idle_for(ctx, 400.0), {"idle_s": 300})
    assert out.ok and out.data["locked"] is True and locks


def test_a_pc_in_use_is_not_locked_and_says_so(ctx, locks):
    out = act().run(idle_for(ctx, 42.0), {"idle_s": 300})
    assert not out.ok and locks == []
    assert out.data == {"locked": False, "reason": "in_use", "idle_s": 42}


def test_a_pc_that_cannot_tell_its_idle_time_is_not_locked(ctx, locks):
    out = act().run(idle_for(ctx, None), {"idle_s": 300})
    assert not out.ok and locks == [] and out.data["reason"] == "idle_unknown"


@pytest.mark.parametrize("idle_s", [0, 59, -5, "soon", None])
def test_no_caller_can_ask_it_to_lock_a_pc_in_use(ctx, locks, idle_s):
    """A threshold under a minute would make this lock_screen without the ask."""
    params = {} if idle_s is None else {"idle_s": idle_s}
    out = act().run(idle_for(ctx, 30.0), params)
    if idle_s is None:  # the default is five minutes: 30 s idle is in use
        assert out.data["reason"] == "in_use"
    else:
        assert out.data["reason"] == "bad_idle_s"
    assert not out.ok and locks == []
