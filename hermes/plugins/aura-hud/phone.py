"""The owner's phone as a set of actions Jarvis can take on it, over the session.

The owner, 2026-09-25: "can jarvis, text, call, set alarms, etc, basically
anything on a phone". The phone already carries those actions (its automation
registry: ``send_sms``, ``place_call``, ``set_alarm``, ``read_sms`` ... the
``ANDROID_TASKER_PARITY`` table), and the house could reach them over its
device channel -- but a phone paired with Hermes alone has no house link, so
nothing could. ``phone_action`` sends ``hud.phone.action`` to the conversation's
session; the phone runs it through the same dispatcher and tier policy the
device channel uses (a text or a call is still confirmed on the phone) and
answers with ``/hud @<sid> phone-result <id> <json>``, which this module hands
back to the waiting tool call.

The house's device channel goes first when the phone is on it (:func:`via_house`): the
session route only reaches a phone that is IN this conversation, and a conversation
held at the desktop was answered by the console -- which ignores the event -- so a
text asked for there waited out the full 90 s and failed (2026-10-06). The phone runs
a house command through the same dispatcher and tiers, so nothing is looser for it.

The conversation's route counts only when its latest prompt came from the phone
(:func:`phone_in_session`): the console, the desktop and a probe's socket receive the event
too and never answer it, so a timer asked for there with the phone off the house waited
the full 90 s before failing (2026-10-09). Such a conversation takes the wake path a
scheduled job takes.

Does not cover a phone on neither route (the tool says so), a phone that watches a
conversation another screen prompted in last (the house reaches it if it is online), and
an answer that arrives after [WAIT_S] is dropped: the tool has already said the phone did
not answer.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator
from _shared import _surface
from _shared._signed import signed
from aura_schema import from_env

ACTION_EVENT = "hud.phone.action"
#: Long enough for the phone's own consent screen to be read and tapped.
WAIT_S = 90.0
#: How long a call with no other way to the phone waits for it to rejoin the house's
#: device channel. A phone in Doze drops its socket and is back within seconds of
#: being touched or of a network wake; "no phone is connected" at once was eight of
#: the phone failures in state.db (2026-09-24..10-08), most from scheduled jobs.
WAKE_S = 45.0
WAKE_POLL_S = 3.0
ACTION_RE = re.compile(r"[a-z][a-z0-9_]{0,48}")
#: The actions that put words in front of another person, and the field holding them.
SIGNED_FIELDS = {"send_sms": "body", "notification_reply": "text"}

_lock = threading.Lock()
_waiting: dict[str, dict[str, Any]] = {}


#: The surface name the phone reports on every prompt, the car's turns included
#: (HermesClient.SURFACE, ``tests/contracts/surface-capabilities.json``).
PHONE_SURFACE = "android"


def _adapter() -> tuple[str, str]:
    settings = from_env()
    return str(settings.adapter_url or "http://127.0.0.1:8643").rstrip("/"), str(settings.adapter_key or "")


def surface_record(sid: str | None) -> dict | None:
    """The adapter's surface record for a session (``{"surface": {...} | None, ...}``), or None
    when nobody can say: no session, no key, the adapter down, an answer that is no record.
    One id, as given: the phone reports under the id the tool call carries."""
    return _surface.current(sid, timeout=3)


def phone_in_session(sid: str | None) -> bool | None:
    """Whether this conversation's latest prompt came from the phone, by the adapter's surface
    record. None when nobody can say -- no live client, no key, the adapter down -- and the
    route is then tried as before. A session that never reported is no phone's: the phone
    reports on every prompt."""
    out = surface_record(sid)
    if out is None:
        return None
    surface = out.get("surface")
    return isinstance(surface, dict) and surface.get("name") == PHONE_SURFACE


#: The house's answers that mean the command never reached the phone, so the session may still.
_UNREACHED = ("not connected", "disconnected before answering", "reconnected on another socket")


def via_house(action: str, params: dict, reason: str, wait_s: float = WAIT_S, wake_s: float = 0.0,
              reserved: bool = False) -> dict | None:
    """The phone's answer over the house's device channel, or None when it is not on it.

    ``wake_s``: how long to wait for the phone to come back to the channel first. Does
    not wake it: GrapheneOS has no push service, so it waits for the phone's own return.
    ``reserved``: the caller is the one tool allowed to send a ``_house.RESERVED_ACTIONS``
    action; ``phone_action`` never sets it.
    """
    from _shared import _house  # the plugin's own sys.path entry; lazy so the tests need no house
    if not _house.configured():
        return None
    try:
        with _house.client(timeout=wait_s + 5) as c:
            deadline = time.monotonic() + wake_s
            while True:
                phones = [d for d in _house.devices(c) if d.get("kind") == "android" and d.get("online")]
                if phones or time.monotonic() >= deadline:
                    break
                time.sleep(WAKE_POLL_S)
            # One phone or none: with two online, "the owner's phone" is not known here, and
            # the session route (the phone in this conversation) is the one that is.
            if len(phones) != 1:
                return None
            # Only passed when True: a stand-in for run_action in a test takes five arguments.
            # Typed Any: a dict[str, bool] splat could fill run_action's str keyword too.
            extra: dict[str, Any] = {"reserved": True} if reserved else {}
            ran = _house.run_action(c, str(phones[0]["device_id"]), action, params, reason, **extra)
    except Exception:  # the house down is a reason to try the session, not to fail
        return None
    response = ran.get("response")
    answer: dict = response if isinstance(response, dict) else {}
    if not ran.get("ok") or any(s in str(answer.get("error") or "") for s in _UNREACHED):
        return None
    status = str(answer.get("status") or "error")
    return {"ok": status == "ok", "via": "house", **answer}


def handle_phone_action(args: dict, emit: Callable[[str, dict], bool], wait_s: float = WAIT_S,
                        house: Callable[..., dict | None] | None = None,
                        here: Callable[[], bool | None] | None = None) -> str:
    action = str(args.get("action") or "").strip()
    if not ACTION_RE.fullmatch(action):
        return json.dumps({"ok": False, "error": "action is a phone action id such as send_sms, or `list`"})
    params = args.get("params") or {}
    if not isinstance(params, dict):
        return json.dumps({"ok": False, "error": "params is an object"})
    # Refused before either route: the house route's own refusal reads here as "the phone is
    # not on the house", and the session route below would then have sent it anyway.
    from _shared import _house
    if action.lower() in _house.RESERVED_ACTIONS:
        return json.dumps({"ok": False, "error": f"{action} is sent only by its own tool"})
    # Signed before the phone sees it, so the approval shows the text that goes (_signed).
    field = SIGNED_FIELDS.get(action)
    if field and isinstance(params.get(field), str):
        params = {**params, field: signed(params[field])}
    return _send(action, params, str(args.get("reason") or "")[:200], emit, wait_s, house, here)


def _send(action: str, params: dict, reason: str, emit: Callable[[str, dict], bool], wait_s: float,
          house: Callable[..., dict | None] | None, here: Callable[[], bool | None] | None) -> str:
    """The two routes to the phone: the house's channel, then this conversation's session."""
    if house is not None:
        answered = house(action, params, reason)
        if answered is not None:
            return json.dumps(answered)
    with awaiting() as (rid, slot):
        # A conversation the phone did not prompt in has listeners that never answer.
        if (here is not None and here() is False) or not emit(
                ACTION_EVENT, {"id": rid, "action": action, "params": params, "reason": reason}):
            # No phone in this conversation either (a scheduled job, the desktop):
            # give a sleeping phone the time to come back to the house's channel.
            if house is not None:
                answered = house(action, params, reason, wake_s=WAKE_S)
                if answered is not None:
                    return json.dumps(answered)
                return json.dumps({"ok": False, "error": f"the phone is asleep or offline: it did not come back to the house within {int(WAKE_S)} s, and no phone is in this conversation. Say so, and offer to try again later"})
            return json.dumps({"ok": False, "error": "no phone is connected to this conversation"})
        if not slot["done"].wait(wait_s):
            return json.dumps({"ok": False, "error": f"the phone did not answer within {int(wait_s)} s"})
        return json.dumps(slot["answer"])


#: tests/contracts/island-settings.json: the phone's island switches and the two actions that carry them.
ISLAND_SETTINGS = ("keep_at_camera", "status", "cover", "conversations_at_bottom")
ISLAND_ACTIONS = {"get": "get_island_settings", "set": "set_island_settings"}


def handle_island(kind: str, args: dict, emit: Callable[[str, dict], bool], wait_s: float = WAIT_S,
                  house: Callable[..., dict | None] | None = None,
                  here: Callable[[], bool | None] | None = None) -> str:
    """``phone_island_get`` / ``phone_island_set``: the island's settings on the owner's phone.

    The change is checked here the way the phone checks it (a name it has, a real boolean, at
    least one), so a bad one fails before a phone is woken for it; the phone checks again.
    The setter is a ``_house.RESERVED_ACTIONS`` action: this is the only caller that sends it,
    on the house route (``reserved=True``) and on the session route alike. Not covered: that
    the owner saw the ask (the matrix does that) or that the island shows a switch that is
    on (the answer's ``notes`` say when it does not).
    """
    params: dict = {}
    if kind == "set":
        unknown = [k for k in args if k not in ISLAND_SETTINGS and k != "reason"]
        if unknown:
            return json.dumps({"ok": False, "error": f"not an island setting: {', '.join(sorted(map(str, unknown)))}"})
        params = {k: args[k] for k in ISLAND_SETTINGS if k in args}
        bad = [k for k, v in params.items() if not isinstance(v, bool)]
        if bad:
            return json.dumps({"ok": False, "error": f"{', '.join(bad)}: true or false"})
        if not params:
            return json.dumps({"ok": False, "error": f"name at least one of {', '.join(ISLAND_SETTINGS)}"})
        send = house and (lambda a, p, r, **kw: house(a, p, r, reserved=True, **kw))
    else:
        send = house
    return _send(ISLAND_ACTIONS[kind], params, str(args.get("reason") or "")[:200], emit, wait_s, send, here)


@contextmanager
def awaiting() -> Iterator[tuple[str, dict[str, Any]]]:
    """An id a screen answers with ``phone-result <id> <json>`` (:func:`deliver`), and the slot
    its answer lands in (``done`` is set then); the id is forgotten on leaving."""
    rid = uuid.uuid4().hex[:16]
    slot: dict[str, Any] = {"done": threading.Event(), "answer": None}
    with _lock:
        _waiting[rid] = slot
    try:
        yield rid, slot
    finally:
        with _lock:
            _waiting.pop(rid, None)


def deliver(rid: str, raw: str) -> str:
    """`/hud @<sid> phone-result <id> <json>`: the phone's answer to a waiting call."""
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"hud error: phone-result is not valid JSON ({exc})"
    if not isinstance(answer, dict):
        return "hud error: phone-result is an object"
    with _lock:
        slot = _waiting.get(rid)
    if slot is None:
        return json.dumps({"ok": False, "error": "nothing is waiting for that id"})
    status = str(answer.get("status") or "error")
    slot["answer"] = {"ok": status == "ok", **answer}
    slot["done"].set()
    return json.dumps({"ok": True})
