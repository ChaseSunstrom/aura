"""Feeds and the reading list (aura-hud/feeds.py): "what's new on Ars", "save that for later".

Jarvis had web_search and web_extract but nothing that remembered which headlines Sir had
already heard, so "what's new" was a fresh search each time and the car's radio show made
its headlines up from the search engine's front page. No network: every fetch goes through
a MockTransport, and "is this inside the house" is a fixed list.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

feeds = load_plugin("aura-hud").feeds

ARS = "https://feeds.example.com/ars.rss"
VERGE = "https://example.org/verge.atom"


def rss(*items: tuple[str, str]) -> str:
    body = "".join(
        f"<item><title>{t}</title><link>https://example.com/{g}</link><guid>{g}</guid>"
        f"<description>&lt;p&gt;About {t} &amp;amp; more&lt;/p&gt;</description></item>"
        for g, t in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Ars</title>{body}</channel></rss>'


ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>The Verge</title>
<entry><title>Phones</title><id>tag:verge,1</id><updated>2026-10-29T10:00:00Z</updated>
<link rel="alternate" href="https://example.org/phones"/><link rel="enclosure" href="https://example.org/a.mp3"/>
<summary type="html">&lt;b&gt;New&lt;/b&gt; phones</summary></entry></feed>"""


@pytest.fixture
def web(tmp_path, monkeypatch):
    """pages: url -> (status, body, headers); requested: every URL fetched, in order."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pages: dict = {}
    requested: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        status, body, headers = pages.get(str(request.url), (404, "", {}))
        return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode(),
                              headers=headers)

    monkeypatch.setattr(feeds, "_client", lambda: httpx.Client(transport=httpx.MockTransport(answer)))
    monkeypatch.setattr(feeds._egress, "private",
                        lambda url: any(h in url for h in ("192.168.", "//10.", ".local", "169.254.")))
    return pages, requested


def call(args: dict) -> dict:
    return json.loads(feeds.handle_feeds(args))


def test_news_says_each_entry_once(web):
    pages, _ = web
    pages[ARS] = (200, rss(("a1", "Chips"), ("a2", "Rockets")), {})
    added = call({"action": "add", "url": ARS})
    assert added["ok"] and added["feed"]["title"] == "Ars"
    assert [e["title"] for e in added["latest"]] == ["Chips", "Rockets"]

    assert call({"action": "news"})["entries"] == []  # adding it counted as hearing them

    pages[ARS] = (200, rss(("a3", "Fusion"), ("a1", "Chips"), ("a2", "Rockets")), {})
    news = call({"action": "news"})
    assert [(e["title"], e["feed"]) for e in news["entries"]] == [("Fusion", "Ars")]
    assert news["entries"][0]["link"] == "https://example.com/a3"
    assert news["entries"][0]["summary"] == "About Fusion & more"
    assert call({"action": "news"})["entries"] == []


def test_atom_takes_the_article_link_not_the_enclosure(web):
    pages, _ = web
    pages[VERGE] = (200, ATOM, {})
    entry = call({"action": "news", "url": VERGE})["entries"][0]
    assert entry["link"] == "https://example.org/phones"
    assert entry["summary"] == "New phones"
    assert entry["date"] == "2026-10-29T10:00:00Z"
    # One feed asked for by URL is a look, not a subscription: it is not saved.
    assert call({"action": "list"})["feeds"] == []


def test_news_beyond_the_limit_waits_for_next_time(web):
    pages, _ = web
    pages[ARS] = (200, rss(("a0", "Old")), {})
    call({"action": "add", "url": ARS})
    pages[ARS] = (200, rss(*[(f"n{i}", f"Story {i}") for i in range(5)]), {})
    assert len(call({"action": "news", "limit": 3})["entries"]) == 3
    assert [e["title"] for e in call({"action": "news"})["entries"]] == ["Story 3", "Story 4"]


@pytest.mark.parametrize("url", ["http://10.20.30.59:8080/feed", "http://homeassistant.local/rss",
                                 "http://169.254.169.254/latest/meta-data"])
def test_a_house_url_is_never_fetched(web, url):
    _, requested = web
    assert not call({"action": "add", "url": url})["ok"]
    assert not call({"action": "news", "url": url})["ok"]
    assert requested == []


def test_a_redirect_into_the_house_is_not_followed(web):
    pages, requested = web
    pages[ARS] = (302, "", {"location": "http://10.20.30.59:5678/webhook/run"})
    out = call({"action": "add", "url": ARS})
    assert not out["ok"] and "inside the house" in out["error"]
    assert requested == [ARS]


def test_a_public_redirect_is_followed(web):
    pages, _ = web
    pages[ARS] = (301, "", {"location": VERGE})
    pages[VERGE] = (200, ATOM, {})
    assert call({"action": "add", "url": ARS})["feed"]["title"] == "The Verge"


def test_an_entity_declaration_or_an_oversized_feed_is_refused(web):
    pages, _ = web
    bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><title>&a;</title></channel></rss>'
    pages[ARS] = (200, bomb, {})
    assert not call({"action": "add", "url": ARS})["ok"]
    pages[ARS] = (200, b"<rss>" + b" " * (feeds.MAX_BYTES + 1), {})
    assert "2 MB" in call({"action": "add", "url": ARS})["error"]
    pages[ARS] = (200, "<html><body>not a feed</body></html>", {})
    assert not call({"action": "add", "url": ARS})["ok"]
    assert call({"action": "list"})["feeds"] == []


def test_one_broken_feed_does_not_hide_the_others(web):
    pages, _ = web
    pages[ARS] = (200, rss(("a1", "Chips")), {})
    pages[VERGE] = (200, ATOM.replace("tag:verge,1", "tag:verge,0"), {})
    call({"action": "add", "url": ARS})
    call({"action": "add", "url": VERGE})
    pages[ARS] = (500, "", {})
    pages[VERGE] = (200, ATOM, {})
    news = call({"action": "news"})
    assert [e["title"] for e in news["entries"]] == ["Phones"]
    assert [f["url"] for f in news["failed"]] == [ARS]


def test_remove_forgets_the_feed(web):
    pages, _ = web
    pages[ARS] = (200, rss(("a1", "Chips")), {})
    call({"action": "add", "url": ARS})
    assert call({"action": "remove", "url": ARS})["ok"]
    assert call({"action": "list"})["feeds"] == []
    assert not call({"action": "remove", "url": ARS})["ok"]


def test_the_reading_list_keeps_an_article_until_it_is_read(web):
    _, requested = web
    art = "https://example.com/long-read"
    assert call({"action": "save", "url": art, "title": "A long read"})["ok"]
    assert call({"action": "save", "url": art})["ok"]  # saved twice is still one
    reading = call({"action": "list"})["reading"]
    assert [(r["url"], r["title"]) for r in reading] == [(art, "A long read")]
    assert call({"action": "done", "url": art})["ok"]
    assert call({"action": "list"})["reading"] == []
    assert not call({"action": "save", "url": "not a url"})["ok"]
    assert requested == []  # saving is a note, not a fetch


def test_an_unknown_action_is_an_error(web):
    assert not call({"action": "subscribe"})["ok"]


# The review's findings (T-0436): each of these failed before the fix.

def test_a_redirect_to_a_bad_port_is_an_error_not_a_crash(web):
    """httpx.InvalidURL is not an HTTPError: one bad Location used to raise out of the tool
    and, in news, lose every other feed's headlines with it."""
    pages, _ = web
    pages[ARS] = (200, rss(("a1", "Chips")), {})
    pages[VERGE] = (200, ATOM, {})
    call({"action": "add", "url": ARS})
    call({"action": "add", "url": VERGE})
    pages[ARS] = (301, "", {"location": "http://example.com:abc/feed"})
    pages[VERGE] = (200, ATOM.replace("tag:verge,1", "tag:verge,2"), {})
    news = call({"action": "news"})
    assert [e["title"] for e in news["entries"]] == ["Phones"]
    assert [f["url"] for f in news["failed"]] == [ARS]
    assert not call({"action": "add", "url": "http://example.com:abc/feed"})["ok"]


def test_a_utf16_feed_cannot_hide_an_entity_declaration(web, monkeypatch):
    """The byte check for <!ENTITY is UTF-8; without defusedxml, expat reads UTF-16 too."""
    pages, _ = web
    monkeypatch.setattr(feeds, "_fromstring", feeds.ET.fromstring)
    bomb = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><title>&a;</title></channel></rss>'
    pages[ARS] = (200, bomb.encode("utf-16"), {})
    assert not call({"action": "add", "url": ARS})["ok"]


def test_a_dead_feed_cannot_hold_the_news_past_the_deadline(web, monkeypatch):
    """Feeds used to be read one after another with no overall deadline: two dead ones held a
    voice turn for twenty seconds."""
    import threading
    import time

    pages, _ = web
    pages[ARS] = (200, rss(("a1", "Chips")), {})
    pages[VERGE] = (200, ATOM.replace("tag:verge,1", "tag:verge,0"), {})
    call({"action": "add", "url": ARS})
    call({"action": "add", "url": VERGE})
    pages[VERGE] = (200, ATOM, {})
    release = threading.Event()
    real = feeds._client

    def hanging() -> httpx.Client:
        client = real()
        transport = client._transport

        def slow(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ARS:
                release.wait(5)
            return transport.handle_request(request)

        return httpx.Client(transport=httpx.MockTransport(slow))

    monkeypatch.setattr(feeds, "_client", hanging)
    monkeypatch.setattr(feeds, "DEADLINE_S", 0.3)
    started = time.monotonic()
    try:
        news = call({"action": "news"})
    finally:
        release.set()
    assert time.monotonic() - started < 2
    assert [e["title"] for e in news["entries"]] == ["Phones"]
    assert [f["url"] for f in news["failed"]] == [ARS]


def test_an_entry_cannot_send_jarvis_into_the_house_or_flood_the_answer(web):
    """An entry's link goes to web_extract, which may read the LAN here: a link into the house
    is blanked. Its id, link and date are capped like its title."""
    pages, _ = web
    long_id = "g" * 5000
    body = rss(("ok", "Fine")).replace(
        "<item>", f"<item><title>Webhook</title><link>http://10.20.30.59:5678/webhook/x</link>"
        f"<guid>{long_id}</guid></item><item>", 1)
    pages[ARS] = (200, body, {})
    latest = call({"action": "add", "url": ARS})["latest"]
    assert latest[0]["link"] == ""
    assert len(latest[0]["id"]) <= 500
    assert latest[1]["link"] == "https://example.com/ok"


def test_adding_a_long_feed_counts_its_newest_entries_as_heard(web):
    """Feeds list newest first; add kept the last 300 ids, the oldest, so the newest came back."""
    pages, _ = web
    pages[ARS] = (200, rss(*[(f"g{i}", f"Story {i}") for i in range(320)]), {})
    call({"action": "add", "url": ARS})
    assert call({"action": "news"})["entries"] == []
    pages[ARS] = (200, rss(*[(f"g{i}", f"Story {i}") for i in range(-1, 320)]), {})
    assert [e["id"] for e in call({"action": "news"})["entries"]] == ["g-1"]
    assert call({"action": "news"})["entries"] == []


def test_news_takes_a_saved_feed_by_its_name(web):
    pages, _ = web
    pages[ARS] = (200, rss(("a1", "Chips")), {})
    call({"action": "add", "url": ARS})
    pages[ARS] = (200, rss(("a2", "Rockets"), ("a1", "Chips")), {})
    assert [e["title"] for e in call({"action": "news", "url": "Ars"})["entries"]] == ["Rockets"]


def test_two_news_calls_at_once_say_a_headline_once(web, monkeypatch):
    """The phone and the car asking together both said the same entries: news filtered against
    what it loaded before fetching, and marked them seen afterwards without looking again."""
    pages, _ = web
    pages[ARS] = (200, rss(("a1", "Chips")), {})
    call({"action": "add", "url": ARS})
    pages[ARS] = (200, rss(("a2", "Rockets"), ("a1", "Chips")), {})
    real = feeds._client
    inner: list = []

    def racing() -> httpx.Client:
        client = real()
        transport = client._transport

        def answer(request: httpx.Request) -> httpx.Response:
            if not inner:  # the other device's call lands while this one is fetching
                inner.append(None)
                inner[0] = call({"action": "news"})
            return transport.handle_request(request)

        return httpx.Client(transport=httpx.MockTransport(answer))

    monkeypatch.setattr(feeds, "_client", racing)
    outer = call({"action": "news"})
    said = [e["title"] for e in inner[0]["entries"] + outer["entries"]]
    assert said == ["Rockets"]
