"""PC routines: the owner's routines that trigger on this PC itself (T-0142).

The routines live in Hermes (``hermes/plugins/aura-hud/routines.py``); the phone keeps a
synced copy and watches its own triggers (``RoutineWatcher.kt``). This is the PC's half, the
same shape: a copy pulled over the HUD socket (``/hud @<sid> routines``, then each
``hud.phone.routines`` push), three PC events watched here --

* ``pc_active`` -- Sir sits down at the PC after ``idle_min`` minutes away,
* ``pc_idle`` -- he steps away from it for ``idle_min`` minutes,
* ``pc_time`` -- a time of day on this PC's clock,

and a routine that fires is a turn to Jarvis (``prompt.submit``), whose answer is a desktop
notification. When a routine may run and what Jarvis is sent are the phone's rules, held to it
by ``tests/contracts/routine-rules.json``.

**A routine is a turn, never an action.** Nothing here reaches the action registry: whatever
the turn decides to do on this PC comes back through ``device_command`` and the policy, as any
other request does -- the same boundary ``triggers.py`` keeps.

Not covered: a PC whose idle time cannot be read (most Wayland sessions; ``triggers.py``'s
``system_idle_seconds``) never fires ``pc_active`` or ``pc_idle``, and the link says so in the
log; a ``pc_time`` minute the agent was not running for is not caught up afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping
from .theme import worn_name

_LOGGER = logging.getLogger(__name__)

PC_EVENTS = ("pc_active", "pc_idle", "pc_time")
SYNC_EVENT = "hud.phone.routines"
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
#: What the PC samples idle time every; a pc_time minute is caught within it.
SAMPLE_S = 30.0

FireFn = Callable[[dict, str], Awaitable[None]]


# --- the phone's rules (tests/contracts/routine-rules.json) --------------------------


def _hhmm(text: Any) -> dt.time | None:
    try:
        h, m = str(text).split(":")
        return dt.time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def may_run(routine: dict, now: dt.datetime, last_fired: float | None) -> bool:
    """Whether a routine may run at ``now``: switched on, on one of its days, inside its hours
    (a window may run past midnight), and past its cooldown since ``last_fired`` (epoch s)."""
    if not routine.get("enabled", True):
        return False
    when = routine.get("when") or {}
    named = when.get("days") or []
    if named and now.weekday() not in [DAYS[d] for d in named if d in DAYS]:
        return False
    after, before = _hhmm(when.get("after")), _hhmm(when.get("before"))
    if after or before:
        t = now.time().replace(microsecond=0)
        lo, hi = after or dt.time.min, before or dt.time.max
        inside = lo <= t <= hi if lo <= hi else (t >= lo or t <= hi)
        if not inside:
            return False
    cooldown = int(routine.get("cooldown_min") or 0)
    if last_fired is not None and cooldown > 0 and now.timestamp() - last_fired < cooldown * 60:
        return False
    return True


def next_fire(routine: dict, now: dt.datetime) -> dt.datetime | None:
    """The next moment a timed routine fires after ``now``, on one of its days."""
    when = routine.get("when") or {}
    at = _hhmm(when.get("at"))
    if at is None:
        return None
    if when.get("date"):
        try:
            once = dt.datetime.combine(dt.date.fromisoformat(str(when["date"])), at, now.tzinfo)
        except ValueError:
            return None
        return once if once > now else None
    days = {DAYS[d] for d in when.get("days") or [] if d in DAYS}
    day = now.date()
    for _ in range(8):
        candidate = dt.datetime.combine(day, at, now.tzinfo)
        if candidate > now and (not days or candidate.weekday() in days):
            return candidate
        day += dt.timedelta(days=1)
    return None


def prompt(routine: dict, what: str) -> str:
    """What the assistant is sent when a routine fires: the name, what happened, its instruction."""
    return (f"[routine: {routine.get('name')}] {what}. Do this now: {routine.get('do')} "
            "(A routine Sir set up; carry it out without asking a question, and answer in one short spoken sentence.)")


# --- the watcher ------------------------------------------------------------------------


class PcRoutines:
    """The synced copy's PC routines, fired from idle samples and the clock.

    Pure apart from ``fire``: the link feeds it ``sample(idle_s, now)`` and ``tick(now)``, so the
    tests drive it without a desktop session or a clock.
    """

    def __init__(self, fire: FireFn, on_spent: Callable[[str], Any] | None = None) -> None:
        self._fire_fn = fire
        self._on_spent = on_spent
        self.routines: list[dict] = []
        self._last_fired: dict[str, float] = {}
        self._last_idle: float | None = None
        self._idle_fired: set[str] = set()
        self._slot_fired: dict[str, dt.datetime] = {}

    def sync(self, snapshot: Any) -> None:
        """Keep the PC's routines from a ``routines`` snapshot (the phone's copy)."""
        items = snapshot.get("routines") if isinstance(snapshot, dict) else None
        self.routines = [
            r for r in items or []
            if isinstance(r, dict) and isinstance(r.get("when"), dict) and r["when"].get("event") in PC_EVENTS
        ]

    def _of(self, event: str) -> Iterable[dict]:
        return [r for r in self.routines if r["when"].get("event") == event]

    async def _fire(self, routine: dict, now: dt.datetime, what: str) -> None:
        rid = str(routine.get("id"))
        if not may_run(routine, now, self._last_fired.get(rid)):
            return
        self._last_fired[rid] = now.timestamp()
        await self._fire_fn(routine, prompt(routine, what))
        if routine.get("once") and self._on_spent:
            self._on_spent(rid)

    async def sample(self, idle_s: float, now: dt.datetime) -> None:
        """One reading of how long the PC has been idle. Input since the last reading ends the
        stretch: ``pc_active`` routines whose ``idle_min`` it lasted fire; ``pc_idle`` ones fire
        once per stretch, when it reaches their ``idle_min``."""
        if self._last_idle is not None and idle_s < self._last_idle:
            # ponytail: the stretch is the last reading before input, so up to SAMPLE_S short of
            # the true length; a threshold within that of the stretch may miss by one sample.
            stretch = self._last_idle
            self._idle_fired.clear()
            for r in self._of("pc_active"):
                if stretch >= int(r["when"].get("idle_min") or 5) * 60:
                    await self._fire(r, now, "Sir sat down at the PC")
        for r in self._of("pc_idle"):
            rid = str(r.get("id"))
            if rid not in self._idle_fired and idle_s >= int(r["when"].get("idle_min") or 5) * 60:
                self._idle_fired.add(rid)
                await self._fire(r, now, "Sir stepped away from the PC")
        self._last_idle = idle_s

    async def tick(self, now: dt.datetime) -> None:
        """Fire each ``pc_time`` routine whose minute has come (once per minute it names)."""
        for r in self._of("pc_time"):
            due = next_fire(r, now - dt.timedelta(minutes=1))
            if due is None or due > now or self._slot_fired.get(str(r.get("id"))) == due:
                continue
            self._slot_fired[str(r.get("id"))] = due
            await self._fire(r, now, f"It is {r['when'].get('at')} on the PC")


# --- the link ------------------------------------------------------------------------------


def link_settings(config: Any, env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """The adapter and its key, when this PC has both: ``adapter_url`` (or ``AURA_ADAPTER_URL``)
    and the key from ``adapter_key_file`` or ``AURA_ADAPTER_KEY`` -- what the console mode takes."""
    env = os.environ if env is None else env
    url = str(getattr(config, "adapter_url", "") or "").strip()
    path = str(getattr(config, "adapter_key_file", "") or "").strip()
    key = ""
    if path:
        try:
            key = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            # The path and why, never the key.
            _LOGGER.warning("PC routines: cannot read the adapter key file %s: %s", path, exc.strerror or exc)
            return None
    key = key or str(env.get("AURA_ADAPTER_KEY", "") or "").strip()
    return (url, key) if url and key else None


def unavailable_reason(config: Any) -> str:
    return ("PC routines are off: set adapter_url and adapter_key_file in the desktop config "
            "(the same adapter and key the console mode takes) to run them on this PC")


#: How often the copy is pulled again: a change made on the phone or at the console is pushed
#: only to that session, so this link would otherwise see it on its next reconnect -- and a
#: routine switched off would keep firing here until then.
REPULL_S = 60.0
#: A notification is a line or two, not the whole answer.
MAX_NOTICE = 300


class _Link:
    """The socket the watcher fires through; swapped on each reconnect, the watcher kept."""

    def __init__(self) -> None:
        self.gateway: Any = None
        self.sid = ""
        self.pending: set[asyncio.Task[Any]] = set()

    async def fire(self, routine: dict, text: str) -> None:
        from .console.app import GatewayError
        from .console.state import SURFACE_REPORT

        if self.gateway is None:
            return
        _LOGGER.info("routine %s fired on this PC", routine.get("id"))
        try:
            await self.gateway.request("prompt.submit", {"session_id": self.sid, "text": text, "surface": SURFACE_REPORT})
        except GatewayError as exc:
            _LOGGER.info("routine %s could not be sent: %s", routine.get("id"), exc)

    def spent(self, rid: str) -> None:
        if self.gateway is None:
            return
        task = asyncio.ensure_future(self.gateway.request(
            "command.dispatch", {"session_id": self.sid, "name": "hud", "arg": f"@{self.sid} routines done {rid}"}))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)


async def run_link(config: Any, notify: Callable[[str, str], Any], stop: asyncio.Event,
                   idle: Callable[[], float | None] | None = None) -> None:
    """Hold the HUD socket for PC routines until ``stop``: pull the copy (and again every minute),
    sample idle time and the clock, send a fired routine as a turn and show its answer."""
    from .console.app import Gateway, ws_url
    from .triggers import system_idle_seconds

    settings = link_settings(config)
    if settings is None:
        _LOGGER.info("%s", unavailable_reason(config))
        return
    adapter, key = settings
    probe = idle or system_idle_seconds
    if probe() is None:
        _LOGGER.info("this PC cannot read its idle time: pc_active / pc_idle routines will not fire")
    link = _Link()
    # One watcher for the agent's life: its cooldowns and what it has fired survive a reconnect,
    # so a pc_idle routine does not fire again because the socket dropped.
    watcher = PcRoutines(link.fire, on_spent=link.spent)
    backoff = 5.0
    while not stop.is_set():
        gateway = Gateway(ws_url(adapter), key)
        try:
            await gateway.connect()
            created = await gateway.request("session.create", {})
            link.gateway, link.sid = gateway, str(created.get("session_id") or "")
            await _pull(link, watcher)
            _LOGGER.info("PC routines: %d on this PC", len(watcher.routines))
            backoff = 5.0
            await _serve(link, watcher, probe, notify, stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- a refused handshake or a dropped socket is a retry, never the end
            _LOGGER.info("PC routines link down (%s: %s); again in %.0f s", type(exc).__name__, exc, backoff)
        finally:
            link.gateway = None
            await gateway.close()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), backoff)
        backoff = min(backoff * 2, 300.0)


async def _pull(link: _Link, watcher: PcRoutines) -> None:
    copy = await link.gateway.request("command.dispatch", {"session_id": link.sid, "name": "hud", "arg": f"@{link.sid} routines"})
    with contextlib.suppress(ValueError):
        watcher.sync(json.loads(str(copy.get("output") or "{}")))


async def _serve(link: _Link, watcher: PcRoutines, probe: Callable[[], float | None],
                 notify: Callable[[str, str], Any], stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    next_sample = 0.0
    next_pull = loop.time() + REPULL_S
    while not stop.is_set():
        if loop.time() >= next_pull:
            next_pull = loop.time() + REPULL_S
            await _pull(link, watcher)
        if loop.time() >= next_sample:
            next_sample = loop.time() + SAMPLE_S
            now = dt.datetime.now().astimezone()
            idle_s = probe()
            if idle_s is not None:
                await watcher.sample(idle_s, now)
            await watcher.tick(now)
        try:
            event = await asyncio.wait_for(link.gateway.events.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        for method, params, notice in _answer(event, link.sid):
            if method:
                await link.gateway.request(method, params)
            if notice:
                notify(f"{worn_name()}", notice[:MAX_NOTICE])
        if event.get("type") == "console.closed":
            raise OSError("socket closed")
        if event.get("type") == SYNC_EVENT and isinstance(event.get("payload"), dict):
            watcher.sync(event["payload"])


def _answer(event: dict, sid: str) -> list[tuple[str, dict, str]]:
    """What the link says back to one event: (method, params, notification). Nobody is at a
    console to answer a routine's question, so an approval is refused and a clarification is
    answered as unanswerable -- each said in a notification, so a routine that needed a yes is
    seen to have stopped. A finished turn's answer is the notification; a failed one says so."""
    kind = event.get("type")
    payload = event.get("payload")
    p: dict[str, Any] = payload if isinstance(payload, dict) else {}
    if kind == "approval.request":
        what = str(p.get("title") or p.get("tool") or "a step")
        return [("approval.respond", {"session_id": sid, "request_id": p.get("request_id"), "choice": "deny"},
                 f"A routine on the PC needed your yes for {what}; it was refused. Ask again at the console.")]
    if kind == "clarify.request":
        return [("clarify.respond", {"session_id": sid, "request_id": p.get("request_id"),
                                     "answer": "Nobody is at the console to answer: this is a routine on the PC. Carry on without it."},
                 "")]
    if kind == "message.complete":
        text = str(p.get("text") or "").strip()
        status = str(p.get("status") or "")
        if text:
            return [("", {}, text)]
        from .console.state import QUIET_ENDS

        if status and status not in QUIET_ENDS:
            return [("", {}, f"A routine on the PC did not finish ({status}).")]
    return []
