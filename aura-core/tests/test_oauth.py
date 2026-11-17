"""OAuth connectors: the consent flow, refresh, re-consent and revoke, against mock servers.

No test here calls Google or GitHub. A `MockProviders` transport answers both
providers' real hostnames, checks PKCE the way the real servers do (the
verifier must hash to the challenge sent at consent) and counts what it was
asked, so "never retries in a loop" is a number rather than a hope.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.core import Jarvis  # noqa: E402
from aura.integrations import oauth as oauth_integration  # noqa: E402

REDIRECT_BASE = "https://console.example"
CONFIG = {
    "redirect_base": REDIRECT_BASE,
    "google": {"client_id": "g-client", "client_secret": "g-secret"},
    "github": {"client_id": "gh-client", "client_secret": "gh-secret"},
    "microsoft": {"client_id": "ms-client", "client_secret": "ms-secret"},
    "slack": {"client_id": "sl-client", "client_secret": "sl-secret"},
    "discord": {"client_id": "dc-client", "client_secret": "dc-secret"},
    "gitlab": {"client_id": "gl-client", "client_secret": "gl-secret",
               "base_url": "https://gitlab.example.test/"},
    "spotify": {"client_id": "sp-client", "client_secret": "sp-secret"},
    "notion": {"client_id": "no-client", "client_secret": "no-secret"},
    "todoist": {"client_id": "td-client", "client_secret": "td-secret"},
}
ACCESS = "ya29.ACCESS-TOKEN-ONE"
REFRESH = "1//REFRESH-TOKEN-ONE"
GH_TOKEN = "gho_GITHUBTOKENONE"


class MockProviders:
    """Both providers' endpoints on one transport."""

    def __init__(self) -> None:
        self.challenges: dict[str, str] = {}  # code -> challenge
        self.calls: list[tuple[str, str]] = []
        self.refresh_fails = False
        self.api_status = 200
        self.token_n = 1

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append((request.method, f"{url.host}{url.path}"))
        form = parse_qs(request.content.decode()) if request.content else {}
        one = lambda k: (form.get(k) or [""])[0]  # noqa: E731
        if url.host == "oauth2.googleapis.com" and url.path == "/token":
            if one("grant_type") == "refresh_token":
                if self.refresh_fails or one("refresh_token") != REFRESH:
                    return httpx.Response(400, json={"error": "invalid_grant",
                                                     "error_description": "Token expired or revoked"})
                self.token_n += 1
                return httpx.Response(200, json={"access_token": f"ya29.REFRESHED-{self.token_n}",
                                                 "expires_in": 3600, "scope": "x"})
            digest = hashlib.sha256(one("code_verifier").encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if one("code") != "good-code" or self.challenges.get("good-code") != challenge:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": ACCESS, "refresh_token": REFRESH,
                                             "expires_in": 3600, "scope": "calendar gmail contacts"})
        if url.host == "github.com" and url.path == "/login/oauth/access_token":
            assert request.headers["accept"] == "application/json"
            if one("code") != "good-code":
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": GH_TOKEN, "scope": "repo,notifications",
                                             "token_type": "bearer"})
        if url.host == "oauth2.googleapis.com" and url.path == "/revoke":
            return httpx.Response(200, json={})
        if url.host == "api.github.com" and request.method == "DELETE":
            return httpx.Response(204)
        if url.host.endswith("googleapis.com") or url.host == "api.github.com":
            if not request.headers.get("authorization", "").startswith("Bearer "):
                return httpx.Response(401, json={})
            if self.api_status != 200:
                return httpx.Response(self.api_status, json={"message": "Bad credentials"})
            return httpx.Response(200, json={"items": [{"id": "1"}], "resourceName": "x"})
        return httpx.Response(404)


@pytest.fixture
async def rig(tmp_path):
    jarvis = Jarvis(tmp_path / "config")
    mock = MockProviders()
    jarvis.data["oauth"] = {"transport": httpx.MockTransport(mock)}
    sent: list[dict] = []

    async def notify(call):
        sent.append(dict(call.data))
        return {"status": "delivered"}

    jarvis.services.register("companion", "notify", notify, supports_response=True)
    assert await oauth_integration.async_setup(jarvis, copy.deepcopy(CONFIG))
    store = jarvis.data["oauth"]["manager"]
    return jarvis, store, mock, sent


def _state_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


async def _connect(store, mock, provider="google"):
    started = await store.start(provider)
    q = parse_qs(urlsplit(started["url"]).query)
    mock.challenges["good-code"] = q["code_challenge"][0]
    return await store.callback({"state": q["state"][0], "code": "good-code"})


# --- start ---------------------------------------------------------------------


async def test_oauth_start_builds_a_pkce_consent_url_with_a_bound_state(rig):
    _, store, _, _ = rig
    started = await store.start("google")
    parts = urlsplit(started["url"])
    q = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert q["response_type"] == ["code"] and q["client_id"] == ["g-client"]
    assert q["redirect_uri"] == [f"{REDIRECT_BASE}/api/oauth/callback"]
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
    assert len(q["state"][0]) >= 32
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert "calendar.readonly" in q["scope"][0] and "gmail.compose" in q["scope"][0]
    assert "client_secret" not in started["url"] and "g-secret" not in started["url"]
    # Two starts never share a state or a verifier.
    assert _state_of((await store.start("google"))["url"]) != q["state"][0]


async def test_oauth_start_refuses_an_unconfigured_provider_and_a_missing_redirect(rig, tmp_path):
    jarvis, store, _, _ = rig
    assert (await store.start("nope"))["error"]
    bare = Jarvis(tmp_path / "bare")
    await oauth_integration.async_setup(bare, {"redirect_base": REDIRECT_BASE})
    assert "not configured" in (await bare.data["oauth"]["manager"].start("google"))["error"]
    bare2 = Jarvis(tmp_path / "bare2")
    await oauth_integration.async_setup(bare2, {"google": CONFIG["google"]})
    assert "redirect_base" in (await bare2.data["oauth"]["manager"].start("google"))["error"]


# --- callback ------------------------------------------------------------------


async def test_oauth_callback_checks_state_and_pkce_and_stores_the_account(rig):
    _, store, mock, _ = rig
    forged = await store.callback({"state": "made-up", "code": "good-code"})
    assert forged["ok"] is False and not mock.calls
    result = await _connect(store, mock)
    assert result["ok"] is True and result["provider"] == "google"
    assert store.status()["google"]["status"] == "connected"
    # A state is single use: replaying the same callback does nothing.
    started = await store.start("google")
    state = _state_of(started["url"])
    await store.callback({"state": state, "code": "bad"})
    assert (await store.callback({"state": state, "code": "good-code"}))["ok"] is False


async def test_oauth_callback_with_the_wrong_verifier_stores_nothing(rig):
    _, store, mock, _ = rig
    started = await store.start("google")
    mock.challenges["good-code"] = "not-the-challenge"
    result = await store.callback({"state": _state_of(started["url"]), "code": "good-code"})
    assert result["ok"] is False
    assert store.status()["google"]["status"] == "disconnected"


async def test_oauth_callback_reports_a_refused_consent_without_echoing_the_provider(rig):
    _, store, _, _ = rig
    started = await store.start("google")
    result = await store.callback({"state": _state_of(started["url"]), "error": "access_denied",
                                   "error_description": "<script>alert(1)</script>"})
    assert result["ok"] is False and "<script>" not in json.dumps(result)


async def test_oauth_a_pending_flow_expires(rig, monkeypatch):
    _, store, mock, _ = rig
    started = await store.start("google")
    for pending in store._pending.values():
        pending["created"] -= oauth_integration.PENDING_TTL + 1
    assert (await store.callback({"state": _state_of(started["url"]), "code": "good-code"}))["ok"] is False
    # And only so many flows can be open at once.
    for _ in range(oauth_integration.MAX_PENDING + 5):
        await store.start("google")
    assert len(store._pending) <= oauth_integration.MAX_PENDING


# --- encryption at rest ----------------------------------------------------------


async def test_oauth_tokens_are_encrypted_on_disk_and_the_key_is_private(rig, tmp_path):
    jarvis, store, mock, _ = rig
    await _connect(store, mock)
    await _connect(store, mock, "github")
    files = [p for p in (tmp_path / "config").rglob("*") if p.is_file()]
    assert files
    blob = b"".join(p.read_bytes() for p in files)
    for secret in (ACCESS, REFRESH, GH_TOKEN, "g-secret"):
        assert secret.encode() not in blob
    key = tmp_path / "config" / ".storage" / "oauth.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    tokens = tmp_path / "config" / ".storage" / "oauth_tokens.enc"
    assert tokens.is_file() and b"access_token" not in tokens.read_bytes()
    # Nothing the API returns carries a token or the key.
    shown = json.dumps(store.status()) + json.dumps(await store.test("google"))
    assert ACCESS not in shown and REFRESH not in shown and key.read_text()[:8] not in shown


async def test_oauth_tokens_survive_a_restart_with_the_same_key(rig, tmp_path):
    jarvis, store, mock, _ = rig
    await _connect(store, mock)
    again = Jarvis(tmp_path / "config")
    again.data["oauth"] = {"transport": httpx.MockTransport(mock)}
    await oauth_integration.async_setup(again, CONFIG)
    assert again.data["oauth"]["manager"].status()["google"]["status"] == "connected"


async def test_oauth_a_tampered_token_file_reads_as_disconnected_not_a_crash(rig, tmp_path):
    jarvis, store, mock, _ = rig
    await _connect(store, mock)
    (tmp_path / "config" / ".storage" / "oauth_tokens.enc").write_bytes(b"garbage")
    again = Jarvis(tmp_path / "config")
    await oauth_integration.async_setup(again, CONFIG)
    assert again.data["oauth"]["manager"].status()["google"]["status"] == "disconnected"


# --- refresh, re-consent, revoke -----------------------------------------------


async def test_oauth_an_expired_token_is_refreshed_before_use(rig):
    _, store, mock, _ = rig
    await _connect(store, mock)
    store._accounts["google"]["expires_at"] = 0
    token = await store.access_token("google")
    assert token and token.startswith("ya29.REFRESHED")
    assert ("POST", "oauth2.googleapis.com/token") in mock.calls
    # A fresh token is reused, not refreshed again.
    n = len(mock.calls)
    assert await store.access_token("google") == token and len(mock.calls) == n


async def test_oauth_two_asks_on_an_expired_token_make_one_refresh_and_stay_connected(rig):
    # A provider that rotates refresh tokens voids the second use of one. Both asks pass the
    # expiry check before either refresh lands unless the refresh is held under a lock.
    _, store, mock, sent = rig
    await _connect(store, mock)
    store._accounts["google"]["expires_at"] = 0
    seen: list[str] = []

    async def rotating(request: httpx.Request) -> httpx.Response:
        used = parse_qs(request.content.decode())["refresh_token"][0]
        await asyncio.sleep(0.01)  # a real round trip: the other ask runs while this one waits
        if used in seen or used != REFRESH:
            return httpx.Response(400, json={"error": "invalid_grant"})
        seen.append(used)
        return httpx.Response(200, json={"access_token": "at-new", "refresh_token": "rt-new",
                                         "expires_in": 3600})

    store.transport = httpx.MockTransport(rotating)
    store._client = None
    first, second = await asyncio.gather(store.access_token("google"), store.access_token("google"))
    assert first == second == "at-new"
    assert seen == [REFRESH]
    assert store.status()["google"]["status"] == "connected" and not sent


@pytest.mark.parametrize("refused", [False, True])
async def test_oauth_a_disconnect_during_a_refresh_is_not_undone_by_it(rig, refused):
    # revoke does not take the lock (it must not wait on a slow provider), so it can run while
    # a refresh is in flight; the refresh must not write the account back or tell the owner.
    _, store, mock, sent = rig
    await _connect(store, mock)
    store._accounts["google"]["expires_at"] = 0
    in_flight, release = asyncio.Event(), asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/revoke":
            return httpx.Response(200, json={})
        in_flight.set()
        await release.wait()
        if refused:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "at-new", "refresh_token": "rt-new",
                                         "expires_in": 3600})

    store.transport = httpx.MockTransport(slow)
    store._client = None
    ask = asyncio.create_task(store.access_token("google"))
    await in_flight.wait()
    assert (await store.revoke("google"))["revoked"] is True
    release.set()
    assert await ask is None
    assert "google" not in store._accounts and store.status()["google"]["status"] == "disconnected"
    store._load()  # what a restart would read: nothing was saved back
    assert "google" not in store._accounts and not sent


async def test_oauth_a_failed_refresh_asks_for_consent_once_and_never_loops(rig):
    _, store, mock, sent = rig
    await _connect(store, mock)
    mock.refresh_fails = True
    store._accounts["google"]["expires_at"] = 0
    mock.calls.clear()
    for _ in range(5):
        assert await store.access_token("google") is None
    assert [c for c in mock.calls if c[1].endswith("/token")] == [("POST", "oauth2.googleapis.com/token")]
    assert store.status()["google"]["status"] == "needs_consent"
    assert len(sent) == 1 and "Google" in sent[0]["message"] and "sign in" in sent[0]["message"].lower()
    # The test call says so instead of calling out.
    mock.calls.clear()
    result = await store.test("google")
    assert result["status"] == "needs_consent" and not mock.calls
    # Consenting again clears it.
    assert (await _connect(store, mock))["ok"] is True
    assert store.status()["google"]["status"] == "connected"


async def test_oauth_status_carries_expires_at_and_no_refresh_error_when_all_is_well(rig):
    _, store, mock, _ = rig
    assert store.status()["google"]["expires_at"] is None
    await _connect(store, mock)
    row = store.status()["google"]
    assert row["last_refresh_error"] is None
    assert abs(row["expires_at"] - (time.time() + 3600)) < 60
    assert ACCESS not in str(store.status()) and REFRESH not in str(store.status())


@pytest.mark.parametrize("answer,reason", [
    (400, "refused"), (401, "refused"), (429, "busy"), (408, "busy"),
    (500, "unreachable"), (503, "unreachable"), ("network", "unreachable"),
])
async def test_oauth_refresh_error_is_a_fixed_word_and_a_good_refresh_clears_it(rig, answer, reason):
    _, store, mock, _ = rig
    await _connect(store, mock)
    store._accounts["google"]["expires_at"] = 0
    secret = "PROVIDER-TEXT-ya29.LEAK"

    def failing(request: httpx.Request) -> httpx.Response:
        if answer == "network":
            raise httpx.ConnectError(secret)
        return httpx.Response(answer, json={"error": secret, "error_description": secret})

    store.transport = httpx.MockTransport(failing)
    store._client = None
    assert await store.access_token("google") is None
    row = store.status()["google"]
    assert row["last_refresh_error"]["reason"] == reason
    assert abs(row["last_refresh_error"]["at"] - time.time()) < 60
    assert secret not in str(row) and ACCESS not in str(row) and REFRESH not in str(row)
    store._load()  # it is stored beside the account, not held in memory only
    assert store.status()["google"]["last_refresh_error"]["reason"] == reason
    if reason == "refused":
        return  # needs_consent: the owner signs in again, which is its own reset (below)
    # The provider recovers: the next ask refreshes and the note is gone.
    store.transport = httpx.MockTransport(mock)
    store._client = None
    assert await store.access_token("google")
    row = store.status()["google"]
    assert row["last_refresh_error"] is None and row["expires_at"] > time.time()


async def test_oauth_a_new_consent_clears_the_refresh_error(rig):
    _, store, mock, _ = rig
    await _connect(store, mock)
    store._accounts["google"]["expires_at"] = 0
    mock.refresh_fails = True
    assert await store.access_token("google") is None
    assert store.status()["google"]["last_refresh_error"]["reason"] == "refused"
    assert (await _connect(store, mock))["ok"] is True
    assert store.status()["google"]["last_refresh_error"] is None


async def test_oauth_refresh_error_for_the_same_reason_saves_once_and_keeps_its_first_time(rig):
    # A provider that stays down used to rewrite the encrypted file on every ask for the same
    # word. Only a changed reason, or a good refresh, is worth a write.
    _, store, mock, _ = rig
    await _connect(store, mock)
    store._accounts["google"]["expires_at"] = 0
    saves: list[int] = []
    save = store._save

    def counted() -> None:
        saves.append(1)
        save()

    store._save = counted

    def answering(status: int):
        def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={})
        return answer

    store.transport = httpx.MockTransport(answering(503))
    store._client = None
    assert await store.access_token("google") is None
    first = store._accounts["google"]["last_refresh_error"]
    assert first["reason"] == "unreachable" and len(saves) == 1
    for _ in range(2):
        assert await store.access_token("google") is None
    assert len(saves) == 1  # the same reason again: nothing written
    assert store._accounts["google"]["last_refresh_error"] is first  # and its time is the first one

    store.transport = httpx.MockTransport(answering(429))
    store._client = None
    assert await store.access_token("google") is None
    assert store._accounts["google"]["last_refresh_error"]["reason"] == "busy"
    assert len(saves) == 2  # a changed reason is written

    store.transport = httpx.MockTransport(mock)
    store._client = None
    assert await store.access_token("google")
    assert store._accounts["google"].get("last_refresh_error") is None
    assert len(saves) == 3  # a good refresh is written too


async def test_oauth_a_refresh_error_is_not_written_for_a_disconnected_account(rig):
    _, store, mock, _ = rig
    await _connect(store, mock)
    store._accounts["google"]["expires_at"] = 0
    in_flight, release = asyncio.Event(), asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/revoke":
            return httpx.Response(200, json={})
        in_flight.set()
        await release.wait()
        return httpx.Response(503)

    store.transport = httpx.MockTransport(slow)
    store._client = None
    ask = asyncio.create_task(store.access_token("google"))
    await in_flight.wait()
    await store.revoke("google")
    release.set()
    assert await ask is None
    store._load()
    assert "google" not in store._accounts


async def test_oauth_a_rejected_access_token_is_needs_consent_not_a_retry(rig):
    _, store, mock, sent = rig
    await _connect(store, mock, "github")
    mock.api_status = 401
    mock.calls.clear()
    result = await store.test("github")
    assert result["status"] == "needs_consent" and len(sent) == 1
    assert [c for c in mock.calls if c[0] == "GET"] and len([c for c in mock.calls if c[0] == "GET"]) == 1


async def test_oauth_a_provider_outage_is_an_error_not_a_lost_account(rig):
    _, store, mock, sent = rig
    await _connect(store, mock, "github")
    mock.api_status = 503
    result = await store.test("github")
    assert result["status"] == "connected" and result["ok"] is False and not sent


async def test_oauth_revoke_tells_the_provider_and_forgets_locally(rig):
    _, store, mock, _ = rig
    await _connect(store, mock)
    result = await store.revoke("google")
    assert result["revoked"] is True and ("POST", "oauth2.googleapis.com/revoke") in mock.calls
    assert store.status()["google"]["status"] == "disconnected"
    assert await store.access_token("google") is None
    # A provider that cannot be reached still loses the local copy.
    await _connect(store, mock, "github")
    mock.api_status = 500

    def boom(request):
        raise httpx.ConnectError("down")

    store.transport = httpx.MockTransport(boom)
    store._client = None
    result = await store.revoke("github")
    assert result["revoked"] is False and store.status()["github"]["status"] == "disconnected"


# --- the providers' test calls ---------------------------------------------------


async def test_google_test_call_reports_calendar_gmail_and_contacts(rig):
    _, store, mock, _ = rig
    await _connect(store, mock)
    mock.calls.clear()
    result = await store.test("google")
    assert result["ok"] is True
    assert {c["name"] for c in result["calls"]} == {"calendar", "gmail", "contacts"}
    assert all(c["ok"] for c in result["calls"])
    hosts = {h for _, h in mock.calls}
    assert {"www.googleapis.com/calendar/v3/users/me/calendarList",
            "gmail.googleapis.com/gmail/v1/users/me/profile",
            "people.googleapis.com/v1/people/me/connections"} <= hosts
    assert not any(m == "POST" and "send" in h for m, h in mock.calls)
    assert store.status()["google"]["last_test"]["ok"] is True


async def test_github_test_call_reports_repos_and_notifications(rig):
    _, store, mock, _ = rig
    await _connect(store, mock, "github")
    result = await store.test("github")
    assert result["ok"] is True and {c["name"] for c in result["calls"]} == {"repos", "notifications"}


async def test_a_test_call_reports_one_scope_missing_and_keeps_the_rest(rig):
    _, store, mock, _ = rig
    await _connect(store, mock)
    real = mock.__call__

    def partial(request):
        if request.url.host == "people.googleapis.com":
            return httpx.Response(403, json={"error": {"message": "<b>no</b>"}})
        return real(request)

    store.transport = httpx.MockTransport(partial)
    store._client = None
    result = await store.test("google")
    by = {c["name"]: c for c in result["calls"]}
    assert result["ok"] is False and by["calendar"]["ok"] and not by["contacts"]["ok"]
    assert by["contacts"]["http"] == 403 and "<b>" not in json.dumps(result)


async def test_a_provider_redirect_is_not_followed(rig):
    _, store, mock, _ = rig
    await _connect(store, mock, "github")

    def redirect(request):
        if request.url.host == "evil.example":
            raise AssertionError("followed a redirect")
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    store.transport = httpx.MockTransport(redirect)
    store._client = None
    result = await store.test("github")
    assert result["ok"] is False


# --- services, the route, the device ---------------------------------------------


async def test_oauth_start_service_puts_the_link_on_the_device_in_use(rig):
    jarvis, store, mock, sent = rig
    out = await jarvis.services.async_call("oauth", "start", {"provider": "github"}, return_response=True)
    out = out.get("service_response", out) if isinstance(out, dict) else out
    assert out["delivered"] is True
    assert sent and "https://github.com/login/oauth/authorize" in sent[0]["message"]
    assert sent[0]["importance"] == "high"
    assert "url" not in out  # delivered: the model does not need the link


async def test_oauth_start_service_hands_back_the_link_when_no_device_answers(rig):
    jarvis, store, mock, sent = rig
    jarvis.services._services["companion"]["notify"].handler = None

    async def down(call):
        return {"status": "queued"}

    jarvis.services.register("companion", "notify", down, supports_response=True)
    out = await jarvis.services.async_call("oauth", "start", {"provider": "github"}, return_response=True)
    out = out.get("service_response", out) if isinstance(out, dict) else out
    assert out["delivered"] is False and out["url"].startswith("https://github.com/")


async def test_oauth_status_service_lists_every_provider_and_holds_no_secret(rig):
    """The console's Accounts section reads this through `call_service` (T-1343): the answer
    is the state of each provider and nothing a provider or the owner's settings wrote."""
    jarvis, store, mock, sent = rig
    await _connect(store, mock)
    await _connect(store, mock, "github")
    await store.test("google")
    out = await jarvis.services.async_call("oauth", "status", {}, return_response=True)
    out = out.get("service_response", out) if isinstance(out, dict) else out
    providers = out["providers"]
    assert list(providers) == list(oauth_integration.PROVIDERS)
    assert providers["google"]["status"] == "connected" and providers["google"]["last_test"]["ok"]
    assert providers["github"]["status"] == "connected"
    assert providers["slack"]["last_test"] is None
    assert providers["spotify"]["status"] == "disconnected"
    shown = json.dumps(out)
    for secret in (ACCESS, REFRESH, GH_TOKEN, *[b["client_id"] for k, b in CONFIG.items() if isinstance(b, dict)],
                   *[b["client_secret"] for k, b in CONFIG.items() if isinstance(b, dict)],
                   "googleapis.com", "calendar.readonly", REDIRECT_BASE):
        assert secret not in shown
    assert set(providers["google"]) == {"label", "configured", "status", "last_test",
                                        "expires_at", "last_refresh_error"}
    assert set(providers["google"]["last_test"]["calls"][0]) == {"name", "ok", "http"}


def test_oauth_callback_route_is_open_and_answers_a_page_a_person_can_read(tmp_path):
    from fastapi.testclient import TestClient

    from aura.api.server import create_app

    async def build():
        jarvis = Jarvis(tmp_path / "config")
        mock = MockProviders()
        jarvis.data["oauth"] = {"transport": httpx.MockTransport(mock)}
        await oauth_integration.async_setup(jarvis, copy.deepcopy(CONFIG))
        return jarvis, mock

    import asyncio

    jarvis, mock = asyncio.run(build())
    app = create_app(jarvis, static_dir=tmp_path / "no-www")
    with TestClient(app) as client:
        r = client.get("/api/oauth/callback", params={"state": "nope", "code": "x"})
        assert r.status_code == 400 and "text/html" in r.headers["content-type"]
        assert "nope" not in r.text  # the state is never echoed


# --- the seven providers added by T-1338 -----------------------------------------
# One table, one mock. What a row needs beyond the shared flow is asserted at the mock (a
# provider that wants Basic auth or a JSON body fails the exchange if it gets the other),
# so the table here is the provider's real shape and the code has to meet it. Nothing
# below reaches a real provider; every token is a short made-up value.

_SLACK_USER = lambda t: {"ok": True, "authed_user": {"id": "U1", "scope": "channels:read",  # noqa: E731
                                                       "access_token": t, "token_type": "user"}}
_PLAIN = lambda t: {"access_token": t, "token_type": "bearer"}  # noqa: E731
_EXPIRING = lambda t, n: {"access_token": t, "refresh_token": f"rt-{n}", "token_type": "bearer"}  # noqa: E731

MORE: dict[str, dict] = {
    "microsoft": dict(
        auth=("login.microsoftonline.com", "/common/oauth2/v2.0/authorize"),
        token=("login.microsoftonline.com", "/common/oauth2/v2.0/token"), revoke=None,
        api="graph.microsoft.com", calls={"profile", "mail", "calendar"}, scope_param="scope",
        pkce=True, basic=False, json=False, expires=3600),
    "slack": dict(
        auth=("slack.com", "/oauth/v2/authorize"), token=("slack.com", "/api/oauth.v2.access"),
        revoke=("slack.com", "/api/auth.revoke"), api="slack.com", calls={"auth", "channels"},
        scope_param="user_scope", pkce=False, basic=False, json=False, expires=None),
    "discord": dict(
        auth=("discord.com", "/oauth2/authorize"), token=("discord.com", "/api/oauth2/token"),
        revoke=("discord.com", "/api/oauth2/token/revoke"), api="discord.com",
        calls={"user", "guilds"}, scope_param="scope", pkce=True, basic=False, json=False,
        expires=604800),
    "gitlab": dict(
        auth=("gitlab.example.test", "/oauth/authorize"),
        token=("gitlab.example.test", "/oauth/token"),
        revoke=("gitlab.example.test", "/oauth/revoke"), api="gitlab.example.test",
        calls={"user", "projects"}, scope_param="scope", pkce=True, basic=False, json=False,
        expires=7200),
    "spotify": dict(
        auth=("accounts.spotify.com", "/authorize"),
        token=("accounts.spotify.com", "/api/token"), revoke=None, api="api.spotify.com",
        calls={"profile", "playlists", "devices"}, scope_param="scope", pkce=True, basic=False,
        json=False, expires=3600),
    "notion": dict(
        auth=("api.notion.com", "/v1/oauth/authorize"), token=("api.notion.com", "/v1/oauth/token"),
        revoke=("api.notion.com", "/v1/oauth/revoke"), api="api.notion.com", calls={"user"},
        scope_param=None, pkce=False, basic=True, json=True, expires=None),
    "todoist": dict(
        auth=("todoist.com", "/oauth/authorize"), token=("todoist.com", "/oauth/access_token"),
        revoke=None, api="api.todoist.com", calls={"projects"}, scope_param="scope", pkce=False,
        basic=False, json=False, expires=None),
}
NEW = sorted(MORE)
REFRESHING = [n for n in NEW if MORE[n]["expires"]]
#: Words that make a scope more than a read. None of the seven may ask for one.
_WRITE = re.compile(r"write|send|modify|manage|admin|compose|update|delete|ReadWrite|full", re.I)


class MockMore:
    """The seven providers on one transport, strict about the shape each one demands."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.challenge: dict[str, str] = {}
        self.current: dict[str, str] = {}  # the access token each API accepts right now
        self.api_status = 200
        self.slack_error = ""
        self.refresh_fails = False

    @staticmethod
    def _fields(request: httpx.Request) -> dict[str, str]:
        if not request.content:
            return {}
        if "json" in request.headers.get("content-type", ""):
            return json.loads(request.content)
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        where = (request.url.host, request.url.path)
        for name, row in MORE.items():
            if where == row["token"]:
                return self._token(name, row, request)
            if where == row["revoke"]:
                return httpx.Response(200, json={"ok": True})
            if request.url.host == row["api"]:
                return self._api(name, row, request)
        return httpx.Response(404)

    def _token(self, name: str, row: dict, request: httpx.Request) -> httpx.Response:
        fields = self._fields(request)
        cid, secret = CONFIG[name]["client_id"], CONFIG[name]["client_secret"]
        if row["basic"]:
            basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
            assert request.headers["authorization"] == f"Basic {basic}"
            assert "client_secret" not in fields
        else:
            assert (fields["client_id"], fields["client_secret"]) == (cid, secret)
        assert ("json" in request.headers["content-type"]) is row["json"]
        if fields["grant_type"] == "refresh_token":
            if self.refresh_fails or fields["refresh_token"] != f"rt-{name}":
                if name == "slack":  # Slack says no with a 200
                    return httpx.Response(200, json={"ok": False, "error": "invalid_refresh_token"})
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.current[name] = f"at-{name}-2"
            body = {"access_token": self.current[name], "expires_in": row["expires"] or 3600}
            if name != "spotify":  # Spotify does not hand the refresh token back
                body["refresh_token"] = f"rt-{name}"
            return httpx.Response(200, json=body)
        assert fields["grant_type"] == "authorization_code"
        assert fields["redirect_uri"] == f"{REDIRECT_BASE}/api/oauth/callback"
        if row["pkce"]:
            digest = hashlib.sha256(fields["code_verifier"].encode()).digest()
            assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == self.challenge[name]
        else:
            assert "code_verifier" not in fields
        if fields["code"] != "good-code":
            if name == "slack":
                return httpx.Response(200, json={"ok": False, "error": "invalid_code"})
            return httpx.Response(400, json={"error": "invalid_grant"})
        token = self.current[name] = f"at-{name}-1"
        if name == "slack":
            return httpx.Response(200, json=_SLACK_USER(token))
        if row["expires"]:
            return httpx.Response(200, json={**_EXPIRING(token, name), "expires_in": row["expires"],
                                             "scope": "x"})
        return httpx.Response(200, json=_PLAIN(token))

    def _api(self, name: str, row: dict, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if name == "notion":
            assert request.headers["notion-version"]
        good = request.headers.get("authorization") == f"Bearer {self.current.get(name)}"
        if name == "slack":  # Slack answers 200 whatever happened; `ok` is the verdict
            err = self.slack_error or ("" if good else "invalid_auth")
            return httpx.Response(200, json={"ok": False, "error": err} if err else {"ok": True})
        if not good:
            return httpx.Response(401, json={})
        if self.api_status != 200:
            return httpx.Response(self.api_status, json={"message": "no"})
        return httpx.Response(200, json={"id": "1"})


@pytest.fixture
async def more(tmp_path):
    jarvis = Jarvis(tmp_path / "config")
    mock = MockMore()
    jarvis.data["oauth"] = {"transport": httpx.MockTransport(mock)}
    sent: list[dict] = []

    async def notify(call):
        sent.append(dict(call.data))
        return {"status": "delivered"}

    jarvis.services.register("companion", "notify", notify, supports_response=True)
    assert await oauth_integration.async_setup(jarvis, copy.deepcopy(CONFIG))
    return jarvis.data["oauth"]["manager"], mock, sent


async def _connect_more(store, mock, name):
    started = await store.start(name)
    q = parse_qs(urlsplit(started["url"]).query)
    mock.challenge[name] = (q.get("code_challenge") or [""])[0]
    return await store.callback({"state": q["state"][0], "code": "good-code"})


@pytest.mark.parametrize("name", NEW)
def test_a_new_provider_has_a_complete_row_and_asks_for_nothing_but_reads(name):
    spec = oauth_integration.PROVIDERS[name]
    web = lambda u: u.replace("{base}", "https://x.test").startswith("https://")  # noqa: E731
    assert spec["label"] and web(spec["auth"]) and web(spec["token"])
    assert {n for n, _ in spec["tests"]} == MORE[name]["calls"]
    assert all(web(url) for _, url in spec["tests"])
    # Only a scope a named write row declares may be a write scope (T-1370: todoist add_task).
    allowed = {w["scope"] for w in spec.get("writes", {}).values()}
    assert not [s for s in spec["scopes"] if _WRITE.search(s) and s not in allowed], spec["scopes"]


@pytest.mark.parametrize("name", NEW)
async def test_new_provider_start_builds_the_consent_url_its_server_expects(more, name):
    store, _, _ = more
    row = MORE[name]
    started = await store.start(name)
    parts = urlsplit(started["url"])
    q = parse_qs(parts.query)
    assert (parts.netloc, parts.path) == row["auth"]
    assert q["client_id"] == [CONFIG[name]["client_id"]] and len(q["state"][0]) >= 32
    assert q["redirect_uri"] == [f"{REDIRECT_BASE}/api/oauth/callback"] and q["response_type"] == ["code"]
    scopes = oauth_integration.PROVIDERS[name]["scopes"]
    for param in ("scope", "user_scope"):
        if param == row["scope_param"]:
            sep = oauth_integration.PROVIDERS[name].get("scope_sep", " ")
            assert q[param] == [sep.join(scopes)]
        else:
            assert param not in q
    if row["scope_param"] is None:
        assert not scopes
    # PKCE where the provider takes it; a provider that validates strictly gets none.
    assert ("code_challenge" in q) is row["pkce"]
    assert (q.get("code_challenge_method") == ["S256"]) is row["pkce"]
    assert CONFIG[name]["client_secret"] not in started["url"]
    if name == "notion":
        assert q["owner"] == ["user"]


@pytest.mark.parametrize("name", NEW)
async def test_new_provider_callback_stores_the_account_whatever_shape_the_token_comes_in(more, name):
    store, mock, _ = more
    result = await _connect_more(store, mock, name)
    assert result["ok"] is True and result["provider"] == name
    assert store.status()[name]["status"] == "connected"
    # What the exchange returned is what the account holds, and the test call used it.
    assert store._accounts[name]["access_token"] == f"at-{name}-1"
    assert result["test"]["ok"] is True
    # A wrong code stores nothing, including Slack's 200-with-ok-false.
    again = await store.revoke(name)
    assert again["provider"] == name
    started = await store.start(name)
    q = parse_qs(urlsplit(started["url"]).query)
    mock.challenge[name] = (q.get("code_challenge") or [""])[0]
    refused = await store.callback({"state": q["state"][0], "code": "bad-code"})
    assert refused["ok"] is False and store.status()[name]["status"] == "disconnected"


@pytest.mark.parametrize("name", NEW)
async def test_new_provider_test_call_is_read_only_and_names_what_it_reached(more, name):
    store, mock, _ = more
    await _connect_more(store, mock, name)
    mock.requests.clear()
    result = await store.test(name)
    assert result["ok"] is True and {c["name"] for c in result["calls"]} == MORE[name]["calls"]
    assert all(r.method == "GET" and r.url.host == MORE[name]["api"] for r in mock.requests)
    assert len(mock.requests) == len(MORE[name]["calls"])
    assert store.status()[name]["last_test"]["ok"] is True
    # The token never comes back out.
    assert f"at-{name}" not in json.dumps(result) + json.dumps(store.status())


@pytest.mark.parametrize("name", REFRESHING)
async def test_new_provider_refreshes_an_expired_token_and_keeps_the_refresh_token(more, name):
    store, mock, _ = more
    await _connect_more(store, mock, name)
    assert store._accounts[name]["expires_at"] is not None
    store._accounts[name]["expires_at"] = 0
    assert await store.access_token(name) == f"at-{name}-2"
    # Spotify answers a refresh without a new refresh token: the old one must survive.
    assert store._accounts[name]["refresh_token"] == f"rt-{name}"
    n = len(mock.requests)
    assert await store.access_token(name) == f"at-{name}-2" and len(mock.requests) == n
    assert (await store.test(name))["ok"] is True


@pytest.mark.parametrize("name", REFRESHING + ["slack"])
async def test_new_provider_refresh_refused_asks_for_consent_once(more, name):
    store, mock, sent = more
    await _connect_more(store, mock, name)
    store._accounts[name]["refresh_token"] = f"rt-{name}"  # Slack's are optional (token rotation)
    mock.refresh_fails = True
    store._accounts[name]["expires_at"] = 0
    mock.requests.clear()
    for _ in range(4):
        assert await store.access_token(name) is None
    assert len(mock.requests) == 1
    assert store.status()[name]["status"] == "needs_consent" and len(sent) == 1


@pytest.mark.parametrize("name", [n for n in NEW if not MORE[n]["expires"] and n != "slack"])
async def test_a_token_that_never_expires_is_used_as_it_is(more, name):
    store, mock, _ = more
    await _connect_more(store, mock, name)
    assert store._accounts[name]["expires_at"] is None
    mock.requests.clear()
    assert await store.access_token(name) == f"at-{name}-1" and not mock.requests


async def test_slack_says_no_with_a_200_and_the_account_still_ends_up_right(more):
    store, mock, sent = more
    await _connect_more(store, mock, "slack")
    mock.slack_error = "ratelimited"  # not a verdict on the account
    result = await store.test("slack")
    assert result["status"] == "connected" and result["ok"] is False and not sent
    mock.slack_error = "token_revoked"
    result = await store.test("slack")
    assert result["status"] == "needs_consent" and result["ok"] is False and len(sent) == 1
    # One first call per test run (the connect, then the two above): the dead token is not retried.
    assert len([r for r in mock.requests if r.url.path == "/api/auth.test"]) == 3
    # The connect and the rate-limited run go on to the second call; the dead token does not.
    assert len([r for r in mock.requests if r.url.path == "/api/conversations.list"]) == 2


async def test_slack_rotating_tokens_refresh_from_the_top_level_answer(more):
    store, mock, _ = more
    await _connect_more(store, mock, "slack")
    store._accounts["slack"].update(refresh_token="rt-slack", expires_at=0)
    assert await store.access_token("slack") == "at-slack-2"
    assert store._accounts["slack"]["expires_at"] is not None


@pytest.mark.parametrize("name", NEW)
async def test_new_provider_revoke_tells_the_provider_how_it_asks_and_always_forgets(more, name):
    store, mock, _ = more
    await _connect_more(store, mock, name)
    mock.requests.clear()
    result = await store.revoke(name)
    assert store.status()[name]["status"] == "disconnected"
    row = MORE[name]
    if row["revoke"] is None:  # no endpoint: say so rather than claim it
        assert result["revoked"] is False and not mock.requests
        return
    assert result["revoked"] is True and len(mock.requests) == 1
    request = mock.requests[0]
    assert (request.url.host, request.url.path) == row["revoke"]
    fields = MockMore._fields(request) if name != "slack" else {}
    token = f"at-{name}-1" if not row["expires"] else f"rt-{name}"
    if name == "slack":
        assert request.headers["authorization"] == f"Bearer {token}"
    else:
        assert fields["token"] == token
    if name in ("notion", "discord"):  # these two want the client as Basic auth
        assert request.headers["authorization"].startswith("Basic ")
        assert ("json" in request.headers["content-type"]) is (name == "notion")
    if name == "gitlab":  # this one wants it in the body
        assert (fields["client_id"], fields["client_secret"]) == ("gl-client", "gl-secret")


async def test_gitlab_uses_the_base_address_from_its_setting_and_never_a_typed_one(more, tmp_path):
    store, mock, _ = more
    started = await store.start("gitlab")
    assert started["url"].startswith("https://gitlab.example.test/oauth/authorize?")  # slash trimmed
    await _connect_more(store, mock, "gitlab")
    await store.test("gitlab")
    assert {r.url.host for r in mock.requests} == {"gitlab.example.test"}
    assert any(r.url.path == "/api/v4/user" for r in mock.requests)
    # Unset, the hosted service; set to something that is not a web address, a refusal that
    # sends nothing anywhere.
    bare = Jarvis(tmp_path / "bare")
    cfg = {"redirect_base": REDIRECT_BASE, "gitlab": {"client_id": "gl-client", "client_secret": "s"}}
    await oauth_integration.async_setup(bare, cfg)
    assert (await bare.data["oauth"]["manager"].start("gitlab"))["url"].startswith(
        "https://gitlab.com/oauth/authorize?")
    bad = Jarvis(tmp_path / "bad")
    cfg["gitlab"]["base_url"] = "ftp://gitlab.example.test"
    await oauth_integration.async_setup(bad, cfg)
    assert "base" in (await bad.data["oauth"]["manager"].start("gitlab"))["error"]


async def test_gitlab_refresh_sends_the_redirect_uri_its_docs_ask_for(more):
    store, mock, _ = more
    await _connect_more(store, mock, "gitlab")
    store._accounts["gitlab"]["expires_at"] = 0
    mock.requests.clear()
    await store.access_token("gitlab")
    assert MockMore._fields(mock.requests[0])["redirect_uri"] == f"{REDIRECT_BASE}/api/oauth/callback"


async def test_status_lists_every_provider_and_marks_unconfigured_ones(tmp_path):
    bare = Jarvis(tmp_path / "bare")
    await oauth_integration.async_setup(bare, {"redirect_base": REDIRECT_BASE,
                                               "notion": CONFIG["notion"]})
    status = bare.data["oauth"]["manager"].status()
    assert set(status) == {"google", "github", *NEW}
    assert status["notion"]["status"] == "disconnected" and status["notion"]["configured"] is True
    assert all(status[n]["status"] == "not_configured" for n in NEW if n != "notion")


# Typed here, not read back from the table: a wrong scope or a wrong test path must fail.
PINNED = {
    "microsoft": (["offline_access", "User.Read", "Mail.Read", "Calendars.Read"],
                  ["/v1.0/me", "/v1.0/me/messages", "/v1.0/me/events"]),
    "slack": (["channels:read"], ["/api/auth.test", "/api/conversations.list"]),
    "discord": (["identify", "guilds"], ["/api/v10/users/@me", "/api/v10/users/@me/guilds"]),
    "gitlab": (["read_api"], ["/api/v4/user", "/api/v4/projects"]),
    "spotify": (["playlist-read-private", "user-read-playback-state"],
                ["/v1/me", "/v1/me/playlists", "/v1/me/player/devices"]),
    "notion": ([], ["/v1/users/me"]),
    "todoist": (["data:read", "task:add"], ["/api/v1/projects"]),  # T-1370: read + add-only
}


@pytest.mark.parametrize("name", NEW)
def test_new_provider_scopes_and_test_paths_are_the_pinned_ones(name):
    spec = oauth_integration.PROVIDERS[name]
    paths = [urlsplit(url.replace("{base}", "https://h.example.test")).path for _, url in spec["tests"]]
    assert (spec["scopes"], paths) == PINNED[name]


@pytest.mark.parametrize("base,ok", [
    ("https://gitlab.example.test", True),
    ("http://10.0.0.7:8929", True),            # plain http only to a private address literal
    ("http://gitlab.example.test", False),     # the client secret would cross in clear
    ("http://8.8.8.8", False),
    ("https://user@gitlab.example.test", False),
    ("https://gitlab.example.test?x=1", False),
    ("https://gitlab example.test", False),
    ("ftp://gitlab.example.test", False),
])
async def test_gitlab_base_is_https_or_a_private_address_and_nothing_else(more, base, ok):
    store, _, _ = more
    store.config["gitlab"]["base_url"] = base
    assert (store._base("gitlab") == base) is ok
    assert ("url" in await store.start("gitlab")) is ok


@pytest.mark.parametrize("code", [408, 429])
async def test_a_rate_limited_refresh_keeps_the_account_connected(more, monkeypatch, code):
    store, mock, _ = more
    await _connect_more(store, mock, "spotify")
    store._accounts["spotify"]["expires_at"] = 0

    async def busy(provider, fields):
        return code, {"error": "slow_down"}
    monkeypatch.setattr(store, "_token_call", busy)
    assert await store.access_token("spotify") is None
    assert store.status()["spotify"]["status"] == "connected"


async def test_slack_refresh_answer_carrying_only_the_user_id_still_yields_the_token(more, monkeypatch):
    store, mock, _ = more
    await _connect_more(store, mock, "slack")
    store._accounts["slack"].update(refresh_token="rt-slack", expires_at=0)

    async def flat(provider, fields):
        return 200, {"ok": True, "access_token": "at-flat", "refresh_token": "rt-slack",
                     "expires_in": 3600, "authed_user": {"id": "U1"}}
    monkeypatch.setattr(store, "_token_call", flat)
    assert await store.access_token("slack") == "at-flat"


@pytest.mark.parametrize("name", [n for n in NEW if not oauth_integration.PROVIDERS[n]["revoke"]])
async def test_revoke_without_a_provider_call_says_where_to_remove_the_app(more, name):
    store, mock, _ = more
    await _connect_more(store, mock, name)
    out = await store.revoke(name)
    assert out["revoked"] is False and "provider's account settings" in out["message"]


def test_an_infinite_or_absurd_token_lifetime_is_capped_not_fatal():
    # json.loads accepts Infinity, and int(inf) raises OverflowError in the callback.
    make = oauth_integration.Connectors._account
    for lifetime in (float("inf"), 1e300):
        acct = make({"expires_in": lifetime}, "t", None)
        assert 0 < acct["expires_at"] - time.time() <= 86400 * 30 + 1
    assert make({"expires_in": float("nan")}, "t", None)["expires_at"] is None


# --- named reads (T-1341) ---------------------------------------------------------------
# One answer per read, in the shape the provider's public docs give (none of it was tried
# against a real provider, GAPS G18), so the keep-list has to meet it. Every token is a short
# made-up value; every address is a provider's public API host or the made-up GitLab base.

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts"
                       / "connect-reads.json").read_text())["reads"]
ALL_READS = [(p, w) for p, names in CONTRACT.items() for w in names]
READ_TOKEN = "at-fake-1"
# Typed here, not read back from the table: a read pointed at another host must fail.
READ_HOSTS = {"google": {"www.googleapis.com", "people.googleapis.com"}, "github": {"api.github.com"},
              "microsoft": {"graph.microsoft.com"}, "slack": {"slack.com"}, "discord": {"discord.com"},
              "gitlab": {"gitlab.example.test"}, "spotify": {"api.spotify.com"},
              "notion": {"api.notion.com"}, "todoist": {"api.todoist.com"}}

CANNED: dict[tuple[str, str], tuple[Any, dict]] = {
    ("google", "calendars"): ({"items": [{"summary": "Home", "primary": True, "accessRole": "owner",
                                          "id": "x@example.test"}]}, {"name": "Home", "primary": True}),
    ("google", "events"): ({"items": [{"summary": "Dentist", "start": {"dateTime": "2026-11-20T09:00:00Z"},
                                       "end": {"dateTime": "2026-11-20T10:00:00Z"},
                                       "location": "Clinic", "htmlLink": "https://x.test/e"}]},
                           {"title": "Dentist", "start": "2026-11-20T09:00:00Z", "where": "Clinic"}),
    ("google", "contacts"): ({"connections": [{"names": [{"displayName": "Sample Person"}],
                                               "resourceName": "people/1"}]},
                             {"name": "Sample Person"}),
    ("github", "repos"): ([{"full_name": "sample/tool", "private": True, "language": "Go",
                            "description": "A tool", "pushed_at": "2026-11-01T00:00:00Z",
                            "owner": {"login": "sample"}}],
                          {"name": "sample/tool", "private": True, "language": "Go"}),
    ("github", "notifications"): ([{"subject": {"title": "Fix it", "type": "PullRequest",
                                                 "url": "https://api.github.com/x"},
                                    "repository": {"full_name": "sample/tool"}, "reason": "mention",
                                    "unread": True, "updated_at": "2026-11-02T00:00:00Z"}],
                                  {"title": "Fix it", "kind": "PullRequest", "repo": "sample/tool"}),
    ("microsoft", "inbox"): ({"value": [{"subject": "Lunch?", "receivedDateTime": "2026-11-13T10:00:00Z",
                                         "isRead": False, "body": {"content": "SECRET BODY"},
                                         "from": {"emailAddress": {"name": "Sample Person",
                                                                   "address": "sp@example.test"}}}]},
                             {"subject": "Lunch?", "from": "Sample Person", "address": "sp@example.test"}),
    ("microsoft", "calendar"): ({"value": [{"subject": "Standup", "isAllDay": False,
                                            "start": {"dateTime": "2026-11-20T09:00:00.0000000"},
                                            "end": {"dateTime": "2026-11-20T09:15:00.0000000"},
                                            "location": {"displayName": "Room 1"}}]},
                                {"title": "Standup", "where": "Room 1"}),
    ("slack", "channels"): ({"ok": True, "channels": [{"name": "general", "num_members": 7,
                                                        "topic": {"value": "Hello"},
                                                        "purpose": {"value": ""}}]},
                            {"name": "general", "members": 7, "topic": "Hello"}),
    ("discord", "servers"): ([{"name": "Club", "owner": False, "permissions": "8"}],
                             {"name": "Club", "owner": False}),
    ("gitlab", "projects"): ([{"path_with_namespace": "grp/proj", "visibility": "private",
                               "last_activity_at": "2026-11-10T00:00:00Z", "description": None}],
                             {"name": "grp/proj", "visibility": "private"}),
    ("gitlab", "issues"): ([{"title": "Crash", "references": {"full": "grp/proj#4"},
                             "updated_at": "2026-11-11T00:00:00Z", "web_url": "https://x.test/i"}],
                           {"title": "Crash", "project": "grp/proj#4"}),
    ("spotify", "playlists"): ({"items": [{"name": "Morning", "tracks": {"total": 12},
                                           "owner": {"display_name": "Sample Person"},
                                           "public": False}]},
                               {"name": "Morning", "tracks": 12, "owner": "Sample Person"}),
    ("spotify", "devices"): ({"devices": [{"name": "Kitchen", "type": "Speaker", "is_active": True,
                                           "volume_percent": 30, "id": "abc"}]},
                             {"name": "Kitchen", "active": True, "volume": 30}),
    ("spotify", "playing"): ({"is_playing": True, "item": {"name": "Song", "album": {"name": "LP"},
                                                            "artists": [{"name": "A"}, {"name": "B"}]},
                              "device": {"name": "Kitchen"}},
                             {"track": "Song", "artists": "A, B", "device": "Kitchen", "playing": True}),
    ("notion", "me"): ({"object": "user", "name": "Sample Person", "type": "person",
                        "person": {"email": "sp@example.test"}},
                       {"name": "Sample Person", "type": "person"}),
    ("todoist", "projects"): ({"results": [{"name": "Inbox", "is_favorite": False, "id": "1"}]},
                              {"name": "Inbox", "favorite": False}),
    ("todoist", "tasks"): ({"results": [{"content": "Buy milk", "priority": 2, "labels": ["home", "shop"],
                                         "due": {"date": "2026-11-15"}, "description": "long"}]},
                           {"task": "Buy milk", "due": "2026-11-15", "labels": "home, shop"}),
    ("todoist", "due"): ({"results": [{"content": "Pay rent", "priority": 4, "labels": [],
                                       "due": {"date": "2026-11-14"}, "description": "long"}]},
                         {"task": "Pay rent", "due": "2026-11-14", "priority": 4}),
}


class ReadMock:
    """Answers each provider's API from one place and records what it was asked."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer: Any = None
        self.status = 200
        self.headers: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, headers=self.headers,
                              content=b"" if self.answer is None else self.answer
                              if isinstance(self.answer, bytes) else json.dumps(self.answer).encode())


@pytest.fixture
async def rd(tmp_path):
    jarvis = Jarvis(tmp_path / "config")
    mock = ReadMock()
    jarvis.data["oauth"] = {"transport": httpx.MockTransport(mock)}
    # A copy with the base set here: earlier tests edit the shared CONFIG's GitLab address.
    config = json.loads(json.dumps(CONFIG))
    config["gitlab"]["base_url"] = "https://gitlab.example.test"
    await oauth_integration.async_setup(jarvis, config)
    store = jarvis.data["oauth"]["manager"]
    for name in oauth_integration.PROVIDERS:
        store._accounts[name] = store._account({"expires_in": 3600}, READ_TOKEN, None)
    return jarvis, store, mock


def test_the_reads_tables_name_exactly_the_contract_and_only_fixed_hosts():
    # The Hermes suite reads the same contract file: a name added on one side fails the other.
    table = {p: sorted(spec.get("reads", {})) for p, spec in oauth_integration.PROVIDERS.items()}
    assert table == {p: sorted(n) for p, n in CONTRACT.items()}
    assert set(CANNED) == set(ALL_READS)
    for provider, name in ALL_READS:
        spec = oauth_integration.PROVIDERS[provider]
        read = spec["reads"][name]
        url = urlsplit(read["url"].replace("{base}", "https://gitlab.example.test"))
        tested = {urlsplit(u.replace("{base}", "https://gitlab.example.test")).hostname
                  for _, u in spec["tests"]}
        # Same host the row's own test calls reach, over https, never a path the model supplies.
        assert url.scheme == "https" and url.hostname in tested, (provider, name)
        assert not url.query and "{" not in url.path, (provider, name)
        assert set(read) <= {"url", "q", "limit", "query", "window", "rows", "keep", "about"}, (provider, name)
        assert read["keep"] and read["about"], (provider, name)


@pytest.mark.parametrize("provider,what", ALL_READS)
async def test_a_named_read_answers_a_clipped_summary_from_the_providers_fixed_host(rd, provider, what):
    jarvis, store, mock = rd
    body, expect = CANNED[(provider, what)]
    mock.answer = body
    out = await store.read(provider, what)
    assert out["provider"] == provider and out["what"] == what and out["count"] == 1, out
    item = out["items"][0]
    assert {k: item[k] for k in expect} == expect
    (request,) = mock.requests
    assert request.method == "GET" and request.url.host in READ_HOSTS[provider]
    assert request.headers["authorization"] == f"Bearer {READ_TOKEN}"
    # Nothing the keep-list does not name comes back: no id, body or link.
    flat = json.dumps(out)
    assert "SECRET BODY" not in flat and "http" not in flat
    assert READ_TOKEN not in flat and READ_TOKEN not in str(request.url)


async def test_a_read_through_the_house_service_matches_the_method(rd):
    jarvis, store, mock = rd
    mock.answer = CANNED[("spotify", "devices")][0]
    out = await jarvis.services.async_call(
        "oauth", "read", {"provider": "Spotify", "what": "devices"}, blocking=True,
        return_response=True)
    assert out["items"][0]["name"] == "Kitchen"
    # A caller cannot name a method, an address or a header: any key beyond the four is refused,
    # and nothing went to the provider for it.
    mock.requests.clear()
    for extra in ({"method": "POST"}, {"url": "https://evil.test/"}, {"headers": {"x": "y"}}):
        out = await jarvis.services.async_call(
            "oauth", "read", {"provider": "spotify", "what": "devices", **extra}, blocking=True,
            return_response=True)
        assert "error" in out and not mock.requests, extra


async def test_an_unknown_read_an_unknown_provider_and_a_disconnected_account_are_clean_answers(rd):
    _, store, mock = rd
    assert "no read" in (await store.read("github", "delete_repo"))["error"]
    assert "no read" in (await store.read("github", "../../user"))["error"]
    assert "unknown provider" in (await store.read("evil", "repos"))["error"]
    store._accounts.pop("github")
    assert "not connected" in (await store.read("github", "repos"))["error"]
    store._accounts["spotify"].update(status="needs_consent", access_token=None, expires_at=None)
    assert "sign in again" in (await store.read("spotify", "devices"))["error"]
    assert mock.requests == []


async def test_a_provider_error_is_a_clean_answer_that_echoes_nothing_it_wrote(rd):
    _, store, mock = rd
    mock.status, mock.answer = 500, {"message": "SECRET-DETAIL at-fake-1"}
    out = await store.read("github", "repos")
    assert out == {"error": "GitHub answered HTTP 500"}
    mock.status = 403
    assert "refused" in (await store.read("github", "repos"))["error"]
    mock.status, mock.answer = 200, b"<html>not json"
    assert "error" in await store.read("github", "repos")
    assert store.status()["github"]["status"] == "connected"  # none of that is a verdict on the account


async def test_a_dead_token_ends_in_needs_consent_once_like_the_test_call(rd):
    _, store, mock = rd
    mock.status = 401
    assert "error" in await store.read("github", "repos")
    assert store.status()["github"]["status"] == "needs_consent"
    mock.requests.clear()
    assert "sign in again" in (await store.read("github", "repos"))["error"]
    assert mock.requests == []  # nothing calls the provider again until the owner consents


async def test_slack_says_no_with_a_200_and_a_read_hears_it(rd):
    _, store, mock = rd
    mock.answer = {"ok": False, "error": "ratelimited"}
    assert "error" in await store.read("slack", "channels")
    assert store.status()["slack"]["status"] == "connected"
    mock.answer = {"ok": False, "error": "token_revoked"}
    assert "error" in await store.read("slack", "channels")
    assert store.status()["slack"]["status"] == "needs_consent"


async def test_a_dead_network_is_an_error_not_a_raise(rd, monkeypatch):
    _, store, _ = rd

    async def boom(*a, **k):
        raise httpx.ConnectError("no")
    monkeypatch.setattr(store, "_http", boom)
    assert "could not be reached" in (await store.read("notion", "me"))["error"]


async def test_a_redirect_is_not_followed_and_the_token_goes_nowhere_else(rd):
    _, store, mock = rd
    mock.status, mock.headers = 302, {"location": "https://evil.test/steal"}
    out = await store.read("github", "repos")
    assert "HTTP 302" in out["error"] and len(mock.requests) == 1
    assert "evil" not in json.dumps(out)


async def test_every_read_goes_only_to_the_provider_it_names_with_the_token_in_the_header_alone(rd):
    _, store, mock = rd
    for provider, what in ALL_READS:
        mock.answer, mock.requests[:] = CANNED[(provider, what)][0], []
        await store.read(provider, what, query="x" if oauth_integration.PROVIDERS[provider][
            "reads"][what].get("query") else "")
        (request,) = mock.requests
        assert request.method == "GET" and not request.content, (provider, what)
        assert READ_TOKEN not in str(request.url), (provider, what)
        assert "cookie" not in request.headers
        assert request.url.host in READ_HOSTS[provider], (provider, what)


async def test_gitlab_reads_use_the_checked_base_and_send_nothing_when_it_is_not_one(rd):
    _, store, mock = rd
    mock.answer = CANNED[("gitlab", "projects")][0]
    await store.read("gitlab", "projects")
    assert mock.requests[0].url.host == "gitlab.example.test"
    mock.requests.clear()
    for base in ("http://gitlab.example.test", "https://u@gitlab.example.test", "ftp://x", ""):
        store.config["gitlab"]["base_url"] = base
        if not base:
            continue
        out = await store.read("gitlab", "projects")
        assert "base address" in out["error"], base
    assert mock.requests == []


async def test_a_query_reaches_only_the_one_parameter_its_read_allows_and_is_capped(rd):
    _, store, mock = rd
    mock.answer = CANNED[("gitlab", "projects")][0]
    await store.read("gitlab", "projects", query="a&scope=admin#x\n" + "z" * 500)
    q = parse_qs(mock.requests[0].url.query.decode())
    assert set(q) == {"membership", "order_by", "per_page", "search"}, q
    assert len(q["search"][0]) <= oauth_integration.READ_QUERY_MAX and "\n" not in q["search"][0]
    assert q["search"][0].startswith("a&scope=admin#x")  # carried as text, not as a parameter
    # A read with no query parameter refuses one instead of dropping it silently.
    out = await store.read("github", "repos", query="anything")
    assert "takes no query" in out["error"]
    assert len(mock.requests) == 1


async def test_limit_is_parsed_without_raising_and_clamped(rd):
    _, store, mock = rd
    mock.answer = [{"name": f"c{i}"} for i in range(100)]
    for raw, want in ((None, oauth_integration.READ_DEFAULT), ("x", oauth_integration.READ_DEFAULT),
                      (float("inf"), oauth_integration.READ_DEFAULT), (float("nan"), oauth_integration.READ_DEFAULT),
                      (-3, 1), (0, 1), ("4", 4), (3.9, 3), (10**9, oauth_integration.READ_ROWS),
                      (True, 1), ([], oauth_integration.READ_DEFAULT)):
        out = await store.read("discord", "servers", limit=raw)
        assert out["count"] == want, (raw, out.get("error"))
        sent = parse_qs(mock.requests[-1].url.query.decode())["limit"][0]
        assert int(sent) == want


async def test_strings_are_cleaned_clipped_and_numbers_never_raise(rd):
    _, store, mock = rd
    huge = 10 ** 400
    mock.answer = (b'[{"name": "a\\u0000b\\u202ec\\nd\\te", "owner": 1e999},'
                   b' {"name": "' + b"x" * 5000 + b'", "owner": ' + str(huge).encode() + b"},"
                   b' {"name": {"nested": 1}, "owner": -Infinity}, {"name": 5, "owner": 12}]')
    out = await store.read("discord", "servers")
    names = [i.get("name") for i in out["items"]]
    assert names[0] == "abc d e"  # NUL and the bidi override gone, newline and tab read as spaces
    assert all(len(n) <= oauth_integration.READ_CLIP for n in names if isinstance(n, str))
    assert "Infinity" not in json.dumps(out) and "NaN" not in json.dumps(out)
    json.loads(json.dumps(out, allow_nan=False))
    assert out["items"][-1] == {"name": 5, "owner": 12}
    assert len(out["items"]) == 3  # the row with nothing readable in it is not an item


async def test_the_answer_is_capped_in_rows_and_in_total_size(rd):
    _, store, mock = rd
    mock.answer = [{"name": "n" * 120, "owner": True} for _ in range(300)]
    out = await store.read("discord", "servers", limit=10**6)
    assert out["count"] <= oauth_integration.READ_ROWS
    assert len(json.dumps(out)) <= oauth_integration.READ_BYTES
    wide = [{"title": "t" * 120, "references": {"full": "p" * 120}, "updated_at": "u" * 120}
            for _ in range(25)]
    mock.answer = wide
    out = await store.read("gitlab", "issues", limit=25)
    assert len(json.dumps(out)) <= oauth_integration.READ_BYTES
    assert out["truncated"] is True and out["count"] == len(out["items"]) < 25


async def test_nothing_playing_is_an_empty_answer_not_an_error(rd):
    _, store, mock = rd
    mock.status, mock.answer = 204, None
    out = await store.read("spotify", "playing")
    assert out["count"] == 0 and out["items"] == []


async def test_windowed_reads_ask_for_a_fixed_look_ahead_and_nothing_else_time_shaped(rd):
    _, store, mock = rd
    mock.answer = CANNED[("microsoft", "calendar")][0]
    await store.read("microsoft", "calendar")
    q = parse_qs(mock.requests[0].url.query.decode())
    assert set(q) == {"$select", "$orderby", "startDateTime", "endDateTime", "$top"}, q
    assert q["startDateTime"][0].endswith("Z") and q["endDateTime"][0] > q["startDateTime"][0]


async def test_a_read_logs_nothing_that_holds_the_token(rd, caplog):
    _, store, mock = rd
    mock.status = 500
    with caplog.at_level("DEBUG"):
        await store.read("github", "repos")
    assert READ_TOKEN not in caplog.text


# Typed here, not read back from the table: a wrong path or parameter name must fail.
READ_SHAPES = {
    ("google", "calendars"): ("/calendar/v3/users/me/calendarList", {"maxResults"}),
    ("google", "events"): ("/calendar/v3/calendars/primary/events",
                           {"maxResults", "orderBy", "q", "singleEvents", "timeMax", "timeMin"}),
    ("google", "contacts"): ("/v1/people/me/connections", {"pageSize", "personFields"}),
    ("github", "repos"): ("/user/repos", {"per_page", "sort"}),
    ("github", "notifications"): ("/notifications", {"per_page"}),
    ("microsoft", "inbox"): ("/v1.0/me/mailFolders/inbox/messages", {"$orderby", "$select", "$top"}),
    ("microsoft", "calendar"): ("/v1.0/me/calendarView",
                                {"$orderby", "$select", "$top", "endDateTime", "startDateTime"}),
    ("slack", "channels"): ("/api/conversations.list", {"exclude_archived", "limit", "types"}),
    ("discord", "servers"): ("/api/v10/users/@me/guilds", {"limit"}),
    ("gitlab", "projects"): ("/api/v4/projects", {"membership", "order_by", "per_page", "search"}),
    ("gitlab", "issues"): ("/api/v4/issues", {"per_page", "scope", "search", "state"}),
    ("spotify", "playlists"): ("/v1/me/playlists", {"limit"}),
    ("spotify", "devices"): ("/v1/me/player/devices", set()),
    ("spotify", "playing"): ("/v1/me/player", set()),
    ("notion", "me"): ("/v1/users/me", set()),
    ("todoist", "projects"): ("/api/v1/projects", {"limit"}),
    ("todoist", "tasks"): ("/api/v1/tasks", {"limit"}),
    ("todoist", "due"): ("/api/v1/tasks/filter", {"limit", "query"}),
}


async def test_every_read_asks_the_pinned_path_with_the_pinned_parameter_names(rd):
    _, store, mock = rd
    assert set(READ_SHAPES) == set(ALL_READS)
    for (provider, what), (path, names) in READ_SHAPES.items():
        mock.answer, mock.requests[:] = CANNED[(provider, what)][0], []
        await store.read(provider, what, query="x" if {"q", "search"} & names else "")
        (request,) = mock.requests
        assert request.url.path == path, (provider, what)
        assert set(request.url.params.keys()) == names, (provider, what)


async def test_todoist_due_sends_its_fixed_filter_beside_the_limit_and_nothing_the_caller_picks(rd):
    """T-1415: the filter is the row's, in the one query string the engine builds (a URL with
    its own query and a params argument is where clients drop one). GUESS: that Todoist's
    `/tasks/filter` takes `query`; this pins what the house sends, not what Todoist accepts."""
    _, store, mock = rd
    mock.answer = CANNED[("todoist", "due")][0]
    out = await store.read("todoist", "due", limit=5)
    (request,) = mock.requests
    assert request.method == "GET" and request.url.host == "api.todoist.com"
    assert request.url.path == "/api/v1/tasks/filter"
    assert parse_qs(request.url.query.decode()) == {"query": ["today | overdue"], "limit": ["5"]}
    assert out["items"] == [{"task": "Pay rent", "due": "2026-11-14", "priority": 4}]
    # The caller cannot swap the filter: a query text is refused, as for every read without one.
    mock.requests[:] = []
    refused = await store.read("todoist", "due", query="all")
    assert refused == {"error": "the due read takes no query"} and mock.requests == []
    # A failed read is one honest error, not a half answer the brief could mistake for "no tasks".
    mock.status = 500
    assert await store.read("todoist", "due") == {"error": "Todoist answered HTTP 500"}


async def test_a_list_of_whole_objects_larger_than_the_small_cap_is_still_read(rd):
    _, store, mock = rd
    mock.answer = [{"full_name": f"o/r{i}", "pad": "x" * 6000} for i in range(20)]  # about 120 kB
    out = await store.read("github", "repos", limit=20)
    assert "error" not in out and out["count"] == 20


async def test_a_read_answer_cut_at_the_cap_says_too_large_and_to_ask_for_fewer_rows(rd):
    _, store, mock = rd
    secret = "CUTBODYSECRET"
    # Valid JSON, but past READ_BODY: the cut makes it unparseable, and that is not "unreadable".
    mock.answer = json.dumps([{"full_name": secret, "pad": "x" * 6000}] * 100).encode()
    mock.answer += b" " * oauth_integration.READ_BODY
    out = await store.read("github", "repos", limit=20)
    assert out == {"error": "GitHub's answer was too large to read (over 512 KB); "
                            "ask for fewer rows with a smaller limit"}
    assert secret not in json.dumps(out) and READ_TOKEN not in json.dumps(out)
    # A 200 of exactly the cap is whole, and an oversize error page keeps its status wording.
    mock.answer = b"[" + b" " * (oauth_integration.READ_BODY - 3) + b"]"
    assert (await store.read("github", "repos")).get("count") == 0
    mock.status, mock.answer = 500, b"x" * (oauth_integration.READ_BODY + 5)
    assert (await store.read("github", "repos"))["error"] == "GitHub answered HTTP 500"
    # Slack says 200 whatever happened: a cut answer there is still too large, not "an error".
    mock.status, mock.answer = 200, b'{"ok": true, "channels": [' + b" " * oauth_integration.READ_BODY
    assert "too large" in (await store.read("slack", "channels"))["error"]


async def test_a_body_nested_past_the_parser_is_an_answer_not_a_crash(rd):
    _, store, mock = rd
    mock.answer = b"[" * 50000
    assert "error" in await store.read("github", "repos")


async def test_a_row_with_nothing_kept_is_not_counted(rd):
    _, store, mock = rd
    mock.answer = [{"unrelated": 1}, CANNED[("github", "repos")][0][0]]
    assert (await store.read("github", "repos"))["count"] == 1


# --- the daily check (T-1367) --------------------------------------------------------
class _DayClock:
    """A clock that sleeps until the test says a day has passed; no wall time is used."""

    def __init__(self) -> None:
        from datetime import datetime, timezone
        self.current = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        self._tick: asyncio.Queue[None] = asyncio.Queue()
        self.parked = asyncio.Event()

    def now(self):
        return self.current

    async def sleep(self, seconds: float) -> None:
        from datetime import timedelta
        self.parked.set()
        await self._tick.get()
        self.current += timedelta(seconds=seconds)

    async def next_day(self) -> None:
        """Let the check fire once and come back to sleep."""
        self.parked.clear()
        self._tick.put_nowait(None)
        await asyncio.wait_for(self.parked.wait(), 5)


async def _daily_rig(rig):
    from aura.automation.util import DATA_CLOCK
    jarvis, store, mock, sent = rig
    clock = _DayClock()
    jarvis.data[DATA_CLOCK] = clock
    check = oauth_integration.DailyCheck(jarvis, store)
    check.start()
    await asyncio.wait_for(clock.parked.wait(), 5)
    return jarvis, store, mock, sent, clock, check


async def test_daily_check_tells_the_owner_once_for_a_refusing_provider_and_not_for_a_working_one(rig):
    jarvis, store, mock, sent, clock, check = await _daily_rig(rig)
    await _connect(store, mock, "google")
    await _connect(store, mock, "github")
    store._accounts["google"]["expires_at"] = 0
    mock.refresh_fails = True
    for _ in range(3):
        await clock.next_day()
    assert len(sent) == 1 and "Google" in sent[0]["message"]
    assert store.status()["google"]["status"] == "needs_consent"
    assert store.status()["github"]["status"] == "connected"  # never expires: nothing to ask
    assert len([c for c in mock.calls if c[1] == "oauth2.googleapis.com/token"
                and c[0] == "POST"]) == 2  # the consent's own call and the one refresh
    store._load()  # a restart reads the saved needs_consent: still silent
    await clock.next_day()
    assert len(sent) == 1
    await check.stop()


async def test_daily_check_is_silent_for_a_working_account_and_keeps_it_fresh(rig):
    jarvis, store, mock, sent, clock, check = await _daily_rig(rig)
    await _connect(store, mock, "google")
    for _ in range(3):
        store._accounts["google"]["expires_at"] = 0
        await clock.next_day()
        assert store._accounts["google"]["expires_at"] > time.time()
    assert not sent and store.status()["google"]["status"] == "connected"
    await check.stop()


@pytest.mark.parametrize("code", [0, 429, 503])
async def test_daily_check_does_not_tell_the_owner_of_a_busy_or_unreachable_provider(rig, monkeypatch, code):
    jarvis, store, mock, sent, clock, check = await _daily_rig(rig)
    await _connect(store, mock, "google")
    store._accounts["google"]["expires_at"] = 0

    async def down(provider, fields):
        if code == 0:
            raise httpx.ConnectError("no route")
        return code, {"error": "later"}
    monkeypatch.setattr(store, "_token_call", down)
    for _ in range(3):
        await clock.next_day()
    assert not sent and store.status()["google"]["status"] == "connected"
    await check.stop()


async def test_daily_check_survives_one_account_raising_and_still_checks_the_rest(rig, monkeypatch):
    jarvis, store, mock, sent, clock, check = await _daily_rig(rig)
    await _connect(store, mock, "google")
    await _connect(store, mock, "github")
    store._accounts["google"]["expires_at"] = 0
    asked: list[str] = []
    real = store.access_token

    async def flaky(provider):
        asked.append(provider)
        if provider == "github":
            raise RuntimeError("boom")
        return await real(provider)
    monkeypatch.setattr(store, "access_token", flaky)
    await clock.next_day()
    assert "github" in asked and "google" in asked
    await clock.next_day()  # the schedule is alive after the failure
    assert asked.count("google") == 2
    await check.stop()


async def test_daily_check_starts_with_the_house_and_stops_at_shutdown(tmp_path):
    jarvis = Jarvis(tmp_path / "config")
    jarvis.data["oauth"] = {"transport": httpx.MockTransport(MockProviders())}
    assert await oauth_integration.async_setup(jarvis, copy.deepcopy(CONFIG))
    check = jarvis.data["oauth"]["daily_check"]
    assert check._task is None  # the house has not started yet
    await jarvis.async_start()
    await asyncio.sleep(0)
    task = check._task
    assert task is not None and not task.done()
    await jarvis.async_stop()
    assert check._task is None and task.done()


# --- the first write: Todoist add_task (T-1370) -----------------------------------------------
# A fake provider only. The Todoist scope name and add-task path are guesses (see the PROVIDERS
# row), so the paths and the scope are typed here: a row that drifts must fail a test, not pass.

WRITE_CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts"
                             / "connect-reads.json").read_text())["writes"]
#: Read plus add-only, the narrowest pair (a guess at Todoist's names, like the path).
ASKED = ["data:read", "task:add"]
WRITE_SCOPE = "task:add"
RECONNECT = "Todoist was connected for reading only: reconnect Todoist to allow adding tasks"


def _can_write(store) -> None:
    store._accounts["todoist"]["scope"] = ",".join(ASKED)


def test_the_write_table_names_exactly_the_contract_and_only_one_fixed_todoist_path():
    table = {p: sorted(spec["writes"]) for p, spec in oauth_integration.PROVIDERS.items()
             if spec.get("writes")}
    assert table == {p: sorted(n) for p, n in WRITE_CONTRACT.items()} == {"todoist": ["add_task"]}
    row = oauth_integration.PROVIDERS["todoist"]["writes"]["add_task"]
    url = urlsplit(row["url"])
    assert (url.scheme, url.hostname, url.path, url.query) == ("https", "api.todoist.com",
                                                               "/api/v1/tasks", "")
    # Never the broad read-write scope: the narrowest pair is what a consent asks for, and the
    # broad one only passes when an account already holds it.
    assert oauth_integration.PROVIDERS["todoist"]["scopes"] == ASKED
    assert row["scope"] == WRITE_SCOPE and row["also"] == ("data:read_write",)
    assert set(row["fields"]) == {"content", "due", "description"} and row["reconnect"] == RECONNECT


async def test_add_task_posts_one_task_to_the_fixed_todoist_path(rd):
    jarvis, store, mock = rd
    _can_write(store)
    mock.answer = {"id": "999", "content": "PROVIDER-WORDS", "url": "https://x.test/t"}
    out = await store.write("todoist", "add_task", {"content": "Buy milk", "due": "tomorrow 9am",
                                                    "description": "2 litres"})
    assert out == {"ok": True, "provider": "todoist", "action": "add_task",
                   "added": {"content": "Buy milk", "due": "tomorrow 9am",
                             "description": "2 litres"}}
    (request,) = mock.requests
    assert (request.method, request.url.host, request.url.path, request.url.query) == (
        "POST", "api.todoist.com", "/api/v1/tasks", b"")
    assert json.loads(request.content) == {"content": "Buy milk", "due_string": "tomorrow 9am",
                                           "description": "2 litres"}
    assert request.headers["authorization"] == f"Bearer {READ_TOKEN}"
    # Neither the provider's own words nor the token come back.
    assert "PROVIDER-WORDS" not in json.dumps(out) and READ_TOKEN not in json.dumps(out)
    assert READ_TOKEN not in str(request.url) and not request.url.params


async def test_add_task_with_only_content_sends_only_content_and_cleans_it(rd):
    _, store, mock = rd
    _can_write(store)
    mock.answer = {}
    out = await store.write("todoist", "add_task", {"content": "  Call\n\x00 the‮ dentist ",
                                                    "due": "", "description": None})
    assert out["added"] == {"content": "Call the dentist"}
    assert json.loads(mock.requests[0].content) == {"content": "Call the dentist"}


@pytest.mark.parametrize("scope", ["", "data:read", "xtask:add", "task:addx", "task:adds data:read",
                                   "xdata:read_write", "data:read_writes", "data:delete"])
async def test_write_with_an_account_that_was_connected_read_only_asks_to_reconnect_and_sends_nothing(
        rd, scope):
    _, store, mock = rd
    store._accounts["todoist"]["scope"] = scope  # "" is an account from before T-1370
    store._accounts["todoist"]["expires_at"] = 0  # nor does it even try to refresh
    out = await store.write("todoist", "add_task", {"content": "Buy milk"})
    assert out == {"error": RECONNECT} and mock.requests == []


async def test_add_task_scope_may_be_listed_with_commas_or_spaces(rd):
    _, store, mock = rd
    mock.answer = {}
    # The add-only pair, and an account that holds the broad read-write scope, both pass.
    for scope in ("data:read,task:add", "data:read task:add", "task:add", "data:read_write",
                  "data:read,data:read_write", "data:read data:read_write"):
        store._accounts["todoist"]["scope"] = scope
        assert (await store.write("todoist", "add_task", {"content": "x"}))["ok"] is True


@pytest.mark.parametrize("fields", [
    {}, {"content": ""}, {"content": "   \n"}, {"content": None}, {"content": 7},
    {"content": ["a"]}, {"content": "x" * 501}, {"content": "x", "due": "d" * 81},
    {"content": "x", "due": 5}, {"content": "x", "description": "y" * 1001},
    {"content": "x", "priority": 4}, {"content": "x", "project_id": "1"},
    {"content": "x", "url": "https://evil.test/"}, {"content": "x", "method": "DELETE"},
    {"content": "x", "headers": {"a": "b"}}])
async def test_write_fields_are_validated_in_the_house_before_any_request(rd, fields):
    _, store, mock = rd
    _can_write(store)
    out = await store.write("todoist", "add_task", fields)
    assert list(out) == ["error"] and mock.requests == [], fields


async def test_write_at_the_length_limits_is_accepted(rd):
    _, store, mock = rd
    _can_write(store)
    mock.answer = {}
    out = await store.write("todoist", "add_task", {"content": "x" * 500, "due": "d" * 80,
                                                    "description": "y" * 1000})
    assert out["ok"] is True and len(mock.requests) == 1


async def test_write_has_no_other_provider_action_or_free_form_and_each_refusal_sends_nothing(rd):
    _, store, mock = rd
    _can_write(store)
    for provider, action in (("github", "add_task"), ("evil", "add_task"), ("todoist", "delete_task"),
                             ("todoist", "../../x"), ("todoist", ["add_task"]), ("todoist", None),
                             ("google", "events")):
        out = await store.write(provider, action, {"content": "x"})
        assert list(out) == ["error"], (provider, action)
    assert list(await store.write("todoist", "add_task", "Buy milk")) == ["error"]
    assert mock.requests == []


async def test_write_to_a_disconnected_or_lapsed_account_sends_nothing(rd):
    _, store, mock = rd
    _can_write(store)
    store._accounts["todoist"].update(status="needs_consent", access_token=None, expires_at=None)
    assert "sign in again" in (await store.write("todoist", "add_task", {"content": "x"}))["error"]
    store._accounts.pop("todoist")
    assert "not connected" in (await store.write("todoist", "add_task", {"content": "x"}))["error"]
    assert mock.requests == []


async def test_write_through_the_house_service_takes_only_its_own_fields(rd):
    jarvis, store, mock = rd
    _can_write(store)
    mock.answer = {}
    out = await jarvis.services.async_call(
        "oauth", "write", {"provider": "Todoist", "action": "add_task", "content": "Buy milk"},
        blocking=True, return_response=True)
    assert out["ok"] is True and len(mock.requests) == 1
    mock.requests.clear()
    # No method, address, header or body can be named: a key beyond these is refused whole.
    for extra in ({"method": "DELETE"}, {"url": "https://evil.test/"}, {"headers": {"x": "y"}},
                  {"body": {"content": "x"}}, {"path": "/api/v1/projects"}, {"fields": {"content": "x"}}):
        out = await jarvis.services.async_call(
            "oauth", "write", {"provider": "todoist", "action": "add_task", "content": "x", **extra},
            blocking=True, return_response=True)
        assert list(out) == ["error"] and not mock.requests, extra


async def test_a_write_the_provider_refuses_is_a_clean_answer_that_echoes_nothing_it_wrote(rd):
    _, store, mock = rd
    _can_write(store)
    mock.status, mock.answer = 500, {"error": "SECRET-DETAIL at-fake-1"}
    out = await store.write("todoist", "add_task", {"content": "x"})
    assert out == {"error": "Todoist answered HTTP 500"}
    mock.status = 403
    assert await store.write("todoist", "add_task", {"content": "x"}) == {
        "error": "Todoist refused the write (HTTP 403)"}
    mock.status = 400
    assert "did not accept" in (await store.write("todoist", "add_task", {"content": "x"}))["error"]
    assert len(mock.requests) == 3  # one request each: a write is never retried
    assert store._accounts["todoist"]["status"] == "connected"


async def test_a_write_the_provider_answers_401_ends_in_needs_consent_once(rd):
    _, store, mock = rd
    _can_write(store)
    mock.status = 401
    out = await store.write("todoist", "add_task", {"content": "x"})
    assert "refused the write" in out["error"] and len(mock.requests) == 1
    assert store._accounts["todoist"]["status"] == "needs_consent"
    assert "sign in again" in (await store.write("todoist", "add_task", {"content": "x"}))["error"]
    assert len(mock.requests) == 1


class _ReplacesTheAccount(ReadMock):
    """The old token's 401 arrives after the owner connected the account anew (T-1370 review)."""

    def __init__(self, store, provider) -> None:
        super().__init__()
        self.store, self.provider, self.fresh = store, provider, None

    def __call__(self, request):
        self.fresh = self.store._account({"expires_in": 3600}, "NEW-TOKEN", None, asked="data:read,task:add")
        self.store._accounts[self.provider] = self.fresh
        self.status = 401
        return super().__call__(request)


async def test_a_401_for_the_old_token_does_not_disconnect_an_account_connected_meanwhile(rd):
    """The decision: after a 401 that belongs to a replaced account, the new account is still
    connected, still holds its token and the owner is not told to sign in again."""
    _, store, _ = rd
    _can_write(store)
    mock = _ReplacesTheAccount(store, "todoist")
    store.transport, store._client = httpx.MockTransport(mock), None
    told = []

    async def tell(message):
        told.append(message)
        return True

    store._tell = tell
    out = await store.write("todoist", "add_task", {"content": "x"})
    assert "refused the write" in out["error"]
    assert store._accounts["todoist"] is mock.fresh
    assert mock.fresh["status"] == "connected" and mock.fresh["access_token"] == "NEW-TOKEN" and told == []
    # the same for a read
    out = await store.read("todoist", "projects")
    assert "refused the read" in out["error"]
    assert store._accounts["todoist"]["status"] == "connected" and told == []


async def test_a_401_for_the_current_account_still_ends_in_needs_consent_through_the_guard(rd):
    _, store, mock = rd
    _can_write(store)
    mock.status = 401
    await store.read("todoist", "projects")
    assert store._accounts["todoist"]["status"] == "needs_consent"


async def test_a_write_to_a_dead_network_is_an_error_not_a_raise_and_says_it_may_have_landed(
        rd, monkeypatch):
    _, store, mock = rd
    _can_write(store)

    async def boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(store, "_http", boom)
    out = await store.write("todoist", "add_task", {"content": "x"})
    assert list(out) == ["error"] and "may not have" in out["error"]


async def test_a_redirect_on_a_write_is_not_followed_and_the_token_goes_nowhere_else(rd):
    _, store, mock = rd
    _can_write(store)
    mock.status, mock.headers = 307, {"location": "https://evil.test/steal"}
    out = await store.write("todoist", "add_task", {"content": "x"})
    assert out == {"error": "Todoist answered HTTP 307"}
    assert [r.url.host for r in mock.requests] == ["api.todoist.com"]


async def test_a_todoist_consent_asks_for_read_and_add_only_and_records_what_it_asked(more):
    store, mock, _ = more
    started = await store.start("todoist")
    # Comma separated (a guess at Todoist's form); the broad scope is never asked for.
    asked = parse_qs(urlsplit(started["url"]).query)["scope"]
    assert asked == [",".join(ASKED)] and "data:read_write" not in asked[0]
    await _connect_more(store, mock, "todoist")
    acct = store._accounts["todoist"]
    assert acct["scope"] == ",".join(ASKED)
    # A refresh answer that carries no scope does not lose it; one that carries a scope wins.
    assert store._account({"expires_in": 60}, "t2", old=acct)["scope"] == ",".join(ASKED)
    assert store._account({"scope": "data:read"}, "t2", old=acct)["scope"] == "data:read"
    # An account stored before this change has no scope and stays read-only until reconnected.
    assert store._account({}, "t", old={"access_token": "old"})["scope"] == ""
