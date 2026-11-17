"""The owner's mirrored texts and chat notifications (T-0615, tests/contracts/message-mirror.json).

The phone sends a ``message_mirror`` device_event for each new SMS or MessagingStyle notification.
This module keeps a bounded window of them in memory, answers read and search for the console and
the model's tools, and decides by the house's do-not-disturb, driving and sleep state whether a
message is pushed to the desktops now or only kept.

What this does *not* cover: it is memory only, so a restart empties it (by design: nothing at rest
means nothing to encrypt). The text is other people's words and is never logged or put on the bus.
Whether a sender is who they claim is not checked; `from` is a label the phone read.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, deque
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .core import Jarvis

_LOGGER = logging.getLogger(__name__)

EVENT = "message_mirror"
DATA_MESSAGES = "message_window"

PER_CONVERSATION = 50
CONVERSATIONS = 40
MAX_AGE_S = 7 * 24 * 3600
TEXT_MAX = 2000
FROM_MAX = 64
CONVERSATION_MAX = 160
SEARCH_RESULTS = 20
SOURCES = ("sms", "app")
EMERGENCY_WORDS = ("emergency", "urgent", "911", "hospital", "accident", "call me now")


def is_emergency(urgent: bool, text: str) -> bool:
    """An urgent person writing a word that means it. A stranger's "URGENT" is a parcel scam."""
    low = str(text).lower()
    return bool(urgent) and any(w in low for w in EMERGENCY_WORDS)


def passes(*, dnd: bool, driving: bool, asleep: bool, urgent: bool, emergency: bool) -> bool:
    """Push now (True) or keep quietly (False). Asleep: emergencies only; do not disturb or
    driving: urgent people only. An emergency flag counts only on an urgent person."""
    emergency = emergency and urgent
    if asleep:
        return emergency
    if dnd or driving:
        return urgent or emergency
    return True


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def clean(data: Any) -> dict[str, Any] | None:
    """The message as the window keeps it, or None when it is not one."""
    if not isinstance(data, dict):
        return None
    text = data.get("text")
    msg_id = _text(data.get("id"), 64)
    conversation = _text(data.get("conversation"), CONVERSATION_MAX)
    source = data.get("source")
    if not isinstance(text, str) or not text.strip() or not msg_id or not conversation or source not in SOURCES:
        return None
    try:
        at = int(data.get("at") or 0)
    except (TypeError, ValueError, OverflowError):  # JSON may carry Infinity
        at = 0
    urgent = data.get("urgent") is True
    return {
        "id": msg_id, "conversation": conversation, "source": source,
        "from": _text(data.get("from"), FROM_MAX), "text": text.strip()[:TEXT_MAX], "at": at,
        "urgent": urgent, "emergency": urgent and data.get("emergency") is True,
    }


class MessageWindow:
    """Per conversation, the newest messages; the oldest conversation and anything past its age go."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # conversation -> deque of (kept_at, message); least recently written first.
        self._convs: OrderedDict[str, deque[tuple[float, dict[str, Any]]]] = OrderedDict()

    def _expire(self) -> None:
        cutoff = self._clock() - MAX_AGE_S
        for key in list(self._convs):
            rows = self._convs[key]
            while rows and rows[0][0] < cutoff:
                rows.popleft()
            if not rows:
                del self._convs[key]

    def add(self, data: Any) -> dict[str, Any] | None:
        """Keep one message; None when it is refused or a repeat of one already kept."""
        message = clean(data)
        if message is None:
            return None
        with self._lock:
            self._expire()
            rows = self._convs.get(message["conversation"])
            if rows is None:
                rows = self._convs[message["conversation"]] = deque(maxlen=PER_CONVERSATION)
            if any(m["id"] == message["id"] for _, m in rows):
                return None
            rows.append((self._clock(), message))
            self._convs.move_to_end(message["conversation"])
            while len(self._convs) > CONVERSATIONS:
                self._convs.popitem(last=False)
        return message

    def read(self, conversation: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Oldest first, the last ``limit`` of one conversation or of all of them by time."""
        limit = max(1, min(int(limit), PER_CONVERSATION * CONVERSATIONS))
        with self._lock:
            self._expire()
            if conversation:
                rows = [m for _, m in self._convs.get(conversation, ())]
            else:
                rows = [m for conv in self._convs.values() for _, m in conv]
                rows.sort(key=lambda m: m["at"])
        return [dict(m) for m in rows[-limit:]]

    def conversations(self) -> list[dict[str, Any]]:
        """Newest conversation first: id, who, the last text's time and how many are kept."""
        with self._lock:
            self._expire()
            return [{"conversation": key, "from": rows[-1][1]["from"], "source": rows[-1][1]["source"],
                     "at": rows[-1][1]["at"], "count": len(rows)} for key, rows in reversed(self._convs.items())]

    def search(self, query: str, limit: int = SEARCH_RESULTS) -> list[dict[str, Any]]:
        needle = " ".join(str(query).lower().split())
        if not needle:
            return []
        limit = max(1, min(int(limit), SEARCH_RESULTS))
        with self._lock:
            self._expire()
            hits = [m for conv in self._convs.values() for _, m in conv
                    if needle in m["text"].lower() or needle in m["from"].lower()]
        hits.sort(key=lambda m: m["at"], reverse=True)
        return [dict(m) for m in hits[:limit]]


def window(jarvis: "Jarvis") -> MessageWindow:
    """The house's window, created on first use."""
    found = jarvis.data.get(DATA_MESSAGES)
    if not isinstance(found, MessageWindow):
        found = jarvis.data.setdefault(DATA_MESSAGES, MessageWindow())
    return found


def relay(jarvis: "Jarvis", from_device: str, data: Any) -> int:
    """Keep a phone's message and push it to the connected desktops unless the house state holds it.

    Answers how many desktops took it. Only a phone raises one. Nothing here logs the text or
    fires the bus: what is on the bus is recorded.
    """
    from .api.devices import get_devices, get_presence

    hub = get_devices(jarvis)
    source = hub.links.get(from_device)
    if source is None or source.platform != "android":
        return 0
    message = window(jarvis).add(data)
    if message is None:
        return 0
    presence = get_presence(jarvis)
    state = presence.house_state()
    if not passes(dnd=state["dnd"], driving=state["driving"], asleep=state["asleep"],
                  urgent=message["urgent"], emergency=message["emergency"]):
        _LOGGER.debug("Held a mirrored message (%s)", message["source"])
        return 0
    frame = {"type": EVENT, **message, "device": source.name, "trust": "untrusted"}
    sent = sum(1 for link in hub.all() if link.platform == "desktop" and link.push(frame))
    _LOGGER.debug("Pushed a mirrored message to %d desktop(s)", sent)
    return sent
