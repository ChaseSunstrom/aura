"""One background task that fires on the jarvis loop.

Four integrations run a single asyncio task that wakes at a wall-clock time
and does one thing — review the day, propose routines, reflect overnight,
deliver a briefing. They start and stop that task identically: create it on
the jarvis loop, and on stop cancel it and await the cancellation without
letting the ``CancelledError`` it raises escape. Getting that dance subtly
wrong is easy (a task that never cancels; a stop that re-raises the cancel it
caused), so it lives here once instead of in four copies.

:class:`ScheduledTask` is the start/stop half. :class:`DailyTask` adds the run
loop for the three that fire one action each day at ``self.time_of_day``;
``briefing`` has several times and keeps its own loop, using only the base.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import time as dt_time
from typing import TYPE_CHECKING

from ..automation.util import get_clock, next_time_of_day

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)


class ScheduledTask:
    """The start/stop half of a single background task.

    The host class provides the ``jarvis`` hub, the ``_task`` slot and an async
    ``_run`` coroutine, and — if "is there anything to schedule?" is not
    ``time_of_day is not None`` — an override of :meth:`_schedule_pending`.
    """

    jarvis: "Jarvis"
    #: Set by the host in ``__init__``; the running task, or None.
    _task: "asyncio.Task | None"
    #: What the diagnostic in :meth:`stop` names if it ever fires (it should not).
    _schedule_label: str = ""

    def _schedule_pending(self) -> bool:
        """True when there is a schedule to run. Default: a ``time_of_day`` is set."""
        return getattr(self, "time_of_day", None) is not None

    def start(self) -> None:
        if self._task is not None or not self._schedule_pending():
            return
        self._task = self.jarvis.async_create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # Unreachable while _run re-raises only CancelledError: reaching here
            # means the schedule died for a reason worth seeing, not swallowing.
            _LOGGER.exception(
                "%s did not stop cleanly", self._schedule_label or type(self).__name__
            )

    async def _run(self) -> None:  # pragma: no cover - the host provides this
        raise NotImplementedError


class DailyTask(ScheduledTask):
    """A schedule that fires :meth:`_fire` once a day at ``self.time_of_day``."""

    #: The wall-clock time the action fires at; None means nothing is scheduled.
    time_of_day: dt_time | None
    #: What :meth:`_run` logs if one firing raises (the schedule survives it).
    _failure_message: str = ""

    async def _fire(self) -> None:  # pragma: no cover - the host provides this
        raise NotImplementedError

    async def _run(self) -> None:
        clock = get_clock(self.jarvis)
        assert self.time_of_day is not None  # start() refuses to schedule without one
        while True:
            when = next_time_of_day(clock.now(), self.time_of_day)
            await clock.sleep(max(1.0, (when - clock.now()).total_seconds()))
            try:
                await self._fire()
            except asyncio.CancelledError:
                raise
            except Exception:  # a failed run must not end the schedule
                _LOGGER.exception("%s", self._failure_message)
