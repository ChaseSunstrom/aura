"""The owner's phone calls on their other screens (T-0616, tests/contracts/call-event.json).

The phone sends a ``call_event`` device_event when a call rings, is answered, ends or is missed. This
module decides by the house's do-not-disturb, driving and sleep state whether the call rings, is held
or is rejected, adds the calendar's reason for a known caller, pushes the ring to the one desktop in
use, remembers a call back for when the owner parks, and keeps the owner's own notes as the summary.

What this does *not* cover: the phone is the line. This side never answers, declines or dials; it
sends the phone a command only for what the owner set (the screening rule, and the canned reply while
driving, both off until they say so), and the phone applies its own tiers. "Hold" is the house's
decision not to interrupt the screens; silencing the phone's own ring needs the screening role there.
It is memory only (a restart empties it), call audio never reaches it, and the number never does: the
caller is a label the phone read, and the label is a stranger's text. Nothing here logs a label.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from .messages import passes

if TYPE_CHECKING:
    from .core import Jarvis

_LOGGER = logging.getLogger(__name__)

EVENT = "call_event"
CALLBACK_DUE = "call_callback_due"
DATA_CALLS = "call_book"

STATES = ("ringing", "active", "ended", "missed")
RELATIONSHIPS = ("starred", "contact", "unknown")
SCREEN_RULES = ("ring", "silence", "reject")
FROM_MAX = 64
REASON_MAX = 80
ID_MAX = 64
RECENT = 30
CALLBACKS = 10
REPEAT_WINDOW_S = 180
NOTES_MAX = 1000
FOLLOW_UPS = 5
FOLLOW_UP_MAX = 120
CALENDAR_WAIT_S = 2.0

_tasks: set[asyncio.Task] = set()


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _digest(label: str) -> str:
    """What the logs and the repeat check keep of a caller: a hash, never the label."""
    return hashlib.sha256(label.strip().lower().encode("utf-8")).hexdigest()[:12]


def decide(*, dnd: bool, driving: bool, asleep: bool, relationship: str, emergency: bool,
           rule: str, auto_reply: bool) -> tuple[str, str | None]:
    """``(action, reply)``: ring, hold or reject, and the canned reply's key when one may be sent.

    The house state is the texts' (messages.passes): a starred person gets through do-not-disturb
    and driving, only an emergency through sleep. The owner's rule is about strangers only. A reply
    goes to someone saved, never to a stranger (a number that texts back is a number that is
    confirmed live), and never while asleep.
    """
    unknown = relationship == "unknown"
    urgent = relationship == "starred"
    if passes(dnd=dnd, driving=driving, asleep=asleep, urgent=urgent, emergency=emergency and urgent):
        if unknown and rule == "reject":
            return "reject", None
        if unknown and rule == "silence":
            return "hold", None
        return "ring", None
    reply = None
    if auto_reply and not unknown and not asleep:
        reply = "driving" if driving else "busy"
    return ("reject" if unknown and rule == "reject" else "hold"), reply


def reason_from_events(name: str, relationship: str, events: Any) -> str:
    """The title of a calendar event that mentions the caller's first name, or "".

    Only for someone the owner knows: a stranger's name matching "Call Acme" is a coincidence, and
    the title is itself other people's words (shown, never obeyed).
    """
    if relationship == "unknown" or not isinstance(events, list):
        return ""
    first = (name.split() or [""])[0].lower()
    if len(first) < 2:
        return ""
    for event in events:
        if not isinstance(event, dict):
            continue
        people = [event.get("summary", "")]
        organizer = event.get("organizer")
        people.append(organizer.get("name", "") if isinstance(organizer, dict) else "")
        people += [a.get("name", "") for a in event.get("attendees") or [] if isinstance(a, dict)]
        if any(first in str(p).lower() for p in people):
            return _text(event.get("summary"), REASON_MAX)
    return ""


def clean(data: Any) -> dict[str, Any] | None:
    """The event as the book keeps it, or None when it is not one."""
    if not isinstance(data, dict):
        return None
    call_id = _text(data.get("id"), ID_MAX)
    state = data.get("state")
    label = data.get("from", "")
    if not call_id or state not in STATES or not isinstance(label, str):
        return None
    relationship = data.get("relationship")
    if relationship not in RELATIONSHIPS:
        relationship = "unknown"
    try:
        at = int(data.get("at") or 0)
    except (TypeError, ValueError, OverflowError):  # JSON may carry Infinity
        at = 0
    # `urgent` is the relationship, not a flag the phone (or a hostile app on it) can set on a stranger.
    return {"id": call_id, "state": state, "from": _text(label, FROM_MAX) or "Unknown caller",
            "relationship": relationship, "urgent": relationship == "starred", "at": at}


class CallBook:
    """The newest calls, the owner's rule and the calls to ring back."""

    def __init__(self, clock: Any = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: OrderedDict[str, dict[str, Any]] = OrderedDict()
        #: The owner's rule for strangers, and whether a held call from someone saved gets a canned text.
        #: Both default to doing nothing; a restart goes back to them.
        self.rule = "ring"
        self.auto_reply = False

    def get(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._calls.get(call_id)
            return dict(row) if row else None

    def put(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._calls[row["id"]] = row
            self._calls.move_to_end(row["id"])
            while len(self._calls) > RECENT:
                self._calls.popitem(last=False)

    def _first(self, state: str) -> dict[str, Any] | None:
        with self._lock:
            for row in reversed(self._calls.values()):
                if row["state"] == state:
                    return dict(row)
        return None

    def ringing(self) -> dict[str, Any] | None:
        return self._first("ringing")

    def active(self) -> dict[str, Any] | None:
        return self._first("active")

    def recent(self, limit: int = RECENT) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in list(self._calls.values())[-max(1, min(int(limit), RECENT)):]][::-1]

    def repeated(self, label_hash: str, call_id: str, now: float) -> bool:
        """A starred caller ringing again inside the window is the owner's emergency signal."""
        with self._lock:
            return any(r["fh"] == label_hash and r["id"] != call_id and r["urgent"]
                       and now - r["seen"] <= REPEAT_WINDOW_S for r in self._calls.values())

    def want_callback(self, call_id: str) -> bool:
        with self._lock:
            row = self._calls.get(call_id)
            wanted = sum(1 for r in self._calls.values() if r.get("callback") == "wanted")
            if row is None or row["relationship"] == "unknown" or wanted >= CALLBACKS:
                return False
            row["callback"] = "wanted"
            return True

    def summary_due(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"id": r["id"], "from": r["from"], "at": r["at"]} for r in reversed(self._calls.values())
                    if r.get("summary_due")]

    def summarise(self, call_id: str, notes: Any, follow_ups: Any) -> dict[str, Any] | None:
        """Keep the owner's own words about a call; None for a call that is not in the book."""
        with self._lock:
            row = self._calls.get(call_id)
            if row is None:
                return None
            row["summary"] = _text(notes, NOTES_MAX)
            row["follow_ups"] = [_text(f, FOLLOW_UP_MAX) for f in (follow_ups if isinstance(follow_ups, list) else [])
                                 if isinstance(f, str) and f.strip()][:FOLLOW_UPS]
            row["summary_due"] = False
            return {k: row[k] for k in ("id", "from", "summary", "follow_ups")}


def book(jarvis: "Jarvis") -> CallBook:
    found = jarvis.data.get(DATA_CALLS)
    if not isinstance(found, CallBook):
        found = jarvis.data.setdefault(DATA_CALLS, CallBook())
    return found


def public(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "state", "from", "relationship", "reason", "urgent", "emergency", "action", "reply", "at")
    return {k: row.get(k) for k in keys}


def _schedule(coro: Any) -> None:
    """Run a command to the phone without holding the socket that is reading its answers."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _dispatch(jarvis: "Jarvis", phone_id: str, action: str, params: dict[str, Any]) -> None:
    hook = jarvis.data.get("calls_dispatch")
    try:
        if hook is not None:
            await hook(phone_id, action, params)
            return
        from .api.devices import get_devices

        link = get_devices(jarvis).links.get(phone_id)
        if link is not None:
            await link.dispatch(action, params, reason="the owner's call setting")
    except Exception as exc:  # noqa: BLE001 -- a phone that cannot be reached is not the call's failure
        _LOGGER.debug("Could not tell the phone %s (%s)", action, type(exc).__name__)


async def _reason(jarvis: "Jarvis", label: str, relationship: str) -> str:
    if relationship == "unknown":
        return ""
    try:
        hook = jarvis.data.get("calls_reason")
        if hook is not None:
            found = hook(label, relationship)
            return str(await found if inspect.isawaitable(found) else found or "")[:REASON_MAX]
        answer = await asyncio.wait_for(
            jarvis.services.async_call("calendar", "events", {"days": 1}, return_response=True), CALENDAR_WAIT_S)
        events = answer.get("events") if isinstance(answer, dict) else None
        return reason_from_events(label, relationship, events)
    except Exception:  # noqa: BLE001 -- no calendar, or a slow one: the call rings without a reason
        return ""


def desktop_in_use(jarvis: "Jarvis"):
    """The one desktop to show a call on: the best-ranked by presence, else any connected one."""
    from .api.devices import get_devices, get_presence

    desktops = {l.device_id: l for l in get_devices(jarvis).all() if l.platform == "desktop"}
    for device in get_presence(jarvis).rank():
        if device.device_id in desktops:
            return desktops[device.device_id]
    return next(iter(desktops.values()), None)


def _frame(row: dict[str, Any], device: str) -> dict[str, Any]:
    frame = {"type": EVENT, **public(row), "device": device, "trust": "untrusted"}
    if row["state"] == "ended":
        frame["summary_offer"] = bool(row.get("summary_due"))
    return frame


async def relay(jarvis: "Jarvis", from_device: str, data: Any) -> int:
    """Keep a phone's call event, decide it, and push it to the desktop in use. Answers how many took it."""
    from .api.devices import get_devices, get_presence

    hub = get_devices(jarvis)
    source = hub.links.get(from_device)
    if source is None or source.platform != "android":
        return 0
    event = clean(data)
    if event is None:
        return 0
    b = book(jarvis)
    now = time.time()
    prev = b.get(event["id"])
    fh = _digest(event["from"])
    if prev is None:
        house = get_presence(jarvis).house_state()
        emergency = event["urgent"] and b.repeated(fh, event["id"], now)
        action, reply = decide(dnd=house["dnd"], driving=house["driving"], asleep=house["asleep"],
                               relationship=event["relationship"], emergency=emergency,
                               rule=b.rule, auto_reply=b.auto_reply)
        row = {**event, "reason": await _reason(jarvis, event["from"], event["relationship"]),
               "emergency": emergency, "action": action, "reply": reply, "fh": fh, "seen": now,
               "routed": None, "summary_due": False}
    else:
        row = {**prev, "state": event["state"]}
    if event["state"] == "active":
        row["answered"] = True
    if event["state"] == "ended" and row.get("answered"):
        row["summary_due"] = True
    b.put(row)
    _LOGGER.debug("Call %s %s %s", event["state"], row["action"], fh)

    if prev is None and row["reply"] and row["action"] in ("hold", "reject"):
        _schedule(_dispatch(jarvis, from_device, "call_decline", {"reply": row["reply"]}))
    if row["action"] != "ring" and not row.get("answered"):
        return 0
    target = hub.links.get(row["routed"]) if row.get("routed") else None
    if target is None or not target.connected:
        target = desktop_in_use(jarvis)
    if target is None:
        return 0
    row["routed"] = target.device_id
    b.put(row)
    return 1 if target.push(_frame(row, source.name)) else 0


def handle(jarvis: "Jarvis", from_device: str, data: Any) -> None:
    """The socket's entry: relay in the background, as the calendar lookup must not hold the read loop."""
    _schedule(relay(jarvis, from_device, data))


def set_rule(jarvis: "Jarvis", rule: Any, auto_reply: bool | None = None) -> bool:
    """The owner's screening rule (and whether to text back while driving); the phone is told, as it
    must apply the rule with no house on the line. A rule that is not one is refused."""
    from .api.devices import get_devices

    if rule is not None and rule not in SCREEN_RULES:
        return False
    b = book(jarvis)
    if auto_reply is not None:
        b.auto_reply = bool(auto_reply)
    if rule is not None:
        b.rule = rule
        for link in get_devices(jarvis).all():
            if link.platform == "android":
                _schedule(_dispatch(jarvis, link.device_id, "call_policy", {"screen_unknown": rule}))
    return True


def callbacks_due(jarvis: "Jarvis") -> int:
    """Offer each wanted call back once the house is no longer driving. Called when presence changes."""
    from .api.devices import get_presence

    b = book(jarvis)
    if get_presence(jarvis).house_state()["driving"]:
        return 0
    target = desktop_in_use(jarvis)
    sent = 0
    with b._lock:
        due = [r for r in b._calls.values() if r.get("callback") == "wanted"]
        for row in due:
            row["callback"] = "offered"
    for row in due:
        frame = {"type": CALLBACK_DUE, "call_id": row["id"], "from": row["from"], "trust": "untrusted"}
        if target is not None and target.push(frame):
            sent += 1
    return sent
