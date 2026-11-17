"""The one WebDAV / CardDAV layer under aura-contacts and -files.

The two plugins talk to a single server (a Nextcloud- or Radicale-shaped
deployment) addressed by the ``dav_*`` settings: ``dav_url`` is the base
(``https://cloud.lan/remote.php/dav``), ``dav_username`` / ``dav_password`` the
login, ``dav_verify_ssl`` whether TLS is checked. The per-service roots
(``carddav_path``, ``webdav_path``) belong to the plugins. Calendars are not
here: jarvis-core's calendar integration is the one CalDAV client in the house
and ``aura-calendar`` reaches it through the house REST — its ``REPORT`` and
``VEVENT`` reader live there, and ``report`` below is kept for the
``addressbook-query`` that CardDAV needs.

Why one module rather than three clients:

* One credential path. A password reaching a tool result or a log line is the
  failure this file exists to make impossible, so every error string leaves
  through :func:`redact` and every handler through :func:`safe_json`.
* One multistatus parser. DAV answers ``207 Multi-Status`` with namespaced XML;
  three hand-rolled parsers would be three different ideas of what a missing
  property means (here: a property whose ``propstat`` status is not 2xx is
  absent, not empty).
* One iCalendar/vCard reader, so the line-unfolding rule of RFC 5545 §3.1 is
  implemented once. A parser that splits on newlines silently truncates every
  summary longer than 75 octets — the bug that looks like "the server dropped
  half my event titles".

What this module does *not* do: it is not a full RFC 4918/5545/6350
implementation. It reads the handful of properties the plugins ask for and
ignores the rest; it does not resolve TZID against a VTIMEZONE (a floating
local time comes back as it was written), does not follow ``VALARM``,
``RRULE``, ``RECURRENCE-ID`` or ``VTODO``, does not decode quoted-printable
vCard 2.1, and does no locking, versioning or conflict resolution beyond the
``If-None-Match`` header a caller passes itself.

``_shared/`` is a sibling of every plugin directory and carries no
``plugin.yaml``, so Hermes never loads it as a plugin; each plugin's
``__init__`` puts the plugins root on ``sys.path`` and imports this as
``_shared._dav`` (the loader gives a plugin no parent package to reach a
sibling through). See ``_approval_hook.py`` for the same register.
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import _egress, _failure
from aura_schema import from_env

_LOGGER = logging.getLogger(__name__)

#: Seconds a DAV call may take. A calendar REPORT over a year of events on a
#: Raspberry-Pi-class server is slow; anything past this is a hung server, and a
#: tool call that never returns wedges the whole Hermes turn.
DEFAULT_TIMEOUT: float = 30.0

#: The PROPFIND body used when a caller asks for no particular properties.
ALLPROP: str = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:allprop/></d:propfind>'
)

#: XML content type DAV servers expect on PROPFIND/REPORT bodies.
XML_CONTENT_TYPE: str = 'application/xml; charset="utf-8"'

#: ``{DAV:}response`` -> ``response``.
_NS_RE = re.compile(r"^\{[^}]*\}")
#: ``https://user:pw@host/`` -> the ``user:pw@`` part, so it can be cut out.
_USERINFO_RE = re.compile(r"(?<=://)[^/@\s]+@")
#: ``HTTP/1.1 200 OK`` -> ``200``.
_STATUS_RE = re.compile(r"\b(\d{3})\b")


# --------------------------------------------------------------------------- #
# Configuration and URLs
# --------------------------------------------------------------------------- #
def available() -> bool:
    """Whether a DAV server is configured at all — the tools' ``check_fn``.

    Only the base URL is required: an unauthenticated Radicale on the LAN is a
    real deployment, so a missing username is not "unconfigured".
    """
    return bool(from_env().dav_url)


def base_url() -> str:
    """``dav_url`` without its trailing slash (empty when unconfigured).

    Raises ``_egress.ServiceUrlError`` (a ValueError, so :func:`safe_result` answers it) for an
    address the DAV password may not be sent to: plain http to a public host, a login in the
    URL, a metadata address (T-1374).
    """
    raw = (from_env().dav_url or "").strip()
    return _egress.service_url(raw, "DAV_URL") if raw else ""


def join(*parts: str) -> str:
    """Join path segments into ``a/b/c``, dropping empties and stray slashes.

    Segments come from settings a person typed (``/calendars/``) and from tool
    arguments, so joining with ``"/".join`` would produce ``//`` runs that some
    DAV servers 404 on and others silently normalise — a difference that only
    shows up against the operator's actual server.
    """
    out = [str(p).strip("/") for p in parts if str(p or "").strip("/")]
    return "/".join(out)


def url_for(target: str) -> str:
    """Absolute URL for a path under ``dav_url`` *or* for a multistatus href.

    A multistatus ``href`` is rooted at the **host** (``/remote.php/dav/...``),
    not at ``dav_url``'s path, so appending it to the base would ask for
    ``/remote.php/dav/remote.php/dav/...``. A leading slash therefore replaces
    the base path; a relative target is appended to it.
    """
    target = (target or "").strip()
    base = base_url()
    if target.startswith("http://") or target.startswith("https://"):
        # An href is the server's text: an absolute one naming another host would carry the
        # DAV password there. Same origin only.
        if urlsplit(target)[:2] != urlsplit(base)[:2]:
            raise ValueError("a DAV link points outside DAV_URL: refused")
        return target
    if target.startswith("/"):
        parts = urlsplit(base)
        return urlunsplit((parts.scheme, parts.netloc, target, "", ""))
    return f"{base}/{target}" if target else base


def redact(text: str) -> str:
    """Strip credentials out of a string on its way to a caller or a log.

    Two ways a password reaches a message: a ``dav_url`` written with userinfo
    (``https://me:pw@cloud.lan``), which httpx echoes in ``exc.request.url``,
    and a server that quotes the request back in its error body. Both are cut
    here, because this is the single funnel every DAV error string goes through.
    """
    out = _USERINFO_RE.sub("", text or "")
    try:
        password = from_env().dav_password
    except Exception:  # an unreadable environment must not break an error path
        password = ""
    if password and password in out:
        out = out.replace(password, "***")
    return out


def not_configured() -> dict[str, Any]:
    """The result every tool returns when ``DAV_URL`` is unset.

    A fresh dict each call: it is handed to callers that may add keys to it.
    """
    return {"ok": False, "error": "DAV is not configured — set DAV_URL "
                                  "(and DAV_USERNAME / DAV_PASSWORD if the server needs them)."}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """An ``httpx.Client`` bound to ``dav_url`` with the DAV credentials.

    Basic auth is only attached when a username is set — passing
    ``("", "")`` would send an ``Authorization`` header that some servers
    answer with 401 rather than treating the request as anonymous.
    """
    cfg = from_env()
    auth = (cfg.dav_username, cfg.dav_password) if cfg.dav_username else None
    return httpx.Client(base_url=base_url(), auth=auth, timeout=timeout,
                        verify=cfg.dav_verify_ssl)


def request(method: str, target: str, *, headers: dict[str, str] | None = None,
            content: bytes | str | None = None,
            timeout: float = DEFAULT_TIMEOUT) -> httpx.Response:
    """One DAV request. ``target`` is a path under ``dav_url`` or an href.

    The response is returned unchecked — the caller decides whether a 404 is an
    error (reading a file) or an answer (probing for a collection).
    """
    with client(timeout) as conn:
        return conn.request(method, url_for(target), headers=headers, content=content)


def propfind(path: str, depth: int = 1, body: str | None = None) -> list[dict[str, Any]]:
    """``PROPFIND`` *path* and return the parsed multistatus rows.

    ``depth`` 0 is the resource itself, 1 adds its direct children (the DAV
    servers we target refuse ``infinity``). Raises ``httpx.HTTPStatusError`` on
    a 4xx/5xx so the caller's :func:`safe_json` turns it into ``ok: false``.
    """
    headers = {"Depth": str(depth), "Content-Type": XML_CONTENT_TYPE}
    response = request("PROPFIND", path, headers=headers,
                       content=(body or ALLPROP).encode("utf-8"))
    response.raise_for_status()
    return parse_multistatus(response.text)


def report(path: str, body: str, depth: int = 1) -> list[dict[str, Any]]:
    """``REPORT`` *path* with a calendar-query / addressbook-query *body*.

    Same multistatus shape as :func:`propfind`; the requested data lands in the
    row's props under ``calendar-data`` / ``address-data``.
    """
    headers = {"Depth": str(depth), "Content-Type": XML_CONTENT_TYPE}
    response = request("REPORT", path, headers=headers, content=body.encode("utf-8"))
    response.raise_for_status()
    return parse_multistatus(response.text)


# --------------------------------------------------------------------------- #
# Multistatus XML
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    return _NS_RE.sub("", tag or "")


def _status_ok(status: str) -> bool:
    """Whether a ``propstat`` status line is a 2xx. A missing line counts as ok.

    RFC 4918 requires the line, but Radicale has shipped responses without one;
    treating those as failures would drop every property of a working server.
    """
    if not status:
        return True
    found = _STATUS_RE.search(status)
    return found is not None and found.group(1).startswith("2")


def _prop_value(element: ET.Element) -> Any:
    """A property's value: its text, its attributes, or a dict of its children.

    ``resourcetype`` and ``supported-report-set`` carry their meaning in child
    *element names*, not in text, so a text-only parser cannot tell a collection
    from a file; ``<C:comp name="VEVENT"/>`` carries it in an attribute, so an
    empty element with attributes returns them. Repeated child names become a
    list rather than overwriting each other, which is what
    ``supported-calendar-component-set`` looks like on a calendar holding both
    VEVENT and VTODO. Not covered: attributes on an element that *also* has text
    or children are dropped — no property these plugins read has that shape.
    """
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if not text and element.attrib:
            return {_local(key): value for key, value in element.attrib.items()}
        return text
    out: dict[str, Any] = {}
    for child in children:
        name = _local(child.tag)
        value = _prop_value(child)
        if name in out:
            previous = out[name]
            out[name] = [*previous, value] if isinstance(previous, list) else [previous, value]
        else:
            out[name] = value
    return out


def parse_multistatus(xml_text: str | bytes) -> list[dict[str, Any]]:
    """A ``207 Multi-Status`` body as ``[{"href": ..., "props": {...}}, ...]``.

    Namespaces are stripped to local names (``{DAV:}displayname`` ->
    ``displayname``) so callers never carry namespace strings; the cost is that
    two namespaces using the same local name collide, which none of the
    properties the plugins read do. Properties from a non-2xx ``propstat`` are
    dropped: a 404 block lists the names the server does *not* have, and keeping
    them would hand callers empty strings that look like real values.

    Raises ``xml.etree.ElementTree.ParseError`` when the body is not XML — which
    is what an HTML login page from a misconfigured reverse proxy looks like.
    """
    root = ET.fromstring(xml_text)
    rows: list[dict[str, Any]] = []
    for response in root.iter():
        if _local(response.tag) != "response":
            continue
        href = ""
        props: dict[str, Any] = {}
        for child in response:
            name = _local(child.tag)
            if name == "href":
                href = (child.text or "").strip()
            elif name == "propstat":
                status = ""
                block: dict[str, Any] = {}
                for part in child:
                    part_name = _local(part.tag)
                    if part_name == "status":
                        status = (part.text or "").strip()
                    elif part_name == "prop":
                        block = {_local(p.tag): _prop_value(p) for p in part}
                if _status_ok(status):
                    props.update(block)
        rows.append({"href": href, "props": props})
    return rows


def is_collection(props: dict[str, Any]) -> bool:
    """Whether a multistatus row is a collection (a directory / a calendar)."""
    resourcetype = props.get("resourcetype")
    return isinstance(resourcetype, dict) and "collection" in resourcetype


def has_resourcetype(props: dict[str, Any], name: str) -> bool:
    """Whether ``resourcetype`` carries *name* (``calendar``, ``addressbook``).

    Used to keep a depth-1 listing's own root, and Nextcloud's scheduling
    inbox/outbox, out of a list of calendars.
    """
    resourcetype = props.get("resourcetype")
    return isinstance(resourcetype, dict) and name in resourcetype


# --------------------------------------------------------------------------- #
# iCalendar / vCard
# --------------------------------------------------------------------------- #
def unfold(text: str) -> list[str]:
    """Split an iCalendar/vCard body into *logical* lines (RFC 5545 §3.1).

    Both formats wrap at 75 octets and mark the continuation with a leading
    space or tab. Splitting on newlines alone therefore cuts every long SUMMARY,
    FN or DESCRIPTION in half and leaves the tail looking like an unknown
    property — the single most common bug in a hand-written parser.

    Blank lines are dropped. Does not cover the octet-counting side of the rule:
    a continuation that splits a multi-byte character is rejoined correctly here
    only because the body was already decoded as UTF-8 by httpx.
    """
    lines: list[str] = []
    for raw in re.split(r"\r\n|\r|\n", text or ""):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
            continue
        lines.append(raw)
    return [line for line in lines if line.strip()]


def split_line(line: str) -> tuple[str, dict[str, str], str]:
    """``SUMMARY;LANGUAGE=en:Dentist`` -> ``("SUMMARY", {"LANGUAGE": "en"}, "Dentist")``.

    The name/value split is on the first colon *outside* double quotes, because
    a parameter value may quote one (``DTSTART;TZID="Europe/Paris":...``), and
    the Apple group prefix (``item1.EMAIL``) is dropped — matching on the raw
    name would miss every grouped EMAIL and TEL in an iCloud-exported vCard.
    """
    in_quotes = False
    cut = -1
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == ":" and not in_quotes:
            cut = index
            break
    if cut < 0:
        return "", {}, ""
    head, value = line[:cut], line[cut + 1:]
    pieces = head.split(";")
    name = pieces[0].strip().upper()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        if "=" in piece:
            key, val = piece.split("=", 1)
            params[key.strip().upper()] = val.strip().strip('"')
    return name, params, value


def unescape(value: str) -> str:
    r"""Undo TEXT escaping: ``\n`` -> newline, ``\,`` ``\;`` ``\\`` -> the char."""
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            following = value[index + 1]
            out.append("\n" if following in ("n", "N") else following)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _components(text: str, kind: str) -> list[list[tuple[str, dict[str, str], str]]]:
    """The parsed lines of each top-level ``BEGIN:<kind>`` ... ``END:<kind>`` block.

    Nested components are skipped, not merged: a VEVENT's VALARM carries its own
    SUMMARY, and folding it into the event renames the appointment after its
    reminder.
    """
    blocks: list[list[tuple[str, dict[str, str], str]]] = []
    current: list[tuple[str, dict[str, str], str]] | None = None
    nested = 0
    for line in unfold(text):
        name, params, value = split_line(line)
        token = value.strip().upper()
        if name == "BEGIN":
            if current is not None:
                nested += 1
            elif token == kind:
                current = []
            continue
        if name == "END":
            if nested:
                nested -= 1
            elif current is not None and token == kind:
                blocks.append(current)
                current = None
            continue
        if current is not None and not nested:
            current.append((name, params, value))
    return blocks


def parse_vcards(text: str) -> list[dict[str, Any]]:
    """The VCARDs in a vCard body, as fn/emails/tels/uid rows.

    EMAIL and TEL repeat, so both are lists in card order — a parser keeping one
    of each loses the work address that the person searched for. When FN is
    missing (vCard 2.1 does not require it) the name is rebuilt from N's
    ``family;given`` so the contact is still findable.
    """
    cards: list[dict[str, Any]] = []
    for block in _components(text, "VCARD"):
        card: dict[str, Any] = {"fn": "", "emails": [], "tels": [], "uid": ""}
        structured = ""
        for name, _params, value in block:
            if name == "FN":
                card["fn"] = unescape(value).strip()
            elif name == "N":
                structured = value
            elif name == "EMAIL":
                if value.strip():
                    card["emails"].append(unescape(value).strip())
            elif name == "TEL":
                if value.strip():
                    card["tels"].append(unescape(value).strip())
            elif name == "UID":
                card["uid"] = value.strip()
        if not card["fn"] and structured:
            pieces = [unescape(p).strip() for p in structured.split(";")]
            given = pieces[1] if len(pieces) > 1 else ""
            card["fn"] = " ".join(p for p in (given, pieces[0]) if p).strip()
        cards.append(card)
    return cards


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #
def safe_result(fn: Callable[[dict], Any], params: dict | None = None) -> Any:
    """Run a DAV call, turning a server failure into an ``ok: false`` result.

    httpx puts the request URL in the message of every error it raises, and that
    URL carries the userinfo when ``DAV_URL`` was written as
    ``https://user:pw@host`` — so an unredacted exception message is a password
    in a tool result. Every DAV failure is funnelled through here, and here
    through :func:`redact`, rather than each plugin remembering to.

    Caught: httpx's errors, an unparseable XML body, and the ValueError/TypeError
    a bad tool argument produces. Not caught, deliberately: ``KeyError``,
    ``AttributeError``, ``IndexError`` — those are bugs in the handler, not
    conditions of the server, and they should fail loudly in the plugin's own
    tests rather than be reported to a user as "the DAV call failed".
    """
    try:
        return fn(params or {})
    except (httpx.HTTPError, ET.ParseError, ValueError, TypeError) as exc:
        _LOGGER.warning("DAV call failed: %s", exc.__class__.__name__)
        # The shared status-and-hint answer (T-1348): no body, no request address.
        return _failure.of(exc) or {"ok": False, "error": redact(f"{exc.__class__.__name__}: {exc}")}


def safe_json(fn: Callable[[dict], Any]) -> Callable[..., str]:
    """Wrap a tool body so it returns a JSON string and never raises.

    Hermes calls a handler inline, so an exception out of one ends the turn;
    :func:`safe_result` does the catching and the redaction, and this only puts
    a JSON string around it — the shape Hermes requires of a tool handler.
    """

    def handler(params: dict, **kwargs: Any) -> str:
        del kwargs
        return json.dumps(safe_result(fn, params or {}))

    return handler
