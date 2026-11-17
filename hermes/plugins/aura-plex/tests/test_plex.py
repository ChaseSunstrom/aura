"""aura-plex against an httpx.MockTransport standing in for the server.

The answers below are written from Plex's documented JSON shapes (``MediaContainer`` with
``Directory`` / ``Metadata`` / ``Hub``); no real Plex server was ever called. A mock cannot show
whether a real server's answers differ.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-plex")
tools = plugin.tools

# Obviously fake, built here so no credential-looking literal sits in the file.
TOKEN = "-".join(["plex", "fake", "token", "123"])
URL = "http://10.0.0.9:32400"
TOOLS = {"plex_libraries", "plex_recent", "plex_now_playing", "plex_search"}

SECTIONS = {"MediaContainer": {"size": 2, "Directory": [
    {"key": "1", "title": "Movies", "type": "movie", "agent": "tv.plex.agents.movie"},
    {"key": "2", "title": "Shows", "type": "show"}]}}

RECENT = {"MediaContainer": {"size": 2, "totalSize": 40, "Metadata": [
    {"ratingKey": "100", "title": "Blade Runner", "type": "movie", "year": 1982,
     "addedAt": 1_700_000_000, "librarySectionTitle": "Movies"},
    {"ratingKey": "101", "title": "The Constant", "type": "episode", "index": 5,
     "parentIndex": 4, "grandparentTitle": "Lost", "parentTitle": "Season 4",
     "addedAt": 1_700_100_000, "librarySectionTitle": "Shows"}]}}

SESSIONS = {"MediaContainer": {"size": 1, "Metadata": [
    {"ratingKey": "100", "title": "Blade Runner", "type": "movie", "year": 1982,
     "viewOffset": 3_600_000, "duration": 7_140_000,
     "User": {"id": "1", "title": "owner"},
     "Player": {"title": "Living Room TV", "product": "Plex for Android", "state": "paused",
                "address": "10.0.0.77"}}]}}

HUBS = {"MediaContainer": {"size": 2, "Hub": [
    {"type": "movie", "title": "Movies", "Metadata": [
        {"ratingKey": "100", "title": "Blade Runner", "type": "movie", "year": 1982}]},
    {"type": "episode", "title": "Episodes", "Metadata": [
        {"ratingKey": "101", "title": "The Constant", "type": "episode", "index": 5,
         "parentIndex": 4, "grandparentTitle": "Lost"}]},
    {"type": "actor", "title": "Actors"}]}}


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("PLEX_URL", URL)
    monkeypatch.setenv("PLEX_TOKEN", TOKEN)
    seen: list[httpx.Request] = []
    state: dict = {"status": 200, "body": None, "client_kwargs": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if state["status"] != 200:
            return httpx.Response(state["status"], text=f"nope {TOKEN}",
                                  headers={"location": "http://10.0.0.66/x"})
        if state["body"] is not None:
            return httpx.Response(200, content=state["body"])
        path = request.url.path
        answers = {"/library/sections": SECTIONS, "/library/recentlyAdded": RECENT,
                   "/status/sessions": SESSIONS, "/hubs/search": HUBS}
        if path in answers:
            return httpx.Response(200, json=answers[path])
        return httpx.Response(404, text="unknown endpoint")

    Orig = httpx.Client

    def make(**kw):
        state["client_kwargs"] = dict(kw)
        kw.pop("verify", None)
        return Orig(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "Client", make)
    return {"seen": seen, "state": state}


def _call(handler, **params):
    return json.loads(handler(params))


# --------------------------------------------------------------------------- #
# The four tools
# --------------------------------------------------------------------------- #
def test_libraries(server):
    out = _call(tools.handle_libraries)
    assert out["ok"] and out["count"] == 2
    assert out["libraries"][0] == {"id": "1", "title": "Movies", "type": "movie"}


def test_recent_rows_say_what_and_where_and_when(server):
    out = _call(tools.handle_recent, limit=5)
    assert out["ok"] and out["count"] == 2 and out["total"] == 40
    movie, episode = out["items"]
    assert movie["title"] == "Blade Runner" and movie["year"] == 1982
    assert movie["library"] == "Movies" and movie["added"].startswith("2023-11-14")
    assert episode["context"] == "Lost / Season 4"
    assert episode["season"] == 4 and episode["episode"] == 5
    params = dict(server["seen"][0].url.params)
    assert params["X-Plex-Container-Size"] == "5" and params["X-Plex-Container-Start"] == "0"


def test_now_playing_has_position_and_state_but_not_the_players_address(server):
    out = _call(tools.handle_now_playing)
    assert out["ok"] and out["count"] == 1
    row = out["sessions"][0]
    assert row["title"] == "Blade Runner" and row["user"] == "owner"
    assert row["device"] == "Living Room TV" and row["state"] == "paused"
    assert row["position_s"] == 3600 and row["duration_s"] == 7140
    assert "10.0.0.77" not in json.dumps(out)


def test_now_playing_with_nothing_playing(server):
    server["state"]["body"] = b'{"MediaContainer": {"size": 0}}'
    out = _call(tools.handle_now_playing)
    assert out == {"ok": True, "count": 0, "sessions": []}


def test_search_gathers_every_hub_and_caps_the_count(server):
    out = _call(tools.handle_search, query="  blade ", limit=1)
    assert out["ok"] and out["count"] == 1 and out["query"] == "blade"
    assert out["items"][0]["title"] == "Blade Runner"
    request = server["seen"][0]
    assert request.url.params["query"] == "blade"
    both = _call(tools.handle_search, query="blade")
    assert [i["title"] for i in both["items"]] == ["Blade Runner", "The Constant"]


def test_search_without_a_query_is_an_answer_not_a_call(server):
    out = _call(tools.handle_search, query="  ")
    assert out["ok"] is False and "query is required" in out["error"]
    assert server["seen"] == []


def test_limits_are_bounded(server):
    _call(tools.handle_recent, limit=10_000)
    assert server["seen"][-1].url.params["X-Plex-Container-Size"] == str(tools.MAX_ROWS)
    _call(tools.handle_recent, limit="junk")
    assert server["seen"][-1].url.params["X-Plex-Container-Size"] == "12"


def test_a_title_cannot_flood_the_reply(server):
    server["state"]["body"] = json.dumps({"MediaContainer": {"Metadata": [
        {"title": "A" * 5000 + "\x00\x1b[31m", "type": "movie"}]}}).encode()
    row = _call(tools.handle_recent)["items"][0]
    assert len(row["title"]) <= tools.CLIP + 1 and "\x00" not in row["title"]
    assert "\x1b" not in row["title"]


def test_rows_are_capped_even_if_the_server_ignores_the_size(server):
    items = [{"title": f"t{i}", "type": "movie"} for i in range(500)]
    server["state"]["body"] = json.dumps({"MediaContainer": {"Metadata": items}}).encode()
    assert _call(tools.handle_recent, limit=5)["count"] == 5


# --------------------------------------------------------------------------- #
# The token and the address
# --------------------------------------------------------------------------- #
def test_the_token_is_a_header_never_in_the_url(server):
    for handler in (tools.handle_libraries, tools.handle_now_playing):
        handler({})
    _call(tools.handle_search, query="x")
    assert len(server["seen"]) == 3
    for request in server["seen"]:
        assert request.method == "GET"
        assert request.headers["x-plex-token"] == TOKEN
        assert TOKEN not in str(request.url)
        assert request.headers["accept"] == "application/json"
        assert request.url.host == "10.0.0.9"


def test_the_client_does_not_follow_redirects_and_verifies_tls(server):
    _call(tools.handle_libraries)
    kwargs = server["state"]["client_kwargs"]
    assert kwargs["follow_redirects"] is False and kwargs["verify"] is True


def test_a_redirect_is_refused_and_the_token_stays_out_of_the_answer(server):
    server["state"]["status"] = 302
    for handler in (tools.handle_libraries, tools.handle_recent, tools.handle_now_playing):
        raw = handler({})
        out = json.loads(raw)
        assert out["ok"] is False and out["error"].startswith("HTTP 302")
        assert TOKEN not in raw and "10.0.0.66" not in raw
    assert len(server["seen"]) == 3  # nothing was followed


def test_a_401_becomes_an_answer_without_the_body(server):
    server["state"]["status"] = 401
    raw = tools.handle_search({"query": "x"})
    assert json.loads(raw)["error"].startswith("HTTP 401") and TOKEN not in raw


def test_an_answer_that_is_not_json_or_too_big_is_an_answer(server):
    server["state"]["body"] = b"<html>login</html>"
    assert _call(tools.handle_libraries)["ok"] is False
    server["state"]["body"] = b" " * (tools.MAX_BYTES + 1)
    out = _call(tools.handle_libraries)
    assert out["ok"] is False and "too large" in out["error"]


def test_the_token_is_in_no_returned_payload(server):
    payloads = [tools.handle_libraries({}), tools.handle_recent({}),
                tools.handle_now_playing({}), tools.handle_search({"query": "x"})]
    server["state"]["status"] = 500
    payloads += [tools.handle_libraries({}), tools.handle_now_playing({})]
    assert all(TOKEN not in p for p in payloads)


@pytest.mark.parametrize("url", [
    "https://plex.example.test", "https://plex.example.test:32400/prefix/",
    "http://10.0.0.9:32400", "http://192.168.1.5:32400", "http://127.0.0.1:32400",
    "http://[fd00::9]:32400"])
def test_addresses_that_are_accepted(url):
    assert tools.check_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "", "plex.example.test", "ftp://10.0.0.9", "http://plex.example.test:32400",
    "http://8.8.8.8:32400", "https://user:pw@plex.example.test", "https://@plex.example.test",
    "https://plex.example.test/?X-Plex-Token=x", "https://plex.example.test/#frag",
    "https://plex.example.test?", "https://", "http://169.254.169.254/", "https://169.254.169.254/",
    "https://metadata.google.internal/", "http://10.0.0.9:99999", "https://plex.example.test\\@evil.test",
    "https://plex.example.test/ x", "http://10.0.0.9\n.evil.test"])
def test_addresses_that_are_refused(url):
    with pytest.raises(ValueError):
        tools.check_url(url)


def test_a_bad_address_is_an_answer_and_no_call(monkeypatch, server):
    monkeypatch.setenv("PLEX_URL", "http://plex.example.test:32400")
    out = _call(tools.handle_libraries)
    assert out["ok"] is False and "http" in out["error"]
    assert server["seen"] == []


# --------------------------------------------------------------------------- #
# No server set
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("missing", ["PLEX_URL", "PLEX_TOKEN"])
def test_with_no_server_or_token_every_tool_refuses_without_a_call(monkeypatch, server, missing):
    monkeypatch.delenv(missing)
    for handler, params in ((tools.handle_libraries, {}), (tools.handle_recent, {}),
                            (tools.handle_now_playing, {}),
                            (tools.handle_search, {"query": "x"})):
        out = json.loads(handler(params))
        assert out["ok"] is False and "not set" in out["error"]
    assert server["seen"] == []


def test_no_tool_is_offered_without_a_server_or_a_token(monkeypatch):
    for missing in ("PLEX_URL", "PLEX_TOKEN"):
        monkeypatch.setenv("PLEX_URL", URL)
        monkeypatch.setenv("PLEX_TOKEN", TOKEN)
        monkeypatch.delenv(missing)
        ctx = plugin.FakeCtx()
        plugin.register(ctx)
        assert set(ctx.tools) == TOOLS
        assert all(ctx.available(name) is False for name in ctx.tools)


def test_register_offers_four_read_only_tools_and_no_hook(server):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == TOOLS
    assert all(entry["toolset"] == "plex" for entry in ctx.tools.values())
    assert all(ctx.available(name) for name in ctx.tools)
    assert not ctx.hooks and not ctx.commands


def test_a_number_that_is_infinity_is_dropped_not_fatal():
    # json.loads accepts Infinity, and int() of it raises OverflowError.
    row = tools.item_row({"title": "T", "type": "movie", "year": float("inf"), "addedAt": float("inf")})
    assert row == {"title": "T", "type": "movie"}
