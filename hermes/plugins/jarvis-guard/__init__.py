"""jarvis-guard — the one gate, the one log, the one brake.

This plugin registers almost no capability. What it registers is governance:

* the ``pre_tool_call`` hook that applies the approval matrix to **every** tool
  call the gateway makes (Hermes fires every plugin's hook for every tool, so
  one hook covers the built-ins the per-plugin gates could never see);
* the ``post_tool_call``, ``pre_approval_request`` and
  ``post_approval_response`` observers that complete the audit record — the
  decision, the human's answer, and what the call actually did;
* ``transform_llm_output``, which is the only chokepoint where the model's
  final reply can be redacted before it leaves the turn. Hermes redacts that
  reply on chat platforms but hands it to the CLI and the ``/v1/runs`` API raw
  (``gateway/run.py``, ``_GATEWAY_RAW_TEXT_PLATFORMS``), and ``/v1/runs`` is
  what the web console, the desktop app, the Android app and the benchmark all
  read. Without this hook a secret the model quoted back would reach every
  JARVIS surface unmasked.

It loads before it can be useful and stays loaded: there is no ``check_fn`` and
no ``requires_env``, because a governor that switches itself off when a
credential is missing is not a governor. If this plugin fails to load, the
gateway runs ungoverned — ``deploy/tests/test_pipeline.py`` pins it into
``plugins.enabled`` so that cannot happen by omission.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared import (  # noqa: E402
    _audit, _egress, _grants, _homemode, _killswitch, _matrix, _plan, _speaker, _taint,
)
from _shared._approval_hook import (  # noqa: E402
    approval_observer,
    audit_outcome_hook,
    guarded_command,
    matrix_gate,
    split_session,
)

logger = logging.getLogger(__name__)

TOOLSET = "guard"

AUDIT_QUERY = {
    "name": "audit_query",
    "description": (
        "Read the JARVIS audit log — every gated tool call, approval decision, settings "
        "write and kill-switch change, newest first, with arguments already redacted. "
        "Filter by kind (tool_call|approval|settings_write|outbound|deploy|kill_switch), "
        "by tool, by session, or by date range (since/until: today, yesterday, week, "
        "YYYY-MM-DD or an ISO time, in house time; a date-only until includes that day). summary=true returns "
        "per-day counts and the records that matter (outbound, settings, deploys, the kill "
        "switch, refusals) instead of raw records. For 'what did you do today', call "
        "since='today', summary=true. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "description": "record kind to filter on"},
            "tool": {"type": "string", "description": "tool name to filter on"},
            "session": {"type": "string", "description": "session id to filter on"},
            "since": {"type": "string",
                      "description": "start: today, yesterday, week (this Monday), YYYY-MM-DD or ISO"},
            "until": {"type": "string", "description": "end (a date includes that day)"},
            "summary": {"type": "boolean", "description": "per-day digest instead of records"},
            "limit": {"type": "integer", "description": "how many records (default 20, max 200)"},
        },
        "required": [],
    },
}

KILL_SWITCH = {
    "name": "kill_switch",
    "description": (
        "The kill switch. `status` reports whether JARVIS is stopped and who stopped it; "
        "`engage` stops it — every tool call that would change the world is refused from "
        "the next one onward, while reads keep working so the owner can still ask what "
        "happened. There is deliberately no `release` action: releasing is a host-side "
        "act (scripts/jarvis-kill release), so nothing the model can be talked into "
        "doing can restart it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "engage"]},
            "reason": {"type": "string", "description": "why, recorded in the audit log"},
            "family": {"type": "string", "enum": ["mail", "phone", "house", "shell"],
                       "description": ("stop only this family of tools (\"stop sending mail\"); "
                                       "omit to stop everything")},
        },
        "required": ["action"],
    },
}


_TS = "%Y-%m-%dT%H:%M:%S.%fZ"  # _audit._now's format: compares as text
#: What a "what did you do" digest names one by one, most consequential first (a refusal
#: ranks last), so a flood of refusals can't push the kill switch out of the capped list.
_NOTABLE_RANK = {_audit.KIND_KILL_SWITCH: 0, _audit.KIND_DEPLOY: 1, _audit.KIND_SETTINGS: 2,
                 _audit.KIND_OUTBOUND: 3}
NOTABLE_CLIP = 200  # a message body read into the car's digest is a paragraph too long
_NOTABLE_FIELDS = ("ts", "kind", "tool", "decision", "verdict", "outcome", "reason", "args")
NOTABLE_CAP = 20


def _bound(text: str, *, end: bool) -> str:
    """A since/until word as the log's UTC ``ts``. Raises ValueError on anything else.

    A word or a bare date is a whole day in house time (the container's TZ), so a
    date-only ``until`` is the start of the next day. A naive ISO time is house
    time too; one with an offset is taken as written.
    """
    word = text.strip().lower()
    today = datetime.now().astimezone().date()
    day = {"today": today, "yesterday": today - timedelta(days=1),
           "week": today - timedelta(days=today.weekday())}.get(word)
    if day is None:
        try:
            day = date.fromisoformat(word)
        except ValueError:
            return _stamp(datetime.fromisoformat(text.strip()).astimezone())
    day += timedelta(days=1 if end else 0)
    return _stamp(datetime(day.year, day.month, day.day).astimezone())


def _stamp(moment: datetime) -> str:
    # isoformat, not strftime(_TS): glibc's %Y drops the zero pad below year 1000, and
    # "999-…" sorts after every real "2026-…".
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")[:-6] + "Z"


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= NOTABLE_CLIP else value[:NOTABLE_CLIP] + "…"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def _house_day(ts: str) -> str:
    try:
        moment = datetime.strptime(ts, _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return ts[:10]
    return moment.astimezone().date().isoformat()


def _summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-house-day counts, newest first, and the records worth naming aloud."""
    days: dict[str, dict[str, Any]] = {}
    notable = []
    for record in records:
        day = days.setdefault(_house_day(str(record.get("ts", ""))),
                              {"count": 0, "kinds": {}, "tools": {}, "decisions": {}})
        day["count"] += 1
        for bucket, value in (("kinds", record.get("kind")), ("tools", record.get("tool")),
                              ("decisions", record.get("decision") or record.get("verdict"))):
            if value:
                key = str(value)  # a planted line's list or dict is not a dict key
                day[bucket][key] = day[bucket].get(key, 0) + 1
        if (str(record.get("kind")) in _NOTABLE_RANK
                or _matrix.DENY in (record.get("verdict"), record.get("decision"))):
            notable.append({k: _clip(record[k]) for k in _NOTABLE_FIELDS if record.get(k)})
    # sorted() is stable: newest first within a rank
    notable.sort(key=lambda n: _NOTABLE_RANK.get(str(n.get("kind")), len(_NOTABLE_RANK)))
    return {"days": [{"day": d, **v} for d, v in days.items()],
            "notable_total": len(notable), "notable": notable[:NOTABLE_CAP]}


def handle_audit_query(args: dict, **kwargs: Any) -> str:
    limit = args.get("limit")
    try:
        limit = max(1, min(200, int(limit))) if limit is not None else 20
    except (TypeError, ValueError):
        limit = 20
    since_arg, until_arg = str(args.get("since") or ""), str(args.get("until") or "")
    bounds = {}
    for name, value in (("since", since_arg), ("until", until_arg)):
        if not value:
            continue
        try:
            bounds[name] = _bound(value, end=name == "until")
        except (ValueError, OverflowError):  # OverflowError: "9999-12-31" as an until
            return (f"audit log: couldn't read the date {value!r}; use today, yesterday, week, "
                    "YYYY-MM-DD or an ISO time")
    if "since" in bounds and "until" in bounds and bounds["since"] >= bounds["until"]:
        return "audit log: the range ends before it starts"
    summary = bool(args.get("summary"))
    filters = {name: str(args.get(name) or "") for name in ("kind", "tool", "session")}
    records = _audit.read(
        # ponytail: a summary reads the whole live file (≤ MAX_BYTES); index by day if it grows
        sys.maxsize if summary else limit,
        **filters,
        **bounds,
    )
    if not records:
        if bounds and not any(filters.values()):
            return (f"audit log: nothing recorded between {since_arg or 'the start'} "
                    f"and {until_arg or 'now'}")
        return "audit log: no matching records"
    if summary:
        return json.dumps(_summarise(records), indent=2, default=str)
    return json.dumps({"count": len(records), "records": records}, indent=2, default=str)


def handle_kill_switch(args: dict, **kwargs: Any) -> str:
    action = str(args.get("action") or "status").strip().lower()
    if action == "engage":
        reason = str(args.get("reason") or "requested in conversation")
        family = str(args.get("family") or "").strip().lower() or None
        try:
            state = _killswitch.engage(actor="model", reason=reason, family=family)
        except ValueError as exc:
            return f"Refused: {exc}"
        _audit.append(_audit.KIND_KILL_SWITCH, actor="model", decision="engage",
                      reason=reason, outcome=f"engaged:{family}" if family else "engaged")
        return _killswitch.describe() + f"\n{json.dumps(state, indent=2, sort_keys=True)}"
    if action == "release":
        # Named explicitly so the refusal is a sentence the model can repeat to
        # the owner, rather than an "unknown action" that reads like a bug.
        return ("Refused: the kill switch is not released from a conversation. "
                "Run `scripts/jarvis-kill release` on the JARVIS host.")
    return _killswitch.describe()


PLAN_APPROVE = {
    "name": "plan_approve",
    "description": (
        "Ask the owner to approve a multi-step plan with ONE card instead of one per step. Use it "
        "before a request that needs two or more gated actions (lights in several rooms, a setting "
        "and a schedule): list every step as {tool, args} with the EXACT arguments you will call it "
        "with. Once approved, each listed call runs without another card, once, for 10 minutes, and "
        "only with those arguments. A step that is refused outright stays refused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "why": {"type": "string", "description": "the request, in a sentence"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "the tool the step calls"},
                        "args": {"type": "object",
                                 "description": "the exact arguments the step will be called with"},
                    },
                    "required": ["tool", "args"],
                },
            },
        },
        "required": ["steps"],
    },
}


def handle_plan_approve(args: dict, **kwargs: Any) -> str:
    """Runs only after the owner approved the card (the matrix row is ASK)."""
    session = str(kwargs.get("session_id") or "")
    if not session:
        return json.dumps({"ok": False, "error": "no session to hold the plan"})
    try:
        steps = _plan.grant(session, args)
    except ValueError as err:
        return json.dumps({"ok": False, "error": str(err)})
    return json.dumps({"ok": True, "approved_steps": [{"tool": t, "args": a} for t, a in steps],
                       "valid_for_s": int(_plan.TTL_S),
                       "note": "Run the steps now; each is used once. Anything not listed still asks."})


GUARD_REVOKE = {
    "name": "guard_revoke",
    "description": (
        "Take back ONE standing approval (an \"always\" the owner gave): name its rule key exactly "
        "as listed (a miss lists the live ones). That rule asks again from the next call. It can "
        "only remove a grant: it never adds or widens one, and it does not touch the kill switch."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "rule": {"type": "string", "description": "the standing approval's rule key, whole"},
        },
        "required": ["rule"],
    },
}


def handle_guard_revoke(args: dict, **kwargs: Any) -> str:
    """The console's revoke (``/guard @<sid> revoke <rule>``), with the same owner-only rule.

    Not covered: which grants exist is shown to the model on a miss (the owner sees the same
    list in the console); a revoke takes the rule's every scope back at once.
    """
    session = str(kwargs.get("session_id") or "")
    if not session:
        # speaker_of("") is the key holder, an owner: no session would revoke for anyone (T-0858).
        return "Refused: no session to check who is asking."
    if _speaker.speaker_of(session)["level"] != "owner":
        return "Refused: standing approvals are the owner's to revoke."
    rule = (args or {}).get("rule")
    base = rule.strip() if isinstance(rule, str) else ""
    if not base or not _grants.revoke(base):
        keys = [g["base_key"] for g in _grants.live()]
        return (f"No standing approval for {base or '(none named)'}. "
                + (f"Standing now: {json.dumps(keys)}" if keys else "None are standing."))
    _audit.append(_audit.KIND_APPROVAL, actor="model", session=session, rule_key=base,
                  decision="revoke", outcome="revoked")
    return f"revoked: {base}"


def register(ctx) -> None:
    # Before anything else: Hermes v0.21.0's redactor does not know a Tailscale
    # auth key or a Proxmox API token, and those are the two credentials that
    # would open this house. Registering them here means they mask everywhere
    # the built-in patterns do, not only in this plugin's own log.
    _audit.register_with_hermes()

    # Hermes sets its home to the stack's mode at start and tightens it again when it saves its
    # credential store; the adapter then cannot reach the kill switch and reads it as engaged,
    # so the console shows a stopped assistant that is running (T-1497). Started here because
    # this plugin is the one that is always loaded; it never raises.
    _homemode.start()

    ctx.register_tool(name="audit_query", toolset=TOOLSET, schema=AUDIT_QUERY,
                      handler=handle_audit_query, emoji="📒")
    ctx.register_tool(name="kill_switch", toolset=TOOLSET, schema=KILL_SWITCH,
                      handler=handle_kill_switch, emoji="🛑")
    ctx.register_tool(name="plan_approve", toolset=TOOLSET, schema=PLAN_APPROVE,
                      handler=handle_plan_approve, emoji="📋")

    ctx.register_tool(name="guard_revoke", toolset=TOOLSET, schema=GUARD_REVOKE,
                      handler=handle_guard_revoke, emoji="🔓")

    ctx.register_hook("pre_tool_call", matrix_gate(logger=logger))
    observe = audit_outcome_hook(logger=logger)

    def after_tool(**kwargs: Any) -> None:
        # One hook per event: which of the turn's reads were someone else's words, for the
        # approval cards (T-0567), then the audit's record of what happened.
        _taint.note_tool(**kwargs)
        observe(**kwargs)

    ctx.register_hook("post_tool_call", after_tool)
    ctx.register_hook("pre_llm_call", _taint.turn_start)
    requested, responded = approval_observer(logger=logger)
    ctx.register_hook("pre_approval_request", requested)
    ctx.register_hook("post_approval_response", responded)
    ctx.register_hook("transform_llm_output", _redact_reply)

    ctx.register_command(
        "guard",
        handler=guarded_command(_command, "guard", logger=logger),
        description="Governance status: kill switch, egress allowlist, recent audit lines, standing approvals (list and revoke)",
        args_hint="[audit|egress|kill <reason>|grants|revoke <rule>]",
    )


def _redact_reply(response_text: str = "", **kwargs) -> str | None:
    """Mask credentials in the model's final answer before it leaves the turn.

    Returning the text unchanged would be a no-op with a cost — Hermes takes the
    first non-empty string a hook returns and stops — so None is returned when
    nothing was masked, leaving the reply exactly as the model wrote it.
    """
    del kwargs
    if not response_text:
        return None
    masked = _audit.redact_text(response_text)
    return masked if masked != response_text else None


def _command(raw_args: str) -> str:
    _sid, rest = split_session(raw_args)
    rest = (rest or "").strip()
    verb, _, tail = rest.partition(" ")
    verb = verb.strip().lower()
    if verb == "audit":
        return handle_audit_query({"limit": 10})
    if verb == "egress":
        return "egress allowlist:\n  " + "\n  ".join(_egress.describe())
    if verb == "kill":
        reason = tail.strip() or "engaged from the console"
        _killswitch.engage(actor="owner", reason=reason)
        _audit.append(_audit.KIND_KILL_SWITCH, actor="owner", decision="engage",
                      reason=reason, outcome="engaged")
        return _killswitch.describe()
    if verb in ("grants", "revoke"):
        # The speaker lookup the gate uses for a guest; revoke only ever takes privilege away,
        # but which standing answers exist is the owner's to know (T-0570).
        # No session is no speaker to look up, and speaker_of("") answers "the key holder",
        # an owner: a relay that dropped the @session would list and revoke for a guest.
        if not _sid:
            return "Standing approvals are listed and revoked from a session: /jarvis-guard @<session_id> grants."
        if _speaker.speaker_of(_sid)["level"] != "owner":
            return "Standing approvals are the owner's to see and revoke."
        if verb == "grants":
            return json.dumps(_grants.live())
        base = tail.strip()
        if not _grants.revoke(base):
            return f"No standing approval for {base}."
        _audit.append(_audit.KIND_APPROVAL, actor="owner", session=_sid or "", rule_key=base,
                      decision="revoke", outcome="revoked")
        return f"revoked: {base}"
    rows = len(_matrix.RULES)
    return (f"{_killswitch.describe()}\n"
            f"approval matrix: {rows} rows over {len(_matrix.known_tools())} tools\n"
            f"egress allowlist: {len(_egress.allowed_hosts())} hosts + the house (*{_egress.house_suffix() or '.<HOUSE_DOMAIN unset>'})\n"
            f"audit log: {_audit.audit_path()}")
