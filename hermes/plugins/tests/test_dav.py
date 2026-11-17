"""The shared DAV layer: URLs, multistatus XML, iCalendar/vCard, error funnelling.

The bodies below are the shapes Nextcloud (SabreDAV) and Radicale actually send,
kept verbatim rather than minimised: the failures this module exists to prevent —
a property from a 404 propstat read as an empty value, a folded FN truncated —
are all invisible in a tidied-up sample. (The iCalendar reader is jarvis-core's now:
`aura-core/tests/test_calendar_invitations.py`.)

Not covered here: a live server. Every request goes through httpx.MockTransport,
so this pins the parsing and the request shape, not the deployment.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _plugin_loader import (  # noqa: E402
    FakeCtx,
    forget_hermes_plugins,
    load_like_hermes,
    scrub_sys_path,
    serve_dav,
    set_dav_env,
)
from _shared import _dav  # noqa: E402

PLUGINS_DIR = Path(__file__).resolve().parents[1]

#: A Nextcloud depth-1 PROPFIND of a principal's calendar home. The first
#: response is the home collection itself (not a calendar); the second carries a
#: second propstat listing the properties the server does *not* have.
CALENDAR_HOME = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns"
               xmlns:cal="urn:ietf:params:xml:ns:caldav"
               xmlns:cs="http://calendarserver.org/ns/">
  <d:response>
    <d:href>/remote.php/dav/calendars/alice/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:displayname>alice</d:displayname>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
    <d:propstat>
      <d:prop><cal:supported-calendar-component-set/><cs:getctag/></d:prop>
      <d:status>HTTP/1.1 404 Not Found</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/alice/personal/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
        <d:displayname>Personal</d:displayname>
        <cal:supported-calendar-component-set>
          <cal:comp name="VEVENT"/>
          <cal:comp name="VTODO"/>
        </cal:supported-calendar-component-set>
        <cs:getctag>http://sabre.io/ns/sync/29</cs:getctag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

#: A vCard with two EMAIL lines, an Apple group prefix and an escaped comma.
TWO_EMAIL_CARD = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "UID:card-0001\r\n"
    "FN:Jane Doe\\, MD\r\n"
    "N:Doe;Jane;;;\r\n"
    "EMAIL;TYPE=HOME:jane@home.test\r\n"
    "item1.EMAIL;TYPE=WORK:j.doe@surgery.test\r\n"
    "TEL;TYPE=CELL:+44 7700 900123\r\n"
    "END:VCARD\r\n"
)


#: What a reverse proxy in front of the DAV server answers with when the session
#: has expired: HTML, not multistatus, and unparseable as XML.
LOGIN_PAGE = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
              "<body>Sign in<br>to continue</body></html>")


@pytest.fixture
def dav_env(monkeypatch):
    set_dav_env(monkeypatch)
    return None


@pytest.fixture
def transport(monkeypatch, dav_env):
    """Replace httpx.Client with one whose transport records and answers requests."""
    del dav_env  # depended on for its env vars, not its value (usefixtures cannot chain)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"method": request.method, "path": request.url.path,
                     "depth": request.headers.get("depth"),
                     "auth": request.headers.get("authorization", ""),
                     "body": request.content.decode("utf-8") if request.content else ""})
        if request.url.path.endswith("/boom"):
            return httpx.Response(403, text="Forbidden for s3cr3t-app-password")
        return httpx.Response(207, text=CALENDAR_HOME)

    serve_dav(monkeypatch, handler)
    return seen


# --------------------------------------------------------------------------- #
# URLs and configuration
# --------------------------------------------------------------------------- #
def test_available_needs_only_the_base_url(monkeypatch):
    monkeypatch.delenv("DAV_URL", raising=False)
    assert _dav.available() is False
    monkeypatch.setenv("DAV_URL", "https://cloud.test/remote.php/dav")
    assert _dav.available() is True


@pytest.mark.usefixtures("dav_env")
def test_url_for_href_does_not_repeat_the_base_path():
    # The bug this pins: joining a server-absolute href onto DAV_URL's path asks
    # for /remote.php/dav/remote.php/dav/... and 404s on every listed resource.
    assert _dav.url_for("calendars/alice/") == "https://cloud.test/remote.php/dav/calendars/alice/"
    assert (_dav.url_for("/remote.php/dav/calendars/alice/personal/")
            == "https://cloud.test/remote.php/dav/calendars/alice/personal/")
    assert (_dav.url_for("https://cloud.test/remote.php/dav/x")
            == "https://cloud.test/remote.php/dav/x")
    # A server-named link to another host would carry the DAV password there (T-1374).
    with pytest.raises(ValueError):
        _dav.url_for("https://other.test/x")


@pytest.mark.parametrize("bad", ["http://cloud.example.test/dav", "https://me:pw@cloud.test/dav",
                                 "http://169.254.169.254/dav"])
def test_a_wrong_dav_address_is_refused_before_any_request(monkeypatch, bad):
    set_dav_env(monkeypatch)
    monkeypatch.setenv("DAV_URL", bad)
    sent = []
    serve_dav(monkeypatch, lambda r: sent.append(r) or httpx.Response(207, text=CALENDAR_HOME))
    out = _dav.safe_result(lambda params: _dav.propfind("calendars"))
    assert out["ok"] is False and "DAV_URL" in out["error"] and "pw" not in out["error"]
    assert sent == []


def test_a_hostile_href_to_another_host_sends_no_credentials(transport):
    out = _dav.safe_result(lambda params: _dav.request("GET", "https://evil.example.test/steal"))
    assert out["ok"] is False and transport == []


def test_join_drops_empty_and_duplicated_separators():
    assert _dav.join("/calendars/", "", "alice") == "calendars/alice"
    assert _dav.join("", "") == ""


@pytest.mark.usefixtures("dav_env")
def test_redact_removes_userinfo_and_the_password():
    leaked = "GET https://alice:s3cr3t-app-password@cloud.test/x failed (s3cr3t-app-password)"
    cleaned = _dav.redact(leaked)
    assert "s3cr3t-app-password" not in cleaned
    assert "cloud.test/x" in cleaned


# --------------------------------------------------------------------------- #
# HTTP shape
# --------------------------------------------------------------------------- #
def test_propfind_sends_the_method_depth_and_basic_auth(transport):
    rows = _dav.propfind("calendars/alice", depth=1)
    assert [r["href"] for r in rows] == ["/remote.php/dav/calendars/alice/",
                                         "/remote.php/dav/calendars/alice/personal/"]
    call = transport[0]
    assert call["method"] == "PROPFIND" and call["depth"] == "1"
    assert call["path"] == "/remote.php/dav/calendars/alice"
    assert call["auth"].startswith("Basic ")


def test_report_sends_the_report_method_and_the_body(transport):
    _dav.report("/remote.php/dav/calendars/alice/personal/", "<c:calendar-query/>", depth=1)
    call = transport[0]
    assert call["method"] == "REPORT" and call["body"] == "<c:calendar-query/>"
    assert call["path"] == "/remote.php/dav/calendars/alice/personal/"


def test_a_failing_status_becomes_ok_false_without_the_password(transport):
    handler = _dav.safe_json(lambda params: _dav.propfind("boom"))
    out = handler({})
    assert '"ok": false' in out
    assert "403" in out
    assert "s3cr3t-app-password" not in out


@pytest.mark.usefixtures("dav_env")
def test_a_non_xml_body_becomes_ok_false(monkeypatch):
    # A reverse proxy answering with an HTML login page is the real case here.
    serve_dav(monkeypatch, lambda r: httpx.Response(207, text=LOGIN_PAGE))
    out = _dav.safe_json(lambda params: _dav.propfind("calendars"))({})
    assert '"ok": false' in out and "ParseError" in out


# --------------------------------------------------------------------------- #
# Multistatus parsing
# --------------------------------------------------------------------------- #
def test_multistatus_parses_hrefs_props_and_resourcetypes():
    rows = _dav.parse_multistatus(CALENDAR_HOME)
    assert len(rows) == 2
    home, personal = rows
    assert home["props"]["displayname"] == "alice"
    assert _dav.is_collection(home["props"]) is True
    assert _dav.has_resourcetype(home["props"], "calendar") is False
    assert _dav.has_resourcetype(personal["props"], "calendar") is True
    assert personal["props"]["getctag"] == "http://sabre.io/ns/sync/29"


def test_props_from_a_404_propstat_are_absent_not_empty():
    # Keeping them would hand callers "" for a property the server does not have,
    # which reads as a real value and makes a missing ctag look like a fresh sync.
    home = _dav.parse_multistatus(CALENDAR_HOME)[0]
    assert "getctag" not in home["props"]
    assert "supported-calendar-component-set" not in home["props"]


def test_repeated_child_elements_keep_their_attributes():
    personal = _dav.parse_multistatus(CALENDAR_HOME)[1]
    comps = personal["props"]["supported-calendar-component-set"]["comp"]
    assert comps == [{"name": "VEVENT"}, {"name": "VTODO"}]


# --------------------------------------------------------------------------- #
# iCalendar / vCard
# --------------------------------------------------------------------------- #
def test_a_folded_line_is_rejoined():
    assert _dav.unfold("FN:Jane Doe, a very long\r\n  name\r\nUID:1\r\n") == [
        "FN:Jane Doe, a very long name", "UID:1"]


def test_a_vcard_keeps_every_email_including_a_grouped_one():
    card = _dav.parse_vcards(TWO_EMAIL_CARD)[0]
    assert card["emails"] == ["jane@home.test", "j.doe@surgery.test"]
    assert card["tels"] == ["+44 7700 900123"]
    assert card["fn"] == "Jane Doe, MD" and card["uid"] == "card-0001"


def test_a_vcard_without_fn_is_named_from_n():
    card = _dav.parse_vcards("BEGIN:VCARD\r\nN:Bloggs;Joe;;;\r\nEND:VCARD\r\n")[0]
    assert card["fn"] == "Joe Bloggs"


def test_unescape_undoes_text_escaping():
    assert _dav.unescape("Lunch\\, drinks\\; then\\nhome") == "Lunch, drinks; then\nhome"


# --------------------------------------------------------------------------- #
# The three plugins must reach _shared the way Hermes loads them
# --------------------------------------------------------------------------- #
# Hermes imports a plugin directory as ``hermes_plugins.<slug>`` with an empty
# namespace parent, so there is no package route to a sibling directory: a plugin
# that forgot its sys.path shim works under the test loader (the plugins root is
# already on sys.path there) and breaks in the gateway. Same scrub as
# tests/test_plugin_loader.py, for the plugins that import from ``_shared`` (the
# calendar through ``_house``, the others through ``_dav``).
DAV_PLUGINS = ("jarvis-calendar", "jarvis-contacts", "jarvis-files")


@pytest.fixture
def scrubbed_sys_path(monkeypatch):
    scrub_sys_path(monkeypatch)
    yield
    forget_hermes_plugins()


@pytest.mark.parametrize("dirname", DAV_PLUGINS)
@pytest.mark.usefixtures("scrubbed_sys_path")
def test_a_dav_plugin_loads_the_way_hermes_loads_it(dirname: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("_shared._dav")  # the scrub is real
    module = load_like_hermes(dirname)
    ctx = FakeCtx()
    module.register(ctx)
    assert ctx.tools, f"{dirname} registered no tools"
    for handler in ctx.commands.values():
        # A session-prefixed command with no DAV configured must still answer.
        assert isinstance(handler("@s"), str)
