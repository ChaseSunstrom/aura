"""Handlers for the ``texts`` toolset: thin calls to jarvis-core's message window and the phone.

What this does *not* cover: that the phone sends the reply (it applies its own CONFIRM tier and may
refuse), or who wrote a mirrored text. Nothing here logs or stores a message body.
"""
from __future__ import annotations

import json
from typing import Any

from _shared import _house

REPLY_MAX = 480  # tests/contracts/message-mirror.json limits.reply_card_max: what the card can show
REPLY_ACTION = "message_reply"


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _get(path: str, params: dict[str, Any]) -> str:
    try:
        with _house.client() as c:
            r = c.get(path, params=params)
    except Exception as exc:  # noqa: BLE001 -- the house is down: say so, do not raise into the loop
        return _err(f"the house could not be reached ({type(exc).__name__})")
    if r.status_code >= 400:
        return _err(f"the house refused ({r.status_code})")
    answer = _house.body_of(r)
    # Always marked: these are other people's words, whatever the house said.
    return json.dumps({**answer, "trust": "untrusted"} if isinstance(answer, dict) else {"trust": "untrusted"})


def _limit(args: dict[str, Any], default: int, top: int) -> int:
    try:
        return max(1, min(int(args.get("limit") or default), top))
    except (TypeError, ValueError):
        return default


def handle_read(args: dict[str, Any], **_: Any) -> str:
    params: dict[str, Any] = {"limit": _limit(args, 20, 50)}
    conversation = str(args.get("conversation") or "").strip()
    if conversation:
        params = {"conversation": conversation, **params}
    return _get("/api/messages", params)


def handle_search(args: dict[str, Any], **_: Any) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return _err("query is required")
    return _get("/api/messages/search", {"q": query[:80], "limit": _limit(args, 20, 20)})


def handle_reply(args: dict[str, Any], **kwargs: Any) -> str:
    conversation = str(args.get("conversation") or "").strip()
    text = args.get("text")
    if not conversation.startswith(("sms:", "app:")) or not isinstance(text, str) or not text.strip():
        return _err("conversation (sms:… or app:…) and text are required")
    if len(text) > REPLY_MAX:
        # The approval card shows the whole text; one it cannot show is not one the owner approved.
        return _err(f"keep a reply to {REPLY_MAX} characters so the approval can show all of it")
    try:
        with _house.client() as c:
            # With two phones online, the one this turn came from (T-1576).
            device_id, token, problem = _house.pick_phone(c, str(kwargs.get("session_id") or ""))
            if problem:
                return _err("two phones are online and this turn did not come from either, so which one "
                            "sends is not known" if problem == "several" else "no phone is online to send from")
            ran = _house.run_action(c, device_id, REPLY_ACTION, {"conversation": conversation, "text": text},
                                    "reply to a mirrored text", device_token=token)
    except Exception as exc:  # noqa: BLE001
        return _err(f"the house could not be reached ({type(exc).__name__})")
    answer = ran.get("response")
    answer = answer if isinstance(answer, dict) else {}
    status = str(answer.get("status") or "error")
    return json.dumps({"ok": status == "ok" and bool(ran.get("ok")), "status": status,
                       "error": str(answer.get("error") or "")[:200] or None})
