"""Handlers for the ``calls`` toolset: thin calls to jarvis-core's call book and to the phone.

What this does *not* cover: that the phone acts (it applies its own tiers and may refuse), who is
calling (a name is the caller's own text), or what was said on a call (no audio reaches the house).
Nothing here logs a caller's name or number; the audit log keeps a hash of a number placed.
"""
from __future__ import annotations

import json
from typing import Any

from _shared import _house

PLACE_ACTION = "place_call"
ANSWER_ACTION, DECLINE_ACTION, BACK_ACTION = "call_answer", "call_decline", "call_back"
REPLIES = ("driving", "busy")  # tests/contracts/call-event.json replies
RULES = ("ring", "silence", "reject")  # ... screen_rules


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _house_call(method: str, path: str, **kw: Any) -> str:
    try:
        with _house.client() as c:
            r = getattr(c, method)(path, **kw)
    except Exception as exc:  # noqa: BLE001 -- the house is down: say so, do not raise into the loop
        return _err(f"the house could not be reached ({type(exc).__name__})")
    if r.status_code >= 400:
        return _err(f"the house refused ({r.status_code})")
    answer = _house.body_of(r)
    return json.dumps({"ok": True, **answer, "trust": "untrusted"} if isinstance(answer, dict) else {"ok": True})


def handle_status(args: dict[str, Any], **_: Any) -> str:
    try:
        limit = max(1, min(int(args.get("limit") or 10), 30))
    except (TypeError, ValueError):
        limit = 10
    return _house_call("get", "/api/calls", params={"limit": limit})


def _on_phone(action: str, params: dict[str, Any], why: str, session: str = "") -> str:
    try:
        with _house.client() as c:
            # With two phones online, the one this turn came from (T-1576).
            device_id, token, problem = _house.pick_phone(c, session)
            if problem:
                return _err("two phones are online and this turn did not come from either, so which one "
                            "rings is not known" if problem == "several" else "no phone is online to place or answer calls")
            ran = _house.run_action(c, device_id, action, params, why, device_token=token)
    except Exception as exc:  # noqa: BLE001
        return _err(f"the house could not be reached ({type(exc).__name__})")
    answer = ran.get("response")
    answer = answer if isinstance(answer, dict) else {}
    status = str(answer.get("status") or "error")
    return json.dumps({"ok": status == "ok" and bool(ran.get("ok")), "status": status,
                       "error": str(answer.get("error") or "")[:200] or None})


def handle_place(args: dict[str, Any], **kwargs: Any) -> str:
    to = args.get("to")
    if not isinstance(to, str) or not to.strip() or len(to) > 80:
        return _err("to (a contact name or a number) is required")
    return _on_phone(PLACE_ACTION, {"to": to.strip()}, "the owner asked to place a call", str(kwargs.get("session_id") or ""))


def handle_answer(args: dict[str, Any], **kwargs: Any) -> str:
    return _on_phone(ANSWER_ACTION, {}, "the owner asked to answer the call", str(kwargs.get("session_id") or ""))


def handle_decline(args: dict[str, Any], **kwargs: Any) -> str:
    reply = args.get("reply")
    if reply not in (None, *REPLIES):
        return _err("reply must be driving or busy")
    return _on_phone(DECLINE_ACTION, {"reply": reply} if reply else {}, "the owner asked to decline the call", str(kwargs.get("session_id") or ""))


def _call_id(args: dict[str, Any]) -> str:
    value = args.get("call_id")
    return value.strip()[:64] if isinstance(value, str) else ""


def handle_back(args: dict[str, Any], **kwargs: Any) -> str:
    call_id = _call_id(args)
    if not call_id:
        return _err("call_id is required")
    return _on_phone(BACK_ACTION, {"call_id": call_id}, "the owner asked to call back", str(kwargs.get("session_id") or ""))


def handle_later(args: dict[str, Any], **_: Any) -> str:
    call_id = _call_id(args)
    if not call_id:
        return _err("call_id is required")
    return _house_call("post", "/api/calls/callback", json={"call_id": call_id})


def handle_rule(args: dict[str, Any], **_: Any) -> str:
    rule, auto = args.get("screen_unknown"), args.get("auto_reply")
    if rule is None and not isinstance(auto, bool):
        return _err("screen_unknown (ring, silence or reject) and/or auto_reply is required")
    if rule is not None and rule not in RULES:
        return _err("screen_unknown must be ring, silence or reject")
    body: dict[str, Any] = {} if rule is None else {"rule": rule}
    if isinstance(auto, bool):
        body["auto_reply"] = auto
    return _house_call("post", "/api/calls/rule", json=body)


def handle_summary(args: dict[str, Any], **_: Any) -> str:
    call_id, notes = _call_id(args), args.get("notes")
    if not call_id or not isinstance(notes, str) or not notes.strip():
        return _err("call_id and notes are required")
    follow = args.get("follow_ups")
    return _house_call("post", "/api/calls/summary",
                       json={"call_id": call_id, "notes": notes, "follow_ups": follow if isinstance(follow, list) else []})
