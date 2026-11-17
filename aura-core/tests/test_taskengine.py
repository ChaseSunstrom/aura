"""The thing that actually runs the work.

`tasks.py` says in its own docstring that it "does not run anything", and for a
while nothing did: `run_background_task` minted an id and fired an event nobody
listened to, while Jarvis Code and research each grew their own unbounded
`ensure_future`. Three jobs at once meant three conversations against one model
server with one KV cache, and the symptom was not an error — it was everything
becoming four times slower at once.

What is pinned here: the queue is bounded and the cap is real, a failure is
retried with backoff rather than lost, cancellation is not a failure, and what
a restart does with the work it cut short is the outcome a person reads: work
still waiting ends failed and says why, running work ends interrupted and is
never run again.
"""

from __future__ import annotations

import asyncio

import pytest

from aura.taskengine import MAX_QUEUED, QueuedWork, TaskEngine
from aura.tasks import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_QUEUED,
    TaskRegistry,
)


class FakeBus:
    def fire(self, event, data):
        pass


class FakeJarvis:
    def __init__(self) -> None:
        self.bus = FakeBus()


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict = {}

    async def load(self):
        return self.data

    async def save(self, data):
        self.data = data


async def _noop(_task_id: str) -> None:
    pass


@pytest.fixture
def registry() -> TaskRegistry:
    jarvis = FakeJarvis()
    reg = TaskRegistry(jarvis, store=MemoryStore())
    jarvis.tasks = reg
    return reg


@pytest.fixture
async def engine(registry):
    """An engine, stopped at the end of the test.

    Without the teardown its pump outlives the test and the event loop it was
    started on, which is a hang rather than a failure — and a hang in a suite is
    a suite nobody runs.
    """
    made = TaskEngine(registry.jarvis, registry, max_concurrent=2)
    registry.jarvis.taskengine = made
    yield made
    await made.async_stop()


# --- the queue ---------------------------------------------------------------


async def test_work_submitted_is_work_run(registry, engine):
    task = await registry.async_add("do the thing")
    ran = asyncio.Event()

    async def worker(task_id: str) -> None:
        assert task_id == task.id
        ran.set()

    assert engine.submit(task.id, worker)
    assert await engine.async_drain(timeout=5)
    assert ran.is_set()


async def test_no_more_than_the_cap_run_at_once(registry, engine):
    """The whole reason the queue exists: every worker ends up talking to one
    model server, and the third concurrent conversation makes the other two slow
    enough to look broken."""
    live = 0
    peak = 0
    release = asyncio.Event()

    async def worker(_task_id: str) -> None:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await release.wait()
        live -= 1

    for _ in range(6):
        task = await registry.async_add("job")
        engine.submit(task.id, worker)

    engine.start()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(engine.running) >= engine.max_concurrent:
            break
    assert peak <= engine.max_concurrent, f"{peak} ran at once, cap is {engine.max_concurrent}"
    release.set()
    assert await engine.async_drain(timeout=5)
    assert peak == engine.max_concurrent, "the cap was never actually reached"


async def test_a_full_queue_refuses_rather_than_growing_for_ever(registry, engine):
    async def worker(_task_id: str) -> None:
        await asyncio.sleep(10)

    for i in range(MAX_QUEUED):
        engine.queue.append(QueuedWork(task_id=f"filler-{i}", kind="background", worker=_noop))
    task = await registry.async_add("one too many")
    assert engine.submit(task.id, worker) is False


async def test_submitting_the_same_task_twice_is_not_an_error(registry, engine):
    task = await registry.async_add("job")

    async def worker(_task_id: str) -> None:
        pass

    assert engine.submit(task.id, worker)
    assert engine.submit(task.id, worker)
    assert len([item for item in engine.queue if item.task_id == task.id]) == 1


# --- failure, retry, backoff -------------------------------------------------


async def test_a_failure_is_retried_and_then_reported(registry, engine, monkeypatch):
    monkeypatch.setattr(TaskEngine, "backoff", staticmethod(lambda attempt: 0.01))
    attempts = 0

    async def worker(_task_id: str) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("the model server refused")

    task = await registry.async_add("flaky")
    engine.submit(task.id, worker, retries=2)
    assert await engine.async_drain(timeout=5)

    assert attempts == 3, "one attempt plus two retries"
    assert registry.get(task.id).status == STATUS_ERROR
    assert "refused" in registry.get(task.id).error


async def test_a_retry_that_works_leaves_the_task_alone(registry, engine, monkeypatch):
    monkeypatch.setattr(TaskEngine, "backoff", staticmethod(lambda attempt: 0.01))
    attempts = 0

    async def worker(task_id: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("not yet")
        await registry.async_update(task_id, status=STATUS_DONE)

    task = await registry.async_add("flaky")
    engine.submit(task.id, worker, retries=1)
    assert await engine.async_drain(timeout=5)
    assert registry.get(task.id).status == STATUS_DONE


def test_the_backoff_grows_and_is_capped_and_jittered():
    first = [TaskEngine.backoff(1) for _ in range(20)]
    third = [TaskEngine.backoff(3) for _ in range(20)]
    assert min(third) > max(first), "backoff does not grow"
    assert max(TaskEngine.backoff(20) for _ in range(20)) <= 200, "backoff is not capped"
    # Jitter: three jobs failing against the same dead server must not retry in
    # lockstep for ever.
    assert len(set(first)) > 1, "no jitter"


# --- cancellation ------------------------------------------------------------


async def test_cancelling_is_not_a_failure(registry, engine):
    started = asyncio.Event()

    async def worker(task_id: str) -> None:
        started.set()
        for _ in range(200):
            registry.raise_if_cancelled(task_id)
            await asyncio.sleep(0.01)

    task = await registry.async_add("long")
    engine.submit(task.id, worker)
    engine.start()
    await asyncio.wait_for(started.wait(), timeout=5)
    await registry.async_update(task.id, status=STATUS_CANCELLED)
    assert await engine.async_drain(timeout=5)

    # Cancelled, not error: somebody asked it to stop and it did.
    assert registry.get(task.id).status == STATUS_CANCELLED


async def test_a_worker_that_ignores_cancellation_still_ends(registry, engine):
    """The registry cannot reach into a coroutine; `async_stop` can."""

    async def worker(_task_id: str) -> None:
        await asyncio.sleep(30)

    task = await registry.async_add("stubborn")
    engine.submit(task.id, worker)
    engine.start()
    await asyncio.sleep(0.05)
    await engine.async_stop()
    assert engine.running == {}


# --- retry by hand -----------------------------------------------------------


async def test_retry_puts_a_finished_task_back(registry, engine):
    task = await registry.async_add("failed once", kind="research")

    async def worker(task_id: str) -> None:
        await registry.async_update(task_id, status=STATUS_DONE)

    engine.submit(task.id, worker)
    assert await engine.async_drain(timeout=5)
    await registry.async_update(task.id, status=STATUS_ERROR, error="boom")

    assert engine.retry(task.id) is True
    assert await engine.async_drain(timeout=5)
    assert registry.get(task.id).status == STATUS_DONE


async def test_retry_refuses_a_task_whose_worker_is_gone(registry, engine):
    task = await registry.async_add("unknown kind", kind="mystery")
    await registry.async_update(task.id, status=STATUS_ERROR)
    engine._workers.clear()
    assert engine.retry(task.id) is False


async def test_status_says_what_is_waiting(registry, engine):
    task = await registry.async_add("job")

    async def worker(_task_id: str) -> None:
        await asyncio.sleep(5)

    engine.submit(task.id, worker)
    status = engine.status()
    assert status["queued"] == 1
    assert status["max_concurrent"] == 2
    assert task.id in status["waiting"]
    assert registry.get(task.id).status == STATUS_QUEUED


# --- what a restart does, as a person sees it ----------------------------------
# Each test is an outcome a person reads on the console after Jarvis came back,
# not a detail of how the engine got there.


async def test_a_task_waiting_at_a_restart_ends_failed_and_says_why(registry, engine):
    """Waiting work is not left at "queued" for ever: the queue did not survive,
    so it ends failed, and says why."""
    task = await registry.async_add("waiting for a slot")
    await registry.async_update(task.id, status=STATUS_QUEUED)
    await registry.async_save()

    second_registry = TaskRegistry(FakeJarvis(), store=registry.store)
    await second_registry.async_load()
    second = TaskEngine(second_registry.jarvis, second_registry)
    second_registry.jarvis.taskengine = second
    second.load()

    after = second_registry.get(task.id)
    assert after.status == STATUS_ERROR
    assert "was waiting when Jarvis stopped" in after.error
    assert second.queue == []


async def test_a_task_running_at_a_restart_ends_interrupted_and_is_not_rerun(registry, engine):
    """Running work ends interrupted, and nothing runs it again: a half-done job
    is reported, not repeated."""
    from aura.tasks import RESTART_ERROR

    task = await registry.async_add("half-done deploy", kind="code")
    await registry.async_update(task.id, status="running")
    await registry.async_save()

    second_registry = TaskRegistry(FakeJarvis(), store=registry.store)
    await second_registry.async_load()
    second = TaskEngine(second_registry.jarvis, second_registry)
    second_registry.jarvis.taskengine = second
    second.load()

    after = second_registry.get(task.id)
    assert after.status == STATUS_ERROR
    assert after.error == RESTART_ERROR
    assert second.queue == []
    assert second.running == {}


async def test_after_a_restart_a_finished_task_cannot_be_retried_by_hand(registry, engine):
    """Retry is a button for this process: a task that failed before a restart
    is refused, because nothing in the new process can run its work."""
    task = await registry.async_add("failed before the restart", kind="research")
    await registry.async_update(task.id, status=STATUS_ERROR, error="boom")
    await registry.async_save()

    second_registry = TaskRegistry(FakeJarvis(), store=registry.store)
    await second_registry.async_load()
    second = TaskEngine(second_registry.jarvis, second_registry)
    second_registry.jarvis.taskengine = second
    second.load()

    assert second.retry(task.id) is False
    assert second_registry.get(task.id).status == STATUS_ERROR


async def test_an_older_store_with_a_queue_and_a_resumed_flag_still_loads(registry, engine):
    """Stores written before the queue stopped being saved carry a "queue" key
    and a per-task "resumed" flag. They must still load: the queue entry is
    ignored, the flag is dropped, and the task it named ends like any other
    waiting work."""
    task = await registry.async_add("waiting before the upgrade")
    await registry.async_update(task.id, status=STATUS_QUEUED)
    old_task = {**task.as_dict(), "resumed": True, "log": []}
    store = MemoryStore()
    store.data = {
        "tasks": [old_task],
        "queue": [{"task_id": task.id, "kind": "background", "idempotent": True}],
    }

    second_registry = TaskRegistry(FakeJarvis(), store=store)
    await second_registry.async_load()
    assert [t.id for t in second_registry.tasks] == [task.id]
    second = TaskEngine(second_registry.jarvis, second_registry)
    second_registry.jarvis.taskengine = second
    second.load()

    after = second_registry.get(task.id)
    assert after.status == STATUS_ERROR
    assert "was waiting when Jarvis stopped" in after.error
    assert not hasattr(after, "resumed")


async def test_the_house_does_not_run_a_job_a_restart_interrupted(tmp_path):
    """Through the house's own path. A house dies with a job running; the next
    house sets up and starts. The job is reported interrupted, and nothing runs
    it again, because the worker that was running it died with the process."""
    from aura.core import Jarvis
    from aura.tasks import RESTART_ERROR

    first = Jarvis(tmp_path)
    await first.async_setup({})
    task = await first.tasks.async_add("read every light", kind="background")

    async def never_finishes(task_id: str) -> None:
        await first.tasks.async_update(task_id, status="running")
        await asyncio.sleep(3600)

    first.taskengine.submit(task.id, never_finishes, kind="background")
    first.taskengine.start()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if first.tasks.get(task.id).status == "running":
            break
    assert first.tasks.get(task.id).status == "running"
    await first.tasks.async_save()
    await first.taskengine.async_stop()

    second = Jarvis(tmp_path)
    await second.async_setup({})
    await second.async_start()
    try:
        after = second.tasks.get(task.id)
        assert after.status == STATUS_ERROR
        assert after.error == RESTART_ERROR
        await asyncio.sleep(0.1)
        assert second.taskengine.running == {}
        assert second.taskengine.queue == []
    finally:
        await second.async_stop()
