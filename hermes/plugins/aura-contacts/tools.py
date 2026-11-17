"""CardDAV client for the contacts toolset, over the shared ``_shared._dav`` layer.

Layout assumed: address books live at ``{DAV_URL}/{CARDDAV_PATH}/{DAV_USERNAME}/``.
Nextcloud keeps them one level deeper than calendars
(``/remote.php/dav/addressbooks/users/alice/contacts/``), which is why
``CARDDAV_PATH`` is a setting and not a constant — set it to ``addressbooks/users``
there, and to the empty string on Radicale.

Searching is done here rather than by the server: an ``addressbook-query``
prop-filter is implemented inconsistently (Radicale supports a subset, Nextcloud
matches differently on ``FN`` vs ``EMAIL``), and a wrong-but-plausible server-side
match is worse than a slow client-side one. What that does *not* cover: every
card in every book is fetched and parsed per search, so this is right for a
household address book of a few hundred cards and wrong for a corporate
directory of ten thousand.
"""
from __future__ import annotations

from typing import Any

from aura_schema import from_env

from _shared import _dav

#: Rows returned when a search does not say otherwise.
DEFAULT_LIMIT: int = 20

#: PROPFIND body for address-book discovery — named properties, not allprop, so
#: Nextcloud does not send a share/ACL block per book that we would discard.
_BOOK_PROPS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
    "<d:prop><d:displayname/><d:resourcetype/></d:prop></d:propfind>"
)

#: The whole of every card in a book. ``address-data`` with no prop filter is the
#: one request every CardDAV server implements the same way.
_CARDS_QUERY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
    "<d:prop><d:getetag/><card:address-data/></d:prop></card:addressbook-query>"
)


def books_root() -> str:
    """The path holding the address books, relative to ``DAV_URL``."""
    cfg = from_env()
    return _dav.join(cfg.carddav_path, cfg.dav_username)


def _name_of(entry: dict[str, Any]) -> str:
    """An address book's display name, falling back to its last href segment."""
    props = entry.get("props") or {}
    name = str(props.get("displayname") or "").strip()
    if name:
        return name
    return (entry.get("href") or "").rstrip("/").rsplit("/", 1)[-1]


def address_books() -> list[dict[str, str]]:
    """Every address-book collection under :func:`books_root`."""
    books = []
    for entry in _dav.propfind(books_root(), depth=1, body=_BOOK_PROPS):
        props = entry.get("props") or {}
        # A depth-1 listing also returns the principal's own collection, which is
        # a collection but not an addressbook; querying it returns 403 on
        # Nextcloud and an empty multistatus on Radicale.
        if _dav.has_resourcetype(props, "addressbook"):
            books.append({"name": _name_of(entry), "href": entry.get("href", "")})
    return books


def _all_cards() -> list[dict[str, Any]]:
    """Every card in every address book, each tagged with the book it came from."""
    cards: list[dict[str, Any]] = []
    for book in address_books():
        for entry in _dav.report(book["href"], _CARDS_QUERY, depth=1):
            data = (entry.get("props") or {}).get("address-data")
            if not isinstance(data, str) or not data.strip():
                continue
            for card in _dav.parse_vcards(data):
                card["book"] = book["name"]
                card["href"] = entry.get("href", "")
                cards.append(card)
    return cards


def _matches(card: dict[str, Any], needle: str) -> bool:
    """Whether *needle* appears in the name, an address or a number.

    Case-insensitive substring, not a prefix: people search for a surname, and
    the phone number they remember is the last four digits.
    """
    haystack = " ".join([str(card.get("fn") or ""), *card.get("emails", []),
                         *card.get("tels", [])]).lower()
    return needle in haystack


def search(query: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Contacts whose name, email or phone number contains *query*."""
    if not _dav.available():
        return _dav.not_configured()
    needle = (query or "").strip().lower()
    if not needle:
        return {"ok": False, "error": "query is required (a name, email or number fragment)"}
    found = [card for card in _all_cards() if _matches(card, needle)]
    found.sort(key=lambda card: str(card.get("fn") or "").lower())
    capped = found[: max(1, int(limit or DEFAULT_LIMIT))]
    return {"ok": True, "query": query, "contacts": capped, "count": len(capped),
            "total_matched": len(found)}


def get(uid: str | None = None, name: str | None = None) -> dict[str, Any]:
    """One contact by UID, or by exact (case-insensitive) full name."""
    if not _dav.available():
        return _dav.not_configured()
    wanted_uid = (uid or "").strip()
    wanted_name = (name or "").strip().lower()
    if not wanted_uid and not wanted_name:
        return {"ok": False, "error": "pass uid or name"}
    cards = _all_cards()
    for card in cards:
        if wanted_uid and card.get("uid") == wanted_uid:
            return {"ok": True, "contact": card}
    for card in cards:
        if wanted_name and str(card.get("fn") or "").strip().lower() == wanted_name:
            return {"ok": True, "contact": card}
    # An exact-match miss is common enough (a middle name in the card) that the
    # near misses are worth returning: without them the model's next move is to
    # call contacts_search with the same string and get nothing again.
    near = [c["fn"] for c in cards
            if wanted_name and wanted_name in str(c.get("fn") or "").lower()]
    asked = f"uid {wanted_uid}" if wanted_uid else f"name {name!r}"
    return {"ok": False, "error": f"no contact with {asked}", "did_you_mean": near[:5]}


_safe = _dav.safe_json  # one wrapper for every plugin: it is also the redaction funnel

handle_search = _safe(lambda p: search(str(p.get("query") or ""),
                                       int(p.get("limit") or DEFAULT_LIMIT)))
handle_get = _safe(lambda p: get(p.get("uid"), p.get("name")))
