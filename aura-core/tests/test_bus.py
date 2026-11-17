"""The event bus's own guarantees, apart from any integration."""

from __future__ import annotations

import asyncio

import pytest

from aura.bus import EventBus


@pytest.mark.asyncio
async def test_block_till_done_returns_when_the_only_pending_task_is_already_finished():
    """A finished task still in the pending set must not spin the helper.

    A task leaves the set from its done callback, which runs on the loop's
    next turn. `gather` over finished tasks is itself finished, and awaiting
    a finished future never suspends — so the helper looped without ever
    giving the callback its turn (GitLab's first pipeline, 27 Aug 2026:
    120 s in `async_block_till_done` with one `Task finished` in the set).
    The state is built as the trace showed it: a done task in the set with no
    callback left to remove it.
    """
    bus = EventBus(asyncio.get_running_loop())
    task = asyncio.get_running_loop().create_task(asyncio.sleep(0))
    await task
    bus._pending.add(task)
    await asyncio.wait_for(bus.async_block_till_done(), timeout=2)
    assert not bus._pending


@pytest.mark.asyncio
async def test_block_till_done_waits_for_a_listener_and_for_what_it_fires():
    """The helper's promise: every scheduled listener, including the ones a
    listener's own event schedules, has finished when it returns."""
    bus = EventBus(asyncio.get_running_loop())
    seen: list[str] = []

    async def first(event):
        await asyncio.sleep(0.01)
        seen.append("first")
        bus.fire("second")

    async def second(event):
        await asyncio.sleep(0.01)
        seen.append("second")

    bus.listen("first", first)
    bus.listen("second", second)
    bus.fire("first")
    await bus.async_block_till_done()
    assert seen == ["first", "second"]
    assert not bus._pending
