"""IMAP/SMTP mailbox access for JARVIS, standard library only.

``imaplib``, ``smtplib`` and ``email`` cover everything this plugin needs, so it
adds no dependency to the Hermes image. Four things about the shape here are
deliberate and each prevents a specific failure:

* **A connection lives inside one call.** :func:`imap_connection` opens, logs in
  and logs out per tool call. A module-level IMAP handle would outlive the turn
  and the server's idle timeout, and the *next* tool call would die inside a
  FETCH with ``socket error: EOF`` — an error with no relation to what the user
  just asked for.
* **Nothing is fetched unbounded.** ``mail_search`` asks the server for at most
  :data:`SNIPPET_FETCH_BYTES` of each hit's body and returns at most
  :data:`SNIPPET_CHARS` of it; ``mail_read`` caps the returned text at
  :data:`BODY_CHARS`. A twenty-hit search that returned whole bodies would put
  the mailbox in the model's context.
* **Headers are decoded.** A raw ``=?UTF-8?B?…?=`` subject reaching the model is
  a bug, not a cosmetic one: it cannot be matched, quoted or read back.
* **The password never leaves.** Every handler's return value and every error
  string goes through :func:`_scrub`.

Reading uses EXAMINE (``readonly=True``) throughout, so looking at mail does not
mark it ``\\Seen``. Every ``MAIL_*`` value is read through the one settings
schema (``aura_schema.from_env()``).
"""
from __future__ import annotations

import base64
import email
import email.errors
import email.header
import email.message
import email.utils
import imaplib
import json
import logging
import re
import smtplib
import time
from contextlib import contextmanager
from email.message import EmailMessage
from html import unescape
from typing import Any, Iterator

from aura_schema import from_env
from _shared._signed import signed

_LOGGER = logging.getLogger(__name__)

#: Characters of body text a search row carries. The number is in the tool
#: description too, so the model knows the snippet is not the message.
SNIPPET_CHARS = 300
#: Bytes of each hit's body ``mail_search`` asks the server for. Big enough that
#: the ``text/plain`` part of a typical multipart message falls inside it, small
#: enough that twenty hits cannot pull megabytes off the server.
SNIPPET_FETCH_BYTES = 4096
#: Characters of ``text/plain`` ``mail_read`` returns before truncating. 20 KB is
#: a long email and a small fraction of a context window.
BODY_CHARS = 20 * 1024
#: Tools whose call changes the world, and so pass through the approval gate.
GATED_TOOLS = {"mail_send"}

#: ``* LIST (\HasNoChildren) "/" "INBOX"`` — flags, delimiter, name.
_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+(?:"(?P<delim>[^"]*)"|NIL)\s+(?P<name>.+)', re.S)
_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)", re.I)
#: A UID is a number. Anything else is refused before it reaches a command line.
_UID_RE = re.compile(r"\A\d+\Z")
#: Month abbreviations for the IMAP date format, hard-coded rather than taken
#: from ``strftime('%b')``: that follows the process locale, and a container with
#: LANG=fr would send ``01-sept-2026``, which the server rejects.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#: What a mailbox call can fail with. ``imaplib.IMAP4.abort`` and
#: ``.readonly`` subclass ``IMAP4.error``; socket, TLS and DNS failures are all
#: ``OSError``; ``binascii.Error`` and ``UnicodeError`` are ``ValueError``.
_ERRORS = (imaplib.IMAP4.error, smtplib.SMTPException, OSError, LookupError,
           ValueError, TypeError, email.errors.MessageError)


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def _secret_values() -> tuple[str, ...]:
    try:
        return tuple(v for v in (from_env().mail_password,) if v)
    except Exception:  # a broken environment must not stop the scrub from running
        return ()


def _replace(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {k: _replace(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace(v, secrets) for v in value]
    return value


def _scrub(value: Any) -> Any:
    """Blank the mailbox password anywhere in a payload before it leaves.

    imaplib and smtplib quote the failing command in some of their exceptions,
    so an error string is as much of a leak path as a result is; this is the one
    choke point every handler and the slash command pass through.

    Does **not** cover: a password that is also an ordinary word would blank that
    word in a subject or snippet too (a false positive we take over a leak); nor
    anything imaplib's own ``debug`` mode writes straight to stderr, which never
    passes through here.
    """
    secrets = _secret_values()
    return _replace(value, secrets) if secrets else value


def _scrub_text(text: str) -> str:
    scrubbed = _scrub(text)
    return scrubbed if isinstance(scrubbed, str) else str(scrubbed)


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #
@contextmanager
def imap_connection() -> Iterator[imaplib.IMAP4]:
    """An IMAP connection that is opened, logged in, and closed inside one call.

    Does **not** cover: a server that needs OAuth or a client certificate — this
    is ``LOGIN`` with the mailbox password only.
    """
    cfg = from_env()
    if not cfg.mail_imap_host:
        raise ValueError("MAIL_IMAP_HOST is not set")
    if not cfg.mail_username:
        raise ValueError("MAIL_USERNAME is not set")
    if cfg.mail_imap_ssl:
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(cfg.mail_imap_host, cfg.mail_imap_port)
    else:
        conn = imaplib.IMAP4(cfg.mail_imap_host, cfg.mail_imap_port)
        # Port 143 without this sends LOGIN — password included — in the clear.
        # A server that does not advertise STARTTLS is the owner's explicit
        # choice of a plaintext port on a trusted LAN, so it is not an error.
        capabilities = {str(c).upper() for c in (getattr(conn, "capabilities", ()) or ())}
        if "STARTTLS" in capabilities:
            conn.starttls()
    try:
        conn.login(cfg.mail_username, cfg.mail_password)
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:  # a logout failure must not replace the error that caused it
            _LOGGER.debug("IMAP logout failed", exc_info=True)


@contextmanager
def smtp_connection() -> Iterator[smtplib.SMTP]:
    """An SMTP connection opened and quit inside one call, TLS chosen by port.

    Does **not** cover: submission servers that require OAuth, or a relay that
    wants a client certificate.
    """
    cfg = from_env()
    if not cfg.mail_smtp_host:
        raise ValueError("MAIL_SMTP_HOST is not set")
    # 465 is implicit TLS: the handshake happens before the greeting, and issuing
    # STARTTLS on it fails with a confusing "unexpected reply". Every other port
    # gets STARTTLS when the setting asks for it.
    implicit = cfg.mail_smtp_port == 465
    if implicit:
        server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.mail_smtp_host, cfg.mail_smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(cfg.mail_smtp_host, cfg.mail_smtp_port, timeout=30)
    try:
        if not implicit and cfg.mail_smtp_starttls:
            server.ehlo()
            server.starttls()
            server.ehlo()  # the EHLO response after STARTTLS is the authoritative one
        if cfg.mail_username and cfg.mail_password:
            server.login(cfg.mail_username, cfg.mail_password)
        yield server
    finally:
        try:
            server.quit()
        except Exception:  # ditto: quitting must not mask a send failure
            _LOGGER.debug("SMTP quit failed", exc_info=True)


# --------------------------------------------------------------------------- #
# IMAP response handling
# --------------------------------------------------------------------------- #
def _first_text(data: Any) -> str:
    for item in data or []:
        if isinstance(item, (bytes, bytearray)):
            return bytes(item).decode("utf-8", "replace")
        if isinstance(item, tuple) and item and isinstance(item[0], (bytes, bytearray)):
            return bytes(item[0]).decode("utf-8", "replace")
    return ""


def _ok(status: str, data: Any, what: str) -> list:
    """Raise unless the server said OK.

    imaplib raises only for a protocol-level failure; a plain ``NO`` (mailbox
    missing, SEARCH rejected) comes back as ordinary data and would otherwise be
    parsed as an empty result — a missing folder silently reading as "no mail".
    """
    if status != "OK":
        raise imaplib.IMAP4.error(f"{what}: server said {status} {_first_text(data)}".strip())
    return list(data or [])


def _fetch_parts(data: Any) -> tuple[bytes, bytes]:
    """``(metadata, literal)`` from one FETCH response.

    imaplib hands back a list whose literal-carrying entries are
    ``(b'1 (UID 3 FLAGS (\\Seen) BODY[…] {123}', b'…payload…')`` tuples and whose
    plain entries are the response's trailing bytes; both halves matter (flags
    live in the metadata, the message in the literal).
    """
    meta, payload = b"", b""
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2:
            if isinstance(item[0], (bytes, bytearray)):
                meta += bytes(item[0])
            if isinstance(item[1], (bytes, bytearray)):
                payload += bytes(item[1])
        elif isinstance(item, (bytes, bytearray)):
            meta += bytes(item)
    return meta, payload


def _flags(meta: bytes) -> list[str]:
    m = _FLAGS_RE.search(meta or b"")
    if not m:
        return []
    return m.group(1).decode("ascii", "replace").split()


# --------------------------------------------------------------------------- #
# Text decoding
# --------------------------------------------------------------------------- #
def decode_header_value(raw: str | bytes | None) -> str:
    """An RFC 2047 header (``=?UTF-8?B?…?=``) as text.

    Does **not** cover: a header the sender encoded with a charset Python has no
    codec for, which falls back to UTF-8 with replacement characters rather than
    failing the whole call.
    """
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    out: list[str] = []
    for text, charset in email.header.decode_header(raw):
        if isinstance(text, (bytes, bytearray)):
            try:
                out.append(bytes(text).decode(charset or "utf-8", "replace"))
            except LookupError:  # a charset the sender named but Python cannot load
                out.append(bytes(text).decode("utf-8", "replace"))
        else:
            out.append(text)
    return "".join(out).strip()


def decode_mailbox(name: str) -> str:
    """IMAP modified UTF-7 (RFC 3501 §5.1.3) as text: ``&APY-`` -> ``ö``.

    Servers spell mailbox names in this encoding by default, so without it a
    folder reaches the model as ``Gel&APY-schte`` — unreadable, and unusable as
    an argument the model then passes back.

    Does **not** cover: re-encoding. A decoded name is matched back to the
    server's own spelling in :func:`_resolve_folder` instead. A malformed run is
    left exactly as the server wrote it rather than raising.
    """
    out: list[str] = []
    i = 0
    while i < len(name):
        if name[i] != "&":
            out.append(name[i])
            i += 1
            continue
        end = name.find("-", i + 1)
        if end < 0:
            out.append(name[i:])
            break
        chunk = name[i + 1:end]
        if chunk == "":
            out.append("&")  # "&-" is a literal ampersand
        else:
            b64 = chunk.replace(",", "/")
            try:
                out.append(base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be"))
            except ValueError:  # binascii.Error and UnicodeDecodeError are both ValueError
                out.append(name[i:end + 1])
        i = end + 1
    return "".join(out)


_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>")
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(html: str) -> str:
    """Tags out of an HTML body so a text-only reader gets something readable.

    Does **not** cover: layout. Tables and nested lists lose their structure,
    link targets are dropped, and inline images vanish — which is why the caller
    reports which part it used, so nobody mistakes this for the real message.
    """
    text = _SCRIPT_RE.sub(" ", html)
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    # &nbsp; unescapes to U+00A0, which looks like a space and is not one: a
    # model that searched the body for "Order 42" would miss "Order\u00a042".
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _part_text(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    # `decode=True` returns bytes for a leaf part and None when the part is
    # multipart or undecodable — but the stub also allows the Message list from
    # `decode=False`, so the narrowing is real and not a formality: a multipart
    # part reaching here would otherwise raise AttributeError mid-read.
    if not isinstance(payload, (bytes, bytearray)):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return bytes(payload).decode(charset, "replace")
    except LookupError:
        return bytes(payload).decode("utf-8", "replace")


def message_text(msg: email.message.Message) -> tuple[str, str]:
    """``(text, which_part)`` — ``text/plain`` when the message has one.

    ``text/html`` stripped of tags is the last resort, and the second element
    says so, because a stripped body is an approximation the reader should not
    quote back verbatim.

    Does **not** cover: attachments. A PDF or an image is listed by
    :func:`read`, never read; a message whose only content is an attachment
    comes back with ``("", "none")``.
    """
    plain: list[str] = []
    html: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in str(part.get("Content-Disposition") or "").lower():
            continue
        if part.get_content_type() == "text/plain":
            plain.append(_part_text(part))
        elif part.get_content_type() == "text/html":
            html.append(_part_text(part))
    if any(p.strip() for p in plain):
        return "\n".join(plain).strip(), "text/plain"
    if any(p.strip() for p in html):
        return strip_html("\n".join(html)), "text/html (tags stripped)"
    return "", "none"


# --------------------------------------------------------------------------- #
# Folders
# --------------------------------------------------------------------------- #
def _quote_mailbox(name: str) -> str:
    """A mailbox as an IMAP quoted string.

    The name is interpolated into the command line, so a quote, a backslash or a
    newline from a model-supplied argument must be escaped or refused rather
    than left to start a second command atom.
    """
    if "\r" in name or "\n" in name:
        raise ValueError("a mailbox name may not contain a newline")
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _folder_rows(conn: imaplib.IMAP4) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _ok(*conn.list(), "LIST"):
        raw = item
        if isinstance(raw, tuple):
            raw = b" ".join(bytes(x) for x in raw if isinstance(x, (bytes, bytearray)))
        if not isinstance(raw, (bytes, bytearray)):
            continue
        m = _LIST_RE.match(bytes(raw))
        if not m:
            continue
        name = m.group("name").decode("utf-8", "replace").strip()
        if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        rows.append({
            "name": decode_mailbox(name),
            "raw": name,
            "flags": m.group("flags").decode("ascii", "replace").split(),
            "delimiter": (m.group("delim") or b"").decode("ascii", "replace"),
        })
    return rows


def _resolve_folder(conn: imaplib.IMAP4, folder: str) -> str:
    """The name to hand SELECT, given what the caller typed.

    An ASCII name goes straight through — the common case costs no round trip. A
    name with non-ASCII characters cannot be sent as typed (the server spells
    mailboxes in modified UTF-7), so it is matched against LIST and the server's
    own spelling is used.

    Does **not** cover: two mailboxes whose decoded names differ only in case —
    the first LIST match wins.
    """
    folder = (folder or "INBOX").strip() or "INBOX"
    if folder.isascii():
        return folder
    for row in _folder_rows(conn):
        if row["name"] == folder or row["name"].casefold() == folder.casefold():
            return row["raw"]
    raise ValueError(f"no mailbox named {folder!r}")


def _select(conn: imaplib.IMAP4, raw_folder: str) -> None:
    # readonly=True issues EXAMINE. Under a plain SELECT the body fetches below
    # would set \Seen, so the model merely looking at the inbox would mark the
    # owner's unread mail read.
    _ok(*conn.select(_quote_mailbox(raw_folder), readonly=True), f"SELECT {raw_folder}")


def folders() -> dict[str, Any]:
    with imap_connection() as conn:
        rows = _folder_rows(conn)
    return {"ok": True, "count": len(rows), "folders": rows}


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def imap_date(value: str) -> str:
    """``2026-09-01`` (or an IMAP date already) as ``01-Sep-2026``."""
    text = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not 1 <= month <= 12:
            raise ValueError(f"since has month {month}: {value!r}")
        return f"{day:02d}-{_MONTHS[month - 1]}-{year}"
    if re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{4}", text):
        return text
    raise ValueError(f"since must be YYYY-MM-DD or DD-Mon-YYYY, got {value!r}")


def _quote_search(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("a search term may not contain a newline")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search_criteria(unread: bool = False, from_: str | None = None,
                    subject: str | None = None, since: str | None = None,
                    text: str | None = None) -> list[str]:
    """IMAP SEARCH tokens from the friendly arguments.

    Does **not** cover: SINCE is day-granular in IMAP, so "since today" matches
    the whole of today, not the last few hours. TEXT is the server's own substring
    match over headers and body: no stemming, and some servers skip attachments.
    """
    crit: list[str] = []
    if unread:
        crit.append("UNSEEN")
    if from_:
        crit += ["FROM", _quote_search(from_)]
    if subject:
        crit += ["SUBJECT", _quote_search(subject)]
    if text:
        crit += ["TEXT", _quote_search(text)]
    if since:
        crit += ["SINCE", imap_date(since)]
    return crit or ["ALL"]


def _envelope(conn: imaplib.IMAP4, uid: bytes) -> dict[str, Any]:
    uid_text = uid.decode("ascii", "replace")
    meta, header = _fetch_parts(_ok(*conn.uid(
        "FETCH", uid_text, "(FLAGS BODY.PEEK[HEADER])"), f"FETCH header {uid_text}"))
    msg = email.message_from_bytes(header)
    row: dict[str, Any] = {
        "uid": uid_text,
        "date": _date_value(msg.get("Date")),
        "from": decode_header_value(msg.get("From")),
        "subject": decode_header_value(msg.get("Subject")),
        "seen": any(f.lower() == r"\seen" for f in _flags(meta)),
    }
    row["snippet"], row["snippet_part"] = _snippet(conn, uid_text, header)
    return row


def _snippet(conn: imaplib.IMAP4, uid_text: str, header: bytes) -> tuple[str, str]:
    """The first :data:`SNIPPET_CHARS` characters of the body, or ``("", …)``.

    Only ``SNIPPET_FETCH_BYTES`` of the body cross the wire; those bytes are
    re-joined to the full header so ``email`` can decode them as MIME — a raw
    partial fetch of a base64 part would otherwise be returned as gibberish. A
    snippet failure is not a search failure: the row still carries its envelope.
    """
    try:
        _, chunk = _fetch_parts(conn.uid(
            "FETCH", uid_text, f"(BODY.PEEK[TEXT]<0.{SNIPPET_FETCH_BYTES}>)")[1])
    except _ERRORS:  # a server without partial fetch still gives usable envelopes
        _LOGGER.debug("snippet fetch failed for uid %s", uid_text, exc_info=True)
        return "", "none"
    if not chunk:
        return "", "none"
    text, part = message_text(email.message_from_bytes(header.rstrip() + b"\r\n\r\n" + chunk))
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed[:SNIPPET_CHARS], part


def _date_value(raw: str | None) -> str:
    """The Date header as ISO 8601 when it parses, otherwise verbatim.

    An ISO date is what the model can compare; a sender with a broken Date
    header must not cost the whole row, so the raw string is the fallback.
    """
    decoded = decode_header_value(raw)
    if not decoded:
        return ""
    try:
        return email.utils.parsedate_to_datetime(decoded).isoformat()
    except (TypeError, ValueError):
        return decoded


def search(folder: str = "INBOX", *, unread: bool = False, from_: str | None = None,
           subject: str | None = None, since: str | None = None, text: str | None = None,
           limit: int = 20) -> dict[str, Any]:
    """Envelope rows for a mailbox search, newest first.

    Does **not** cover: message bodies. Each row carries at most
    :data:`SNIPPET_CHARS` characters; ``mail_read`` is how a body is read.
    ``matched`` counts every hit, ``messages`` holds only the newest ``limit``.
    """
    crit = search_criteria(unread, from_, subject, since, text)
    # imaplib encodes command arguments as ASCII, so a non-ASCII term would die
    # as a UnicodeEncodeError deep inside _command; say what is wrong instead.
    unsendable = [c for c in crit if not c.isascii()]
    if unsendable:
        return {"ok": False, "error": "IMAP SEARCH is ASCII-only here; these terms are not: "
                                      + ", ".join(unsendable)}
    with imap_connection() as conn:
        raw_folder = _resolve_folder(conn, folder)
        _select(conn, raw_folder)
        # No charset slot: `UID SEARCH` takes the criteria directly. The `None` that
        # usually sits here is a habit carried over from IMAP4.search(charset, …) —
        # imaplib drops it before the wire, so leaving it out changes nothing except
        # that the call now matches the type imaplib declares.
        data = _ok(*conn.uid("SEARCH", *crit), "SEARCH")
        blob = data[0] if data and isinstance(data[0], (bytes, bytearray)) else b""
        uids = bytes(blob).split()
        # IMAP returns UIDs ascending, so the newest are the tail; reverse for output.
        newest = list(reversed(uids[-max(1, limit):])) if uids else []
        rows = [_envelope(conn, uid) for uid in newest]
    return {"ok": True, "folder": folder, "criteria": crit, "matched": len(uids),
            "returned": len(rows), "snippet_chars": SNIPPET_CHARS, "messages": rows}


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
def _attachments(msg: email.message.Message) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        disposition = str(part.get("Content-Disposition") or "").lower()
        if not filename and "attachment" not in disposition:
            continue
        payload = part.get_payload(decode=True) or b""
        out.append({"filename": decode_header_value(filename) or "(unnamed)",
                    "content_type": part.get_content_type(), "bytes": len(payload)})
    return out


def read(uid: str, folder: str = "INBOX") -> dict[str, Any]:
    """One message: its headers and its text body, capped at :data:`BODY_CHARS`.

    The cap is not an argument on purpose — a model that could raise it would,
    and a 40 MB newsletter would land in the context.

    Does **not** cover: attachments (listed by name, type and size, never
    decoded) and inline images. Reading uses EXAMINE, so the message is *not*
    marked read as a side effect.
    """
    uid = str(uid or "").strip()
    # The uid is interpolated into "UID FETCH <uid> …"; digits only, so a value
    # like "1 (BODY[]) UID STORE 1 +FLAGS (\\Deleted" cannot smuggle a command.
    if not _UID_RE.match(uid):
        raise ValueError(f"uid must be a number, got {uid!r}")
    with imap_connection() as conn:
        raw_folder = _resolve_folder(conn, folder)
        _select(conn, raw_folder)
        meta, payload = _fetch_parts(_ok(*conn.uid(
            "FETCH", uid, "(FLAGS BODY.PEEK[])"), f"FETCH {uid}"))
    if not payload:
        return {"ok": False, "error": f"no message with uid {uid} in {folder}"}
    msg = email.message_from_bytes(payload)
    text, part = message_text(msg)
    truncated = len(text) > BODY_CHARS
    if truncated:
        text = (text[:BODY_CHARS]
                + f"\n\n[truncated: body cut at {BODY_CHARS} characters "
                  f"({BODY_CHARS // 1024} KB), the mail_read cap]")
    return {
        "ok": True, "uid": uid, "folder": folder,
        "date": _date_value(msg.get("Date")),
        "from": decode_header_value(msg.get("From")),
        "to": decode_header_value(msg.get("To")),
        "cc": decode_header_value(msg.get("Cc")),
        "subject": decode_header_value(msg.get("Subject")),
        "message_id": (msg.get("Message-ID") or "").strip(),
        "seen": any(f.lower() == r"\seen" for f in _flags(meta)),
        "body_part": part, "body": text,
        "truncated": truncated, "body_cap_chars": BODY_CHARS,
        "attachments": _attachments(msg),
    }


# --------------------------------------------------------------------------- #
# Send
# --------------------------------------------------------------------------- #
def addresses(value: Any) -> list[str]:
    """A recipient argument as a list, from a list or a comma-separated string.

    Does **not** cover: display names with commas (``"Doe, Jane" <j@x>``) given
    as one string — pass those as a list.
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def _check_addresses(items: list[str], label: str) -> None:
    for item in items:
        # A newline in an address or a header value ends the header and starts
        # the next one: "a@x\nBcc: everyone@y" would silently add a recipient.
        if "\r" in item or "\n" in item:
            raise ValueError(f"{label} address contains a newline: {item!r}")
        if "@" not in item:
            raise ValueError(f"{label} address has no @: {item!r}")


def _compose(to: Any, subject: str, body: str, cc: Any = None) -> tuple[EmailMessage, list[str], list[str]]:
    """The one plain-text message `send` sends and `draft` saves, with its checked recipients."""
    cfg = from_env()
    recipients = addresses(to)
    copies = addresses(cc)
    if not recipients:
        raise ValueError("to is required")
    _check_addresses(recipients, "to")
    _check_addresses(copies, "cc")
    sender = cfg.mail_from or cfg.mail_username
    if not sender:
        raise ValueError("neither MAIL_FROM nor MAIL_USERNAME is set")
    subject = str(subject or "").replace("\r", " ").replace("\n", " ").strip()

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    if copies:
        msg["Cc"] = ", ".join(copies)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()
    msg.set_content(str(body or ""))
    return msg, recipients, copies


def _special_folder(conn: imaplib.IMAP4, use: str) -> dict[str, Any]:
    """The owner's ``use`` mailbox ("Drafts", "Sent"): the RFC 6154 special-use flag, else the name."""
    rows = _folder_rows(conn)
    for row in rows:
        if any(f.lower() == "\\" + use.lower() for f in row["flags"]):
            return row
    for row in rows:
        if row["name"].casefold() == use.casefold():
            return row
    raise ValueError(f"this mailbox has no {use} folder")


def _drafts_folder(conn: imaplib.IMAP4) -> dict[str, Any]:
    return _special_folder(conn, "Drafts")


def _file_sent(msg: EmailMessage) -> dict[str, Any]:
    """Put a message SMTP accepted in the owner's Sent folder, so their mail app shows it
    (T-0576). The send stands whatever happens here; a failure is said, never swallowed."""
    if not from_env().mail_imap_host:
        return {"sent_folder": None, "sent_folder_error": "no IMAP mailbox is set up to file it in"}
    try:
        with imap_connection() as conn:
            target = _special_folder(conn, "Sent")
            _ok(*conn.append(_quote_mailbox(target["raw"]), r"(\Seen)",
                             imaplib.Time2Internaldate(time.time()), msg.as_bytes()), "APPEND sent")
        return {"sent_folder": target["name"]}
    except _ERRORS as exc:
        return {"sent_folder": None, "sent_folder_error": _scrub_text(str(exc))[:200]}


def draft(to: Any, subject: str, body: str, cc: Any = None, reply_to: str | None = None,
          folder: str = "INBOX") -> dict[str, Any]:
    """Save one plain-text message to the owner's Drafts folder, unsent.

    A ``reply_to`` uid (in ``folder``) threads it: In-Reply-To and References from that message,
    and its subject with "Re: " when none is given. Sending it is the owner's, from their mail
    app, or ``mail_send`` with its approval. Does **not** cover: attachments, or replacing an
    earlier draft (each call is a new one).
    """
    msg, recipients, copies = _compose(to, subject, body, cc)
    with imap_connection() as conn:
        if reply_to:
            uid = str(reply_to).strip()
            if not _UID_RE.match(uid):
                raise ValueError("reply_to is a uid from a mail_search row (digits)")
            _select(conn, _resolve_folder(conn, folder))
            _meta, header = _fetch_parts(_ok(*conn.uid(
                "FETCH", uid, "(BODY.PEEK[HEADER])"), f"FETCH header {uid}"))
            original = email.message_from_bytes(header)
            their_id = str(original.get("Message-ID") or "").strip()
            if their_id:
                msg["In-Reply-To"] = their_id
                msg["References"] = " ".join(
                    x for x in (str(original.get("References") or "").strip(), their_id) if x)
            if not msg["Subject"]:
                theirs = decode_header_value(original.get("Subject"))
                del msg["Subject"]
                msg["Subject"] = theirs if theirs.lower().startswith("re:") else f"Re: {theirs}"
        target = _drafts_folder(conn)
        _ok(*conn.append(_quote_mailbox(target["raw"]), r"(\Draft)",
                         imaplib.Time2Internaldate(time.time()), msg.as_bytes()), "APPEND draft")
    return {"ok": True, "sent": False, "folder": target["name"], "to": recipients, "cc": copies,
            "subject": str(msg["Subject"]), "message_id": msg["Message-ID"]}


def send(to: Any, subject: str, body: str, cc: Any = None) -> dict[str, Any]:
    """Send one plain-text message and report who the server accepted.

    Does **not** cover: attachments, HTML alternatives, or delivery. SMTP
    accepting the message is not the recipient receiving it — a later bounce
    lands in the mailbox, not here.
    """
    msg, recipients, copies = _compose(to, subject, body, cc)
    sender = str(msg["From"])
    subject = str(msg["Subject"])

    with smtp_connection() as server:
        # to_addrs is passed explicitly: send_message would otherwise derive the
        # envelope from the headers, and a Bcc-shaped header change would move
        # who actually receives it without the approval message saying so.
        refused = server.send_message(msg, from_addr=sender, to_addrs=recipients + copies)
    return {"ok": not refused, "from": sender, "to": recipients, "cc": copies,
            "subject": subject, "refused": sorted(refused or {}),
            "message_id": msg["Message-ID"], **_file_sent(msg)}


def approval_for(tool_name: str, args: dict) -> dict[str, Any] | None:
    """The ``pre_tool_call`` verdict: approve ``mail_send``, pass the rest through.

    The message names every recipient and the subject, because that is what
    leaves the house and what the owner is actually deciding about. The rule key
    carries the recipients, so a remembered approval means "mail these people",
    never "mail anyone from now on".
    """
    if tool_name not in GATED_TOOLS:
        return None
    args = args if isinstance(args, dict) else {}
    to = addresses(args.get("to"))
    cc = addresses(args.get("cc"))
    who = ", ".join(to) or "(no recipient)"
    if cc:
        who += " (cc " + ", ".join(cc) + ")"
    subject = str(args.get("subject") or "").strip() or "(no subject)"
    return {
        "action": "approve",
        "message": f"mail_send will send an email to {who} with the subject {subject!r}",
        "rule_key": "mail:send:" + ",".join(sorted(to + cc)),
    }


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _safe(fn):
    def handler(params: dict, **kwargs):
        del kwargs
        try:
            return json.dumps(_scrub(fn(params or {})))
        except _ERRORS as exc:
            return json.dumps(_scrub({"ok": False,
                                      "error": f"{exc.__class__.__name__}: {exc}"}))
    return handler


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _search_params(p: dict) -> dict[str, Any]:
    return search(
        str(p.get("folder") or "INBOX"),
        unread=bool(p.get("unread")),
        # Models emit `from` about as often as the declared `from_`; accept both
        # rather than silently searching the whole mailbox.
        from_=_text(p.get("from_") if p.get("from_") is not None else p.get("from")),
        subject=_text(p.get("subject")),
        since=_text(p.get("since")),
        text=_text(p.get("text")),
        limit=_int(p.get("limit"), 20, 1, 100),
    )


def _read_params(p: dict) -> dict[str, Any]:
    return read(str(p.get("uid") or ""), str(p.get("folder") or "INBOX"))


def _send_params(p: dict) -> dict[str, Any]:
    # Every message Jarvis sends says Jarvis wrote it (_shared/_signed). Signed here, after
    # the approval the args were shown in: the one difference is that disclosure line.
    return send(p.get("to"), str(p.get("subject") or ""), signed(str(p.get("body") or "")), p.get("cc"))


handle_mail_folders = _safe(lambda p: folders())
handle_mail_search = _safe(_search_params)
handle_mail_read = _safe(_read_params)
def _draft_params(p: dict) -> dict[str, Any]:
    # Signed like a sent one: whoever sends it later, the draft says Jarvis wrote it.
    return draft(p.get("to"), str(p.get("subject") or ""), signed(str(p.get("body") or "")), p.get("cc"),
                 _text(p.get("reply_to")), str(p.get("folder") or "INBOX"))


handle_mail_send = _safe(_send_params)
handle_mail_draft = _safe(_draft_params)


def command(raw: str = "") -> str:
    """``/mail`` — the unread count and the newest few subjects in INBOX.

    Never raises and never returns the password: an error comes back as one
    scrubbed line, because the gateway's command dispatcher runs this inline.
    """
    limit = _int(raw.strip(), 5, 1, 20) if raw.strip() else 5
    try:
        result = search("INBOX", unread=True, limit=limit)
    except _ERRORS as exc:
        return _scrub_text(f"mail error: {exc.__class__.__name__}: {exc}")
    if not result.get("ok"):
        return _scrub_text(f"mail error: {result.get('error')}")
    rows = result["messages"]
    head = f"{result['matched']} unread in INBOX"
    if not rows:
        return head + "."
    lines = [f"  {r['subject'] or '(no subject)'} — {r['from'] or 'unknown sender'}" for r in rows]
    return _scrub_text(head + f", newest {len(rows)}:\n" + "\n".join(lines))
