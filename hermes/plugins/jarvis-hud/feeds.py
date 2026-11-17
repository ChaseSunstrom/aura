"""Feeds and the reading list: "what's new on Ars?", "save that for later", "read me the rest".

Jarvis had web_search and web_extract but nothing that remembered which headlines Sir had
already heard, so "what's new" was a fresh search every time and the same story came back
each morning. The ``feeds`` tool keeps his RSS/Atom feeds and the ids of the entries he has
been told, so ``news`` says each entry once; and a reading list for articles saved for later.
Reading one aloud is the model's job (web_extract the link, hud_say it in parts).

Only public feeds: a URL inside the house, or a redirect into it, is refused before any
request (a GET on the LAN can run an n8n webhook). Not covered: DNS that changes between the
check and the request, a feed that needs a login, podcast audio (an enclosure is skipped),
and a feed that drops entries and lists them again past the 300 it remembers (they repeat).
An entry's link into the house is blanked, since web_extract may read the LAN here.
"""
from __future__ import annotations

import html
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import httpx
from _shared import _egress
from _shared._jsonstore import err as _err, ok as _ok, read_json, write_json

try:  # refuses DTD and entity tricks outright; in the Hermes image today, not promised by it
    from defusedxml.ElementTree import fromstring as _fromstring
except ImportError:  # parse() refuses an entity declaration and a UTF-16 body before expat sees it
    _fromstring = ET.fromstring

ACTIONS = ("news", "add", "remove", "list", "save", "done")
MAX_FEEDS = 50
MAX_READING = 200
MAX_BYTES = 2 * 1024 * 1024
MAX_HOPS = 3
TIMEOUT_S = 10.0
#: All of "what's new" waits this long; a feed still reading after it goes in ``failed``.
DEADLINE_S = 15.0
#: An entry's id, link and date; a hostile feed's megabyte guid would otherwise fill the answer.
FIELD_CAP = 500
#: Ids remembered per feed; a feed only ever shows its newest few dozen.
SEEN_PER_FEED = 300
_LOCK = threading.Lock()
_URL = re.compile(r"^https?://\S+$")
_TAG = re.compile(r"<[^>]+>")
_UA = "Jarvis feed reader"


class _Refused(Exception):
    pass


#: What a fetch can fail with. InvalidURL is not an HTTPError: a Location with a bad port.
_FAILED = (_Refused, httpx.HTTPError, httpx.InvalidURL)


def _path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-feeds" / "feeds.json"


def _load() -> dict:
    data = read_json(_path())
    return {"feeds": list(data.get("feeds") or []), "seen": dict(data.get("seen") or {}),
            "reading": list(data.get("reading") or [])}


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_S, follow_redirects=False)


def _fetch(url: str) -> bytes:
    """The body at ``url``, each redirect hop checked against the house before it is asked."""
    with _client() as client:
        for _ in range(MAX_HOPS + 1):
            if _egress.private(url):
                raise _Refused(f"{url} is inside the house (or does not resolve); I only read public feeds")
            with client.stream("GET", url, headers={"User-Agent": _UA}) as r:
                if r.is_redirect:
                    url = str(r.url.join(r.headers.get("location", "")))
                    continue
                if r.status_code >= 400:
                    raise _Refused(f"{url} answered {r.status_code}")
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise _Refused(f"{url} is bigger than 2 MB; not a feed")
                return bytes(body)
    raise _Refused(f"{url} redirects more than {MAX_HOPS} times")


def _name(el: ET.Element) -> str:
    return str(el.tag).rsplit("}", 1)[-1].lower()


def _clean(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", text)).split())


def _entry(el: ET.Element) -> dict:
    fields: dict[str, str] = {}
    link = ""
    for child in el:
        name = _name(child)
        if name == "link":
            href = child.get("href")
            if href is None:  # RSS: the link is the text
                link = link or (child.text or "").strip()
            elif child.get("rel", "alternate") == "alternate":  # Atom: skip enclosures
                link = link or href
        else:
            fields.setdefault(name, "".join(child.itertext()).strip())
    summary = next((fields[k] for k in ("description", "summary", "content", "encoded") if fields.get(k)), "")
    return {"id": (fields.get("guid") or fields.get("id") or link or fields.get("title", ""))[:FIELD_CAP],
            "title": _clean(fields.get("title", ""))[:200], "link": link[:FIELD_CAP],
            "date": next((fields[k] for k in ("pubdate", "published", "updated", "date") if fields.get(k)),
                         "")[:FIELD_CAP],
            "summary": _clean(summary)[:300]}


def _public(entry: dict) -> dict:
    """The entry as told to the model: a link into the house blanked (it would be web_extract's next GET)."""
    if entry["link"] and _egress.private(entry["link"]):
        return {**entry, "link": ""}
    return entry


def parse(body: bytes) -> tuple[str, list[dict]]:
    """(feed title, entries in the feed's order). Raises _Refused when it is not RSS or Atom."""
    if b"<!ENTITY" in body:  # entity expansion is the XML bomb; no real feed declares one
        raise _Refused("the feed declares XML entities; refused")
    if b"\x00" in body:  # UTF-16 or -32, where the check above cannot see; XML never has a NUL in UTF-8
        raise _Refused("the feed is not UTF-8; refused")
    try:
        root = _fromstring(body)
    except (ET.ParseError, ValueError) as exc:  # defusedxml's refusals are ValueErrors
        raise _Refused(f"not a feed ({exc})") from None
    if _name(root) not in ("rss", "feed", "rdf"):
        raise _Refused(f"not a feed (a <{_name(root)}> document)")
    heads = [root] + [c for c in root if _name(c) == "channel"]
    title = next((_clean("".join(c.itertext())) for h in heads for c in h if _name(c) == "title"), "")
    # Newest first, as feeds list them; past SEEN_PER_FEED nothing is remembered anyway.
    return title, [_entry(el) for el in root.iter() if _name(el) in ("item", "entry")][:SEEN_PER_FEED]


def _read(url: str) -> tuple[str, list[dict]]:
    if not _URL.match(url):
        raise _Refused("a feed is an http(s) URL")
    return parse(_fetch(url))


def _news(data: dict, url: str, limit: int) -> str:
    # "What's new on Ars": a saved feed by its name as well as its URL.
    url = next((f["url"] for f in data["feeds"] if url and url == f.get("title")), url)
    saved = {f["url"]: f for f in data["feeds"]}
    if url and url not in saved:  # one look at a feed he has not saved: its newest, nothing remembered
        title, entries = _read(url)
        return _ok(entries=[{**_public(e), "feed": title} for e in entries[:limit]], failed=[])
    targets = [saved[url]] if url else list(saved.values())
    if not targets:
        return _ok(entries=[], failed=[])
    # Read together under one deadline: one after another, two dead feeds held a voice turn 20 s.
    pool = ThreadPoolExecutor(max_workers=min(8, len(targets)))
    jobs = {pool.submit(_read, f["url"]): f for f in targets}
    finished, _late = wait(jobs, timeout=DEADLINE_S)
    pool.shutdown(wait=False, cancel_futures=True)
    read, failed = [], []
    for job, feed in jobs.items():
        if job not in finished:
            failed.append({"url": feed["url"], "error": f"no answer in {DEADLINE_S:.0f} s"})
            continue
        try:
            read.append((feed, *job.result()))
        except _FAILED as exc:
            failed.append({"url": feed["url"], "error": str(exc)})
    per = max(1, limit // len(targets))
    found = []
    # Filter against what is seen NOW, under the lock: two devices asking together, or a remove
    # landing mid-fetch, otherwise said an entry twice or brought a removed feed's list back.
    with _LOCK:
        latest = _load()
        still = {f["url"] for f in latest["feeds"]}
        current = {}
        for feed, title, entries in read:
            if feed["url"] not in still:
                continue
            current[feed["url"]] = {e["id"] for e in entries}
            seen = set(latest["seen"].get(feed["url"], []))
            fresh = [e for e in entries if e["id"] not in seen][:per]
            found += [{**e, "feed": feed.get("title") or title, "_url": feed["url"]} for e in fresh]
        found = found[:limit]
        for e in found:
            url_ = e.pop("_url")
            ids = latest["seen"].setdefault(url_, [])
            ids.append(e["id"])
            # Past the cap, forget only what the feed no longer lists: dropping the oldest-stored
            # forgot add's newest entry, and it was said again (parse() lists at most the cap).
            if len(ids) > SEEN_PER_FEED:
                ids[:] = [i for i in ids if i in current[url_]]
        if found:
            write_json(_path(), latest)
    return _ok(entries=[_public(e) for e in found], failed=failed)


def handle_feeds(args: dict, **_: Any) -> str:
    action = str(args.get("action") or "").strip()
    url = str(args.get("url") or "").strip()
    try:
        limit = max(1, min(30, int(args.get("limit") or 10)))
    except (TypeError, ValueError):
        limit = 10
    data = _load()
    try:
        if action == "news":
            return _news(data, url, limit)
        if action == "add":
            if len(data["feeds"]) >= MAX_FEEDS:
                return _err(f"already {MAX_FEEDS} feeds; remove one first")
            title, entries = _read(url)
            feed = {"url": url, "title": str(args.get("title") or title or url)[:120],
                    "added": time.strftime("%Y-%m-%d")}
            with _LOCK:
                latest = _load()
                latest["feeds"] = [f for f in latest["feeds"] if f["url"] != url] + [feed]
                # What is in the feed now counts as heard: the next "news" is only what is new.
                latest["seen"][url] = [e["id"] for e in entries]
                write_json(_path(), latest)
            return _ok(feed=feed, latest=[_public(e) for e in entries[:3]])
        if action == "remove":
            with _LOCK:
                latest = _load()
                kept = [f for f in latest["feeds"] if url not in (f["url"], f.get("title"))]
                if len(kept) == len(latest["feeds"]):
                    return _err(f"no saved feed {url!r}; feeds list shows them")
                gone = {f["url"] for f in latest["feeds"]} - {f["url"] for f in kept}
                latest["feeds"] = kept
                for u in gone:
                    latest["seen"].pop(u, None)
                write_json(_path(), latest)
            return _ok(removed=sorted(gone))
        if action == "list":
            return _ok(feeds=data["feeds"], reading=data["reading"])
        if action == "save":
            if not _URL.match(url):
                return _err("save needs the article's http(s) url")
            with _LOCK:
                latest = _load()
                if not any(r["url"] == url for r in latest["reading"]):
                    if len(latest["reading"]) >= MAX_READING:
                        return _err(f"the reading list has {MAX_READING} articles; mark some done")
                    latest["reading"].append({"url": url, "title": str(args.get("title") or "")[:200],
                                              "saved": time.strftime("%Y-%m-%d")})
                    write_json(_path(), latest)
            return _ok(reading=len(latest["reading"]))
        if action == "done":
            with _LOCK:
                latest = _load()
                kept = [r for r in latest["reading"] if r["url"] != url]
                if len(kept) == len(latest["reading"]):
                    return _err(f"{url!r} is not on the reading list")
                latest["reading"] = kept
                write_json(_path(), latest)
            return _ok(reading=len(kept))
    except _Refused as exc:
        return _err(str(exc))
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return _err(f"{url} could not be fetched: {exc}")
    return _err(f"unknown action {action!r}; one of {', '.join(ACTIONS)}")
