"""Emergency outreach, the house's trigger (T-1013): a smoke, carbon-monoxide or gas alarm turning on
starts the alert the assistant's ``emergency_alert`` tool sends (T-0947).

    emergency:
      enabled: true     # false: an alarm is noticed by ``narrate`` and ``departure`` only
      fallback_url: ""  # an ntfy topic or n8n webhook URL; empty (the default): nothing leaves the house

The alert is the phone's: this sends it the same ``emergency_alert`` device command the plugin does
(``tests/contracts/emergency.json``), so the countdown a tap cancels, the location and the text and the
call to the first contact are all the phone's. The list is the plugin's (``emergency_contacts_set``,
which needs the owner's fingerprint) and reaches the house through ``POST /api/emergency/contacts``;
the house keeps a copy because the plugin lives in another container.

The phone's own fall and crash watch (T-1209) needs the same list, so the house hands it over (T-1262):
the ``emergency_contacts_sync`` device command (``tests/contracts/emergency-contacts-sync.json``), to every
connected phone that lists it when the list is saved, and to a phone a few seconds after it registers
(once per link). The phone only stores it; with no list the house sends an empty one. The list is never
logged here, only a count and a status; a phone on an older app is skipped, never prompted.

* One alert per alarm: a ``binary_sensor`` whose device class is smoke, carbon_monoxide or gas going
  ``off`` -> ``on``, then ``COOLDOWN_S`` of quiet for that entity (a flapping sensor, a cancelled
  countdown). The phone refuses a second alert within the contract's ``latch_s`` of one it sent, so two
  alarms at once are one alert.
* When nobody could be told (no contacts, no single phone online, the phone's error) the house says so
  through ``companion.notify`` at ``critical``. An absent phone does not use up the cool-down.
* The fallback (T-1208): when the phone cannot send it and ``fallback_url`` is set, the house itself
  makes one POST to that URL: JSON ``{"what", "kind", "time"}`` (the sentence, ``smoke`` /
  ``carbon_monoxide`` / ``gas``, UTC), no contact name or number and no location. It has a five second
  budget, follows no redirect and takes no proxy from the environment. The critical notice says whether
  the post was accepted (2xx; then the cool-down starts) or why not (refused, redirected, timed out,
  unreachable; then the next alarm tries again). It never raises into the sensor path.

Not covered: the fallback is one attempt to one URL, not a delivery: a 2xx says the owner's flow took
the post, not that anyone read it or was told, so that flow should confirm with the owner before it
tells anyone. The house never messages the contacts itself, and the URL is unset by default, so with no
URL and no single online phone nothing is sent. The phone's countdown is the only confirmation of its
own alert; a sensor that is ``on`` at boot or comes back from ``unavailable`` is not a new alarm; the
emergency number is never contacted from here or from the phone.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
import weakref
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from ...auth import get_auth
from ...api.devices import EVENT_DEVICE_REGISTERED, DeviceLink, get_devices
from ...const import EVENT_STATE_CHANGED
from ...store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "emergency"
ACTION = "emergency_alert"
ALARMS = {"smoke": "smoke", "carbon_monoxide": "carbon monoxide", "gas": "gas"}
#: The plugin's latch (tests/contracts/emergency.json limits.latch_s), so the house and the owner's own
#: request keep the same quiet.
COOLDOWN_S = 600
COUNTDOWN_S = 10
CONTACTS_MAX, NAME_MAX = 5, 40
#: The phone's answer waits on its countdown, so the wait is longer than a plain command's.
WAIT_S = 90.0
#: The fallback post's whole budget (connect, send and the answer's status line): the alert must not hang
#: the sensor's handler on a flow that does not answer.
FALLBACK_TIMEOUT_S = 5.0
#: tests/contracts/emergency-contacts-sync.json: the command that hands the phone the stored list (T-1262).
SYNC_ACTION = "emergency_contacts_sync"
#: How long a connecting phone is left alone before it is sent the list. The register handler fires
#: ``jarvis_device_registered`` before its reply leaves, and the phone ignores every command until that
#: reply arrives, so a push sent at once would be dropped.
SYNC_DELAY_S = 3.0
#: One push's wait for the phone's answer: bounded, so a phone that never answers holds no task for long.
SYNC_WAIT_S = 15.0
_NUMBER = re.compile(r"\+?[0-9]{7,15}")
_STORE_KEY = "emergency_contacts"
_CONTACTS = "emergency_contacts"
_SECRET, _ISSUED, _REFUSED_AT = "emergency_secret", "emergency_issued", "emergency_refused_at"
#: tests/contracts/emergency-proof.json: the proof's form and the windows around it.
PROOF_PREFIX = "jarvis-emergency-contacts/1"
MAX_AGE_S, SKEW_S, REFUSED_NOTICE_EVERY_S = 300, 60, 600
_NOT_THE_EMERGENCY_NUMBER = "If anyone is in danger, call the emergency number."


def what(device_class: str, name: Any) -> str:
    """One plain sentence for the text. The alarm's name is the owner's own, but a sensor's name is
    free text that goes out under their name: letters and spaces only, so no link, address or number
    survives (the phone refuses a ``what`` that carries one)."""
    words = " ".join("".join(c if c.isalpha() or c == " " else " " for c in str(name or "")).split())[:60]
    return f"The {ALARMS[device_class]} alarm{' ' + words if words else ''} is sounding."


def _valid(contacts: Any) -> list[dict[str, str]]:
    ok = isinstance(contacts, list) and len(contacts) <= CONTACTS_MAX and all(
        isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("number"), str)
        and 0 < len(c["name"].strip()) <= NAME_MAX and _NUMBER.fullmatch(c["number"]) for c in contacts)
    if not ok:
        raise ValueError(f"contacts must be a list of at most {CONTACTS_MAX} people, each with a name and a "
                         "number with its country code")
    return [{"name": c["name"].strip(), "number": c["number"]} for c in contacts]


class Refused(PermissionError):
    """A push of the list that carried no valid proof. The list is unchanged."""


def proof(secret: str, contacts: Any, issued: int) -> str:
    """The plugin's proof of a list (``tests/contracts/emergency-proof.json``): hex HMAC-SHA256 of the
    prefix, the list as sorted compact JSON, and the issue time. The list is signed as it is sent, so a
    proof for one list is no proof for another."""
    body = json.dumps(contacts, sort_keys=True, separators=(",", ":"))
    text = f"{PROOF_PREFIX}\n{body}\n{issued}"
    return hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest()


def _check(jarvis: "Jarvis", contacts: Any, issued: Any, mac: Any) -> None:
    secret = jarvis.data.get(_SECRET)
    if not isinstance(secret, str) or not secret:
        # An HMAC under the empty secret is one anybody can make: no secret, no list.
        raise Refused("this house has no emergency secret set")
    if type(issued) is not int or not isinstance(mac, str):
        raise Refused("the push carries no issue time or no proof")
    now = (jarvis.data.get("emergency_clock") or time.time)()
    if issued < now - MAX_AGE_S or issued > now + SKEW_S:
        raise Refused("the proof is outside its window")
    try:
        want = proof(secret, contacts, issued)
    except (TypeError, ValueError):
        raise Refused("the list cannot be proved") from None
    if not hmac.compare_digest(mac.encode(), want.encode()):
        raise Refused("the proof is not the plugin's for this list")
    if issued <= jarvis.data.get(_ISSUED, 0):
        # A captured push replayed, or a rollback to an older list the owner replaced.
        raise Refused("the proof is not newer than the last list kept")


async def _critical(jarvis: "Jarvis", message: str) -> None:
    if not jarvis.services.has_service("companion", "notify"):
        _LOGGER.warning("emergency: %s (companion.notify is not available)", message)
        return
    await jarvis.services.async_call(
        "companion", "notify", {"message": message, "importance": "critical", "kind": "notify"}, blocking=True)


async def _tell_the_owner(jarvis: "Jarvis", message: str) -> None:
    try:
        await _critical(jarvis, message)
    except Exception:  # noqa: BLE001 -- the notice failing must not undo a list the owner approved
        _LOGGER.exception("emergency: the owner could not be told")


async def set_contacts(jarvis: "Jarvis", contacts: Any, issued: Any, mac: Any) -> list[dict[str, str]]:
    """Keep the owner's list, whole or not at all, when the push carries the plugin's proof.

    Raises ``Refused`` for a push with no valid proof (and says so to the owner, at most once per
    ``REFUSED_NOTICE_EVERY_S``), ``ValueError`` for a proved list the phone would refuse. A list that
    differs from the one held tells the owner at ``critical`` with the count, never a name or number.

    The proof is a secret shared by the plugin and this house (``AURA_EMERGENCY_SECRET``), not the owner's
    fingerprint: this house cannot check the phone's signature (the adapter holds that key). So it stops
    whoever has only the house token; it does not stop whoever holds the secret too, which means the Hermes
    container (its code, or a model that has run code there) or anyone who can read either container's
    environment, and it does not stop an owner-level attacker on this host."""
    try:
        _check(jarvis, contacts, issued, mac)
    except Refused as why:
        _LOGGER.warning("emergency: a contact list push was refused: %s", why)
        now = (jarvis.data.get("emergency_clock") or time.time)()
        if now - jarvis.data.get(_REFUSED_AT, -1e9) >= REFUSED_NOTICE_EVERY_S:
            jarvis.data[_REFUSED_AT] = now
            await _tell_the_owner(
                jarvis, "A change to the emergency contact list was refused: it carried no valid proof from "
                "the owner's approval. The list is unchanged. If you did not just change it, someone with "
                "the house token tried to.")
        raise
    cleaned = _valid(contacts)
    changed = cleaned != jarvis.data.get(_CONTACTS)
    # Set before the await, so two pushes with one issue time cannot both pass the check above.
    jarvis.data[_CONTACTS] = cleaned
    jarvis.data[_ISSUED] = issued
    await Store(jarvis.config_dir, _STORE_KEY).save({"contacts": cleaned, "issued": issued})
    sync_to_phones(jarvis)
    if changed:
        await _tell_the_owner(
            jarvis, f"The emergency contact list was changed: the house now has {len(cleaned)} "
            f"{'person' if len(cleaned) == 1 else 'people'}. If you did not approve this, "
            "check who has the house token.")
    return cleaned


def _takes_the_list(jarvis: "Jarvis", link: DeviceLink | None) -> bool:
    """A phone the owner paired, on the channel, whose app knows the command.

    Who it is comes from the token the socket authenticated with (the house sets `link.token_id`, the
    device never does) and must be one the pairing flow minted, checked live so a revoked phone stops
    getting the list. The platform and manifest are the device's own words: they only say whether its
    app knows the command (an app that does not would meet it as an unknown action, tier 3: a consent
    prompt thrown over the owner for plumbing), never whether it may have the owner's contacts.

    The hub has no owner scope on a token, so "minted by pairing" is the nearest identity it keeps: a
    REST token, the first-run token and the environment token never qualify, and neither does a token
    stored before the mark existed (that phone pairs again). It does not tell one paired phone from
    another, or a paired phone from someone holding its token."""
    auth = get_auth(jarvis)
    return (link is not None and link.platform == "android" and link.connected
            and link.action(SYNC_ACTION) is not None
            and auth is not None and auth.is_paired(link.token_id))


async def _push_list(jarvis: "Jarvis", link: DeviceLink) -> None:
    """Hand one phone the stored list (an empty one when there is none, so a list the owner removed is
    forgotten). The phone only keeps it for its fall and crash watch. Never raises, and says only how
    many people and what became of it: not the list, and not the phone's own words."""
    contacts = jarvis.data.get(_CONTACTS) or []
    try:
        # The one send the generic `dispatch` refuses for every caller: only this private door sends it.
        outcome = await link.dispatch_house_only(
            SYNC_ACTION, {"contacts": contacts}, reason="the house keeps the emergency contacts on the phone",
            timeout=SYNC_WAIT_S)
    except Exception:  # noqa: BLE001 -- the owner's save and the phone's connect must not meet this
        _LOGGER.warning("emergency: the contact list could not be sent to a phone", exc_info=False)
        return
    if outcome.get("status") != "ok":
        _LOGGER.warning("emergency: a phone did not take the contact list of %d (%s)", len(contacts),
                        str(outcome.get("status") or "error")[:32])


def sync_to_phones(jarvis: "Jarvis") -> None:
    """After a save: every connected phone is sent the stored list, without waiting on any of them."""
    for link in get_devices(jarvis).all():
        if _takes_the_list(jarvis, link):
            jarvis.async_create_task(_push_list(jarvis, link))


class Outreach:
    def __init__(self, jarvis: "Jarvis", fallback_url: Any = "") -> None:
        self.jarvis = jarvis
        self.fallback_url = fallback_url.strip() if isinstance(fallback_url, str) else ""
        self.clock = jarvis.data.get("emergency_clock") or time.time
        self._last: dict[str, float] = {}
        #: Links already sent the list on this connection. A phone re-registers on the same socket whenever
        #: a permission changes (the list has not); a new socket is a new link, and a fresh install may
        #: hold nothing.
        self._synced: weakref.WeakSet[DeviceLink] = weakref.WeakSet()

    async def on_registered(self, event: Any) -> None:
        """A phone connected: send it the list once the register reply has had time to reach it."""
        try:
            hub = get_devices(self.jarvis)
            link = hub.get(str(event.data.get("device_id") or ""))
            if not _takes_the_list(self.jarvis, link) or link in self._synced:
                return
            assert link is not None
            self._synced.add(link)
            await asyncio.sleep(float(self.jarvis.data.get("emergency_sync_delay_s", SYNC_DELAY_S)))
            # not gone, replaced or revoked meanwhile
            if hub.get(link.device_id) is link and _takes_the_list(self.jarvis, link):
                await _push_list(self.jarvis, link)
        except Exception:  # a notice must never break the register path
            _LOGGER.exception("emergency: could not send a connecting phone the contact list")

    async def on_state(self, event: Any) -> None:
        try:
            await self._handle(event.data)
        except Exception:  # a notice must never break the state machine
            _LOGGER.exception("emergency: could not handle a state change")

    async def _handle(self, data: dict[str, Any]) -> None:
        new, old = data.get("new_state"), data.get("old_state")
        kind = new.attributes.get("device_class") if new is not None else None
        if (new is None or old is None or old.state != "off" or new.state != "on" or kind not in ALARMS
                or not new.entity_id.startswith("binary_sensor.")):
            return
        now = self.clock()
        if now - self._last.get(new.entity_id, -1e9) < COOLDOWN_S:
            return
        name = new.attributes.get("friendly_name")
        if await self.alert(kind, name):
            self._last[new.entity_id] = now

    async def alert(self, kind: str, name: Any) -> bool:
        """Send the phone the alert. True when the phone was asked (it answered or not) or the fallback
        took the post, so the cool-down starts; False when nothing could be sent, so the next alarm tries
        again."""
        contacts = self.jarvis.data.get(_CONTACTS)
        if not contacts:
            return await self.unsent(kind, name, "no emergency contacts are set")
        phones = [d for d in get_devices(self.jarvis).all() if d.platform == "android" and d.connected]
        if len(phones) != 1:
            return await self.unsent(kind, name, "the owner's phone is not the one online phone on the "
                                     "house channel" if phones else "no phone is online to send from")
        outcome = await phones[0].dispatch(
            ACTION, {"contacts": contacts, "what": what(kind, name), "countdown_s": COUNTDOWN_S},
            reason=f"the house's {ALARMS[kind]} alarm", timeout=WAIT_S)
        result = outcome.get("result")
        # The house wraps the phone's answer: `status` is whether the command ran there, and
        # `result.status` is what the phone said (ok, cancelled, error). Reading the outer one alone
        # took a cancelled countdown for a sent alert.
        status = str(outcome.get("status") or "error")
        if status == "ok" and isinstance(result, dict):
            status = str(result.get("status") or "error")
        if status == "cancelled":
            return True  # the owner's own tap: said by nothing, and not asked again for the cool-down
        if status != "ok":
            await self.unsent(kind, name, str(outcome.get("error") or "the phone did not send the alert")[:120])
        return True

    async def unsent(self, kind: str, name: Any, why: str) -> bool:
        """The phone could not send the alert: post it to the fallback, if the owner set one, and say at
        ``critical`` what happened. True when the fallback accepted the post."""
        failed = await self.fallback(kind, name) if self.fallback_url else None
        subject = f"The {ALARMS[kind]} alarm{' ' + str(name)[:60] if name else ''} is sounding"
        if self.fallback_url and failed is None:
            message = (f"{subject} and the phone could not send the alert ({why}); the house posted it to the "
                       "fallback address instead. That the post was accepted is not a sign anyone was reached.")
        else:
            also = f"; the fallback address was tried and {failed}" if failed else ""
            message = f"{subject} but nobody was told: {why}{also}."
        await _critical(self.jarvis, f"{message} {_NOT_THE_EMERGENCY_NUMBER}")
        return self.fallback_url != "" and failed is None

    async def fallback(self, kind: str, name: Any) -> str | None:
        """One POST of what happened, the kind and the time to the owner's fallback URL: ``None`` when it
        was accepted (2xx), else why not. Never raises, never follows a redirect, never uses a proxy from
        the environment, and the reason never carries the URL (it can hold the topic or token). One
        attempt, no retry: the next alarm after the cool-down is the retry."""
        body = {"what": what(kind, name), "kind": kind,
                "time": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")}
        try:
            if urlsplit(self.fallback_url).scheme not in ("http", "https"):
                return "it is not an http or https address"
            async with httpx.AsyncClient(timeout=FALLBACK_TIMEOUT_S, follow_redirects=False, trust_env=False) as client:
                # Streamed and never read: only the status matters, and a body is the owner's flow's to send.
                async with asyncio.timeout(FALLBACK_TIMEOUT_S):
                    async with client.stream("POST", self.fallback_url, json=body) as response:
                        code = response.status_code
        except (TimeoutError, httpx.TimeoutException):
            return "it did not answer in time"
        except Exception:  # noqa: BLE001 -- a bad URL or a dead network must not reach the sensor path
            _LOGGER.warning("emergency: the fallback could not be reached", exc_info=False)
            return "it could not be reached"
        if 200 <= code < 300:
            return None
        return "it answered with a redirect (not followed)" if 300 <= code < 400 else f"it answered HTTP {code}"


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}
    if options.get("enabled", True) is False:
        return True
    secret = options.get("contacts_secret")
    jarvis.data[_SECRET] = secret if isinstance(secret, str) else ""
    if not jarvis.data[_SECRET]:
        _LOGGER.warning("emergency: no AURA_EMERGENCY_SECRET is set, so the contact list cannot be changed")
    stored = await Store(jarvis.config_dir, _STORE_KEY).load()
    issued = (stored or {}).get("issued")
    jarvis.data[_ISSUED] = issued if type(issued) is int else 0
    try:
        jarvis.data[_CONTACTS] = _valid((stored or {}).get("contacts") or [])
    except ValueError:
        _LOGGER.warning("emergency: the stored contact list is not valid and was ignored")
        jarvis.data[_CONTACTS] = []
    outreach = jarvis.data["emergency_outreach"] = Outreach(jarvis, options.get("fallback_url"))
    unsub = jarvis.bus.listen(EVENT_STATE_CHANGED, outreach.on_state)
    jarvis.register_shutdown(unsub)
    jarvis.register_shutdown(jarvis.bus.listen(EVENT_DEVICE_REGISTERED, outreach.on_registered))
    return True
