"""calendar — CalDAV, over HTTP, with no new dependency.

    calendar:
      url: https://dav.example/remote.php/dav/calendars/alice/   # a calendar, or the home holding them
      username: !secret caldav_user
      password: !secret caldav_password
      address: alice@example.com        # which invitee is you (defaults to username if that is an address)
      refresh_minutes: 10               # how often the `calendar.*` entities the brief reads are refreshed

CalDAV is HTTP verbs and XML, and that is all this speaks: `PROPFIND` to find the
calendars, `REPORT` to read a window or one event, `PUT` to write, `DELETE` to
remove. The `caldav` library would bring `lxml`, and `jarvis-core`'s dependency
list is seven pure-Python wheels on purpose (`DEVIATIONS.md` §9) — `xml.etree`
and `httpx` are already here.

## The one CalDAV client in the house

This is the only thing that speaks to the calendar server. It does four jobs, so
nothing else needs a second copy of the configuration:

* the model's own tools (`calendar_list`, `calendar_create`, `calendar_rsvp`, …);
* the `calendar.events`, `calendar.calendars`, `calendar.create_event` and
  `calendar.rsvp` services, which is how Hermes's `jarvis-calendar` plugin reads
  and writes (house REST, `HASS_URL`/`HASS_TOKEN`) instead of holding DAV
  credentials of its own;
* the `calendar.<name>` entities (how many events today and tomorrow, whether
  one is on now, when the next starts — times and counts, never a title) and,
  beside them in-process, the titles the morning/evening brief names, refreshed
  on a timer without who else is invited;
* availability.

## What is read-only and what is not

Reading the diary is Tier 1: it is somebody's own calendar and the model is
answering a question about it. Writing is Tier 3, always — an event that
appears in a shared calendar is visible to other people, and an event that
quietly moves is worse than one that was never made. Answering an invitation
(`calendar_rsvp`) is a write: the organiser sees it.

What an RSVP does **not** do: it edits the owner's own ATTENDEE line and `PUT`s
the event back with `If-Match`. A server that does scheduling (RFC 6638:
Nextcloud, iCloud, SabreDAV) tells the organiser by itself; one that does not
(Radicale) stores the answer and nothing more — no mail is sent from here.
Everyone else on the invitation is text somebody else wrote: it is cleaned of
control and bidi characters on the way out and is data, never instructions.

Availability ("am I free on Tuesday afternoon") is read-only and is the query
this exists for. It is computed here rather than asked of the model, because
"free" is arithmetic over busy periods and a model doing arithmetic over
timestamps is a model getting it wrong occasionally and confidently.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

from ...automation.util import get_clock
from ...const import EVENT_JARVIS_START
from ...services import ServiceCall
from .._schedule import ScheduledTask
from ..plugins import PluginTool, ToolPlugin, get_registry

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "calendar"
DEPENDENCIES = ["llm"]

#: How far ahead "what's on" looks when nobody says.
DEFAULT_WINDOW_DAYS = 7
#: A ceiling, because a REPORT over a year is a lot of XML for a spoken answer.
MAX_WINDOW_DAYS = 92
#: A 500-person all-hands must not become the model's whole context.
MAX_ATTENDEES_SHOWN = 25
DEFAULT_REFRESH_MINUTES = 10
#: How long the list of calendars on the server is trusted before asking again.
COLLECTIONS_TTL = 600.0

CALDAV_NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}

#: What the owner may say, and the PARTSTAT it becomes (RFC 5545 §3.2.12).
RSVP_ANSWERS = {
    "accept": "ACCEPTED", "accepted": "ACCEPTED", "yes": "ACCEPTED",
    "decline": "DECLINED", "declined": "DECLINED", "no": "DECLINED",
    "tentative": "TENTATIVE", "maybe": "TENTATIVE",
}

#: The window query. `VEVENT` only: tasks and journals are a different feature
#: and returning them here would answer "what's on" with somebody's to-do list.
REPORT_BODY = """<?xml version="1.0" encoding="utf-8" ?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start}" end="{end}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

#: One event by UID, in any window: an RSVP is about an invitation, not a date.
UID_BODY = """<?xml version="1.0" encoding="utf-8" ?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:prop-filter name="UID"><c:text-match>{uid}</c:text-match></c:prop-filter>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

PROPFIND_BODY = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:displayname/><d:resourcetype/><c:supported-calendar-component-set/></d:prop>
</d:propfind>"""


class CalendarError(Exception):
    """Something to tell the user in a sentence, not a stack trace."""


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_stamp(raw: str) -> datetime | None:
    text = (raw or "").strip()
    for shape in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, shape)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None


def _clean(text: Any, cap: int = 200) -> str:
    """Text somebody else wrote, as data: control, format and bidi characters
    gone (a right-to-left override reorders what a person reads, a control
    character can hide a line), whitespace flattened, capped."""
    kept = "".join(c for c in str(text or "") if unicodedata.category(c)[0] != "C" or c in "\t\n")
    flat = " ".join(kept.split())
    return flat if len(flat) <= cap else flat[:cap].rstrip() + "…"


def _unescape(text: str) -> str:
    """iCalendar TEXT escapes; a newline becomes a space (this is a one-line summary)."""
    return re.sub(r"\\(.)", lambda m: " " if m.group(1) in "nN" else m.group(1), text)


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
        .replace("\r\n", "\\n").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545 §3.1: at most 75 octets a line, continued with a leading space, never mid-character."""
    parts: list[str] = []
    current, size = "", 0
    for char in line:
        width = len(char.encode())
        if size + width > (74 if parts else 75):
            parts.append(current)
            current, size = "", 0
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


# --- content lines ------------------------------------------------------------
def _split_outside_quotes(text: str, sep: str) -> list[str]:
    parts, current, quoted = [], "", False
    for char in text:
        if char == '"':
            quoted = not quoted
        if char == sep and not quoted:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts


def _content_line(line: str) -> tuple[str, list[str], str] | None:
    """`NAME;P=V;P2="a:b":value` -> (NAME, ["P=V", 'P2="a:b"'], value).

    The colon that ends the parameters is the first one outside quotes: a
    display name like `CN="Boss: The"` must not be taken for the value.
    """
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            head = _split_outside_quotes(line[:index], ";")
            return head[0].strip().upper(), head[1:], line[index + 1:]
    return None


def _param(params: list[str], name: str) -> str:
    for raw in params:
        key, _, value = raw.partition("=")
        if key.strip().upper() == name:
            return value.strip().strip('"')
    return ""


def _email(value: str) -> str:
    text = value.strip()
    return (text[7:] if text.lower().startswith("mailto:") else text).strip().lower()


def _person(params: list[str], value: str) -> dict[str, str]:
    return {
        "name": _unescape(_param(params, "CN")),
        "email": _email(value),
        "partstat": (_param(params, "PARTSTAT") or "NEEDS-ACTION").upper(),
        "role": (_param(params, "ROLE") or "REQ-PARTICIPANT").upper(),
    }


def _when(field_: tuple[str, list[str]] | None, zone: tzinfo) -> tuple[datetime | None, bool]:
    """A DTSTART/DTEND as a moment, and whether it was a bare date (an all-day event).

    UTC (`Z`) is taken as UTC; a `TZID` is looked up; a floating time, or a zone
    this machine has never heard of, is the house's own zone — which is what a
    floating time means.
    """
    if not field_:
        return None, False
    text, params = field_[0].strip(), field_[1]
    for shape in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            naive = datetime.strptime(text.rstrip("Z"), shape)
        except ValueError:
            continue
        date_only = shape == "%Y%m%d" or _param(params, "VALUE").upper() == "DATE"
        if text.endswith("Z"):
            return naive.replace(tzinfo=timezone.utc), date_only
        tzid = _param(params, "TZID")
        try:
            where = ZoneInfo(tzid) if tzid else zone
        except (KeyError, ValueError, OSError):
            where = zone
        return naive.replace(tzinfo=where), date_only
    return None, False


@dataclass
class Event:
    """One VEVENT, reduced to what a person asks about."""

    uid: str
    summary: str
    start: datetime | None
    end: datetime | None
    location: str = ""
    all_day: bool = False
    organizer: dict[str, str] = field(default_factory=dict)
    attendees: list[dict[str, str]] = field(default_factory=list)
    #: The owner's own PARTSTAT, or "" when the owner is not on the invitation.
    response: str = ""
    calendar: str = ""
    href: str = ""
    etag: str = ""
    #: The whole resource this came from, so an answer rewrites the server's own bytes.
    source: str = field(default="", repr=False)

    def as_dict(self) -> dict[str, Any]:
        """For the model, the services and the console: every word somebody else wrote is cleaned."""

        def moment(value: datetime | None) -> str | None:
            if value is None:
                return None
            return value.date().isoformat() if self.all_day else value.isoformat()

        who = lambda p: {"name": _clean(p.get("name"), 80), "email": _clean(p.get("email"), 120)}  # noqa: E731
        return {
            "uid": self.uid,
            "summary": _clean(self.summary),
            "start": moment(self.start),
            "end": moment(self.end),
            "location": _clean(self.location) or None,
            "all_day": self.all_day,
            "calendar": _clean(self.calendar, 80),
            "organizer": who(self.organizer) if self.organizer else None,
            "attendee_count": len(self.attendees),
            "attendees": [
                {**who(a), "partstat": _clean(a["partstat"], 20), "role": _clean(a["role"], 20)}
                for a in self.attendees[:MAX_ATTENDEES_SHOWN]
            ],
            "response": self.response or None,
        }


def parse_ical(
    text: str, owners: frozenset[str] | set[str] = frozenset(), zone: tzinfo = timezone.utc
) -> list[Event]:
    """Every VEVENT in an iCalendar document.

    A hand-rolled reader for what a person asks about — when, what, where, who
    is invited and what the owner said — not a general parser: folding, escaping
    and timezones are handled to the extent the fixtures, Radicale and
    Nextcloud produce them. Anything it cannot read becomes an event with a
    blank field rather than an exception — a diary that fails to load because
    one entry has an odd RRULE is worse than one that shows the entry without
    its recurrence.
    """
    events: list[Event] = []
    # Unfold: a continued line starts with a space or a tab.
    unfolded = re.sub(r"\r?\n[ \t]", "", text or "")
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S):
        # An alarm has a SUMMARY and a DESCRIPTION of its own, and is not the event.
        block = re.sub(r"BEGIN:VALARM.*?END:VALARM", "", block, flags=re.S)
        fields: dict[str, tuple[str, list[str]]] = {}
        attendees: list[dict[str, str]] = []
        organizer: dict[str, str] = {}
        for line in block.splitlines():
            parsed = _content_line(line)
            if parsed is None:
                continue
            name, params, value = parsed
            if name == "ATTENDEE":
                attendees.append(_person(params, value))
            elif name == "ORGANIZER":
                organizer = _person(params, value)
            else:
                fields[name] = (value.strip(), params)
        start, all_day = _when(fields.get("DTSTART"), zone)
        end, _ = _when(fields.get("DTEND"), zone)
        mine = next((a["partstat"] for a in attendees if a["email"] in owners), "")
        events.append(
            Event(
                uid=fields.get("UID", ("", []))[0],
                summary=_unescape(fields.get("SUMMARY", ("", []))[0]),
                start=start,
                end=end,
                location=_unescape(fields.get("LOCATION", ("", []))[0]),
                all_day=all_day,
                organizer={k: v for k, v in organizer.items() if k in ("name", "email")},
                attendees=attendees,
                response=mine,
            )
        )
    events.sort(key=lambda e: e.start or datetime.max.replace(tzinfo=timezone.utc))
    return events


def set_partstat(text: str, owners: frozenset[str] | set[str], partstat: str) -> tuple[str, int]:
    """The document with the owner's ATTENDEE line answering `partstat`, and how many such lines there are.

    Only that line is rewritten (re-folded if it grew); every other byte,
    including the line endings, is the server's own. A recurring event with
    overrides carries the owner on each VEVENT, and each is answered.
    """
    sep = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(sep)
    out: list[str] = []
    matched = 0
    index = 0
    while index < len(lines):
        nxt = index + 1
        while nxt < len(lines) and lines[nxt][:1] in (" ", "\t"):
            nxt += 1
        group = lines[index:nxt]
        parsed = _content_line("".join([group[0]] + [g[1:] for g in group[1:]]))
        if parsed and parsed[0] == "ATTENDEE" and _email(parsed[2]) in owners:
            matched += 1
            if _param(parsed[1], "PARTSTAT").upper() != partstat:
                params = [
                    f"PARTSTAT={partstat}" if p.partition("=")[0].strip().upper() == "PARTSTAT" else p
                    for p in parsed[1]
                ]
                if not _param(parsed[1], "PARTSTAT"):
                    params.append(f"PARTSTAT={partstat}")
                group = _fold(f"ATTENDEE;{';'.join(params)}:{parsed[2]}").split("\r\n")
        out.extend(group)
        index = nxt
    return sep.join(out), matched


#: The longest reminder an alarm takes, in minutes: a week ahead. Past that it is a date to note,
#: and an unbounded number is a TRIGGER some clients reject.
MAX_ALARM_MINUTES = 7 * 24 * 60


def _minutes(value: Any, what: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise CalendarError(f"{what} is a number of minutes") from None
    if not 0 <= minutes <= MAX_ALARM_MINUTES:
        raise CalendarError(f"{what} is between 0 and {MAX_ALARM_MINUTES} minutes")
    return minutes


def alarms(summary: str, alarm_minutes: Any = None, travel_minutes: Any = None) -> list[str]:
    """VALARM content lines (RFC 5545 §3.6.6): a reminder, and one at the time to leave.

    The time to leave is the start less the travel time the caller gives (Hermes' `route_eta`,
    or the owner); nothing here works a journey out, so a stale travel time stays stale.
    """
    lines: list[str] = []
    for minutes, text in (
        (_minutes(alarm_minutes, "alarm_minutes"), f"Coming up: {summary}"),
        (_minutes(travel_minutes, "travel_minutes"), f"Time to leave for {summary}"),
    ):
        if minutes is not None:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:-PT{minutes}M",
                      f"DESCRIPTION:{_escape(text)}", "END:VALARM"]
    return lines


def reschedule_ical(text: str, start: datetime, end: datetime, all_day: bool = False,
                    summary: str = "", location: str = "", extra: list[str] | None = None) -> str:
    """The document with its one VEVENT moved to `start`..`end`, the server's bytes kept elsewhere.

    Rewritten: DTSTART, DTEND (a DURATION is dropped for it), DTSTAMP, SEQUENCE (bumped), and
    SUMMARY / LOCATION when given; `extra` (alarms) goes in before END:VEVENT. Every other line,
    attendees and the server's own properties included, is left as it was. Refused: a repeating
    event (RRULE, or overrides), where "move it" could mean the series or one occurrence.
    """
    sep = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(sep)
    if all_day:
        new = {"DTSTART": f"DTSTART;VALUE=DATE:{start:%Y%m%d}", "DTEND": f"DTEND;VALUE=DATE:{end:%Y%m%d}"}
    else:
        new = {"DTSTART": f"DTSTART:{_stamp(start)}", "DTEND": f"DTEND:{_stamp(end)}"}
    new["DTSTAMP"] = f"DTSTAMP:{_stamp(datetime.now(timezone.utc))}"
    if summary:
        new["SUMMARY"] = f"SUMMARY:{_escape(summary)}"
    if location:
        new["LOCATION"] = f"LOCATION:{_escape(location)}"
    out: list[str] = []
    events, in_event, in_alarm, seen, sequence = 0, False, False, set(), None
    index = 0
    while index < len(lines):
        nxt = index + 1
        while nxt < len(lines) and lines[nxt][:1] in (" ", "\t"):
            nxt += 1
        group = lines[index:nxt]
        index = nxt
        parsed = _content_line("".join([group[0]] + [g[1:] for g in group[1:]]))
        name, value = (parsed[0], parsed[2].strip().upper()) if parsed else ("", "")
        if name == "BEGIN" and value == "VEVENT":
            events, in_event = events + 1, True
        elif name == "BEGIN" and value == "VALARM":
            in_alarm = True
        elif name == "END" and value == "VALARM":
            in_alarm = False
        elif name == "END" and value == "VEVENT" and in_event:
            for key, line in new.items():
                if key not in seen:
                    out += _fold(line).split("\r\n")
            if sequence is None:
                out.append("SEQUENCE:1")
            for line in extra or []:
                out += _fold(line).split("\r\n")
            in_event = False
        elif in_event and not in_alarm:
            if name in ("RRULE", "RDATE", "RECURRENCE-ID"):
                raise CalendarError("it repeats: move the series or one occurrence in your calendar app")
            if name in new:
                if name not in seen:
                    out += _fold(new[name]).split("\r\n")
                    seen.add(name)
                continue
            if name == "DURATION":
                continue
            if name == "SEQUENCE":
                sequence = int(value) + 1 if value.isdigit() else 1
                out.append(f"SEQUENCE:{sequence}")
                continue
        out.extend(group)
    if events != 1:
        raise CalendarError("it repeats: move the series or one occurrence in your calendar app"
                            if events > 1 else "the event could not be read")
    return sep.join(out)


def build_ical(summary: str, start: datetime, end: datetime, location: str = "",
               uid: str = "", description: str = "", all_day: bool = False,
               extra: list[str] | None = None) -> str:
    """One VEVENT, as a whole VCALENDAR — which is what a PUT takes.

    An all-day event is a DATE, not midnight UTC — written as a timestamp it
    lands on the wrong day for anyone east or west of UTC; `end` is exclusive
    (RFC 5545 §3.8.2.2), so a one-day event ends the next morning.
    """
    identifier = uid or f"{uuid.uuid4().hex}@jarvis"
    if all_day:
        when = [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
    else:
        when = [f"DTSTART:{_stamp(start)}", f"DTEND:{_stamp(end)}"]
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Jarvis//EN", "BEGIN:VEVENT",
        f"UID:{identifier}", f"DTSTAMP:{_stamp(datetime.now(timezone.utc))}", *when,
        f"SUMMARY:{_escape(summary)}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    lines += [*(extra or []), "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def free_windows(
    events: list[Event], start: datetime, end: datetime, minutes: int = 30
) -> list[tuple[datetime, datetime]]:
    """Gaps of at least `minutes` between `start` and `end`.

    Arithmetic, here, rather than a question for the model: "am I free on
    Tuesday afternoon" is a fold over busy periods, and a model doing that over
    timestamps gets it wrong occasionally and confidently.
    """
    busy = sorted(
        (e.start, e.end or e.start + timedelta(hours=1))
        for e in events
        if e.start is not None
    )
    free: list[tuple[datetime, datetime]] = []
    cursor = start
    for busy_start, busy_end in busy:
        if busy_start > cursor and (busy_start - cursor) >= timedelta(minutes=minutes):
            free.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if end > cursor and (end - cursor) >= timedelta(minutes=minutes):
        free.append((cursor, end))
    return free


@dataclass
class Collection:
    """One calendar on the server."""

    name: str
    url: str  # absolute, ending in "/"

    @property
    def slug(self) -> str:
        return urlsplit(self.url).path.rstrip("/").rsplit("/", 1)[-1].lower()

    @property
    def entity_id(self) -> str:
        return "calendar." + (re.sub(r"[^a-z0-9]+", "_", self.slug).strip("_") or "diary")


class Calendar(ToolPlugin, ScheduledTask):
    """CalDAV, as the house's tools, services and brief feed."""

    domain = DOMAIN
    _schedule_label = "calendar feed"

    def __init__(self, jarvis: "Jarvis", config: Any = None) -> None:
        super().__init__(jarvis, config)
        self._task: asyncio.Task | None = None
        self._collections: list[Collection] | None = None
        self._collections_until = 0.0
        self._published: set[str] = set()
        #: entity id -> today's and tomorrow's events (summary, start, end, all_day), for the brief.
        self.agenda: dict[str, list[dict[str, Any]]] = {}

    @property
    def url(self) -> str:
        return str(self.config.get("url") or "").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.url)

    @property
    def owners(self) -> frozenset[str]:
        """The calendar-user addresses that are the owner (lower-case, no `mailto:`)."""
        raw = self.config.get("address")
        items = raw if isinstance(raw, (list, tuple)) else [raw]
        found = {_email(str(item)) for item in items if str(item or "").strip()}
        user = self.secret("username")
        if not found and "@" in user:
            found.add(_email(user))
        return frozenset(found)

    @property
    def refresh_seconds(self) -> float:
        try:
            minutes = float(self.config.get("refresh_minutes") or DEFAULT_REFRESH_MINUTES)
        except (TypeError, ValueError):
            minutes = DEFAULT_REFRESH_MINUTES
        return max(1.0, minutes) * 60

    def _zone(self) -> tzinfo:
        try:
            return get_clock(self.jarvis).now().tzinfo or timezone.utc
        except Exception:  # noqa: BLE001 - a clock that fails is UTC, not a dead diary
            return timezone.utc

    # --- the wire ---------------------------------------------------------
    async def _request(self, method: str, target: str = "", body: str = "",
                       headers: dict[str, str] | None = None) -> tuple[int, str]:
        import httpx

        if method in ("PUT", "DELETE") and not target:
            # An unnamed target would fall through to the configured URL itself: the whole
            # calendar. Writers name the resource they mean or do not write.
            raise CalendarError(f"{method} needs the resource it is for")
        auth = None
        user = self.secret("username")
        if user:
            auth = (user, self.secret("password"))
        if target.startswith(("http://", "https://")):
            url = target
        else:
            url = f"{self.url}/{target.lstrip('/')}" if target else self.url + "/"
        async with httpx.AsyncClient(timeout=30.0, auth=auth) as http:
            answer = await http.request(method, url, content=body or None, headers=headers)
            return answer.status_code, answer.text

    def _resolve(self, href: str) -> str | None:
        """A server-sent href as an absolute URL on the configured server, or None.

        The credentials go with every request, so a multistatus that names
        `http://elsewhere/` must not be followed.
        """
        absolute = urljoin(self.url + "/", href.strip())
        mine, theirs = urlsplit(self.url), urlsplit(absolute)
        return absolute if (mine.scheme, mine.netloc) == (theirs.scheme, theirs.netloc) else None

    @staticmethod
    def _addressable(event: Event) -> None:
        """Refuse to write or delete an event the server named somewhere we will not go."""
        if not event.href:
            raise CalendarError(
                "the server put that event somewhere else than the calendar I was given, "
                "so I will not change it"
            )

    @staticmethod
    def _rows(text: str) -> list[ET.Element]:
        # CalDAV never sends a DTD. An entity declaration is the one thing that would turn a
        # small response into a very large one (expat limits the worst of it; this refuses it).
        if "<!doctype" in text[:2048].lower() or "<!entity" in text.lower():
            raise CalendarError("the calendar sent XML we could not read: it declares a DTD")
        try:
            return list(ET.fromstring(text).iter(f"{{{CALDAV_NS['d']}}}response"))
        except ET.ParseError as err:
            raise CalendarError(f"the calendar sent XML we could not read: {err}") from err

    # --- calendars ----------------------------------------------------------
    async def collections(self) -> list[Collection]:
        """Every VEVENT calendar at the configured URL: the URL itself if it is one, else its children."""
        if self._collections is not None and time.monotonic() < self._collections_until:
            return self._collections
        found: list[Collection] = []
        for depth in ("0", "1"):
            status, text = await self._request(
                "PROPFIND", "", PROPFIND_BODY,
                {"depth": depth, "content-type": "application/xml; charset=utf-8"},
            )
            if status >= 400:
                # A server that will not PROPFIND: the URL is what we were given, take it as the calendar.
                found = [Collection(self.url.rsplit("/", 1)[-1] or "calendar", self.url + "/")]
                break
            for row in self._rows(text):
                kind = row.find(".//d:resourcetype", CALDAV_NS)
                parts = [c.get("name") for c in row.iter(f"{{{CALDAV_NS['c']}}}comp")]
                href = (row.findtext("d:href", "", CALDAV_NS) or "").strip()
                resolved = self._resolve(href) if href else None
                if (
                    kind is None
                    or kind.find("c:calendar", CALDAV_NS) is None
                    or (parts and "VEVENT" not in parts)
                    or resolved is None
                ):
                    continue
                name = (row.findtext(".//d:displayname", "", CALDAV_NS) or "").strip()
                url = resolved if resolved.endswith("/") else resolved + "/"
                found.append(Collection(name or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1], url))
            if found:
                break
        self._collections, self._collections_until = found, time.monotonic() + COLLECTIONS_TTL
        return found

    async def _chosen(self, calendar: str = "") -> list[Collection]:
        every = await self.collections()
        wanted = str(calendar or "").strip().lower()
        if not wanted:
            return every
        hits = [c for c in every if wanted in c.name.lower() or wanted in c.slug]
        if not hits:
            raise CalendarError(
                f"no calendar matching {calendar!r}; the calendars are "
                f"{', '.join(c.name for c in every) or 'none'}"
            )
        return hits

    async def _report(self, collection: Collection, body: str) -> list[Event]:
        status, text = await self._request(
            "REPORT", collection.url, body,
            {"depth": "1", "content-type": "application/xml; charset=utf-8"},
        )
        if status >= 400:
            raise CalendarError(f"the calendar answered {status}")
        zone, owners = self._zone(), self.owners
        events: list[Event] = []
        for row in self._rows(text):
            data = row.findtext(".//c:calendar-data", "", CALDAV_NS)
            href = self._resolve(row.findtext("d:href", "", CALDAV_NS) or "") or ""
            etag = (row.findtext(".//d:getetag", "", CALDAV_NS) or "").strip()
            for event in parse_ical(data or "", owners, zone):
                event.href, event.etag, event.calendar = href, etag, collection.name
                event.source = data or ""
                events.append(event)
        return events

    async def events_between(self, start: datetime, end: datetime, calendar: str = "") -> list[Event]:
        events: list[Event] = []
        for collection in await self._chosen(calendar):
            events += await self._report(
                collection, REPORT_BODY.format(start=_stamp(start), end=_stamp(end))
            )
        events.sort(key=lambda e: e.start or datetime.max.replace(tzinfo=timezone.utc))
        return events

    async def _find(self, uid: str, calendar: str = "") -> list[Event]:
        """Every VEVENT with this UID (a recurring event with overrides has several)."""
        found: list[Event] = []
        for collection in await self._chosen(calendar):
            body = UID_BODY.format(uid=xml_escape(uid))
            found += [e for e in await self._report(collection, body) if e.uid == uid]
        return found

    async def _the_event(self, uid: str, calendar: str = "") -> list[Event]:
        """The one event an action on `uid` means: its VEVENTs, all from a single resource.

        The RSVP, the delete and the lookup an approval prompt is built from all come
        through here, so the event the person is shown is the event that is acted on. A uid
        that is in two calendars is refused until the caller says which: guessing would
        approve the event in one calendar and answer the one in the other.
        """
        found = await self._find(uid, calendar)
        if not found:
            raise CalendarError(f"no event with uid {uid!r}")
        if len({e.href for e in found}) > 1:
            where = ", ".join(sorted({_clean(e.calendar, 80) for e in found}))
            raise CalendarError(
                f"the uid {_clean(uid, 120)!r} is in more than one calendar ({where}): say which calendar"
            )
        return found

    # --- the tools and services -----------------------------------------------
    def _window(self, args: dict[str, Any]) -> tuple[datetime, datetime]:
        zone = self._zone()
        start = _parse_iso(args.get("start"), zone) or datetime.now(zone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        raw_end = str(args.get("end") or "").strip()
        end = _parse_iso(raw_end, zone)
        if end is not None and len(raw_end) == 10:
            end += timedelta(days=1)  # a bare date is that whole day
        days = max(1, min(int(args.get("days") or DEFAULT_WINDOW_DAYS), MAX_WINDOW_DAYS))
        if end is None or end <= start:
            end = start + timedelta(days=days)
        return start, min(end, start + timedelta(days=MAX_WINDOW_DAYS))

    async def _guarded(self, work: Any) -> dict[str, Any]:
        if not self.configured:
            return {"status": "error", "error": "no calendar is configured"}
        # Every answer from here can quote an event's title, an organiser or a calendar's
        # name — words somebody else chose — so every one says so, and a consumer that
        # honours the flag (the tool layer, an automation, the plugin) can taint on it.
        try:
            return {**await work, "content_is_untrusted": True}
        except CalendarError as err:
            return {"status": "error", "error": str(err), "content_is_untrusted": True}

    async def list_calendars(self, args: dict[str, Any]) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            every = await self.collections()
            return {"status": "ok", "calendars": [{"name": _clean(c.name, 80)} for c in every]}

        return await self._guarded(work())

    async def list_events(self, args: dict[str, Any]) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            calendar, uid = str(args.get("calendar") or ""), str(args.get("uid") or "").strip()
            if uid:  # one event, whenever it is: what an approval prompt needs to name
                events = await self._the_event(uid, calendar)
                return {"status": "ok", "count": len(events), "events": [e.as_dict() for e in events]}
            start, end = self._window(args)
            events = await self.events_between(start, end, calendar)
            return {
                "status": "ok",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "count": len(events),
                "events": [e.as_dict() for e in events[:50]],
            }

        return await self._guarded(work())

    async def availability(self, args: dict[str, Any]) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            days = max(1, min(int(args.get("days") or 1), MAX_WINDOW_DAYS))
            minutes = max(5, int(args.get("minutes") or 30))
            start = datetime.now(timezone.utc)
            end = start + timedelta(days=days)
            # A declined invitation is not a reason to be busy.
            events = [e for e in await self.events_between(start, end) if e.response != "DECLINED"]
            windows = free_windows(events, start, end, minutes)
            return {
                "status": "ok",
                "free": [{"from": a.isoformat(), "to": b.isoformat()} for a, b in windows[:20]],
                "busy": len(events),
            }

        return await self._guarded(work())

    async def create_event(self, args: dict[str, Any]) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            summary = str(args.get("summary") or "").strip()
            if not summary:
                raise CalendarError("an event needs a summary")
            zone = self._zone()
            raw_start, raw_end = str(args.get("start") or "").strip(), str(args.get("end") or "").strip()
            start = _parse_iso(raw_start, zone)
            if start is None:
                raise CalendarError("an event needs a start time (ISO 8601)")
            all_day = len(raw_start) == 10 and len(raw_end) in (0, 10)
            if all_day:
                end = (_parse_iso(raw_end, zone) or start) + timedelta(days=1)
            else:
                end = _parse_iso(raw_end, zone) or start + timedelta(
                    minutes=int(args.get("minutes") or 60)
                )
            target = (await self._chosen(str(args.get("calendar") or "")))[:1]
            if not target:
                raise CalendarError("there is no calendar to put it in")
            uid = f"{uuid.uuid4().hex}@jarvis"
            extra = alarms(summary, args.get("alarm_minutes"), args.get("travel_minutes"))
            body = build_ical(summary, start, end, str(args.get("location") or ""), uid,
                              str(args.get("description") or ""), all_day, extra)
            # If-None-Match: * makes the PUT a create — a UID collision must fail, not overwrite.
            status, text = await self._request(
                "PUT", f"{target[0].url}{uid}.ics", body,
                {"content-type": "text/calendar; charset=utf-8", "if-none-match": "*"},
            )
            if status >= 400:
                raise CalendarError(f"the calendar refused it ({status}): {_clean(text, 200)}")
            await self._publish_quietly()
            return {"status": "ok", "uid": uid, "summary": _clean(summary),
                    "start": start.isoformat(), "calendar": target[0].name,
                    **await self._clashes(uid, start, end, all_day, args.get("travel_minutes"))}

        return await self._guarded(work())

    async def _clashes(self, uid: str, start: datetime, end: datetime, all_day: bool,
                       travel_minutes: Any) -> dict[str, Any]:
        """What the new time overlaps (a declined invitation and all-day events do not count),
        and the time to leave when a travel time was given."""
        found = [] if all_day else [
            e for e in await self.events_between(start, end)
            if e.uid != uid and e.response != "DECLINED" and not e.all_day
        ]
        answer: dict[str, Any] = {"conflicts": [
            {k: v for k, v in e.as_dict().items() if k in ("uid", "summary", "start", "end", "calendar")}
            for e in found[:10]
        ]}
        travel = _minutes(travel_minutes, "travel_minutes")
        if travel is not None and not all_day:
            answer["leave_by"] = (start - timedelta(minutes=travel)).isoformat()
        return answer

    async def move_event(self, args: dict[str, Any]) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            uid = str(args.get("uid") or "").strip()
            if not uid:
                raise CalendarError("which event? give its uid")
            zone = self._zone()
            raw_start, raw_end = str(args.get("start") or "").strip(), str(args.get("end") or "").strip()
            start = _parse_iso(raw_start, zone)
            if start is None:
                raise CalendarError("a move needs the new start time (ISO 8601)")
            head = (await self._the_event(uid, str(args.get("calendar") or "")))[0]
            self._addressable(head)
            if not head.etag:
                raise CalendarError("the server gave no ETag, so I will not overwrite the event blind")
            all_day = len(raw_start) == 10
            kept = (head.end - head.start) if head.start and head.end else timedelta(hours=1)
            if all_day:
                end = ((_parse_iso(raw_end, zone) or start) + timedelta(days=1)) if raw_end else start + max(kept, timedelta(days=1))
            elif raw_end:
                end = _parse_iso(raw_end, zone) or start + kept
            else:
                length = _minutes(args.get("minutes"), "minutes")
                end = start + (timedelta(minutes=length) if length else kept)
            if end <= start:
                raise CalendarError("the event would end before it starts")
            summary = str(args.get("summary") or "").strip()
            extra = alarms(summary or head.summary, args.get("alarm_minutes"), args.get("travel_minutes"))
            body = reschedule_ical(head.source, start, end, all_day, summary,
                                   str(args.get("location") or "").strip(), extra)
            status, text = await self._request(
                "PUT", head.href, body,
                {"content-type": "text/calendar; charset=utf-8", "if-match": head.etag},
            )
            if status == 412:
                raise CalendarError("the event changed on the server while I was moving it; ask again")
            if status >= 400:
                raise CalendarError(f"the calendar refused the move ({status}): {_clean(text, 200)}")
            await self._publish_quietly()
            return {"status": "ok", "uid": uid, "summary": _clean(summary or head.summary),
                    "start": start.date().isoformat() if all_day else start.isoformat(),
                    "end": end.date().isoformat() if all_day else end.isoformat(),
                    "calendar": head.calendar,
                    **await self._clashes(uid, start, end, all_day, args.get("travel_minutes"))}

        return await self._guarded(work())

    async def rsvp(self, args: dict[str, Any]) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            uid = str(args.get("uid") or "").strip()
            partstat = RSVP_ANSWERS.get(str(args.get("response") or "").strip().lower())
            if not uid:
                raise CalendarError("which invitation? give its uid")
            if partstat is None:
                raise CalendarError("answer accept, decline or tentative")
            owners = self.owners
            if not owners:
                raise CalendarError(
                    "I do not know which invitee is you: set `address:` in the calendar block"
                )
            head = (await self._the_event(uid, str(args.get("calendar") or "")))[0]
            self._addressable(head)
            data = head.source
            new, matched = set_partstat(data, owners, partstat)
            if not matched:
                organiser = head.organizer.get("email") or "its organiser"
                raise CalendarError(
                    f"you are not invited to {_clean(head.summary, 80)!r} (organised by "
                    f"{_clean(organiser, 120)}): there is no answer to give"
                )
            if not head.etag:
                raise CalendarError("the server gave no ETag, so I will not overwrite the event blind")
            if new != data:
                status, text = await self._request(
                    "PUT", head.href, new,
                    {"content-type": "text/calendar; charset=utf-8", "if-match": head.etag},
                )
                if status == 412:
                    raise CalendarError("the event changed on the server while I was answering; ask again")
                if status >= 400:
                    raise CalendarError(f"the calendar refused the answer ({status})")
                await self._publish_quietly()
            return {
                "status": "ok", "uid": uid, "response": partstat, "unchanged": new == data,
                "summary": _clean(head.summary), "start": head.as_dict()["start"],
            }

        return await self._guarded(work())

    async def delete_event(self, args: dict[str, Any]) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            uid = str(args.get("uid") or "").strip()
            if not uid:
                raise CalendarError("which event? give its uid")
            head = (await self._the_event(uid, str(args.get("calendar") or "")))[0]
            self._addressable(head)
            headers = {"if-match": head.etag} if head.etag else None
            status, _text = await self._request("DELETE", head.href, "", headers)
            if status >= 400:
                raise CalendarError(f"the calendar refused ({status})")
            await self._publish_quietly()
            return {"status": "ok", "uid": uid}

        return await self._guarded(work())

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "no url configured"}
        try:
            status, _ = await self._request("OPTIONS")
        except Exception as err:  # noqa: BLE001
            return {"ok": False, "error": f"{type(err).__name__}"}
        return {"ok": status < 400, "status": status}

    def _as_tool(self, handler: Any) -> Any:
        """The handler as the model's tool: what it answers, refusals included, is a stranger's words.

        A title, an organiser, a calendar's name, a server's error text. Same treatment as
        a web page or a mail body (M43): the result is flagged and the turn is tainted, so
        whatever an invitation asks Jarvis to DO needs a human. The flag comes from
        `_guarded`, which sets it on every answer that can quote the server's words.
        """
        from ...api.devices import mark_untrusted_result

        async def tool(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
            return mark_untrusted_result(self.jarvis, context, await handler(args))

        return tool

    def tools(self):
        return [
            PluginTool(
                "calendar_list",
                "What is in the diary: events with who is invited and the owner's own answer.",
                {
                    "days": {"type": "integer", "description": "how far ahead (default 7)"},
                    "start": {"type": "string", "description": "ISO date or datetime to start from"},
                    "end": {"type": "string", "description": "ISO date or datetime to end at"},
                    "calendar": {"type": "string", "description": "one calendar, by name"},
                },
                self._as_tool(self.list_events),
                read_only=True,
                wants_context=True,
            ),
            PluginTool(
                "calendar_availability",
                "When the user is free — gaps between what is already booked.",
                {
                    "days": {"type": "integer", "description": "how far ahead (default 1)"},
                    "minutes": {"type": "integer", "description": "shortest useful gap"},
                },
                self.availability,
                read_only=True,
            ),
            PluginTool(
                "calendar_create",
                "Put an event in the diary. Needs a human to say yes.",
                {
                    "summary": {"type": "string", "description": "what it is"},
                    "start": {"type": "string", "description": "ISO 8601 start"},
                    "end": {"type": "string", "description": "ISO 8601 end (or use minutes)"},
                    "minutes": {"type": "integer", "description": "length, if no end"},
                    "location": {"type": "string"},
                    "description": {"type": "string"},
                    "calendar": {"type": "string", "description": "which calendar, by name"},
                },
                self._as_tool(self.create_event),
                wants_context=True,
            ),
            PluginTool(
                "calendar_rsvp",
                "Answer an invitation as the user: accept, decline or tentative. The organiser "
                "sees it, so it needs a human to say yes.",
                {
                    "uid": {"type": "string", "description": "the invitation's uid"},
                    "response": {"type": "string", "description": "accept, decline or tentative"},
                    "calendar": {"type": "string", "description": "which calendar, by name"},
                },
                self._as_tool(self.rsvp),
                wants_context=True,
            ),
            PluginTool(
                "calendar_delete",
                "Remove an event from the diary. Needs a human to say yes.",
                {
                    "uid": {"type": "string", "description": "the event's uid"},
                    "calendar": {"type": "string", "description": "which calendar, if the uid is in more than one"},
                },
                self._as_tool(self.delete_event),
                wants_context=True,
            ),
        ]

    # --- services: how the rest of the house (and Hermes) reaches this client -----
    def register_services(self) -> None:
        for name, handler, text in (
            ("calendars", self.list_calendars, "The calendars on the server."),
            ("events", self.list_events, "Events in a window, with attendees and the owner's answer."),
            ("create_event", self.create_event, "Put an event in a calendar."),
            ("move_event", self.move_event, "Move or rename one event in place, with alarms; says what it clashes with."),
            ("rsvp", self.rsvp, "Answer an invitation as the owner (accept, decline, tentative)."),
        ):
            async def run(call: ServiceCall, handler: Any = handler) -> dict[str, Any]:
                return await handler(dict(call.data))

            self.jarvis.services.register(DOMAIN, name, run, supports_response=True, description=text)

    # --- the brief's feed -------------------------------------------------------------
    async def publish(self) -> None:
        """Refresh `calendar.<name>` (times and counts) and `agenda` (the words) for the brief.

        An entity is read by every tool that reads state — core's own and Hermes's
        homeassistant toolset — and none of them taints the turn. So the entity carries
        no word anybody else wrote: how many events today and tomorrow, whether one is
        on now, when the next starts. The titles the brief names live in `agenda`, read
        in-process by the briefing (whose `get_briefing` tool taints the turn that
        carries them); `agenda` is not in the state machine and not on any API.
        """
        zone = self._zone()
        today = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            every = await self.collections()
            events = await self.events_between(today, today + timedelta(days=2))
        except Exception as err:  # noqa: BLE001 - a far end is allowed to fail
            _LOGGER.warning("calendar: could not refresh (%s)", type(err).__name__)
            self._collections = None
            for entity_id in self._published:
                old = self.jarvis.states.get(entity_id)
                if old is not None:
                    self.jarvis.states.set(entity_id, "unavailable", dict(old.attributes))
            return
        now = datetime.now(zone)
        agenda: dict[str, list[dict[str, Any]]] = {}
        for collection in every:
            mine = [e for e in events if e.href.startswith(collection.url)]
            agenda[collection.entity_id] = [
                {k: v for k, v in e.as_dict().items() if k in ("summary", "start", "end", "all_day")}
                for e in mine
            ]
            upcoming = [e.start for e in mine if e.start and not e.all_day and e.start >= now]
            ongoing = any(
                not e.all_day and e.start and e.end and e.start <= now < e.end for e in mine
            )
            # No friendly_name either: a shared calendar's name is somebody else's word too.
            self.jarvis.states.set(
                collection.entity_id, "on" if ongoing else "off",
                {
                    "event_count": len(mine),
                    "all_day_count": sum(1 for e in mine if e.all_day),
                    "next_start": min(upcoming).astimezone(zone).isoformat() if upcoming else None,
                },
            )
            self._published.add(collection.entity_id)
        self.agenda = agenda

    async def _publish_quietly(self) -> None:
        try:
            await self.publish()
        except Exception:  # noqa: BLE001 - the answer was stored; the brief catches up on its timer
            _LOGGER.debug("calendar: publish after a write failed", exc_info=True)

    def _schedule_pending(self) -> bool:
        return self.configured

    async def _run(self) -> None:
        while True:
            await self.publish()
            await asyncio.sleep(self.refresh_seconds)


def _parse_iso(value: Any, zone: tzinfo = timezone.utc) -> datetime | None:
    """ISO 8601 as a moment; a date or a time with no offset is in `zone` — the house's own."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _parse_stamp(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    plugin = Calendar(jarvis, config)
    jarvis.data[DOMAIN] = plugin
    get_registry(jarvis).add(plugin)
    if not plugin.configured:
        _LOGGER.info("calendar: no url configured; no tools registered")
        return True
    plugin.register()
    plugin.register_services()

    def _start(event: Any = None) -> None:
        plugin.start()

    if jarvis.is_running:
        plugin.start()
    else:
        jarvis.bus.listen(EVENT_JARVIS_START, _start)
    jarvis.register_shutdown(plugin.stop)
    return True
