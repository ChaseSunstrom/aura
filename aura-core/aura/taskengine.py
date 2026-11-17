"""The thing that actually runs the work.

`tasks.py` is the record: what is happening, how far it has got, what it printed.
It says so in its own docstring — *"it does not run anything"* — and for a while
nothing did. `run_background_task` minted an id, fired an event nothing listened
to, and told the model to say the result would arrive later. Jarvis Code and
research each grew their own `ensure_future`, unbounded and unaware of each
other, so three jobs at once meant three concurrent model conversations against
one server with one KV cache.

This is the missing half: a queue, a small pool of workers, and retries with
backoff. The queue is held in memory. What a restart does with the work is in
the next section.

## The shape

    engine.submit(task_id, worker)          # queued, runs when a slot is free
    engine.submit(..., retries=2)           # and again on failure, with backoff

A worker is `async def worker(task_id: str) -> None`. It reports through the
registry it was given, calls `raise_if_cancelled` where stopping is safe, and
raises to fail. That is the whole contract: the engine owns *when* work runs and
*how often it is retried*, and knows nothing about what the work is.

## Why a limit at all

Every worker here eventually talks to one model server. Two coding jobs and a
research run at the same time are three conversations sharing one KV cache, and
the symptom is not an error — it is everything becoming four times slower at
once, which reads as "Jarvis is broken today". `llm.max_concurrent` is the cap;
the queue is what makes exceeding it impossible rather than merely discouraged.

## What a restart does

Nothing is carried across a restart. A worker is a closure in the process that
submitted it, and nothing can rebuild one from what is stored, so the queue is
not persisted. What the next start does with the work that was in it:

- Work that was *waiting* ends failed, with the reason "was waiting when Jarvis
  stopped, and nothing has it now" (`TaskEngine.load`).
- Work that was *running* ends as interrupted, "interrupted when Jarvis
  restarted" (`Task.restored`), and is not run again. Re-running half of
  something that already half-happened is a worse failure than reporting that
  it stopped.
- `retry` works within one process: a task that failed is put back on the queue
  with the worker that ran it. After a restart that worker is gone, so `retry`
  refuses. A scheduled job a restart cut short is the scheduler's to catch up
  (`integrations/schedule`), not this engine's.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .tasks import (
    STATUS_ERROR,
    STATUS_QUEUED,
    STATUS_RUNNING,
    TaskCancelled,
    TaskRegistry,
)

_LOGGER = logging.getLogger(__name__)

__all__ = ["TaskEngine", "QueuedWork", "Worker", "DEFAULT_MAX_CONCURRENT"]

#: How many jobs may run at once. Two, because every one of them ends up talking
#: to the same model server, and the third concurrent conversation does not fail
#: — it makes the other two slow enough to look broken.
DEFAULT_MAX_CONCURRENT = 2

#: How long the queue may get before submissions are refused. A refusal a person
#: can read beats an unbounded queue that quietly grows for an hour.
MAX_QUEUED = 100

#: Retry backoff: 2s, 8s, 32s, capped. Jittered, so three jobs failing on the
#: same dead model server do not retry in lockstep for ever.
RETRY_BASE_SECONDS = 2.0
RETRY_FACTOR = 4.0
RETRY_CAP_SECONDS = 120.0

#: What a task still waiting at a restart says. Shown to a person, so it names
#: what happened and that nothing will pick it up.
WAITING_ERROR = "was waiting when Jarvis stopped, and nothing has it now"

Worker = Callable[[str], Awaitable[None]]


@dataclass
class QueuedWork:
    """One piece of work waiting for a slot, or running in one."""

    task_id: str
    #: What kind of work it is, for the log. Not used to choose a worker.
    kind: str
    #: What runs it. In memory only: nothing is persisted, so nothing restores it.
    worker: Worker
    #: How many more times this may be retried after a failure.
    retries: int = 0
    attempts: int = 0


class TaskEngine:
    """A bounded queue and a small pool of workers, over the task registry."""

    def __init__(
        self,
        jarvis: Any,
        registry: TaskRegistry,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self.jarvis = jarvis
        self.registry = registry
        self.max_concurrent = max(1, int(max_concurrent))
        self.queue: list[QueuedWork] = []
        #: task_id -> the coroutine running it.
        self.running: dict[str, asyncio.Task] = {}
        #: task_id -> the worker that last ran it. Kept after it finishes, so a
        #: failed task can be retried by hand while this process lives.
        self._workers: dict[str, Worker] = {}
        self._wake = asyncio.Event()
        self._pump: asyncio.Task | None = None
        self._stopping = False

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._pump is None or self._pump.done():
            self._pump = asyncio.ensure_future(self._run())

    async def async_stop(self) -> None:
        """Stop taking work and let what is running finish or be cancelled."""
        self._stopping = True
        self._wake.set()
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass
        for task in list(self.running.values()):
            task.cancel()
        self.running.clear()

    # --- submitting ----------------------------------------------------------
    def submit(
        self,
        task_id: str,
        worker: Worker,
        *,
        kind: str = "background",
        retries: int = 0,
    ) -> bool:
        """Queue one piece of work. False when the queue is full."""
        if len(self.queue) >= MAX_QUEUED:
            _LOGGER.error("task engine: the queue is full (%d); refusing %s", MAX_QUEUED, task_id)
            return False
        if any(item.task_id == task_id for item in self.queue) or task_id in self.running:
            return True  # already ours; submitting twice is not an error
        # The registry keeps a bounded list, so forget the workers of tasks it has
        # dropped: a process that runs for weeks must not hold every closure it
        # ever submitted.
        held = {task.id for task in self.registry.tasks}
        for known in [tid for tid in self._workers if tid not in held]:
            del self._workers[known]
        self._workers[task_id] = worker
        self.queue.append(
            QueuedWork(
                task_id=task_id,
                kind=kind,
                worker=worker,
                retries=max(0, int(retries)),
            )
        )
        self._wake.set()
        self.start()
        return True

    def retry(self, task_id: str) -> bool:
        """Put a finished task back on the queue, by hand.

        The button somebody presses after fixing whatever broke. It needs the
        worker that ran the task, which only this process has: after a restart
        there is nothing to run, and this says so by refusing.
        """
        task = self.registry.get(task_id)
        if task is None or not task.finished:
            return False
        worker = self._workers.get(task_id)
        if worker is None:
            return False
        return self.submit(task_id, worker, kind=task.kind)

    # --- what a restart does -------------------------------------------------
    def load(self) -> None:
        """Fail the work a restart left waiting for nobody.

        Called once, after the registry has loaded its tasks and before anything
        is submitted. Nothing is carried across a restart (see the module
        docstring), so a task still at "queued" when the process comes back has
        no queue to be in. `Task.restored` cannot tell that apart from work the
        engine still holds, and this is the one place that can, so it is where
        such a task ends failed, with the reason saying so. Left at "queued" it
        would sit there for ever, which is the failure a restart marking exists
        to prevent.
        """
        held = {item.task_id for item in self.queue}
        for task in self.registry.tasks:
            if task.status == STATUS_QUEUED and task.id not in held:
                task.status = STATUS_ERROR
                task.error = task.error or WAITING_ERROR

    # --- the pump ------------------------------------------------------------
    async def _run(self) -> None:
        while not self._stopping:
            started = self._start_ready()
            if not started:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                except (asyncio.TimeoutError, TimeoutError):
                    pass
            else:
                await asyncio.sleep(0)

    def _start_ready(self) -> int:
        started = 0
        while self.queue and len(self.running) < self.max_concurrent:
            item = self.queue.pop(0)
            self.running[item.task_id] = asyncio.ensure_future(self._drive(item))
            started += 1
        return started

    async def _drive(self, item: QueuedWork) -> None:
        """Run one piece of work, and decide what its failure means."""
        item.attempts += 1
        try:
            await self.registry.async_update(item.task_id, status=STATUS_RUNNING)
            await item.worker(item.task_id)
        except TaskCancelled:
            # Not an error: somebody asked it to stop and it did.
            _LOGGER.info("task engine: %s was cancelled", item.task_id)
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception as err:
            await self._failed(item, err)
        finally:
            self.running.pop(item.task_id, None)
            self._wake.set()

    async def _failed(self, item: QueuedWork, err: Exception) -> None:
        reason = f"{type(err).__name__}: {err}"[:400]
        if item.retries > 0:
            item.retries -= 1
            delay = self.backoff(item.attempts)
            _LOGGER.warning(
                "task engine: %s failed (%s); retrying in %.0fs", item.task_id, reason, delay
            )
            self.registry.output(item.task_id, f"failed: {reason}\nretrying in {delay:.0f}s", stream="note")
            await self.registry.async_update(item.task_id, status=STATUS_QUEUED, detail="waiting to retry")
            await asyncio.sleep(delay)
            self.queue.append(item)
            self._wake.set()
            return
        _LOGGER.exception("task engine: %s failed for the last time", item.task_id)
        await self.registry.async_update(item.task_id, status=STATUS_ERROR, error=reason)

    @staticmethod
    def backoff(attempt: int) -> float:
        """2s, 8s, 32s, capped — jittered so failures do not retry in lockstep."""
        raw = RETRY_BASE_SECONDS * (RETRY_FACTOR ** max(0, attempt - 1))
        capped = min(RETRY_CAP_SECONDS, raw)
        return capped * (0.75 + random.random() * 0.5)

    async def async_drain(self, timeout: float = 10.0) -> bool:
        """Wait until the queue is empty and nothing is running.

        For tests and for shutdown. Returns False if it gave up: a drain that
        hangs is worse than one that says it could not finish.
        """
        self.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.queue and not self.running:
                return True
            self._wake.set()
            await asyncio.sleep(0.01)
        return False

    # --- what a client sees --------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "queued": len(self.queue),
            "running": len(self.running),
            "max_concurrent": self.max_concurrent,
            "waiting": [item.task_id for item in self.queue],
        }
