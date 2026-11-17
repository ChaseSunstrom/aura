"""aura-jellyfin against an httpx.MockTransport standing in for the server.

The fixture is the n8n-trigger one: replace ``httpx.Client`` with a client whose
transport is a handler, so every request the plugin makes is inspectable and no
socket is opened.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-jellyfin")
tools = plugin.tools

TOKEN = "jf-api-key-123"

USERS = [{"Id": "u-first", "Name": "owner"}, {"Id": "u-second", "Name": "housemate"}]

VIEWS = {"Items": [{"Id": "lib-movies", "Name": "Movies", "CollectionType": "movies",
                    "Type": "CollectionFolder"},
                   {"Id": "lib-shows", "Name": "Shows", "CollectionType": "tvshows",
                    "Type": "CollectionFolder"}]}

SEARCH = {"TotalRecordCount": 2, "Items": [
    {"Id": "it-blade", "Name": "Blade Runner", "Type": "Movie", "ProductionYear": 1982,
     "RunTimeTicks": 7_140_000_000},
    {"Id": "it-ep", "Name": "The Constant", "Type": "Episode", "SeriesName": "Lost",
     "ParentIndexNumber": 4, "IndexNumber": 5, "ProductionYear": 2008},
]}

RESUME = {"Items": [
    {"Id": "it-dune", "Name": "Dune", "Type": "Movie", "ProductionYear": 2021,
     "RunTimeTicks": 9_360_000_000,
     "UserData": {"PlaybackPositionTicks": 3_600_000_000, "PlayedPercentage": 38.46}},
]}

SESSIONS = [
    {"Id": "sess-tv", "DeviceName": "Living Room TV", "Client": "Jellyfin Android TV",
     "UserName": "owner", "SupportsRemoteControl": True,
     "NowPlayingItem": {"Id": "it-dune", "Name": "Dune", "Type": "Movie",
                        "ProductionYear": 2021, "RunTimeTicks": 9_360_000_000},
     "PlayState": {"PositionTicks": 3_600_000_000, "IsPaused": True}},
    {"Id": "sess-phone", "DeviceName": "Pixel", "Client": "Jellyfin Android",
     "UserName": "owner", "SupportsRemoteControl": False},
]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("JELLYFIN_URL", "https://media.test")
    monkeypatch.setenv("JELLYFIN_TOKEN", TOKEN)
    monkeypatch.delenv("JELLYFIN_USER_ID", raising=False)
    seen: list[tuple[str, str, dict, dict]] = []
    state = {"status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params),
                     dict(request.headers)))
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": "Unauthorized"})
        path = request.url.path
        if path == "/Users":
            return httpx.Response(200, json=USERS)
        if path == "/UserViews":
            return httpx.Response(200, json=VIEWS)
        if path == "/Items":
            return httpx.Response(200, json=SEARCH)
        if path == "/UserItems/Resume":
            return httpx.Response(200, json=RESUME)
        if path == "/Sessions":
            return httpx.Response(200, json=SESSIONS)
        if path == "/Sessions/sess-tv/Playing":
            return httpx.Response(204)
        return httpx.Response(404, text="unknown endpoint")

    Orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: Orig(
        base_url=kw.get("base_url", ""), headers=kw.get("headers"),
        transport=httpx.MockTransport(handler)))
    seen.clear()
    return {"seen": seen, "state": state}


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_ticks_become_seconds():
    assert tools._seconds(3_600_000_000) == 360
    assert tools._seconds(0) == 0
    assert tools._seconds(None) is None


def test_match_session_prefers_a_remote_controllable_device():
    rows = [tools.session_row(s) for s in SESSIONS]
    assert tools.match_session(rows, "Living Room TV")["session_id"] == "sess-tv"
    assert tools.match_session(rows, "sess-phone")["session_id"] == "sess-phone"
    # People say "the telly", not the exact device name.
    assert tools.match_session(rows, "living room")["session_id"] == "sess-tv"
    assert tools.match_session(rows, "kitchen radio") is None
    assert tools.match_session(rows, "") is None


# --------------------------------------------------------------------------- #
# Read-only tools
# --------------------------------------------------------------------------- #
def test_the_api_key_travels_in_both_headers(server):
    json.loads(tools.handle_libraries({}))
    headers = server["seen"][0][3]
    assert headers["authorization"] == f'MediaBrowser Token="{TOKEN}"'
    assert headers["x-emby-token"] == TOKEN


def test_libraries_resolve_the_first_user_when_none_is_configured(server):
    out = json.loads(tools.handle_libraries({}))
    assert out["ok"] and out["user_id"] == "u-first"
    assert [row["name"] for row in out["libraries"]] == ["Movies", "Shows"]
    paths = [(method, path) for method, path, _params, _headers in server["seen"]]
    assert paths == [("GET", "/Users"), ("GET", "/UserViews")]


def test_a_configured_user_id_skips_the_users_call(server, monkeypatch):
    monkeypatch.setenv("JELLYFIN_USER_ID", "u-second")
    out = json.loads(tools.handle_libraries({}))
    assert out["user_id"] == "u-second"
    assert [path for _m, path, _p, _h in server["seen"]] == ["/UserViews"]
    assert server["seen"][0][2]["userId"] == "u-second"


def test_search_rows_carry_what_identifies_a_title(server):
    out = json.loads(tools.handle_search({"query": "blade", "item_types": ["Movie", "Episode"],
                                          "limit": 5}))
    assert out["ok"] and out["count"] == 2 and out["total"] == 2
    assert out["items"][0] == {"id": "it-blade", "name": "Blade Runner", "type": "Movie",
                               "year": 1982, "runtime_s": 714}
    assert out["items"][1]["series"] == "Lost"
    assert out["items"][1]["season"] == 4 and out["items"][1]["episode"] == 5
    params = [p for _m, path, p, _h in server["seen"] if path == "/Items"][0]
    assert params["searchTerm"] == "blade" and params["recursive"] == "true"
    assert params["includeItemTypes"] == "Movie,Episode" and params["limit"] == "5"


def test_search_without_a_query_is_an_answer_not_a_call(server):
    out = json.loads(tools.handle_search({"query": "  "}))
    assert out["ok"] is False and "query is required" in out["error"]
    assert server["seen"] == []


def test_resume_reports_the_position(server):
    out = json.loads(tools.handle_resume({}))
    assert out["ok"] and out["items"][0]["name"] == "Dune"
    assert out["items"][0]["position_s"] == 360 and out["items"][0]["percent"] == 38.5


def test_sessions_parse_the_active_playback_and_keep_idle_devices(server):
    out = json.loads(tools.handle_sessions({}))
    assert out["ok"] and out["count"] == 2 and out["active"] == 1
    tv, phone = out["sessions"]
    assert tv["device"] == "Living Room TV" and tv["playing"] is True
    assert tv["item"]["name"] == "Dune" and tv["position_s"] == 360 and tv["paused"] is True
    assert tv["remote_control"] is True
    assert phone["playing"] is False and "item" not in phone


# --------------------------------------------------------------------------- #
# Playback
# --------------------------------------------------------------------------- #
def test_play_targets_the_matching_session(server):
    out = json.loads(tools.handle_play({"item_id": "it-dune", "device": "living room",
                                        "start_position_s": 60}))
    assert out["ok"] and out["http"] == 204
    assert out["session_id"] == "sess-tv" and out["device"] == "Living Room TV"
    method, path, params, _headers = server["seen"][-1]
    assert (method, path) == ("POST", "/Sessions/sess-tv/Playing")
    assert params["playCommand"] == "PlayNow" and params["itemIds"] == "it-dune"
    assert params["startPositionTicks"] == str(60 * tools.TICKS_PER_SECOND)


def test_play_on_an_unknown_device_lists_what_there_is(server):
    out = json.loads(tools.handle_play({"item_id": "it-dune", "device": "greenhouse"}))
    assert out["ok"] is False and "greenhouse" in out["error"]
    assert out["devices"] == ["Living Room TV", "Pixel"]


# --------------------------------------------------------------------------- #
# Failures
# --------------------------------------------------------------------------- #
def test_a_401_becomes_an_answer_not_an_exception(server):
    server["state"]["status"] = 401
    for handler in (tools.handle_libraries, tools.handle_sessions, tools.handle_resume):
        out = json.loads(handler({}))
        assert out["ok"] is False and out["error"].startswith("HTTP 401")
    out = json.loads(tools.handle_play({"item_id": "it-dune", "device": "Living Room TV"}))
    assert out["ok"] is False and "401" in out["error"]


def test_a_mistyped_url_is_an_answer_not_an_exception(monkeypatch):
    # httpx.InvalidURL does not subclass httpx.HTTPError, so this is the one
    # failure that escapes a handler that only catches HTTPError. "800O" is a
    # letter O — the shape of a real .env typo. Since T-1342 the shared address
    # rule catches it first (ServiceUrlError, a ValueError), still as an answer.
    monkeypatch.setenv("JELLYFIN_URL", "http://10.0.0.9:800O")
    monkeypatch.setenv("JELLYFIN_TOKEN", TOKEN)
    out = json.loads(tools.handle_libraries({}))
    assert out["ok"] is False and "JELLYFIN_URL is not a valid address" in out["error"]


def test_the_token_is_in_no_returned_payload(server):
    payloads = [tools.handle_libraries({}), tools.handle_search({"query": "blade"}),
                tools.handle_resume({}), tools.handle_sessions({}),
                tools.handle_play({"item_id": "it-dune", "device": "Living Room TV"})]
    server["state"]["status"] = 401
    payloads += [tools.handle_libraries({}), tools.handle_sessions({})]
    for payload in payloads:
        assert TOKEN not in payload


# --------------------------------------------------------------------------- #
# Registration and the gate
# --------------------------------------------------------------------------- #
def test_register_gates_only_playback(server):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"jellyfin_libraries", "jellyfin_search", "jellyfin_resume",
                              "jellyfin_sessions", "jellyfin_play"}
    assert all(entry["toolset"] == "jellyfin" for entry in ctx.tools.values())
    assert "jellyfin" in ctx.commands

    gate = ctx.hooks["pre_tool_call"]
    directive = gate("jellyfin_play", {"item_id": "it-dune", "item_name": "Dune",
                                       "device": "Living Room TV"}, "")
    assert directive["action"] == "approve"
    assert "Dune" in directive["message"] and "Living Room TV" in directive["message"]
    assert directive["rule_key"].rsplit(":g", 1)[0] == "jellyfin:play:Living Room TV"
    # With no friendly name the id still has to reach the prompt.
    assert "it-dune" in gate("jellyfin_play", {"item_id": "it-dune", "device": "x"}, "")["message"]
    for read_only in ("jellyfin_libraries", "jellyfin_search", "jellyfin_resume",
                      "jellyfin_sessions"):
        assert gate(read_only, {"query": "x"}, "") is None


def test_no_tool_is_offered_without_a_token(monkeypatch):
    monkeypatch.setenv("JELLYFIN_URL", "https://media.test")
    monkeypatch.delenv("JELLYFIN_TOKEN", raising=False)
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert all(ctx.available(name) is False for name in ctx.tools)


def test_the_slash_command_names_the_libraries_and_what_is_playing(server):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    reply = ctx.commands["jellyfin"]("")
    assert reply.startswith("Libraries: Movies, Shows")
    assert "Living Room TV: paused Dune" in reply
