"""session_delete and chat_model_set: what the console's two chat buttons do, asked in words (T-1325).

The console sends ``session.delete`` and ``config.set key=model`` over the HUD socket. These
tools call those same gateway methods in-process (``tui_gateway.server._methods``), so the
gateway's own rules apply unchanged: a session open on any screen is never deleted, and a model
picked while a turn runs is held until the next turn starts. Both ask first (the matrix); the
card names the chat (``_shared/_messages.py``).

Not covered: whether the gateway's answer shape stays what it is on this Hermes (the live smoke
and ``scripts/verify/hermes-imports.py`` cover the import, not the reply).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from _shared import _chats

#: A stored session id (``20260902_015412_31226c``) or a short id: word characters and dashes.
_ID = re.compile(r"^[\w-]{1,64}$")
#: One token: a space-separated ``--global`` would reach the gateway's flag parser and rewrite the
#: default model for every chat, which this tool is not asked to do.
_MODEL = re.compile(r"^[A-Za-z0-9][\w./:@+-]{0,199}$")

SCHEMAS: dict[str, dict] = {
    "session_delete": {
        "name": "session_delete",
        "description": (
            "Delete a past chat session and its transcript for good, by its stored session id (the "
            "id session_search returns). Asks the person first, naming the chat. Refuses this "
            "conversation and any chat open on a screen. Not for clearing this chat: that is not a "
            "request this tool can serve."
        ),
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string", "description": "The stored id of the chat to delete."}},
            "required": ["session_id"]},
    },
    "chat_model_set": {
        "name": "chat_model_set",
        "description": (
            "Switch the model THIS chat uses, as the console's model picker does: a model name such "
            "as `vendor/model`, nothing else. It takes effect when this turn ends. Does not load the "
            "model, and does not change the default for other chats (settings_set does)."
        ),
        "parameters": {"type": "object", "properties": {
            "model": {"type": "string", "description": "One model name, no flags."}}, "required": ["model"]},
    },
}


def _gateway() -> Optional[Any]:
    try:
        from tui_gateway import server
    except Exception:
        return None
    return server


def _call(server: Any, method: str, params: dict) -> dict:
    """The gateway's JSON-RPC answer; an exception inside it is an error answer, not ours to raise."""
    try:
        return server._methods[method]("tool", params) or {}
    except Exception as exc:
        return {"error": {"message": f"{type(exc).__name__}: {exc}"}}


def _live_ids(server: Any, key: str) -> list[str]:
    """The screens' short ids whose session carries this durable key (two screens can share one)."""
    return [sid for sid, entry in list(server._sessions.items())
            if isinstance(entry, dict) and entry.get("session_key") == key]


def _plain(text: object, top: int = 120) -> str:
    return " ".join(str(text).split())[:top]


def handle_session_delete(args: dict, **kwargs) -> str:
    stored = (args or {}).get("session_id")
    if not isinstance(stored, str) or not _ID.fullmatch(stored.strip()):
        return "session_delete needs the stored session id of the chat to delete."
    stored = stored.strip()
    server = _gateway()
    if server is None:
        return "session_delete only works inside the gateway; no chat was deleted."
    mine = str(kwargs.get("session_id") or kwargs.get("session_key") or "")
    # The console's own gateway refuses a live session too, but its words are a code; this says
    # why, and a model told only "refused" tends to retry under another id.
    if stored == mine or stored in server._sessions or stored in _live_ids(server, mine):
        return "That is this conversation (or one open on a screen); it was not deleted."
    title = _chats.title_of(stored)
    reply = _call(server, "session.delete", {"session_id": stored})
    err = reply.get("error")
    if err:
        code = err.get("code")
        if code == 4023:
            return "That chat is open on a screen, so it was not deleted. Close it there first."
        if code == 4007:
            return "There is no chat with that id, so nothing was deleted."
        return f"The chat was not deleted: {_plain(err.get('message', 'the gateway refused'))}"
    return f"Deleted the chat {title or '(untitled)'} ({stored})."


def handle_chat_model_set(args: dict, **kwargs) -> str:
    model = (args or {}).get("model")
    if not isinstance(model, str) or not _MODEL.fullmatch(model.strip()):
        return "chat_model_set needs one model name (no spaces or flags)."
    model = model.strip()
    server = _gateway()
    if server is None:
        return "chat_model_set only works inside the gateway; the model was not changed."
    mine = str(kwargs.get("session_id") or kwargs.get("session_key") or "")
    sids = _live_ids(server, mine)
    if not sids:
        return "No live chat to switch: this turn is not running in a console or phone session."
    held = []
    for sid in sids:
        reply = _call(server, "config.set", {"key": "model", "value": model, "session_id": sid})
        err = reply.get("error")
        if err:
            return f"The model was not switched: {_plain(err.get('message', 'the gateway refused'))}"
        result = reply.get("result") or {}
        if result.get("confirm_required"):
            return (f"The model was not switched: {_plain(result.get('confirm_message') or 'it needs a yes')} "
                    "Only the person can confirm that, from the console's model picker.")
        held.append(bool(result.get("deferred")))
    when = "when this turn ends" if any(held) else "now"
    return f"This chat will use {model} {when}. It is not loaded yet; its first turn may be slow."


def _model_now(session: str) -> str:
    """The model this chat's next turn runs on, from the gateway's live table; "" when unknown.

    A pick held for the next turn (``pending_model_switch``) is what the next turn runs, so it
    wins over the agent's own field, which still reads the old model until then. In-process and
    instant: no call out. Not the chat's model on a screen with no live session (there is none
    to ask), and not the default every new chat gets.
    """
    server = _gateway()
    if server is None or not session:
        return ""
    for sid in _live_ids(server, session):
        entry = server._sessions.get(sid) or {}
        pending = entry.get("pending_model_switch")
        held = str(pending.get("display_model") or "").strip() if isinstance(pending, dict) else ""
        return held or str(getattr(entry.get("agent"), "model", "") or "").strip()
    return ""


def change_for(tool_name: str, args: dict, session: str = "") -> Optional[dict]:
    """The approval card's Now and Becomes for ``chat_model_set`` (T-1329), or None for another tool.

    ``known`` is false when the gateway has no live chat for this turn or the chat's model is
    not readable: the card then shows no values rather than a made-up "now". Never raises (the
    hook would show no values at all, which is the same, with a log line). Not claimed: that the
    switch is accepted, or that the model is still this one when the person answers.
    """
    if tool_name != "chat_model_set":
        return None
    model = (args or {}).get("model") if isinstance(args, dict) else None
    if not isinstance(model, str) or not _MODEL.fullmatch(model.strip()):
        return None
    try:
        now = _model_now(session)
    except Exception:
        now = ""
    return {"key": "model of this chat", "before": now, "after": model.strip(),
            "known": bool(now), "secret": False}


HANDLERS = {"session_delete": handle_session_delete, "chat_model_set": handle_chat_model_set}
