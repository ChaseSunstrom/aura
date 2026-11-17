"""Pairing the desktop with the house: a link or a code becomes a token in the config.

The owner (2026-09-10): *"allow automatic pairing of the desktop app when
opening it (via setup, that allows you to set a link and claim the pairing
token)"*. The console's pairing panel draws a QR whose text is
``jarvis://pair?v=1&u=<house url>&c=<code>`` (``aura-web/src/lib/hud/data/pairing.ts``)
and shows the same code in figures. Either is enough here: the link is parsed,
or the server and the code are asked for, and ``POST {u}/api/pair/claim`` is
exchanged for the long-lived token exactly as the phone does
(``aura-core/aura/api/pairing.py``, ``async_claim``: unauthenticated, single
use, the token exists only in that reply).

What this writes: ``server_url`` (the ws form of the house URL) and ``token``
into ``config.json``, nothing else. What it does not do: it does not store the
code (spent on claim), does not print the token, and does not retry a refused
claim -- a code is one use, and a second attempt on a refused one is a pairing
attempt against the house's rate limit.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlparse

from .config import default_config_path, normalize_server_url

_LOGGER = logging.getLogger(__name__)

#: The scheme and version the console's QR carries.
LINK_SCHEME = "jarvis"
LINK_VERSION = "1"
#: What the house calls this device unless told otherwise.
DEFAULT_NAME = "desktop"
CLAIM_TIMEOUT_S = 15.0


class PairingError(ValueError):
    """Anything that stops a pairing, in a sentence a person can act on."""


@dataclass(frozen=True)
class PairingLink:
    """A parsed ``jarvis://pair`` link: where the house is, and the one-use code."""

    url: str
    code: str


def parse_link(text: str) -> PairingLink | None:
    """``jarvis://pair?v=1&u=…&c=…`` -> its parts; None for anything else.

    None rather than an error, so a caller can tell a pasted link from a pasted
    server address and ask for the code separately in the second case.
    """
    raw = (text or "").strip()
    if not raw.lower().startswith(f"{LINK_SCHEME}://"):
        return None
    parsed = urlparse(raw)
    if parsed.netloc.lower() != "pair" and parsed.path.strip("/").lower() != "pair":
        raise PairingError("that is a jarvis:// link, but not a pairing link")
    query = parse_qs(parsed.query)
    if query.get("v", [LINK_VERSION])[0] != LINK_VERSION:
        raise PairingError(f"pairing link version {query['v'][0]} is not one this app reads (v{LINK_VERSION})")
    url = (query.get("u") or [""])[0].strip()
    code = (query.get("c") or [""])[0].replace(" ", "").strip()
    if not url or not code:
        raise PairingError("the pairing link is missing the server or the code")
    return PairingLink(url=url, code=code)


def house_http_url(server: str) -> str:
    """The house's HTTP base from whatever was typed: ``host``, ``host:8080``,
    ``http://…``, ``ws://…/api/websocket``. The claim is an HTTP POST."""
    text = (server or "").strip()
    if not text:
        raise PairingError("a server address is required")
    if "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if scheme not in ("http", "https"):
        raise PairingError(f"the server address must be http(s):// or ws(s):// (got {parsed.scheme}://)")
    return f"{scheme}://{parsed.netloc}"


def claim(server: str, code: str, name: str = DEFAULT_NAME,
          opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Exchange the code for a token at ``{server}/api/pair/claim``.

    Returns the house's reply (``token``, ``token_id``, ``name``). ``opener``
    is injectable for the tests; the default is urllib with a short timeout,
    the same client the rest of this package uses.
    """
    base = house_http_url(server)
    clean = (code or "").replace(" ", "").strip()
    if not clean:
        raise PairingError("a pairing code is required")
    # "desktop": the console mints no adapter key for it, since this agent never talks to the
    # adapter (tests/contracts/pairing-claim.json, T-0569).
    body = json.dumps({"code": clean, "name": name or DEFAULT_NAME, "kind": "desktop"}).encode("utf-8")
    req = urlrequest.Request(f"{base}/api/pair/claim", data=body, method="POST",
                             headers={"content-type": "application/json", "accept": "application/json"})
    open_ = opener or (lambda r: urlrequest.urlopen(r, timeout=CLAIM_TIMEOUT_S))
    try:
        with open_(req) as resp:
            reply = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:  # noqa: BLE001 - the body is optional colour on the status
            detail = ""
        if exc.code in (400, 401, 403, 404, 429):
            raise PairingError(detail or f"the house refused the code (HTTP {exc.code})") from exc
        raise PairingError(f"the house answered HTTP {exc.code} to the claim") from exc
    except urlerror.URLError as exc:
        raise PairingError(f"could not reach {base}: {exc.reason}") from exc
    except ValueError as exc:
        raise PairingError("the house's reply to the claim was not JSON") from exc
    token = str(reply.get("token") or "")
    if not token:
        raise PairingError("the house returned no token for the code")
    return reply


def write_pairing(config_path: str | os.PathLike[str] | None, server: str, token: str) -> Path:
    """``server_url`` and ``token`` into the config file, keeping everything else in it.

    The file is made readable by this user only, because it now holds a token.
    """
    path = Path(config_path) if config_path else default_config_path()
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, ValueError):
            existing = {}
    existing["server_url"] = normalize_server_url(server)
    existing["token"] = token
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - Windows has no mode bits worth setting
        _LOGGER.debug("could not tighten the config file's permissions")
    return path


def pair(link_or_server: str, code: str = "", name: str = DEFAULT_NAME,
         config_path: str | os.PathLike[str] | None = None,
         opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    """One pairing, start to finish: parse, claim, write. Returns what to say."""
    link = parse_link(link_or_server)
    server = link.url if link else link_or_server
    the_code = link.code if link else code
    reply = claim(server, the_code, name, opener=opener)
    path = write_pairing(config_path, server, str(reply["token"]))
    return {"name": reply.get("name") or name, "token_id": reply.get("token_id"),
            "server_url": normalize_server_url(server), "config": str(path)}


def interactive(config_path: str | os.PathLike[str] | None = None,
                ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                opener: Callable[..., Any] | None = None) -> int:
    """The setup a person walks through: paste the link, or the address and the code."""
    say("Pair this desktop with the house.")
    say("Open the console's Pairing panel and paste the pairing link here, or type the house's address.")
    first = ask("Link or address: ").strip()
    if not first:
        say("Nothing to pair with.")
        return 2
    code = ""
    if parse_link(first) is None:
        code = ask("Pairing code (the figures under the QR): ").strip()
    name = ask(f"Name for this device [{DEFAULT_NAME}]: ").strip() or DEFAULT_NAME
    try:
        out = pair(first, code, name, config_path=config_path, opener=opener)
    except PairingError as exc:
        say(f"Not paired: {exc}")
        return 1
    say(f"Paired as {out['name']!r} with {out['server_url']}; written to {out['config']}.")
    return 0


def can_ask() -> bool:
    """A terminal on both ends: setup can ask questions."""
    return sys.stdin.isatty() and sys.stdout.isatty()
