"""The phone's signature on a sensitive approval (T-0945).

A sensitive call (``hermes/plugins/_shared/_matrix.py`` says which: locks and alarms, the
vault, destructive commands, settings and capability changes) is not put to Hermes' own
approval queue. Hermes will take a yes for a queued approval from any socket, from its legacy
``approval.respond`` call, from ``/v1/runs/…/approval`` and from ``/approve`` on a chat
platform, and a relay can only watch the first of those. Instead the approval gate, inside the
tool call, asks here (``POST /api/approval/proof``) and waits: the :class:`Desk` shows the ask
on every app as an ``approval.request`` with ``confirm: "phone"`` and a challenge, and says yes
only for an ``approval.respond`` whose ``signature`` over that challenge verifies against a key
a paired phone registered (``POST /api/approval/key``). On the phone that key lives in the
Android Keystore and signs only after a fingerprint or the device credential, so a tap on the
console, a word in the car or a script holding a device key cannot say yes to an unlock.

The challenge is the ask's id and its marked rule key, both minted per ask from 128 random
bits each, so a captured signature answers nothing else, ever; and a digest of the tool and
the text the card shows (T-1030), so the signature is over what the owner read.

Does not cover: the master key, which can pair a "phone" of its own and register a key for it
(Hermes holds that key too, so a model that read it out of its own environment could); a house
where no phone has registered a key yet, or whose adapter the gate cannot reach, where a
sensitive ask is Hermes' ordinary one-time ask as before (refusing would lock the owner out of
every sensitive row); proof that a registered key really is Keystore-held (no attestation).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import secrets
from typing import Any, Callable, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

#: Anchored at the end: the gate appends the mark last, so text earlier in a key is not one.
_MARK = re.compile(r":sensitive:[0-9a-f]{32}$")
#: A P-256 SubjectPublicKeyInfo is 91 bytes; anything much longer is not one.
_MAX_KEY_CHARS = 256
_MAX_SIGNATURE_CHARS = 256

REFUSED = "this approval needs your fingerprint on the phone"
#: A sensitive yes is for this one call.
CHOICES = ["once", "deny"]
#: Where the phone is in a pocket: long enough to take it out, short enough that a tool call
#: nobody is watching does not hold its turn for the whole approval window.
WAIT_S = 120.0
MAX_WAIT_S = 600.0
#: Nobody honest has this many unlocks waiting; a looping model must not fill every screen.
MAX_OPEN = 8
_MAX_TEXT = 4000


def challenge(params: dict[str, Any]) -> str | None:
    """What the phone signs for this approval, or None when it is not a sensitive one."""
    key, rid = params.get("pattern_key"), params.get("request_id")
    if not isinstance(key, str) or not rid or not _MARK.search(key):
        return None
    # The tool and the text the owner reads are in it too (T-1030): the id and the rule alone
    # would let the same signature stand under a card that said something else.
    # Built here from what the gate sent: no defence against a holder of the master key, who
    # can ask for any text to be shown. JSON, so a newline in the text cannot move the seam.
    shown = hashlib.sha256(json.dumps([params.get("command"), params.get("description")]).encode()).hexdigest()
    return f"{rid}\n{key}\n{shown}"


def _load(public: Any) -> ec.EllipticCurvePublicKey | None:
    if not isinstance(public, str) or not 0 < len(public) <= _MAX_KEY_CHARS:
        return None
    try:
        key = serialization.load_der_public_key(base64.b64decode(public, validate=True))
    except (ValueError, binascii.Error, TypeError):
        return None
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        return None
    return key


def valid_key(public: Any) -> bool:
    """True for a base64 DER P-256 public key, which is what the phone's Keystore makes."""
    return _load(public) is not None


def verify(keys: Iterable[str], signed: str, signature: Any) -> bool:
    """True when ``signature`` (base64 DER ECDSA, SHA-256) over ``signed`` is by one of ``keys``."""
    if not isinstance(signature, str) or not 0 < len(signature) <= _MAX_SIGNATURE_CHARS:
        return False
    try:
        raw = base64.b64decode(signature, validate=True)
    except (ValueError, binascii.Error):
        return False
    for public in keys:
        key = _load(public)
        if key is None:
            continue
        try:
            key.verify(raw, signed.encode(), ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            continue
    return False


def _event(kind: str, payload: dict[str, Any]) -> str:
    # No session_id: every app drops an event for a session it is not showing, and this ask
    # is the house's. It must reach the phone whichever conversation raised it.
    return json.dumps({"jsonrpc": "2.0", "method": "event", "params": {"type": kind, "payload": payload}},
                      separators=(",", ":"))


class Desk:
    """The sensitive asks waiting for a fingerprint, for the whole adapter.

    ``keys`` reads the paired phones' signing keys when needed (one registered or revoked
    while an ask is open counts at once); ``show`` puts a frame on every app socket.
    """

    def __init__(self, keys: Callable[[], list[str]], show: Callable[[str], None]) -> None:
        self._keys, self._show = keys, show
        #: request id -> (session id, what the phone signs, the event as shown, its answer)
        self._open: dict[str, tuple[str, str, str, asyncio.Future[str]]] = {}

    def available(self) -> bool:
        """False until a phone has registered a key: nothing could sign."""
        return bool(self._keys())

    def holds(self, request_id: str) -> bool:
        return request_id in self._open

    def shown(self) -> list[str]:
        """The open asks, for an app that connects while they wait."""
        return [frame for _sid, _signed, frame, _answer in self._open.values()]

    async def ask(self, session_id: str, tool: str, message: str, rule_key: str, wait_s: float = WAIT_S) -> str:
        """Show one ask and wait: "once" (signed by a phone), "deny" or "timeout"."""
        rid = "proof-" + secrets.token_hex(16)
        # The same fields Hermes' own ask carries, so every app's card reads it unchanged.
        params = {"request_id": rid, "command": f"<{tool}> (plugin approval rule)",
                  "description": message[:_MAX_TEXT], "pattern_key": f"plugin_rule:{rule_key}",
                  "choices": CHOICES, "options": CHOICES, "confirm": "phone"}
        signed = challenge(params)
        if signed is None:
            raise ValueError("not a sensitive rule key")
        params["challenge"] = signed
        if len(self._open) >= MAX_OPEN:
            return "deny"
        answer: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._open[rid] = (session_id, signed, _event("approval.request", params), answer)
        self._show(self._open[rid][2])
        choice = "timeout"
        try:
            choice = await asyncio.wait_for(answer, min(max(wait_s, 1.0), MAX_WAIT_S))
        except asyncio.TimeoutError:
            pass
        finally:
            del self._open[rid]
            # Every app takes the card down, whichever of them answered (or none did).
            self._show(_event("approval.resolved", {"request_id": rid, "choice": choice}))
        return choice

    def answer(self, request_id: str, choice: str, signature: Any) -> bool:
        """An app's answer to an open ask; False when it is an allow without the phone's signature."""
        _sid, signed, _frame, answer = self._open[request_id]
        if choice != "deny":
            if not verify(self._keys(), signed, signature):
                return False
            choice = "once"  # never remembered, whatever the app asked for
        if not answer.done():
            answer.set_result(choice)
        return True
