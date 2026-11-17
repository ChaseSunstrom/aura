"""One budget for a whole crew: every agent a goal's turn delegates to, and theirs (T-0252).

Hermes caps each child (``delegation.max_iterations``) and how many run at once, but
nothing caps a crew: a goal that delegates eight times, each child delegating again, spends
without a ceiling. This counts per crew (the session that first delegated, and every child
under it, followed through ``subagent_start``) and refuses through ``pre_tool_call``, which
Hermes runs for children's tool calls too (read in the image, 2026-10-09):

* ``spawns`` -- helpers asked for and let through (counted at the ``delegate_task`` call); one
  past it is refused, as is one asking for more tasks than are left;
* ``calls`` -- tool calls made by the crew's children together;
* ``minutes`` -- since the crew's first child started.

Worker types: a helper whose goal starts ``[researcher]`` or ``[planner]`` (or any name in
``belts``) may use only that belt's tools; a helper it delegates to inherits the belt unless it
names its own, and never gets a wider one than its parent's. An untagged helper carries no belt
(Hermes already keeps children within their parent's toolsets).

``stop(root)`` refuses every further call the crew's children make, so each ends at its
next tool call. Refusing is the only lever a plugin has: a child already thinking finishes
that thought. Not covered: the parent's own turns (``goals.max_turns`` caps those), model
spend in tokens or money, and a child Hermes starts with no session id: its calls cannot be
told apart from a new goal's, so its own helpers would open a second crew (Hermes gives every
child one today; ``subagent_start`` ignores a child without it).
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ponytail: fixed limits; a setting in aura_schema when the owner wants to tune them.
SPAWNS, CALLS, MINUTES = 8, 120, 20
#: delegate_task actions that act on helpers already running. Exactly these: any other value
#: (or a typo) is treated as a spawn, so an invented action cannot walk past the budget.
CONTROL = frozenset({"list", "steer", "stop"})
_TAG = re.compile(r"^\s*\[([a-z]+)\]")


@dataclass
class Crew:
    started: float
    spawns: int = 0
    calls: int = 0
    stopped: bool = False
    children: set[str] = field(default_factory=set)


class CrewBudget:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 spawns: int = SPAWNS, calls: int = CALLS, minutes: float = MINUTES,
                 belts: Optional[dict[str, frozenset[str]]] = None) -> None:
        self._clock = clock
        self.limits = (spawns, calls, minutes)
        self.belts = dict(belts or {})
        self.crews: dict[str, Crew] = {}
        self._root: dict[str, str] = {}
        self._belt: dict[str, tuple[str, frozenset[str]]] = {}
        self._lock = threading.Lock()

    def root_of(self, session_id: Any) -> str:
        sid = str(session_id or "")
        return self._root.get(sid, sid)

    def _spent(self, crew: Crew, spawning: bool = False) -> Optional[str]:
        """Why the crew may go no further, or None. The helper count stops new helpers only:
        the ones already out keep working until calls or minutes run out."""
        spawns, calls, minutes = self.limits
        if crew.stopped:
            return "the crew was stopped"
        if spawning and crew.spawns >= spawns:
            return f"all {spawns} helpers are spent"
        if crew.calls >= calls:
            return f"the crew's {calls} tool calls are spent"
        if self._clock() - crew.started >= minutes * 60:
            return f"the crew's {minutes:g} minutes are up"
        return None

    # --- hooks -------------------------------------------------------------------------

    def subagent_start(self, parent_session_id: Any = None, child_session_id: Any = None,
                       child_goal: Any = None, **_: Any) -> None:
        if not parent_session_id or not child_session_id:
            return
        child = str(child_session_id)
        with self._lock:
            root = self.root_of(parent_session_id)
            crew = self.crews.setdefault(root, Crew(started=self._clock()))
            crew.children.add(child)
            self._root[child] = root
            inherited = self._belt.get(str(parent_session_id))
            m = _TAG.match(str(child_goal or ""))
            named = (m.group(1), self.belts[m.group(1)]) if m and m.group(1) in self.belts else None
            if named and inherited:
                # Never wider than the parent's: a researcher cannot hand out a doer's tools.
                named = (named[0], named[1] & inherited[1])
            if named or inherited:
                self._belt[child] = named or inherited  # type: ignore[assignment]

    def pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: Any = None,
                      **_: Any) -> Optional[dict]:
        sid = str(session_id or "")
        with self._lock:
            root = self.root_of(sid)
            crew = self.crews.get(root)
            is_child = sid in self._root
            # list / steer / stop act on helpers already running: never refused, least of
            # all when the budget is spent and stopping is what is left to do.
            control = isinstance(args, dict) and args.get("action") in CONTROL
            if tool_name == "delegate_task" and not control:
                if crew is not None and (why := self._spent(crew, spawning=True)):
                    return _block(f"No more helpers for this goal: {why}. Finish with what you have.")
                left = self.limits[0] - (crew.spawns if crew else 0)
                asked = (len(args.get("tasks") or []) or 1) if isinstance(args, dict) else 1
                if asked > left:
                    return _block(f"That asks for {asked} helpers and this goal has {left} left. "
                                  "Ask for fewer, or do some of it yourself.")
                # Counted here, when the spawn is let through, not when a child reports in:
                # a child Hermes starts without a session id would otherwise never count.
                crew = self.crews.setdefault(root, Crew(started=self._clock()))
                crew.spawns += asked
            if is_child and crew is not None:
                if why := self._spent(crew):
                    return _block(f"Stop here and report what you have: {why}.")
                belt = self._belt.get(sid)
                if belt and tool_name not in belt[1]:
                    return _block(f"A {belt[0]} does not use {tool_name}: report what you found and "
                                  "let the goal hand that step to a helper whose work it is.")
                crew.calls += 1
        return None

    def stop(self, session_id: Any) -> bool:
        """Refuse every further call of this session's crew; False when it has none."""
        with self._lock:
            crew = self.crews.get(self.root_of(session_id))
            if crew is None:
                return False
            crew.stopped = True
            return True


def _block(message: str) -> dict:
    return {"action": "block", "message": message}
