"""Handlers for the ``emergency`` toolset: the owner's emergency contacts, and the alert that reaches them.

The list lives here; the countdown, the location and the send are the phone's (``emergency_alert`` in
``tests/contracts/emergency.json``). What this does *not* cover: that the phone sends (it may be off, out
of signal or cancelled), that a contact reads the text, or the emergency number, which this never
contacts. Nothing here logs a contact's name or number.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from aura_schema import from_env

from _shared import _house
from _shared._jsonstore import read_json, write_json

ACTION = "emergency_alert"
CONTACTS_MAX, NAME_MAX, WHAT_MAX = 5, 40, 200
COUNTDOWN_S = 10
#: tests/contracts/emergency-proof.json: the house checks the same form (integrations/emergency).
PROOF_PREFIX = "jarvis-emergency-contacts/1"
#: One alert, then quiet: a flapping alarm or a model in a loop must not text the list twice.
LATCH_S = 600
#: tests/contracts/emergency.json never_numbers: this is not a way to reach emergency services.
NEVER = frozenset({"911", "112", "999", "000", "111", "110", "119", "118", "102", "108", "115", "122"})
NOT_THE_EMERGENCY_NUMBER = "This does not reach emergency services: if anyone is in danger, call the emergency number."
_NUMBER = re.compile(r"\+?[0-9]{7,15}")
#: A link, a mail address, a domain, or seven digits (any script's) with no letter between them:
#: a phone number however it is spaced. tests/contracts/emergency.json what_refused / what_allowed.
_LINK = re.compile(r"(?i)://|\b[a-z][a-z0-9+.-]{1,15}:\S|www\.|@|\b[\w-]+\.[^\W\d_]{2,}\b|\d(?:[\W_]*\d){6,}")  # pragma: allowlist secret (a pattern, not a URL)
_LOCK = threading.Lock()


def _path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-emergency" / "contacts.json"


def _err(message: str, **kw: Any) -> str:
    return json.dumps({"ok": False, **kw, "error": message})


def _contacts() -> list[dict[str, str]]:
    stored = read_json(_path()).get("contacts")
    return [c for c in stored if _clean(c) == c] if isinstance(stored, list) else []


def _clean(entry: Any) -> dict[str, str] | None:
    """The entry as it is stored, or None when it is not a person and a dialable number."""
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("number"), str):
        return None
    name = " ".join(entry["name"].split())
    number = re.sub(r"[\s().-]", "", entry["number"])
    if not name or len(name) > NAME_MAX or not _NUMBER.fullmatch(number) or number.lstrip("+") in NEVER:
        return None
    return {"name": name, "number": number}


def handle_contacts_list(args: dict[str, Any], **_: Any) -> str:
    """The saved list, and the house given its copy on the way (T-1212): a list saved before the house
    kept one reached it only on the next save, so its alarm had nobody to tell. The push is the list
    the fingerprint already approved, unchanged. An empty list pushes nothing (a save clears the house)."""
    saved = _contacts()
    return json.dumps({"ok": True, "contacts": saved, "house_has_it": bool(saved) and _tell_the_house(saved)})


def handle_contacts_set(args: dict[str, Any], **_: Any) -> str:
    given = args.get("contacts")
    if not isinstance(given, list) or len(given) > CONTACTS_MAX:
        return _err(f"contacts must be a list of at most {CONTACTS_MAX} people, each with a name and a number")
    # A short code is checked before the length rule, so "911" is told why and not "too short".
    if any(isinstance(c, dict) and re.sub(r"\D", "", str(c.get("number") or "")) in NEVER for c in given):
        return _err("an emergency-services number cannot be an emergency contact. " + NOT_THE_EMERGENCY_NUMBER)
    cleaned = [c for c in map(_clean, given) if c is not None]
    if len(cleaned) != len(given):
        return _err("each contact needs a name and a full phone number with its country code; nothing was changed")
    with _LOCK:
        write_json(_path(), {**read_json(_path()), "contacts": cleaned})
    return json.dumps({"ok": True, "contacts": cleaned, "house_has_it": _tell_the_house(cleaned)})


def _tell_the_house(contacts: list[dict[str, str]]) -> bool:
    """Give the house its copy of the list, so a smoke or CO alarm there can start the same alert
    (jarvis-core ``integrations/emergency``). The list stays this plugin's: this is a push, not a second
    editor. False when the house could not be told, and then an alarm there alerts nobody until the next
    save; the owner's own request is not affected.

    The push carries a proof (``tests/contracts/emergency-proof.json``): an HMAC of the list and the time
    under ``AURA_EMERGENCY_SECRET``, which the house requires. It is made only here, after the owner's
    fingerprint approved the save, so the house token alone cannot change the house's copy. It is a
    shared secret, not the fingerprint's signature (the house cannot check that): it does not stop
    whoever holds the secret too. With no secret set nothing is pushed."""
    secret = from_env().emergency_contacts_secret
    if not _house.configured() or not secret:
        return False
    issued = int(time.time())
    body = json.dumps(contacts, sort_keys=True, separators=(",", ":"))
    text = f"{PROOF_PREFIX}\n{body}\n{issued}"
    mac = hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest()
    try:
        with _house.client(timeout=5.0) as c:
            sent = c.post("/api/emergency/contacts", json={"contacts": contacts, "issued": issued, "proof": mac})
            return sent.status_code < 400
    except Exception:  # noqa: BLE001 -- the house is down: the list is saved here, say it is not there
        return False


def handle_alert(args: dict[str, Any], **kwargs: Any) -> str:
    what = args.get("what")
    if not isinstance(what, str) or not what.strip() or len(what) > WHAT_MAX:
        return _err(f"what (one short sentence, at most {WHAT_MAX} characters) is required")
    if _LINK.search(what):
        # The text goes out under the owner's name to people who trust it: no link, no number to ring.
        return _err("what must be a plain sentence with no link, address or phone number in it (leave the "
                    "date and time out: the phone adds them)")
    contacts = _contacts()
    if not contacts:
        return _err("no emergency contacts are set, so nobody was told. Ask the owner who to reach in an "
                    "emergency and save them with emergency_contacts_set. " + NOT_THE_EMERGENCY_NUMBER)
    with _LOCK:
        last = read_json(_path()).get("last_alert")
        if isinstance(last, (int, float)) and 0 <= time.time() - last < LATCH_S:
            return _err("an alert already went to the emergency contacts in the last ten minutes; it was not sent again")
        try:
            with _house.client() as c:
                # With two phones online, the one this turn came from (T-1576).
                device_id, token, problem = _house.pick_phone(c, str(kwargs.get("session_id") or ""))
                if problem:
                    return _err(("two phones are online and this turn did not come from either, so which "
                                 "one sends is not known" if problem == "several" else "no phone is online to "
                                 "send from") + ", so nobody was told. " + NOT_THE_EMERGENCY_NUMBER)
                ran = _house.run_action(c, device_id, ACTION,
                                        {"contacts": contacts, "what": what.strip(), "countdown_s": COUNTDOWN_S},
                                        "the owner asked for help", reserved=True, device_token=token)
        except Exception as exc:  # noqa: BLE001 -- the house is down: say so, do not raise into the loop
            return _err(f"the house could not be reached ({type(exc).__name__}), so nobody was told. "
                        + NOT_THE_EMERGENCY_NUMBER)
        # The house wraps the phone's answer: `status` is whether the command ran there, and
        # `result` is what the phone said (its own status, texted, called, located). Reading the
        # outer one alone took a cancelled countdown for a sent alert, and latched on it.
        outer = ran.get("response")
        outer = outer if isinstance(outer, dict) else {}
        result = outer.get("result")
        answer: dict = result if isinstance(result, dict) else {}
        status = str(outer.get("status") or "error")
        if status == "ok":
            status = str(answer.get("status") or "error")
        else:
            answer = outer
        if status == "cancelled":
            return _err("cancelled on the phone; nothing was sent", status=status)
        if status != "ok" or not ran.get("ok"):
            return _err((str(answer.get("error") or "")[:200] or "the phone did not send the alert")
                        + ". " + NOT_THE_EMERGENCY_NUMBER, status=status)
        write_json(_path(), {**read_json(_path()), "last_alert": time.time()})
    return json.dumps({"ok": True, "status": status, "texted": answer.get("texted"), "called": answer.get("called"),
                       "located": answer.get("located"), "note": NOT_THE_EMERGENCY_NUMBER})
