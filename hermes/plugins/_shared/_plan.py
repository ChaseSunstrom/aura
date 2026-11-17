"""One approval card for a multi-step plan.

The owner, 2026-10-06: one card for a multi-step request instead of one per gated tool. The
model calls ``plan_approve`` with its steps -- each a tool and the exact arguments it will call
it with -- and that call is itself ASK, so the card shows the whole plan. Only once it is
approved does its handler run and :func:`grant` record the steps for that session.
``matrix_gate`` then lets a call through without a second card when :func:`consume` finds an
unused step with the same tool and *identical* arguments; each step is used once, and the grant
lapses after ``TTL_S``.

Exact arguments, not a target substring: the first cut matched a target anywhere in the call's
JSON, so ``lock.front_door`` with ``"note": "light.kitchen"`` beside it would have passed as the
approved kitchen light (security review, 2026-10-06). Each card's rule_key is unique
(:func:`rule_key`): Hermes remembers a "session"/"always" answer by rule_key, and a fixed key
would have let one "always" approve every later plan by itself.

What it never does: override a DENY row, the kill switch or a guest's turn (``matrix_gate``
consults it only for an ASK verdict from an owner or member), stand for a call whose arguments
differ in any way, or outlive the gateway process (in memory on purpose: a restart asks again).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Any

#: How long an approved plan stands.
TTL_S = 600.0

#: The most steps one card may carry: a longer plan is several cards, not one blank cheque.
MAX_STEPS = 12

#: The longest a step's arguments may be, as canonical JSON. The card shows each step whole, and
#: a step too long for one line of the phone's consent sheet would be approved partly unseen.
STEP_MAX = 200

_now = time.monotonic
_lock = threading.Lock()
#: session -> (expires at, [[tool, canonical JSON of its arguments, the tool_call_id that used it or None], ...])
_grants: dict[str, tuple[float, list[list]]] = {}


def _canon(obj: Any) -> str:
    return json.dumps(obj if isinstance(obj, dict) else {}, sort_keys=True, separators=(",", ":"), default=str)


def steps_of(args: Any) -> list[tuple[str, dict]]:
    """The (tool, arguments) pairs a plan_approve call asks for; ValueError when malformed."""
    raw = args.get("steps") if isinstance(args, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("steps must be a non-empty list of {tool, args}")
    if len(raw) > MAX_STEPS:
        raise ValueError(f"a plan carries at most {MAX_STEPS} steps; ask for the rest separately")
    out = []
    for step in raw:
        tool = str(step.get("tool") or "").strip() if isinstance(step, dict) else ""
        call = step.get("args") if isinstance(step, dict) else None
        if not tool or tool == "plan_approve" or not isinstance(call, dict) or not call:
            raise ValueError("every step needs a tool (not plan_approve) and the exact arguments it will be called with")
        if len(_canon(call)) > STEP_MAX:
            raise ValueError(f"{tool}'s arguments are too long to show on one card; ask for it on its own")
        out.append((tool, call))
    return out


def describe(args: Any, show=None) -> str:
    """The card: every step, in order, with the whole of the arguments it will run with.

    *show* renders one step's arguments (the hook passes the audit's redaction, so a credential
    in a step is masked on the card as it is in the log); it must not clip.
    """
    try:
        steps = steps_of(args)
    except ValueError as err:
        return f"plan_approve with a malformed plan ({err})"
    show = show or _canon
    why = str((args or {}).get("why") or "").strip()
    lines = [f"{n}. {tool} {show(call)}" for n, (tool, call) in enumerate(steps, 1)]
    head = f"JARVIS asks to do these {len(steps)} steps with one approval" + (f" ({why[:160]})" if why else "")
    return (head + ":\n" + "\n".join(lines)
            + f"\nValid for {int(TTL_S // 60)} minutes, each step once and only with exactly these arguments.")


def rule_key(args: Any) -> str:
    """A key no other plan shares, so a remembered "always" can never approve a later plan."""
    digest = hashlib.sha256(_canon(args).encode()).hexdigest()[:12]
    return f"plan_approve:{digest}:{uuid.uuid4().hex[:8]}"


def grant(session: str, args: Any) -> list[tuple[str, dict]]:
    """Record an approved plan for *session* (called only by the approved tool's handler)."""
    steps = steps_of(args)
    with _lock:
        _grants[session] = (_now() + TTL_S, [[tool, _canon(call), None] for tool, call in steps])
    return steps


def consume(session: str, tool: str, args: Any, call_id: str = "") -> str | None:
    """The arguments of the plan step this call is, or None when it is not one.

    Two gates ask about one call -- the matrix's and, for some tools, the plugin's own --
    in an order Hermes does not promise, so the step is bound to the call's
    ``tool_call_id`` by whichever asks first and the other is answered from that. A
    different call, even with the same arguments a moment later, finds the step used. A
    call with no id never shares a step: each such ask needs an unused one.
    """
    if not session:
        return None
    wanted = _canon(args)
    with _lock:
        held = _grants.get(session)
        if held is None:
            return None
        expires, steps = held
        if _now() >= expires:
            _grants.pop(session, None)
            return None
        matching = [st for st in steps if st[0] == tool and st[1] == wanted]
        if call_id and any(st[2] == call_id for st in matching):
            return wanted
        for step in matching:
            if step[2] is None:
                step[2] = call_id or "used"
                return wanted
    return None


def reset() -> None:
    with _lock:
        _grants.clear()
