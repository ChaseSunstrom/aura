"""aura-contacts against a mocked CardDAV server.

Pinned here: which collections are treated as address books, that every EMAIL
line of a card survives, that matching is case-insensitive across name, email
and number, and that the plugin registers two read-only tools and no approval
hook. Not pinned: a live server — every call goes through httpx.MockTransport.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin, serve_dav, set_dav_env  # noqa: E402

plugin = load_plugin("aura-contacts")
tools = plugin.tools

#: The real class, captured before any test replaces it.
BOOKS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:response>
    <d:href>/remote.php/dav/addressbooks/users/alice/</d:href>
    <d:propstat>
      <d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/addressbooks/users/alice/contacts/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>
        <d:displayname>Contacts</d:displayname>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

# The vCards sit flush against the tag: indenting them would turn every line
# into a fold continuation and the test would pass for the wrong reason.
CARDS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:response>
    <d:href>/remote.php/dav/addressbooks/users/alice/contacts/jane.vcf</d:href>
    <d:propstat>
      <d:prop><d:getetag>"c1"</d:getetag><card:address-data>BEGIN:VCARD
VERSION:3.0
UID:card-jane
FN:Jane Doe
N:Doe;Jane;;;
EMAIL;TYPE=HOME:jane@home.test
item1.EMAIL;TYPE=WORK:j.doe@surgery.test
TEL;TYPE=CELL:+44 7700 900123
END:VCARD
</card:address-data></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/addressbooks/users/alice/contacts/plumber.vcf</d:href>
    <d:propstat>
      <d:prop><d:getetag>"c2"</d:getetag><card:address-data>BEGIN:VCARD
VERSION:3.0
UID:card-plumber
FN:Alan Wrench (plumber)
TEL;TYPE=WORK:+44 7700 900456
END:VCARD
</card:address-data></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""




@pytest.fixture
def dav(monkeypatch):
    set_dav_env(monkeypatch)
    monkeypatch.setenv("CARDDAV_PATH", "addressbooks/users")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"method": request.method, "path": request.url.path})
        if request.method == "PROPFIND":
            return httpx.Response(207, text=BOOKS)
        if request.method == "REPORT":
            return httpx.Response(207, text=CARDS)
        return httpx.Response(405, text="not allowed")

    serve_dav(monkeypatch, handler)
    return seen


def test_only_addressbook_collections_are_queried(dav):
    json.loads(tools.handle_search({"query": "jane"}))
    # The principal's own collection is a collection but not an addressbook;
    # querying it answers 403 on Nextcloud and empties the result.
    reports = [call for call in dav if call["method"] == "REPORT"]
    assert [call["path"] for call in reports] == [
        "/remote.php/dav/addressbooks/users/alice/contacts/"]
    assert dav[0]["path"] == "/remote.php/dav/addressbooks/users/alice"


def test_search_keeps_every_email_of_a_card(dav):
    out = json.loads(tools.handle_search({"query": "JANE"}))
    assert out["ok"] and out["count"] == 1
    card = out["contacts"][0]
    assert card["emails"] == ["jane@home.test", "j.doe@surgery.test"]
    assert card["book"] == "Contacts" and card["uid"] == "card-jane"


def test_search_matches_an_email_or_a_number_too(dav):
    assert json.loads(tools.handle_search({"query": "surgery.test"}))["count"] == 1
    assert json.loads(tools.handle_search({"query": "900456"}))["count"] == 1
    assert json.loads(tools.handle_search({"query": "nobody"}))["count"] == 0


def test_search_needs_a_query(dav):
    out = json.loads(tools.handle_search({"query": "  "}))
    assert out["ok"] is False and "query is required" in out["error"]


def test_get_by_uid_and_by_exact_name(dav):
    assert json.loads(tools.handle_get({"uid": "card-plumber"}))["contact"]["tels"] == [
        "+44 7700 900456"]
    assert json.loads(tools.handle_get({"name": "jane doe"}))["contact"]["uid"] == "card-jane"


def test_a_near_miss_suggests_the_names_that_exist(dav):
    out = json.loads(tools.handle_get({"name": "Alan Wrench"}))
    assert out["ok"] is False and out["did_you_mean"] == ["Alan Wrench (plumber)"]


def test_get_needs_a_uid_or_a_name(dav):
    assert json.loads(tools.handle_get({}))["ok"] is False


def test_a_failing_status_becomes_ok_false_without_the_password(monkeypatch, dav):
    serve_dav(monkeypatch, lambda r: httpx.Response(401, text="password s3cr3t-app-password bad"))
    out = tools.handle_search({"query": "jane"})
    assert json.loads(out)["ok"] is False and "401" in out
    assert "s3cr3t-app-password" not in out


def test_without_dav_url_the_tools_say_so(monkeypatch):
    monkeypatch.delenv("DAV_URL", raising=False)
    out = json.loads(tools.handle_search({"query": "jane"}))
    assert out["ok"] is False and "DAV_URL" in out["error"]


def test_register_declares_two_read_only_tools_and_no_hook():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"contacts_search", "contacts_get"}
    # No write tool means nothing to approve: a hook here would be a no-op that
    # later reads as "writes are gated" when no write exists.
    assert ctx.hooks == {}
    assert ctx.commands == {}
