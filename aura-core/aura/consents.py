"""The phone's approval prompts, answerable from the console (the owner, 2026-10-08).

"I can't approve it with the phone locked, can you make it so I can approve things in the
web console/desktop app for things on my phone?" A phone action that asks (a text, a call,
a routine's unattended step) asks on the phone, and the phone hides the request while its
keyguard is up -- deliberately, so whoever holds the phone cannot approve. The console is
behind its own password, so it is a fair second place to answer.

The phone reports each prompt it raises (``consent_pending``) and each it settles
(``consent_resolved``) as device events on its own socket; :class:`ConsentRelay` keeps the
open ones, the console lists them beside the house's own approvals and answers one, and the
answer goes back to that phone as a ``consent_answer`` frame. The phone settles it exactly
as a tap would and remains the one that runs anything.

What it does not do: decide anything. An id that is not open, a prompt past its own
deadline, or a phone that is no longer connected answers an error and nothing is sent. A
socket that is itself a device can neither list nor answer (a phone is not a console, and the
parameters are a text's number and body), a device cannot take another's prompt id, and a
phone with remote answers switched off never reports a prompt at all. Every other house token
can: they are all the owner's, and the console's own password is what stands in front of it.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

#: Fired when the open prompts change, so a console reloads its approvals.
EVENT_PHONE_CONSENT = "jarvis_phone_consent"
#: The frame the phone settles a prompt from.
TYPE_CONSENT_ANSWER = "consent_answer"
#: No prompt waits longer than this, whatever the phone says.
MAX_TTL_S = 300.0
MAX_OPEN = 20
_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")


@dataclass(slots=True)
class PhoneConsent:
    id: str
    device_id: str
    device_name: str
    action: str
    params: str
    reason: str
    description: str
    created: float
    expires: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConsentRelay:
    """The open phone prompts. ``send(device_id, frame)`` answers True when it was pushed."""

    def __init__(self, send: Callable[[str, dict[str, Any]], bool],
                 fire: Callable[[str, dict[str, Any]], None],
                 clock: Callable[[], float] = time.time) -> None:
        self._send = send
        self._fire = fire
        self._clock = clock
        self._open: dict[str, PhoneConsent] = {}

    def _expire(self) -> None:
        now = self._clock()
        for cid in [c.id for c in self._open.values() if c.expires <= now]:
            self._open.pop(cid, None)

    def offer(self, device_id: str, device_name: str, data: dict[str, Any]) -> bool:
        """A prompt the phone just raised. Malformed or one too many: ignored."""
        cid = str(data.get("id") or "")
        action = str(data.get("action") or "")
        if not _ID.match(cid) or not _ACTION.match(action):
            _LOGGER.info("phone consent from %s ignored: malformed", device_id)
            return False
        self._expire()
        held = self._open.get(cid)
        if held is not None and held.device_id != device_id:
            # Another device's open prompt: taking its id would route the console's answer here.
            _LOGGER.warning("phone consent %s from %s refused: the id is %s's", cid, device_id, held.device_id)
            return False
        if len(self._open) >= MAX_OPEN and held is None:
            _LOGGER.warning("phone consent from %s dropped: %d prompts already open", device_id, MAX_OPEN)
            return False
        try:
            ttl = min(MAX_TTL_S, max(1.0, float(data.get("timeout_s") or MAX_TTL_S)))
        except (TypeError, ValueError):
            ttl = MAX_TTL_S
        now = self._clock()
        self._open[cid] = PhoneConsent(
            id=cid, device_id=device_id, device_name=device_name[:80], action=action,
            params=str(data.get("params") or "")[:8000], reason=str(data.get("reason") or "")[:500],
            description=str(data.get("description") or "")[:500], created=now, expires=now + ttl,
        )
        self._fire(EVENT_PHONE_CONSENT, {"id": cid, "state": "open"})
        return True

    def resolved(self, device_id: str, consent_id: str) -> None:
        """The phone settled it itself (a tap, the car, its countdown)."""
        held = self._open.get(str(consent_id))
        if held is not None and held.device_id == device_id:
            self._open.pop(held.id, None)
            self._fire(EVENT_PHONE_CONSENT, {"id": held.id, "state": "resolved"})

    def listing(self) -> list[dict[str, Any]]:
        """Every open prompt, parameters included: for a console, never a device (the caller checks)."""
        self._expire()
        return [c.as_dict() for c in sorted(self._open.values(), key=lambda c: c.created)]

    def answer(self, consent_id: str, approved: Any, *, from_device: bool) -> dict[str, Any]:
        """The console's yes or no. Only ``True`` is a yes; anything else is a no."""
        if from_device:
            return {"ok": False, "error": "a device cannot answer a phone's prompt; answer it from the console"}
        self._expire()
        held = self._open.get(str(consent_id))
        if held is None:
            return {"ok": False, "error": "that prompt is no longer open on the phone"}
        yes = approved is True
        if not self._send(held.device_id, {"type": TYPE_CONSENT_ANSWER, "id": held.id, "approved": yes}):
            return {"ok": False, "error": f"{held.device_name or 'the phone'} is not connected; answer it there"}
        self._open.pop(held.id, None)
        _LOGGER.info("phone consent %s (%s on %s) %s from the console",
                     held.id, held.action, held.device_name, "approved" if yes else "denied")
        self._fire(EVENT_PHONE_CONSENT, {"id": held.id, "state": "approved" if yes else "denied"})
        return {"ok": True, "approved": yes, "action": held.action, "device": held.device_name}


#: Where the one relay lives on the running house.
DATA_CONSENTS = "phone_consents"


def get_consents(jarvis: Any) -> ConsentRelay:
    """The house's relay, pushing answers down the device's own socket."""
    relay = jarvis.data.get(DATA_CONSENTS)
    if relay is None:
        from .api.devices import get_devices  # local: the API imports this module

        def send(device_id: str, frame: dict[str, Any]) -> bool:
            link = get_devices(jarvis).get(device_id)
            return bool(link and link.push(frame))

        relay = ConsentRelay(send, lambda event, data: jarvis.bus.fire(event, data))
        jarvis.data[DATA_CONSENTS] = relay
    return relay
