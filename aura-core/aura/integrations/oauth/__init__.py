"""oauth — connect the owner's accounts (Google, GitHub, Microsoft 365, Slack, Discord,
GitLab, Spotify, Notion, Todoist) with real OAuth.

    oauth:
      redirect_base: https://console.example      # the console's origin
      google: {client_id: ..., client_secret: ...}
      github: {client_id: ..., client_secret: ...}
      gitlab: {client_id: ..., client_secret: ..., base_url: https://gitlab.example.test}
      # likewise microsoft, slack, discord, spotify, notion, todoist

The authorization-code flow with PKCE (S256) and a single-use `state`. The
provider sends the browser back to ``<redirect_base>/api/oauth/callback``; the
console relays that one GET to the house (``jarvis-web``'s
``routes/api/oauth/callback``), and :func:`callback` finishes the exchange.

## Tokens

Encrypted at rest with Fernet (``cryptography``); the key is
``<config>/.storage/oauth.key``, created 0600 on first use. The key and the
tokens are never logged and no service, route or return value carries either:
what leaves this module is a status, a scope list and the test call's result.

What encryption does **not** cover: anything that can read both files (root, a
backup of the whole ``.storage`` directory) has the tokens. It keeps a stray
copy of one file, or a grep, from being a credential leak.

## Refresh and re-consent

An access token is refreshed one minute before it expires. When the provider
refuses the refresh (Google expires refresh tokens after 7 days for an app left
in *Testing*, GAPS G16, and on revocation) the account becomes
``needs_consent``, Jarvis says so on the device in use **once**, and nothing
calls the provider again until the owner consents: a refresh is attempted at
most once per call, and never from a loop. A 5xx or a dead network is not a
verdict on the account, so it stays ``connected`` and the caller sees an error.

## Untrusted input

Everything a provider returns is untrusted. Nothing it writes is passed on: the
test call reports the HTTP status and a name this module chose; an
``error`` from a callback is shown only if it is a short lower-case code from
the OAuth spec. Bodies are capped (:data:`MAX_BODY`), every call has a timeout,
and redirects are not followed.

## Named reads

``oauth.read`` runs one of the ``reads`` in a provider's row (T-1341): a GET to that provider's
fixed host (GitLab: the checked base), with the one search text and the row count as the only
things a caller supplies, and the answer cut to the row's keep-list. The token stays here: no
caller names an address, and a read with no row is refused. Every read stays inside the scopes
the row already asks for. What it does **not** cover: the text it returns is the provider's,
so whoever shows it to a model must mark it as outside text; none of the reads has run
against a real provider (GAPS G18).

## Named writes

``oauth.write`` (T-1370) is the one write on a connected account: Todoist ``add_task``. Like a
read it is a row in the provider's table (``writes``): a fixed URL, POST, three field names with
a type and a length each, checked here before a request is made. The account must have been
consented with the row's write scope, which this module records at consent (Todoist's token
answer does not name one); an account from before that, or one connected read-only, gets a fixed
"reconnect" sentence and no request. The Hermes side asks the owner every time. What it does
**not** cover: whether a timed-out request landed, and the scope and path names, which are
guesses about Todoist that no real provider has confirmed (GAPS G18).

Services: ``oauth.start`` (provider) · ``oauth.status`` · ``oauth.test`` ·
``oauth.revoke`` · ``oauth.read`` (provider, what, query, limit) ·
``oauth.write`` (provider, action, content, due, description).
"""

from __future__ import annotations

import asyncio
import ipaddress
import base64
import hashlib
import html
import json
import logging
import math
import os
import re
import secrets
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from ...const import EVENT_JARVIS_START
from .._schedule import DailyTask

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "oauth"
DEPENDENCIES = ["companion"]

#: A consent link is good for ten minutes; at most this many are open at once,
#: so a loop of `oauth.start` cannot grow the table.
PENDING_TTL = 600
MAX_PENDING = 20
#: A provider's answer is read to this many bytes and no further.
MAX_BODY = 64 * 1024
TIMEOUT = httpx.Timeout(10.0)
#: Refresh this long before the token runs out.
SKEW = 60
CALLBACK_PATH = "/api/oauth/callback"
#: What a named read hands back: at most this many rows (the default when the caller names no
#: limit), each string clipped, the whole answer under READ_BYTES once written as JSON.
READ_ROWS = 25
READ_DEFAULT = 10
READ_CLIP = 120
READ_BYTES = 8000
#: A list read's answer is read to this many bytes: providers send whole objects (a
#: repository is several kB), and a body cut at MAX_BODY would be unreadable JSON.
READ_BODY = 512 * 1024
#: What ``_http(mark_cut=True)`` returns for a body that ran past its cap and is not JSON.
_CUT: Any = object()
#: A read's one free-text parameter (a search) is cut to this many characters.
READ_QUERY_MAX = 80
#: A number outside this is a provider's mistake or an attack, not a count to relay.
_READ_INT_MAX = 2 ** 53

_CODE_RE = re.compile(r"^[a-z_]{1,40}$")

#: The provider table: endpoints, scopes, and one read-only test call per scope.
#: Google has no scope that drafts without sending (`gmail.compose` does both);
#: the never-send promise is this house's — no tool here or in the plugin sends.
PROVIDERS: dict[str, dict[str, Any]] = {
    "google": {
        "label": "Google",
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "revoke": "https://oauth2.googleapis.com/revoke",
        "scopes": [
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/contacts.readonly",
        ],
        # offline + consent is what makes Google hand out a refresh token.
        "auth_extra": {"access_type": "offline", "prompt": "consent"},
        "tests": [
            ("calendar", "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1"),
            ("gmail", "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
            ("contacts", "https://people.googleapis.com/v1/people/me/connections"
                         "?personFields=names&pageSize=1"),
        ],
        "reads": {
            "calendars": {
                "about": "the calendars the owner can see",
                "url": "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                "limit": ("maxResults", 250), "rows": "items",
                "keep": {"name": "summary", "primary": "primary", "access": "accessRole"}},
            "events": {
                "about": "upcoming events on the main calendar, next 14 days; query searches them",
                "url": "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                "q": {"singleEvents": "true", "orderBy": "startTime"},
                "window": ("timeMin", "timeMax", 14), "query": "q",
                "limit": ("maxResults", 250), "rows": "items",
                "keep": {"title": "summary", "start": "start.dateTime", "start_date": "start.date",
                         "end": "end.dateTime", "where": "location"}},
            "contacts": {
                "about": "contact names",
                "url": "https://people.googleapis.com/v1/people/me/connections",
                "q": {"personFields": "names"}, "limit": ("pageSize", 1000), "rows": "connections",
                "keep": {"name": "names.displayName"}},
        },
    },
    "github": {
        "label": "GitHub",
        "auth": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        # `repo` is GitHub's only scope for private repositories, and it is read-write;
        # nothing here writes to one. `notifications` reads and marks as read.
        "revoke": "https://api.github.com/applications/{client_id}/grant",
        "scopes": ["repo", "notifications"],
        "auth_extra": {},
        "tests": [
            ("repos", "https://api.github.com/user/repos?per_page=1"),
            ("notifications", "https://api.github.com/notifications?per_page=1"),
        ],
        "reads": {
            "repos": {
                "about": "the repositories the owner can reach, most recently pushed first",
                "url": "https://api.github.com/user/repos", "q": {"sort": "pushed"},
                "limit": ("per_page", 100), "rows": "",
                "keep": {"name": "full_name", "private": "private", "language": "language",
                         "description": "description", "pushed": "pushed_at"}},
            "notifications": {
                "about": "unread and recent notifications",
                "url": "https://api.github.com/notifications", "limit": ("per_page", 100), "rows": "",
                "keep": {"title": "subject.title", "kind": "subject.type",
                         "repo": "repository.full_name", "reason": "reason", "unread": "unread",
                         "updated": "updated_at"}},
        },
        "revoke_mode": "github",
    },
    # --- T-1338: the rest of the table. Keys beyond the four above, each only where the
    # provider differs from the plain flow (code + PKCE, client in the body, form-encoded,
    # Bearer calls, a 4xx on refusal): `scope_param` (the query name for the scopes),
    # `pkce: False` (a server that validates strictly), `token_auth: "basic"` and
    # `token_json` (how the exchange wants the client and its body), `token_in` (the object a
    # token comes wrapped in), `ok_body` (a server that answers 200 and says no in `ok`; the
    # set is the errors that mean the token is dead), `headers` (sent on the test calls),
    # `base` (a self-hosted address: its settings key and default, replacing `{base}` in
    # every URL), `refresh_redirect`, and `revoke_mode`. Every scope here is a read: none of
    # the seven is asked for a write, and no tool here or in the plugin writes. None of
    # these has been tried against a real provider (GAPS G18).
    "microsoft": {
        "label": "Microsoft 365",
        # "common" signs in a work, school or personal account; the app registration says which.
        "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "revoke": None,  # Microsoft has no token revocation endpoint: the owner removes the app in their account
        "scopes": ["offline_access", "User.Read", "Mail.Read", "Calendars.Read"],
        "auth_extra": {},
        "tests": [
            ("profile", "https://graph.microsoft.com/v1.0/me"),
            ("mail", "https://graph.microsoft.com/v1.0/me/messages?$top=1"),
            ("calendar", "https://graph.microsoft.com/v1.0/me/events?$top=1"),
        ],
        "reads": {
            # Subject, sender and time only: the message body is never asked for.
            "inbox": {
                "about": "recent mail: subject, sender, time and whether it is read (no body)",
                "url": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
                "q": {"$select": "subject,from,receivedDateTime,isRead",
                      "$orderby": "receivedDateTime desc"},
                "limit": ("$top", 100), "rows": "value",
                "keep": {"subject": "subject", "from": "from.emailAddress.name",
                         "address": "from.emailAddress.address", "received": "receivedDateTime",
                         "read": "isRead"}},
            "calendar": {
                "about": "upcoming events, next 14 days",
                "url": "https://graph.microsoft.com/v1.0/me/calendarView",
                "q": {"$select": "subject,start,end,location,isAllDay",
                      "$orderby": "start/dateTime"},
                "window": ("startDateTime", "endDateTime", 14), "limit": ("$top", 100),
                "rows": "value",
                "keep": {"title": "subject", "start": "start.dateTime", "end": "end.dateTime",
                         "where": "location.displayName", "all_day": "isAllDay"}},
        },
    },
    "slack": {
        "label": "Slack",
        "auth": "https://slack.com/oauth/v2/authorize",
        "token": "https://slack.com/api/oauth.v2.access",
        "revoke": "https://slack.com/api/auth.revoke",
        # A person's token, not a bot's: these are `user_scope`s, and Slack wraps that token
        # in `authed_user`. Slack has no PKCE.
        "scopes": ["channels:read"], "scope_param": "user_scope", "pkce": False,
        "token_in": "authed_user", "revoke_mode": "bearer",
        "ok_body": {"invalid_auth", "token_revoked", "token_expired", "account_inactive",
                    "not_authed"},
        "auth_extra": {},
        "tests": [
            ("auth", "https://slack.com/api/auth.test"),
            ("channels", "https://slack.com/api/conversations.list?types=public_channel&limit=1"),
        ],
        "reads": {
            "channels": {
                "about": "public channels: name, member count, topic",
                "url": "https://slack.com/api/conversations.list",
                "q": {"types": "public_channel", "exclude_archived": "true"},
                "limit": ("limit", 200), "rows": "channels",
                "keep": {"name": "name", "members": "num_members", "topic": "topic.value",
                         "purpose": "purpose.value"}},
        },
    },
    "discord": {
        "label": "Discord",
        "auth": "https://discord.com/oauth2/authorize",
        "token": "https://discord.com/api/oauth2/token",
        "revoke": "https://discord.com/api/oauth2/token/revoke",
        "scopes": ["identify", "guilds"], "revoke_mode": "basic",
        "auth_extra": {},
        "tests": [
            ("user", "https://discord.com/api/v10/users/@me"),
            ("guilds", "https://discord.com/api/v10/users/@me/guilds?limit=1"),
        ],
        "reads": {
            "servers": {
                "about": "the servers the owner is in",
                "url": "https://discord.com/api/v10/users/@me/guilds", "limit": ("limit", 200),
                "rows": "", "keep": {"name": "name", "owner": "owner"}},
        },
    },
    "gitlab": {
        "label": "GitLab",
        # The address is a setting (`gitlab.base_url`), so a self-hosted GitLab works; unset,
        # it is the hosted service.
        "base": ("base_url", "https://gitlab.com"),
        "auth": "{base}/oauth/authorize", "token": "{base}/oauth/token",
        "revoke": "{base}/oauth/revoke", "revoke_mode": "creds",
        "scopes": ["read_api"], "refresh_redirect": True,
        "auth_extra": {},
        "tests": [
            ("user", "{base}/api/v4/user"),
            ("projects", "{base}/api/v4/projects?membership=true&per_page=1"),
        ],
        "reads": {
            "projects": {
                "about": "the projects the owner belongs to, latest activity first; query searches names",
                "url": "{base}/api/v4/projects",
                "q": {"membership": "true", "order_by": "last_activity_at"}, "query": "search",
                "limit": ("per_page", 100), "rows": "",
                "keep": {"name": "path_with_namespace", "visibility": "visibility",
                         "description": "description", "activity": "last_activity_at"}},
            "issues": {
                "about": "open issues assigned to the owner; query searches them",
                "url": "{base}/api/v4/issues", "q": {"scope": "assigned_to_me", "state": "opened"},
                "query": "search", "limit": ("per_page", 100), "rows": "",
                "keep": {"title": "title", "project": "references.full", "updated": "updated_at"}},
        },
    },
    "spotify": {
        "label": "Spotify",
        "auth": "https://accounts.spotify.com/authorize",
        "token": "https://accounts.spotify.com/api/token",
        "revoke": None,  # no revocation endpoint: the owner removes the app at spotify.com/account/apps
        "scopes": ["playlist-read-private", "user-read-playback-state"],
        "auth_extra": {},
        "tests": [
            ("profile", "https://api.spotify.com/v1/me"),
            ("playlists", "https://api.spotify.com/v1/me/playlists?limit=1"),
            ("devices", "https://api.spotify.com/v1/me/player/devices"),
        ],
        "reads": {
            "playlists": {
                "about": "the owner's playlists",
                "url": "https://api.spotify.com/v1/me/playlists", "limit": ("limit", 50),
                "rows": "items",
                "keep": {"name": "name", "tracks": "tracks.total", "owner": "owner.display_name",
                         "public": "public"}},
            "devices": {
                "about": "the devices Spotify can play on, and which is active",
                "url": "https://api.spotify.com/v1/me/player/devices", "rows": "devices",
                "keep": {"name": "name", "type": "type", "active": "is_active",
                         "volume": "volume_percent"}},
            "playing": {
                "about": "what is playing now (nothing playing is an empty answer)",
                "url": "https://api.spotify.com/v1/me/player", "rows": ".",
                "keep": {"track": "item.name", "artists": "item.artists.name",
                         "album": "item.album.name", "device": "device.name",
                         "playing": "is_playing"}},
        },
    },
    "notion": {
        "label": "Notion",
        # No scopes: what the app may read is picked on Notion's page, and `owner=user` asks
        # for a person's access. Notion wants Basic auth and a JSON body, and refuses unknown
        # fields, so no PKCE.
        "auth": "https://api.notion.com/v1/oauth/authorize",
        "token": "https://api.notion.com/v1/oauth/token",
        "revoke": "https://api.notion.com/v1/oauth/revoke", "revoke_mode": "basic",
        "scopes": [], "scope_param": None, "pkce": False,
        "token_auth": "basic", "token_json": True,
        "headers": {"Notion-Version": "2022-06-28"},
        "auth_extra": {"owner": "user"},
        "tests": [("user", "https://api.notion.com/v1/users/me")],
        # Pages and databases are a POST to /v1/search: a read in effect, but not a GET, so
        # not here.
        "reads": {
            "me": {
                "about": "whose Notion account this is (name and kind)",
                "url": "https://api.notion.com/v1/users/me", "rows": ".",
                "keep": {"name": "name", "type": "type"}},
        },
    },
    "todoist": {
        "label": "Todoist",
        "auth": "https://todoist.com/oauth/authorize",
        "token": "https://todoist.com/oauth/access_token",
        "revoke": None,  # the owner removes the app in Todoist's integrations settings
        # T-1370: the first write. GUESS, not checked against Todoist's docs or a live app: the
        # scope names `data:read` and `task:add` (the narrowest pair: read, and add-only; the
        # broad `data:read_write` is never asked for), their comma separator and the add-task
        # path below. Todoist's token answer carries no scope, so the house records what it
        # asked for (see `_account`).
        "scopes": ["data:read", "task:add"], "scope_sep": ",", "pkce": False,
        "auth_extra": {},
        "tests": [("projects", "https://api.todoist.com/api/v1/projects?limit=1")],
        # Named writes, like the named reads: a fixed URL on the provider's host, fixed field
        # names (field -> (JSON key, longest text, required)), the scope the account must have
        # been consented with, and the one sentence an account without it hears.
        "writes": {
            "add_task": {
                "about": "add one task: content, an optional due text and an optional description",
                "url": "https://api.todoist.com/api/v1/tasks",  # GUESS: Todoist API v1 path
                # Exact membership of the account's recorded scopes; an account that already
                # holds one of the broader ones in `also` passes too (GUESS: the name).
                "scope": "task:add", "also": ("data:read_write",),
                "reconnect": "Todoist was connected for reading only: reconnect Todoist to "
                             "allow adding tasks",
                "fields": {"content": ("content", 500, True),
                           "due": ("due_string", 80, False),
                           "description": ("description", 1000, False)}}},
        "reads": {
            "projects": {
                "about": "the owner's projects",
                "url": "https://api.todoist.com/api/v1/projects", "limit": ("limit", 200),
                "rows": "results",
                "keep": {"name": "name", "favorite": "is_favorite", "shared": "is_shared"}},
            "tasks": {
                "about": "open tasks: content, due date, priority, labels",
                "url": "https://api.todoist.com/api/v1/tasks", "limit": ("limit", 200),
                "rows": "results",
                "keep": {"task": "content", "due": "due.date", "priority": "priority",
                         "labels": "labels"}},
            # T-1415: what the morning brief reads. GUESS, not checked against Todoist's docs
            # or a live account: the filter path `/tasks/filter`, its `query` parameter taking
            # the filter language (`today | overdue`) and its accepting `limit`. The fixed
            # filter rides in `q`, so it shares the one query string the engine builds.
            "due": {
                "about": "open tasks due today or overdue: content, due date, priority",
                "url": "https://api.todoist.com/api/v1/tasks/filter",  # GUESS: path and `query`
                "q": {"query": "today | overdue"}, "limit": ("limit", 200),
                "rows": "results",
                "keep": {"task": "content", "due": "due.date", "priority": "priority"}},
        },
    },
}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _clean(text: str) -> str:
    """Printable text on one line: control and format characters (NUL, bidi overrides) go,
    every kind of whitespace becomes one space."""
    kept = "".join(" " if c.isspace() else c for c in text
                   if c.isspace() or unicodedata.category(c)[0] != "C")
    return " ".join(kept.split())


def _scalar(value: Any) -> Any:
    """A provider's value as something safe to relay, or ``None`` (dropped): a clipped clean
    string, a bool, a number that is finite and sane. Never raises: JSON from a provider can
    carry Infinity, NaN or an integer with hundreds of digits."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= _READ_INT_MAX else None
    if isinstance(value, float):
        return value if math.isfinite(value) and abs(value) <= _READ_INT_MAX else None
    if isinstance(value, str):
        text = _clean(value)
        if len(text) > READ_CLIP:
            text = text[:READ_CLIP - 1] + "…"
        return text or None
    return None


def _dig(node: Any, path: list[str]) -> Any:
    """Follow a dotted path; through a list it goes into each element and collects."""
    if not path:
        return node
    if isinstance(node, list):
        found = [_dig(n, path) for n in node[:READ_ROWS]]
        return [f for f in found if f is not None]
    if isinstance(node, dict):
        return _dig(node.get(path[0]), path[1:])
    return None


def _pick(row: dict[str, Any], path: str) -> Any:
    found = _dig(row, path.split("."))
    if not isinstance(found, list):
        return _scalar(found)
    # Several values (an artist list, labels, a person's names) read as one line.
    seen: list[str] = []
    for item in found[:10]:
        item = _scalar(item)
        if item is not None and str(item) not in seen:
            seen.append(str(item))
    joined = ", ".join(seen)
    return _scalar(joined)


def _limit(raw: Any) -> int:
    """The caller's row count, clamped to 1..READ_ROWS; anything unreadable is the default."""
    if isinstance(raw, (bool, int, float, str)):
        try:
            number = float(raw)
        except (ValueError, OverflowError):
            return READ_DEFAULT
        if math.isfinite(number):
            return max(1, min(READ_ROWS, int(number)))
    return READ_DEFAULT


class Connectors:
    """The accounts, their encrypted file, and the flow that fills it."""

    def __init__(self, jarvis: "Jarvis", config: dict[str, Any], transport: Any = None) -> None:
        self.jarvis = jarvis
        self.config = config
        self.transport = transport
        self.dir = Path(jarvis.config_dir) / ".storage"
        self.key_path = self.dir / "oauth.key"
        self.file = self.dir / "oauth_tokens.enc"
        self._client: httpx.AsyncClient | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._accounts: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._fernet = self._open_key()
        self._load()

    # --- the encrypted file ---------------------------------------------------
    def _open_key(self) -> Fernet:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            # O_EXCL: two starts cannot each mint a key and orphan the other's tokens.
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
        except FileExistsError:
            pass
        return Fernet(self.key_path.read_bytes().strip())

    def _load(self) -> None:
        try:
            raw = self._fernet.decrypt(self.file.read_bytes())
            data = json.loads(raw)
            self._accounts = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            self._accounts = {}
        except (InvalidToken, ValueError, OSError):
            # A file this key cannot open is a lost account, not a crash at boot.
            _LOGGER.warning("oauth: the token file could not be read; accounts start empty")
            self._accounts = {}

    def _save(self) -> None:
        blob = self._fernet.encrypt(json.dumps(self._accounts).encode())
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), prefix="oauth.", suffix=".tmp")  # 0600
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
        os.replace(tmp, self.file)

    # --- http -------------------------------------------------------------------
    async def _http(self, method: str, url: str, *, cap: int = MAX_BODY, mark_cut: bool = False,
                    **kwargs: Any) -> tuple[int, Any]:
        """One bounded call: status and parsed JSON (``None`` if it is not JSON).

        With ``mark_cut`` a body that ran past ``cap`` and so no longer parses comes back as
        :data:`_CUT`, not ``None``: the caller can tell "too big" from "not JSON". The cut text
        itself is never returned.
        """
        if self._client is None:
            # Discord's edge refuses a client that names itself nothing; every API wants a UA.
            self._client = httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT,
                                             follow_redirects=False,
                                             headers={"User-Agent": "jarvis-core-oauth/1"})
        async with self._client.stream(method, url, **kwargs) as response:
            body = b""
            cut = False
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > cap:
                    body, cut = body[:cap], True
                    break
            try:
                return response.status_code, json.loads(body) if body else None
            except (ValueError, RecursionError):  # cut short, not JSON, or nested past the parser
                return response.status_code, _CUT if cut and mark_cut else None

    # --- config -------------------------------------------------------------------
    def _creds(self, provider: str) -> tuple[str, str]:
        block = self.config.get(provider)
        block = block if isinstance(block, dict) else {}
        return str(block.get("client_id") or ""), str(block.get("client_secret") or "")

    def redirect_uri(self) -> str:
        base = str(self.config.get("redirect_base") or "").strip().rstrip("/")
        return f"{base}{CALLBACK_PATH}" if base.startswith(("http://", "https://")) else ""

    def _base(self, provider: str) -> str:
        """A self-hosted provider's address from its setting; ``""`` if it is not a web address."""
        key, default = PROVIDERS[provider].get("base", (None, ""))
        if not key:
            return ""
        block = self.config.get(provider)
        raw = str((block.get(key) if isinstance(block, dict) else "") or default).strip().rstrip("/")
        try:
            url = httpx.URL(raw)
        except httpx.InvalidURL:
            return ""
        # No userinfo, query or fragment: the endpoints are appended to this as text.
        if not re.fullmatch(r"[A-Za-z0-9.:-]+", url.host) or url.userinfo or url.query or url.fragment:
            return ""
        if url.scheme == "https":
            return raw
        # Plain http would carry the client secret and tokens in clear, so it is
        # allowed only to a private address literal (a GitLab on the same LAN).
        try:
            private = url.scheme == "http" and ipaddress.ip_address(url.host).is_private
        except ValueError:
            private = False
        return raw if private else ""

    def _url(self, provider: str, url: str) -> str:
        # An unusable base leaves a relative URL, which httpx refuses (a TransportError),
        # so a bad setting never sends a token to a host nobody chose.
        return url.replace("{base}", self._base(provider))

    async def _token_call(self, provider: str, fields: dict[str, str]) -> tuple[int, Any]:
        """The token endpoint, asked the way this provider wants (Basic or body, form or JSON)."""
        spec = PROVIDERS[provider]
        client_id, secret = self._creds(provider)
        kwargs: dict[str, Any] = {"headers": {"Accept": "application/json"}}
        if spec.get("token_auth") == "basic":
            kwargs["auth"] = (client_id, secret)
        else:
            fields = {**fields, "client_id": client_id, "client_secret": secret}
        kwargs["json" if spec.get("token_json") else "data"] = fields
        return await self._http("POST", self._url(provider, spec["token"]), **kwargs)

    @staticmethod
    def _unwrap(spec: dict[str, Any], body: Any) -> Any:
        """Slack hands a person's token inside ``authed_user``; everyone else's is at the top."""
        inner = body.get(spec["token_in"]) if isinstance(body, dict) and "token_in" in spec else None
        # Only when the token is really in there: a refresh answer may be flat and still
        # carry `authed_user: {id}`, which must not hide the token at the top.
        return inner if isinstance(inner, dict) and inner.get("access_token") else body

    # --- consent ------------------------------------------------------------------
    async def start(self, provider: str) -> dict[str, Any]:
        spec = PROVIDERS.get(provider)
        if spec is None:
            return {"error": f"unknown provider {provider!r}; one of: {', '.join(PROVIDERS)}"}
        client_id, _ = self._creds(provider)
        if not client_id:
            return {"error": f"{spec['label']} is not configured: set its client id and secret "
                             "(docs/runbooks/connectors.md)"}
        if not self.redirect_uri():
            return {"error": "oauth redirect_base is not set (the console's address; "
                             "docs/runbooks/connectors.md)"}
        if "base" in spec and not self._base(provider):
            return {"error": f"{spec['label']}'s base address is not a web address "
                             "(http:// or https://; docs/runbooks/connectors.md)"}
        now = time.time()
        for state in [s for s, p in self._pending.items() if now - p["created"] > PENDING_TTL]:
            del self._pending[state]
        while len(self._pending) >= MAX_PENDING:
            del self._pending[min(self._pending, key=lambda s: self._pending[s]["created"])]
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64) if spec.get("pkce", True) else None
        self._pending[state] = {"provider": provider, "verifier": verifier, "created": now,
                                "scope": spec.get("scope_sep", " ").join(spec["scopes"])}
        query = {"response_type": "code", "client_id": client_id,
                 "redirect_uri": self.redirect_uri(), "state": state}
        if spec["scopes"]:
            query[spec.get("scope_param", "scope")] = spec.get("scope_sep", " ").join(spec["scopes"])
        if verifier:
            query["code_challenge"] = _b64(hashlib.sha256(verifier.encode()).digest())
            query["code_challenge_method"] = "S256"
        query.update(spec["auth_extra"])
        return {"url": f"{self._url(provider, spec['auth'])}?{urlencode(query)}",
                "provider": provider, "expires_in": PENDING_TTL}

    async def callback(self, params: dict[str, str]) -> dict[str, Any]:
        """Finish a consent: the query the provider sent the browser back with."""
        state = str(params.get("state") or "")
        pending = self._pending.pop(state, None)  # single use, hit or miss
        if pending is None or time.time() - pending["created"] > PENDING_TTL:
            return {"ok": False, "message": "This link has expired or was already used. "
                                            "Ask Jarvis to start the connection again."}
        provider = pending["provider"]
        label = PROVIDERS[provider]["label"]
        err = str(params.get("error") or "")
        if err:
            shown = err if _CODE_RE.match(err) else "error"
            return {"ok": False, "provider": provider, "message": f"{label} did not connect ({shown})."}
        code = str(params.get("code") or "")
        if not code or len(code) > 2048:
            return {"ok": False, "provider": provider, "message": f"{label} sent no code."}
        fields = {"grant_type": "authorization_code", "code": code,
                  "redirect_uri": self.redirect_uri()}
        if pending["verifier"]:
            fields["code_verifier"] = pending["verifier"]
        try:
            status, body = await self._token_call(provider, fields)
        except httpx.HTTPError:
            return {"ok": False, "provider": provider, "message": f"{label} could not be reached."}
        body = self._unwrap(PROVIDERS[provider], body)
        token = body.get("access_token") if isinstance(body, dict) else None
        if status != 200 or not isinstance(token, str) or not token:
            return {"ok": False, "provider": provider, "message": f"{label} refused the sign-in."}
        self._accounts[provider] = self._account(body, token, old=None, asked=pending["scope"])
        self._save()
        result = await self.test(provider)
        return {"ok": True, "provider": provider, "test": result,
                "message": f"{label} is connected."}

    @staticmethod
    def _account(body: dict[str, Any], token: str, old: dict[str, Any] | None,
                 asked: str = "") -> dict[str, Any]:
        # The granted scope: the provider's word if it gives one, else what this consent asked
        # for (`asked`, consent only), else what the account already held (a refresh answer
        # rarely repeats it). An account from before T-1370 holds "" and counts as read-only.
        scope = str(body.get("scope") or asked or (old or {}).get("scope") or "")[:500]
        expires = body.get("expires_in")
        refresh = body.get("refresh_token") or (old or {}).get("refresh_token")
        return {
            "status": "connected", "access_token": token,
            "refresh_token": refresh if isinstance(refresh, str) else None,
            "expires_at": time.time() + min(expires, 86400 * 30)
            if isinstance(expires, (int, float)) and expires > 0 else None,
            "scope": scope,
            "connected_at": (old or {}).get("connected_at") or time.time(),
            "last_test": (old or {}).get("last_test"),
        }

    # --- using an account -----------------------------------------------------------
    async def access_token(self, provider: str) -> str | None:
        """A live access token, or ``None`` (not connected, needs consent, or the provider is down)."""
        # One lock per provider, held from the expiry check to the saved refresh: a second ask
        # for the same account waits, then reads the token the first one stored. Without it
        # both send one refresh token and a provider that rotates them refuses the second,
        # which ends the account in needs_consent. ponytail: it also holds the lock while
        # _needs_consent tells the owner; per-provider, so another account never waits on it.
        async with self._locks.setdefault(provider, asyncio.Lock()):
            acct = self._accounts.get(provider)
            if not acct or acct.get("status") != "connected":
                return None
            expires = acct.get("expires_at")
            if expires is None or expires - SKEW > time.time():  # None: GitHub's tokens do not expire
                return acct["access_token"]
            # One attempt. A refusal ends in needs_consent, which stops every later attempt.
            if not acct.get("refresh_token"):
                await self._needs_consent(provider)
                return None
            spec = PROVIDERS[provider]
            fields = {"grant_type": "refresh_token", "refresh_token": acct["refresh_token"]}
            if spec.get("refresh_redirect"):  # GitLab's docs list it on a refresh
                fields["redirect_uri"] = self.redirect_uri()
            try:
                status, body = await self._token_call(provider, fields)
            except httpx.HTTPError:
                status, body = 0, None  # unreachable, noted below once the account is checked
            if self._accounts.get(provider) is not acct:
                # Disconnected (or connected anew) while the call was out: revoke does not take
                # the lock, so the answer is for an account that is gone. Writing it would undo it.
                return None
            body = self._unwrap(spec, body)
            # Slack refuses with a 200 and `ok: false`; that is a refusal like any 4xx.
            said_no = "ok_body" in spec and isinstance(body, dict) and body.get("ok") is False
            # 408 and 429 are "try later", not a refusal: forcing a new consent on a
            # rate limit would disconnect a healthy account.
            if (400 <= status < 500 and status not in (408, 429)) or said_no:
                self._note_refresh_error(acct, "refused")
                await self._needs_consent(provider)
                return None
            token = body.get("access_token") if isinstance(body, dict) else None
            if status != 200 or not isinstance(token, str) or not token:
                # One of our own words, never the provider's text: the console shows it as is.
                self._note_refresh_error(
                    acct, "busy" if status in (408, 429) else "unreachable" if status == 0 or status >= 500
                    else "refused")
                return None
            self._accounts[provider] = self._account(body, token, old=acct)
            self._save()
            return token

    def _note_refresh_error(self, acct: dict[str, Any], reason: str) -> None:
        """Keep why the last refresh failed beside the account, so status shows it instead of silence.

        ``reason`` is refused, unreachable or busy: chosen here, so no provider text or token can
        reach the file or the console. A good refresh builds a fresh account and drops it.
        A repeat of the same reason keeps the first time and writes nothing: a provider that
        stays down would otherwise re-encrypt and rewrite the token file on every ask.
        """
        prev = acct.get("last_refresh_error")
        if isinstance(prev, dict) and prev.get("reason") == reason:
            return
        acct["last_refresh_error"] = {"reason": reason, "at": time.time()}
        self._save()

    async def _needs_consent(self, provider: str, only: dict[str, Any] | None = None) -> None:
        # `only`: the account a request was made with. A 401 answers that account's token, so
        # when the owner reconnected meanwhile (a new account object) the new one stays connected.
        acct = self._accounts.get(provider)
        if not acct or acct.get("status") == "needs_consent":
            return
        if only is not None and acct is not only:
            return
        acct.update(status="needs_consent", access_token=None, refresh_token=None, expires_at=None)
        self._save()
        label = PROVIDERS[provider]["label"]
        await self._tell(f"{label} needs you to sign in again before I can use it. "
                         f"Ask me to connect {label} and I will send the link.")

    async def _tell(self, message: str) -> bool:
        if not self.jarvis.services.has_service("companion", "notify"):
            return False
        try:
            result = await self.jarvis.services.async_call(
                "companion", "notify",
                {"message": message, "importance": "high", "kind": "notify"},
                blocking=True, return_response=True)
        except Exception:  # noqa: BLE001 - the state is saved; telling is best effort
            _LOGGER.exception("oauth: could not tell the owner")
            return False
        if isinstance(result, dict) and "service_response" in result:
            result = result["service_response"]
        return isinstance(result, dict) and result.get("status") == "delivered"

    async def test(self, provider: str) -> dict[str, Any]:
        """The provider's read-only calls, and which of them the token can make."""
        spec = PROVIDERS.get(provider)
        if spec is None:
            return {"error": f"unknown provider {provider!r}"}
        acct = self._accounts.get(provider)
        if not acct:
            return {"provider": provider, "status": "disconnected", "ok": False, "calls": []}
        token = await self.access_token(provider)
        if token is None:
            status = self._accounts[provider]["status"]
            return {"provider": provider, "status": status, "ok": False, "calls": []}
        calls: list[dict[str, Any]] = []
        for name, url in spec["tests"]:
            try:
                code, body = await self._http(
                    "GET", self._url(provider, url),
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                             **spec.get("headers", {})})
            except httpx.HTTPError:
                calls.append({"name": name, "ok": False, "http": 0})
                continue
            ok, dead = code == 200, code == 401
            if "ok_body" in spec:  # Slack: 200 whatever happened, `ok` and `error` say what
                said = isinstance(body, dict)
                ok = ok and said and body.get("ok") is True
                dead = said and body.get("error") in spec["ok_body"]
            calls.append({"name": name, "ok": ok, "http": code})
            if dead:  # revoked or expired behind our back: ask, don't retry
                await self._needs_consent(provider)
                break
        status = self._accounts[provider]["status"]
        ok = status == "connected" and len(calls) == len(spec["tests"]) and all(c["ok"] for c in calls)
        if status == "connected":
            self._accounts[provider]["last_test"] = {"ok": ok, "at": time.time(), "calls": calls}
            self._save()
        return {"provider": provider, "status": status, "ok": ok, "calls": calls,
                "reaches": [c["name"] for c in calls if c["ok"]]}

    async def read(self, provider: str, what: Any, query: Any = "", limit: Any = None) -> dict[str, Any]:
        """One named, read-only call of a connected account, reduced to a short summary.

        Only a name in the provider row's ``reads`` runs; the address, the method (GET) and
        every parameter but the one search text and the row count come from the row, never
        from the caller. The answer holds the row's keep-list fields and nothing else, so no
        id, link, mail body or token comes back. Does **not** check what the model does with
        the text: that is outside text (the plugin marks it so).
        """
        spec = PROVIDERS.get(provider)
        if spec is None:
            return {"error": f"unknown provider {str(provider)[:40]!r}; one of: {', '.join(PROVIDERS)}"}
        label = spec["label"]
        reads = spec.get("reads", {})
        read = reads.get(what) if isinstance(what, str) else None
        if read is None:
            return {"error": f"no read named {str(what)[:40]!r} for {label}; "
                             f"one of: {', '.join(reads) or 'none'}"}
        acct = self._accounts.get(provider)
        if not acct:
            return {"error": f"{label} is not connected"}
        if acct.get("status") != "connected":
            return {"error": f"{label} needs you to sign in again; ask me to connect {label}"}
        if "{base}" in read["url"] and not self._base(provider):
            return {"error": f"{label}'s base address is not a web address "
                             "(docs/runbooks/connectors.md)"}
        text = _clean(query)[:READ_QUERY_MAX].strip() if isinstance(query, str) else ""
        if text and "query" not in read:
            return {"error": f"the {what} read takes no query"}
        want = _limit(limit)
        params = dict(read.get("q", {}))
        if "window" in read:  # a fixed look-ahead from now, not a range the caller picks
            lo, hi, days = read["window"]
            now = datetime.now(timezone.utc)
            params[lo] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            params[hi] = (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if "limit" in read:
            params[read["limit"][0]] = min(want, read["limit"][1])
        if text:
            params[read["query"]] = text
        token = await self.access_token(provider)
        if token is None:
            if self._accounts.get(provider, {}).get("status") == "needs_consent":
                return {"error": f"{label} needs you to sign in again; ask me to connect {label}"}
            return {"error": f"{label} could not be asked just now"}
        url = f"{self._url(provider, read['url'])}?{urlencode(params, safe='$,:/')}"
        asked_with = self._accounts.get(provider)
        try:
            code, body = await self._http(
                "GET", url, cap=READ_BODY, mark_cut=True,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                     **spec.get("headers", {})})
        except httpx.HTTPError:
            return {"error": f"{label} could not be reached"}
        if body is _CUT and code == 200:  # a fixed sentence: none of the cut text comes back
            return {"error": f"{label}'s answer was too large to read (over {READ_BODY // 1024} KB); "
                             "ask for fewer rows with a smaller limit"}
        if body is _CUT:  # an oversize error page: its status is the answer
            body = None
        said_no = False
        if "ok_body" in spec and code == 200:  # Slack: 200 whatever happened
            said_no = not (isinstance(body, dict) and body.get("ok") is True)
            if isinstance(body, dict) and body.get("error") in spec["ok_body"]:
                await self._needs_consent(provider, only=asked_with)
        if code == 401:  # revoked or expired behind our back: ask, don't retry
            await self._needs_consent(provider, only=asked_with)
        # Nothing of the provider's own wording is passed on: its status and our label only.
        if code in (401, 403):
            return {"error": f"{label} refused the read (HTTP {code})"}
        if said_no or not (code == 200 or (code == 204 and body is None)):
            return {"error": f"{label} answered HTTP {code}" if code != 200
                    else f"{label} answered with an error"}
        rows = self._rows(read, body)
        if rows is None:
            return {"error": f"{label} answered something unreadable"}
        out: dict[str, Any] = {"provider": provider, "what": what, "count": 0, "items": [],
                               "truncated": len(rows) > want}
        for row in rows[:want]:
            item = {}
            for name, path in read["keep"].items():
                value = _pick(row, path)
                if value is not None:
                    item[name] = value
            if not item:  # nothing of this row survived the keep-list: not an item to count
                continue
            trial = {**out, "count": out["count"] + 1, "items": [*out["items"], item]}
            if len(json.dumps(trial)) > READ_BYTES:
                out["truncated"] = True
                break
            out = trial
        return out

    async def write(self, provider: str, action: Any, fields: Any) -> dict[str, Any]:
        """One named write (T-1370), the only kind there is: ``todoist`` ``add_task``.

        Like :meth:`read`, the address, the method and the field names come from the provider
        row's ``writes``; the caller supplies only text for those fields, each checked here for
        type and length before anything is sent. An account whose recorded scope lacks the row's
        write scope hears one fixed sentence and nothing is sent, not even a token refresh. One
        attempt, never retried (a retry could add the task twice). Does **not** know whether a
        timed-out request landed, and says so; and the recorded scope is what consent asked for
        where the provider names none, so a scope the owner narrowed on the provider's page is
        found only when the provider answers 403.
        """
        spec = PROVIDERS.get(provider)
        if spec is None:
            return {"error": f"unknown provider {str(provider)[:40]!r}; one of: {', '.join(PROVIDERS)}"}
        label = spec["label"]
        writes = spec.get("writes", {})
        row = writes.get(action) if isinstance(action, str) else None
        if row is None:
            return {"error": f"no write named {str(action)[:40]!r} for {label}; "
                             f"one of: {', '.join(writes) or 'none'}"}
        if not isinstance(fields, dict):
            return {"error": f"the {action} write takes its fields as a name-to-text object"}
        extra = sorted(set(map(str, fields)) - set(row["fields"]))
        if extra:
            return {"error": f"the {action} write takes {', '.join(row['fields'])}, not {extra[:5]}"}
        body: dict[str, str] = {}  # what is sent, under the provider's keys
        shown: dict[str, str] = {}  # the same under ours, for the answer
        for name, (key, longest, required) in row["fields"].items():
            value = fields.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                if required:
                    return {"error": f"the {action} write needs {name}"}
                continue
            if not isinstance(value, str):
                return {"error": f"{name} must be text"}
            text = _clean(value)
            if len(text) > longest:
                return {"error": f"{name} is too long: {longest} characters at most"}
            if text:
                body[key] = shown[name] = text
            elif required:
                return {"error": f"the {action} write needs {name}"}
        acct = self._accounts.get(provider)
        if not acct:
            return {"error": f"{label} is not connected"}
        if acct.get("status") != "connected":
            return {"error": f"{label} needs you to sign in again; ask me to connect {label}"}
        held = re.split(r"[,\s]+", str(acct.get("scope") or ""))
        if not any(s in held for s in (row["scope"], *row.get("also", ()))):
            return {"error": row["reconnect"]}
        token = await self.access_token(provider)
        if token is None:
            if self._accounts.get(provider, {}).get("status") == "needs_consent":
                return {"error": f"{label} needs you to sign in again; ask me to connect {label}"}
            return {"error": f"{label} could not be asked just now"}
        asked_with = self._accounts.get(provider)
        try:
            code, _ = await self._http(
                "POST", self._url(provider, row["url"]), json=body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                         **spec.get("headers", {})})
        except httpx.HTTPError:
            return {"error": f"{label} could not be reached, so the task may not have been added; "
                             "look at the list before asking again"}
        if code == 401:  # revoked behind our back: ask, don't retry
            await self._needs_consent(provider, only=asked_with)
        # Nothing of the provider's own wording is passed on: its status and our label only.
        if code in (401, 403):
            return {"error": f"{label} refused the write (HTTP {code})"}
        if code in (400, 422):
            return {"error": f"{label} did not accept the task (HTTP {code})"}
        if code not in (200, 201, 204):
            return {"error": f"{label} answered HTTP {code}"}
        return {"ok": True, "provider": provider, "action": action, "added": shown}

    @staticmethod
    def _rows(read: dict[str, Any], body: Any) -> list[dict[str, Any]] | None:
        """The list of rows in a provider's answer (``None``: not the shape the row says)."""
        where = read["rows"]
        if where == ".":  # one object is the answer; an empty body is nothing playing
            found = [body] if isinstance(body, dict) else ([] if body is None else None)
        elif where == "":
            found = body if isinstance(body, list) else None
        else:
            found = body.get(where) if isinstance(body, dict) else None
        if not isinstance(found, list):
            return None
        return [r for r in found if isinstance(r, dict)]

    async def revoke(self, provider: str) -> dict[str, Any]:
        """Tell the provider, then forget — the local copy goes either way."""
        spec = PROVIDERS.get(provider)
        if spec is None:
            return {"error": f"unknown provider {provider!r}"}
        acct = self._accounts.pop(provider, None)
        if acct is None:
            return {"provider": provider, "revoked": False, "message": "it was not connected"}
        self._save()
        token = acct.get("refresh_token") or acct.get("access_token")
        revoked = False
        if token and spec["revoke"]:  # a provider with no endpoint is forgotten locally only
            client_id, secret = self._creds(provider)
            mode = spec.get("revoke_mode", "form")
            url = self._url(provider, spec["revoke"])
            try:
                if mode == "github":
                    code, body = await self._http(
                        "DELETE", url.format(client_id=client_id), auth=(client_id, secret),
                        json={"access_token": acct.get("access_token")})
                elif mode == "bearer":  # Slack voids the token it is shown
                    code, body = await self._http(
                        "POST", url, headers={"Authorization": f"Bearer {acct.get('access_token')}"})
                elif mode == "basic":  # Discord (form) and Notion (JSON) want the client as Basic auth
                    kwargs: dict[str, Any] = {"json" if spec.get("token_json") else "data": {"token": token}}
                    code, body = await self._http("POST", url, auth=(client_id, secret), **kwargs)
                elif mode == "creds":  # GitLab wants the client in the body
                    code, body = await self._http("POST", url, data={
                        "token": token, "client_id": client_id, "client_secret": secret})
                else:
                    code, body = await self._http("POST", url, data={"token": token})
                said_no = isinstance(body, dict) and body.get("ok") is False
                revoked = code in (200, 204) and not said_no
            except httpx.HTTPError:
                pass
        out: dict[str, Any] = {"provider": provider, "revoked": revoked}
        if not spec["revoke"]:
            out["message"] = ("this provider has no revoke call: the token is deleted here; "
                              "remove the app in the provider's account settings")
        return out

    def status(self) -> dict[str, Any]:
        """Every provider's state. No token, no key, nothing a provider wrote."""
        out: dict[str, Any] = {}
        for name, spec in PROVIDERS.items():
            acct = self._accounts.get(name)
            configured = bool(self._creds(name)[0])
            out[name] = {
                "label": spec["label"], "configured": configured,
                "status": acct["status"] if acct else ("disconnected" if configured else "not_configured"),
                "last_test": (acct or {}).get("last_test"),
                "expires_at": (acct or {}).get("expires_at"),
                "last_refresh_error": (acct or {}).get("last_refresh_error"),
            }
        return out

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


class DailyCheck(DailyTask):
    """Once a day, ask each connected account for its token, so a dead one is found by the clock.

    Nothing new talks to a provider: :meth:`Connectors.access_token` refreshes a token that is
    expired or about to be, and on a refusal it moves the account to needs_consent and tells the
    owner. That state is the notify-once memory: a needs_consent account is no longer "connected",
    so the next day's pass skips it, a restart reads it from the encrypted file, and only a new
    consent (which makes it connected again) can bring a later notice. Unreachable and busy
    answers tell nobody and leave the account connected, so the next day asks again.

    Does not cover a token that is valid for more than a day: it is not refreshed early, so a
    revoked grant behind it is found at the first pass after the token expires. An owner who was
    unreachable when the notice went out is not told again (status still says needs_consent).
    """

    # Quiet hours; a fixed time, because no owner has a reason to pick one.
    time_of_day = dt_time(4, 15)
    _schedule_label = "oauth: the daily account check"
    _failure_message = "oauth: the daily account check failed"

    def __init__(self, jarvis: "Jarvis", mgr: Connectors) -> None:
        self.jarvis = jarvis
        self.mgr = mgr
        self._task: asyncio.Task | None = None

    async def _fire(self) -> None:
        for provider in list(self.mgr._accounts):
            try:
                await self.mgr.access_token(provider)
            except asyncio.CancelledError:
                raise
            except Exception:  # one account's failure must not skip the others
                _LOGGER.exception("oauth: the daily check of %s failed", provider)


def _register_services(jarvis: "Jarvis", mgr: Connectors) -> None:
    def provider_of(call: Any) -> str:
        return str((call.data or {}).get("provider") or "").lower()

    async def start(call: Any) -> dict[str, Any]:
        started = await mgr.start(provider_of(call))
        if "error" in started:
            return started
        label = PROVIDERS[started["provider"]]["label"]
        delivered = await mgr._tell(
            f"To connect {label}, open this link and approve: {started['url']}")
        out: dict[str, Any] = {"provider": started["provider"], "delivered": delivered,
                               "expires_in": started["expires_in"]}
        if not delivered:  # nobody reachable: the caller must pass the link on itself
            out["url"] = started["url"]
        return out

    async def status(call: Any) -> dict[str, Any]:
        return {"providers": mgr.status()}

    async def test(call: Any) -> dict[str, Any]:
        return await mgr.test(provider_of(call))

    async def revoke(call: Any) -> dict[str, Any]:
        return await mgr.revoke(provider_of(call))

    async def read(call: Any) -> dict[str, Any]:
        data = call.data or {}
        # No method, address or header can be named: a key beyond these is refused, not ignored.
        extra = sorted(set(data) - {"provider", "what", "query", "limit"})
        if extra:
            return {"error": f"oauth.read takes provider, what, query and limit, not {extra[:5]}"}
        return await mgr.read(provider_of(call), data.get("what"), data.get("query") or "",
                              data.get("limit"))

    async def write(call: Any) -> dict[str, Any]:
        data = call.data or {}
        # The fields ride flat beside provider and action; any other key (a method, an address,
        # a header, a body) is refused whole by write(), which names only the fields in the row.
        rest = {k: v for k, v in data.items() if k not in ("provider", "action")}
        return await mgr.write(provider_of(call), data.get("action"), rest)

    fields = {"provider": {"description": f"one of: {', '.join(PROVIDERS)}", "required": True}}
    jarvis.services.register(
        DOMAIN, "write", write, supports_response=True, owner_only=True,
        description="Do one named write on a connected account: todoist add_task (content, "
                    "due, description). Needs the write scope; else asks to reconnect.",
        fields={**fields, "action": {"description": "the write's name", "required": True},
                "content": {"description": "the task's text (add_task, at most 500 characters)"},
                "due": {"description": "a due date in words (add_task, at most 80 characters)"},
                "description": {"description": "a longer note (add_task, at most 1000 characters)"}})
    jarvis.services.register(
        DOMAIN, "read", read, supports_response=True, owner_only=True,
        description="Read from a connected account through one of its named read-only calls "
                    "(connectors.md lists them): a clipped summary, never the token.",
        fields={**fields, "what": {"description": "the read's name", "required": True},
                "query": {"description": "a search text, for the reads that take one"},
                "limit": {"description": f"rows wanted, up to {READ_ROWS}"}})
    for name, handler, text in (
        ("start", start, "Start connecting an account: sends the consent link to the device in use."),
        ("status", status, "Which accounts are connected, and whether any needs consent again."),
        ("test", test, "Run the read-only test calls for one connected account."),
        ("revoke", revoke, "Disconnect an account and tell the provider to void the token."),
    ):
        jarvis.services.register(DOMAIN, name, handler, description=text,
                                 fields={} if name == "status" else fields, supports_response=True,
                                 owner_only=name != "status")  # status: the console reads it


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = config if isinstance(config, dict) else {}
    store = jarvis.data.setdefault(DOMAIN, {})
    mgr = Connectors(jarvis, cfg, transport=store.get("transport"))
    store["manager"] = mgr
    _register_services(jarvis, mgr)
    check = store["daily_check"] = DailyCheck(jarvis, mgr)
    if jarvis.is_running:
        check.start()
    else:
        jarvis.bus.listen(EVENT_JARVIS_START, lambda event=None: check.start())
    jarvis.register_shutdown(check.stop)  # shutdown runs last-registered first: stop before close
    jarvis.register_shutdown(mgr.close)
    return True


def page(result: dict[str, Any]) -> str:
    """The page the owner's browser lands on. Fixed text only; nothing is echoed raw."""
    mark = "Connected" if result.get("ok") else "Not connected"
    body = html.escape(str(result.get("message") or ""))
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{mark}</title><body style='font:18px system-ui;margin:3rem auto;max-width:28rem'>"
            f"<h1>{mark}</h1><p>{body}</p><p>You can close this tab.</p>")
