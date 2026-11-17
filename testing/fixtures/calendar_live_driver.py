#!/usr/bin/env python3
"""The calendar against a real CalDAV server: run by calendar_live.sh, which starts Radicale.

    python3 testing/fixtures/calendar_live_driver.py http://127.0.0.1:5232   # a Radicale you started

The path under test is the real one, hop for hop: Hermes's `jarvis-calendar` handlers ->
`_shared._house` over HTTP to a running jarvis-core REST app (`calendar.*` services) ->
the calendar integration's CalDAV client -> Radicale. Nothing is mocked except the clock
being the machine's. Radicale has no scheduling (RFC 6638), so what is proved about an RSVP
is that the owner's answer is STORED, with If-Match; whether an organiser is told is the
server's business and is not measured here.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO / "aura-core", REPO / "hermes" / "plugins"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from _plugin_loader import load_plugin  # noqa: E402
from aura.api.server import create_app  # noqa: E402
from aura.auth import DATA_AUTH, AuthManager  # noqa: E402
from aura.automation.util import get_clock  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import briefing as briefing_integration  # noqa: E402
from aura.integrations import calendar as calendar_integration  # noqa: E402
from aura.tools import ToolRegistry  # noqa: E402

OWNER = "alice@example.test"
AUTH = ("alice", "not-checked-by-radicale")


def ics(uid: str, start: datetime, summary: str, people: str) -> str:
    stamp = "%Y%m%dT%H%M%SZ"
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//jarvis live probe//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:{datetime.now(timezone.utc).strftime(stamp)}\r\n"
        f"DTSTART:{start.astimezone(timezone.utc).strftime(stamp)}\r\n"
        f"DTEND:{(start + timedelta(hours=1)).astimezone(timezone.utc).strftime(stamp)}\r\n"
        f"SUMMARY:{summary}\r\n{people}END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def owner_line(stored: str) -> str:
    """The owner's ATTENDEE line out of what the server stored (unfolded)."""
    flat = stored.replace("\r\n ", "").replace("\n ", "")
    return next(ln for ln in flat.splitlines() if ln.startswith("ATTENDEE") and OWNER in ln)


async def main(radicale: str) -> int:
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    work = Path(tempfile.mkdtemp(prefix="calendar-live-"))
    jarvis = Jarvis(work)
    jarvis.data["llm_tools"] = ToolRegistry(jarvis)
    local = get_clock(jarvis).now().replace(hour=15, minute=0, second=0, microsecond=0)
    base = f"{radicale}/alice/diary/"

    # --- seed the server the way an organiser's invitation arrives -------------------
    with httpx.Client(auth=AUTH, timeout=20) as server:
        made = server.request("MKCALENDAR", base)
        check(made.status_code == 201, f"a calendar is made on Radicale ({made.status_code})")
        invitation = ics("inv-1", local, "Kickoff",
                         'ORGANIZER;CN="Boss, The":mailto:boss@example.test\r\n'
                         f"ATTENDEE;CN=Alice;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{OWNER}\r\n"
                         "ATTENDEE;CN=Pat;PARTSTAT=ACCEPTED;ROLE=OPT-PARTICIPANT:mailto:pat@example.test\r\n")
        lunch = ics("lunch-1", local - timedelta(hours=4), "Team lunch",
                    "ORGANIZER:mailto:boss@example.test\r\nATTENDEE:mailto:pat@example.test\r\n")
        for name, body in (("inv-1", invitation), ("lunch-1", lunch)):
            put = server.put(f"{base}{name}.ics", content=body,
                             headers={"content-type": "text/calendar; charset=utf-8"})
            check(put.status_code == 201, f"{name} is on the server ({put.status_code})")
        # The same uid in two calendars: a forwarded invitation saved in both, as clients do.
        # Made before the house first looks (it remembers the list of calendars for ten minutes).
        other = f"{radicale}/alice/second/"
        check(server.request("MKCALENDAR", other).status_code == 201, "a second calendar is made")
        for where, title in ((base, "Dup in diary"), (other, "Dup in second")):
            server.put(f"{where}dup-1.ics", headers={"content-type": "text/calendar; charset=utf-8"},
                       content=ics("dup-1", local + timedelta(hours=3), title,
                                   "ORGANIZER:mailto:boss@example.test\r\n"
                                   f"ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:{OWNER}\r\n"))

        def stored(name: str) -> httpx.Response:
            return server.get(f"{base}{name}.ics")

        # --- the house: the real client, the real REST app, a real token --------------
        manager = AuthManager()
        jarvis.data[DATA_AUTH] = manager
        _info, secret = await manager.create_token("calendar-live")
        configured = await calendar_integration.async_setup(
            jarvis, {"url": f"{radicale}/alice/", "username": AUTH[0], "password": AUTH[1],
                     "address": OWNER})
        check(bool(configured), "the calendar integration sets up from one `calendar:` block")

        port = free_port()
        app = create_app(jarvis, static_dir=work / "no-www")
        service = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        serving = asyncio.create_task(service.serve())
        while not service.started:
            await asyncio.sleep(0.05)
        os.environ["HASS_URL"] = f"http://127.0.0.1:{port}"
        os.environ["HASS_TOKEN"] = secret
        tools = load_plugin("jarvis-calendar").tools

        # The CalDAV client's own requests, for the If-Match proof; and one stale-write race.
        sent: list[tuple[str, str, str | None]] = []
        race = {"armed": False}
        real_request = httpx.AsyncClient.request

        async def spy(self, method, url, **kwargs):  # noqa: ANN001
            headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
            sent.append((method, str(url), headers.get("if-match")))
            if race["armed"] and method == "PUT" and str(url).endswith("inv-1.ics"):
                race["armed"] = False  # another client saves the event before our answer lands
                current = server.get(f"{base}inv-1.ics")
                server.put(f"{base}inv-1.ics", content=current.text.replace(
                    "SUMMARY:Kickoff", "SUMMARY:Kickoff (moved)"),
                    headers={"if-match": current.headers["etag"],
                             "content-type": "text/calendar; charset=utf-8"})
            return await real_request(self, method, url, **kwargs)

        httpx.AsyncClient.request = spy  # type: ignore[method-assign]
        try:
            check(await asyncio.to_thread(tools.available), "Hermes sees a calendar in the house")

            # --- an invitation is listed with attendees and the owner's answer ----------
            listed = json.loads(await asyncio.to_thread(tools.handle_events, {}))
            rows = {r["uid"]: r for r in listed.get("events", [])}
            kick = rows.get("inv-1", {})
            check(listed.get("ok") is True and "inv-1" in rows, "the invitation is listed")
            check({a["email"] for a in kick.get("attendees", [])} == {OWNER, "pat@example.test"},
                  "with its attendees")
            check((kick.get("organizer") or {}).get("email") == "boss@example.test",
                  "and its organiser")
            check(kick.get("response") == "NEEDS-ACTION", "and the owner's own answer (NEEDS-ACTION)")
            check(rows.get("lunch-1", {}).get("response") is None,
                  "an event the owner is not invited to has no answer")
            check(listed.get("trust") == "untrusted", "marked untrusted: others wrote it")

            # --- the brief lists the event ------------------------------------------------
            await jarvis.data["calendar"].publish()
            await briefing_integration.async_setup(jarvis, {})
            brief = await jarvis.services.async_call(
                "briefing", "generate", {"kind": "morning"}, blocking=True, return_response=True)
            check("Kickoff at 15:00" in brief["text"], f"the brief lists it: {brief['text']!r}")

            # --- the entities carry no stranger's words -------------------------------------
            entities = {s.entity_id: s for s in jarvis.states.all("calendar")}
            shown = json.dumps({k: [s.state, dict(s.attributes)] for k, s in entities.items()})
            check(bool(entities) and not any(w in shown for w in ("Kickoff", "Boss", "boss@", "Pat", "lunch")),
                  f"calendar.* entities hold times and counts only: {shown}")

            # --- a uid in two calendars is never guessed --------------------------------
            tags =(server.get(f"{base}dup-1.ics").headers["etag"], server.get(f"{other}dup-1.ics").headers["etag"])
            dup = {"uid": "dup-1", "response": "accept"}
            ambiguous = await asyncio.to_thread(tools.build_approval, "calendar_rsvp", dup)
            check("more than one calendar" in ambiguous["message"] and "Dup in" not in ambiguous["message"],
                  f"the prompt says it is ambiguous and names no event: {ambiguous['message']!r}")
            named = await asyncio.to_thread(tools.build_approval, "calendar_rsvp", {**dup, "calendar": "second"})
            check("Dup in second" in named["message"] and "alice/second" in named["message"],
                  f"the prompt names the event and the calendar the action will use: {named['message']!r}")
            refused_dup = json.loads(await asyncio.to_thread(tools.handle_rsvp, dup))
            check(refused_dup.get("ok") is False and "which calendar" in refused_dup.get("error", "")
                  and (server.get(f"{base}dup-1.ics").headers["etag"], server.get(f"{other}dup-1.ics").headers["etag"]) == tags,
                  "the action is refused and neither copy is written")
            chosen = json.loads(await asyncio.to_thread(tools.handle_rsvp, {**dup, "calendar": "second"}))
            check(chosen.get("ok") is True
                  and "PARTSTAT=ACCEPTED" in owner_line(server.get(f"{other}dup-1.ics").text)
                  and "PARTSTAT=NEEDS-ACTION" in owner_line(server.get(f"{base}dup-1.ics").text),
                  "with the calendar named, that copy is answered and the other is not")

            # --- refused: the owner is not invited --------------------------------------
            before = stored("lunch-1").headers["etag"]
            refused = json.loads(await asyncio.to_thread(
                tools.handle_rsvp, {"uid": "lunch-1", "response": "accept"}))
            check(refused.get("ok") is False and "not invited" in refused.get("error", ""),
                  "an RSVP to an event the owner is not invited to is refused")
            check(stored("lunch-1").headers["etag"] == before, "and nothing was written")

            # --- If-Match: a stale ETag is refused by the real server -------------------
            race["armed"] = True
            stale = json.loads(await asyncio.to_thread(
                tools.handle_rsvp, {"uid": "inv-1", "response": "tentative"}))
            check(stale.get("ok") is False and "changed" in stale.get("error", ""),
                  "an answer over a changed event is refused, not written over it")
            check("PARTSTAT=NEEDS-ACTION" in owner_line(stored("inv-1").text)
                  and "(moved)" in stored("inv-1").text,
                  "and the other client's edit stands")

            # --- the RSVP is stored as PARTSTAT=ACCEPTED, with If-Match -------------------
            etag = stored("inv-1").headers["etag"]
            sent.clear()
            answered = json.loads(await asyncio.to_thread(
                tools.handle_rsvp, {"uid": "inv-1", "response": "accept"}))
            check(answered.get("ok") is True and answered.get("response") == "ACCEPTED",
                  f"the RSVP is accepted: {answered}")
            puts = [s for s in sent if s[0] == "PUT" and s[1].endswith("inv-1.ics")]
            check(len(puts) == 1 and puts[0][2] == etag, "written with If-Match of the ETag it read")
            body = stored("inv-1").text
            check("PARTSTAT=ACCEPTED" in owner_line(body), "stored on the server as PARTSTAT=ACCEPTED")
            flat = body.replace("\r\n ", "")  # Radicale folds at 75 octets; the content is the same
            intact = ("pat@example.test" in flat and "boss@example.test" in flat
                      and "ROLE=OPT-PARTICIPANT" in flat)
            check(intact, "and everyone else's lines are intact" + ("" if intact else f": {body!r}"))

            again = json.loads(await asyncio.to_thread(tools.handle_events, {}))
            mine = next((r for r in again["events"] if r["uid"] == "inv-1"), {})
            check(mine.get("response") == "ACCEPTED", "the next read shows the owner's answer")
        finally:
            httpx.AsyncClient.request = real_request  # type: ignore[method-assign]
            service.should_exit = True
            await serving
    print("LIVE OK" if not failures else f"LIVE FAILED: {len(failures)} check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1].rstrip("/"))))
