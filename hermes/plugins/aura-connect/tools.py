"""Handlers for the ``connect`` toolset: thin calls to jarvis-core's ``oauth`` services.

The house does the work and holds the tokens; nothing returned here is a token.
What this does *not* cover: the consent link itself, which the house sends to the
device in use and returns here only when no device could be reached.
"""
from __future__ import annotations

import json
from typing import Any

from _shared import _house

PROVIDERS = ("google", "github", "microsoft", "slack", "discord", "gitlab", "spotify", "notion",
             "todoist")


#: The named reads the house runs (jarvis-core's oauth PROVIDERS rows, ``reads``). This plugin
#: cannot import the house, so ``tests/contracts/connect-reads.json`` pins both lists to the
#: same names in both suites. A name here is only a name: the house decides what it fetches.
READS: dict[str, tuple[str, ...]] = {
    "google": ("calendars", "events", "contacts"),
    "github": ("repos", "notifications"),
    "microsoft": ("inbox", "calendar"),
    "slack": ("channels",),
    "discord": ("servers",),
    "gitlab": ("projects", "issues"),
    "spotify": ("playlists", "devices", "playing"),
    "notion": ("me",),
    "todoist": ("projects", "tasks", "due"),
}


#: The named writes the house runs (the rows' ``writes``, T-1370): one, adding a Todoist task.
#: Pinned to jarvis-core's table by the same contract file as READS. A name here is only a
#: name: the house decides the address, the method and the fields, and checks the account's
#: scope before it makes any request.
WRITES: dict[str, tuple[str, ...]] = {"todoist": ("add_task",)}

#: The fields of a write the model may name, and no others. The house refuses a title over 500
#: characters, a due text over 80 and a description over 1000; the cut here only bounds what is
#: sent, so an oversize value is refused there rather than quietly shortened.
_WRITE_FIELDS = ("content", "due", "description")
_SEND_AT_MOST = 2000


#: What a write answers when it cannot know: the house may have sent the task on before the
#: connection dropped or timed out, so "could not be reached" would invite a duplicate.
_MAYBE_ADDED = "the task may or may not have been added: check the list before trying again"


def _call(service: str, provider: str = "", **more: Any) -> str:
    if provider and provider not in PROVIDERS:
        return json.dumps({"error": f"provider must be one of: {', '.join(PROVIDERS)}"})
    body: dict[str, Any] = {"return_response": True, **more}
    if provider:
        body["provider"] = provider
    # Only a write has an effect that a lost answer leaves unknown.
    unsure = service == "write"
    try:
        with _house.client() as c:
            r = c.post(f"/api/services/oauth/{service}", json=body)
    except Exception as exc:  # noqa: BLE001 -- the house is down: say so, do not raise into the loop
        if unsure:
            return json.dumps({"error": _MAYBE_ADDED})
        return json.dumps({"error": f"the house could not be reached ({type(exc).__name__})"})
    answer = _house.body_of(r)
    if unsure and r.status_code >= 500:
        return json.dumps({"error": _MAYBE_ADDED})
    if r.status_code >= 400:
        return json.dumps({"error": f"the house refused ({r.status_code}); is the oauth connector configured?"})
    if isinstance(answer, dict) and "service_response" in answer:
        answer = answer["service_response"]
    return json.dumps(answer)


def handle_start(args: dict[str, Any], **_: Any) -> str:
    return _call("start", str(args.get("provider") or "").lower())


def handle_revoke(args: dict[str, Any], **_: Any) -> str:
    return _call("revoke", str(args.get("provider") or "").lower())


def handle_read(args: dict[str, Any], **_: Any) -> str:
    provider = str(args.get("provider") or "").lower()
    what = args.get("what")
    if provider not in READS:
        return json.dumps({"error": f"provider must be one of: {', '.join(READS)}"})
    if what not in READS[provider]:
        return json.dumps({"error": f"{provider} has no read named that; one of: "
                                    f"{', '.join(READS[provider])}"})
    more: dict[str, Any] = {"what": what}
    # Only these two ride along, and the house clips and clamps them again: this is the model's
    # text, so it is never trusted to be short or a number.
    if isinstance(args.get("query"), str) and args["query"].strip():
        more["query"] = args["query"][:200]
    try:
        more["limit"] = int(float(args.get("limit")))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):  # absent, Infinity, NaN, a list: the house default
        pass
    return _call("read", provider, **more)


def handle_write(args: dict[str, Any], **_: Any) -> str:
    args = args if isinstance(args, dict) else {}
    provider = str(args.get("provider") or "").lower()
    action = args.get("action")
    if provider not in WRITES:
        return json.dumps({"error": f"provider must be one of: {', '.join(WRITES)}"})
    if action not in WRITES[provider]:
        return json.dumps({"error": f"{provider} has no write named that; one of: "
                                    f"{', '.join(WRITES[provider])}"})
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return json.dumps({"error": "content is required: the task's title, as text"})
    # Only the named fields ride along (never an address, a method, a header or a project),
    # and only as text; the house validates again and decides what is sent to the provider.
    more: dict[str, Any] = {"action": action, "content": content[:_SEND_AT_MOST]}
    for name in _WRITE_FIELDS[1:]:
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            more[name] = value[:_SEND_AT_MOST]
    return _call("write", provider, **more)


def handle_status(args: dict[str, Any], **_: Any) -> str:
    provider = str(args.get("provider") or "").lower()
    status = json.loads(_call("status"))
    if not provider or "error" in status:
        return json.dumps(status)
    tested = json.loads(_call("test", provider))
    return json.dumps({**status, "test": tested})
