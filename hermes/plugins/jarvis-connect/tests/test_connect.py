"""jarvis-connect against a mocked house. Pinned: the three tools register, the matrix asks
for start and revoke and not for status, status adds the test call result for a provider, and
a down house or a bad provider is an error string, never a raise. Not pinned: a live house."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _matrix  # noqa: E402

plugin = load_plugin("jarvis-connect")
tools = plugin.tools
_REAL = httpx.Client


def serve(monkeypatch, handler):
    monkeypatch.setenv("HASS_URL", "http://house.test")
    monkeypatch.setenv("HASS_TOKEN", "t")
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))


def test_registers_five_tools():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert sorted(ctx.tools) == ["connect_read", "connect_revoke", "connect_start", "connect_status",
                                 "connect_write"]


@pytest.mark.parametrize("tool,verdict", [("connect_start", "ask"), ("connect_revoke", "ask"),
                                          ("connect_status", "free"), ("connect_read", "free"),
                                          ("connect_write", "ask")])
def test_oauth_matrix_rows(tool, verdict):
    assert _matrix.decide(tool, {}).verdict == verdict


def test_status_with_provider_runs_the_test_call(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path.endswith("/status"):
            return httpx.Response(200, json={"service_response": {"providers": {"github": {"connected": True}}}})
        return httpx.Response(200, json={"service_response": {"ok": True, "detail": "3 repos"}})

    serve(monkeypatch, handler)
    out = json.loads(tools.handle_status({"provider": "github"}))
    assert out["test"]["ok"] is True and out["providers"]["github"]["connected"]
    assert seen == ["/api/services/oauth/status", "/api/services/oauth/test"]


def test_start_returns_house_answer_and_rejects_unknown_provider(monkeypatch):
    serve(monkeypatch, lambda req: httpx.Response(200, json={"service_response": {"delivered": True}}))
    assert json.loads(tools.handle_start({"provider": "Google"})) == {"delivered": True}
    assert "error" in json.loads(tools.handle_start({"provider": "evil"}))


NEW = ("microsoft", "slack", "discord", "gitlab", "spotify", "notion", "todoist")


def test_the_seven_new_providers_are_listed_and_accepted(monkeypatch):
    # T-1338: the schema enum, the handler's list and the house's providers name the same set.
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(NEW) <= set(tools.PROVIDERS)
    for name, tool in ctx.tools.items():
        enum = tool["schema"]["parameters"]["properties"]["provider"]["enum"]  # all four carry it
        # connect_write names only the one account that has a write (T-1370).
        assert sorted(enum) == sorted(tools.WRITES if name == "connect_write" else tools.PROVIDERS), name
    seen = []

    def handler(req):
        seen.append(json.loads(req.content)["provider"])
        return httpx.Response(200, json={"service_response": {"delivered": True}})

    serve(monkeypatch, handler)
    for name in NEW:
        assert json.loads(tools.handle_start({"provider": name.upper()})) == {"delivered": True}
        assert json.loads(tools.handle_revoke({"provider": name})) == {"delivered": True}
    assert seen == [n for n in NEW for _ in (0, 1)]


# --- connect_read (T-1341) ----------------------------------------------------------------
CONTRACT = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts"
                       / "connect-reads.json").read_text())["reads"]


def test_the_reads_are_exactly_the_contract_and_the_schema_enum_comes_from_them():
    # jarvis-core's suite reads the same file against its PROVIDERS rows.
    assert {p: sorted(n) for p, n in tools.READS.items()} == {p: sorted(n) for p, n in CONTRACT.items()}
    assert set(tools.READS) == set(tools.PROVIDERS)
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    props = ctx.tools["connect_read"]["schema"]["parameters"]["properties"]
    assert sorted(props["what"]["enum"]) == sorted({w for n in CONTRACT.values() for w in n})
    assert ctx.tools["connect_read"]["schema"]["parameters"]["required"] == ["provider", "what"]
    # No parameter can carry an address, a method or a header.
    assert set(props) == {"provider", "what", "query", "limit"}


def test_connect_read_offers_the_todoist_due_read_after_projects_and_tasks():
    """T-1415: the morning brief names this read; the model's list of Todoist reads carries it."""
    assert tools.READS["todoist"] == ("projects", "tasks", "due")
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    schema = ctx.tools["connect_read"]["schema"]
    assert "due" in schema["parameters"]["properties"]["what"]["enum"]
    assert "todoist: projects, tasks, due." in schema["description"]


def test_a_todoist_due_read_posts_only_the_named_read(monkeypatch):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"service_response": {"items": [], "count": 0}})

    serve(monkeypatch, handler)
    tools.handle_read({"provider": "todoist", "what": "due", "query": " ", "filter": "all",
                       "url": "https://evil.test/"})
    # A filter or address the model invents is not forwarded; the house owns the filter text.
    assert sent == [{"return_response": True, "provider": "todoist", "what": "due"}]


def test_connect_read_is_outside_text_and_closed_to_a_guest():
    from _shared import _taint
    assert _taint._reads_outside("connect_read")
    assert not _matrix.guest_may("connect_read")
    assert _matrix.decide("connect_read", {}).free  # an owner's read does not ask


def test_a_read_posts_only_the_named_read_and_returns_the_house_answer(monkeypatch):
    sent = []

    def handler(req):
        sent.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"service_response": {"items": [{"name": "Home"}], "count": 1}})

    serve(monkeypatch, handler)
    out = json.loads(tools.handle_read({"provider": "Google", "what": "events", "query": " dentist ",
                                        "limit": "5", "url": "https://evil.test/", "method": "POST"}))
    assert out == {"items": [{"name": "Home"}], "count": 1}
    # Extra arguments the model invented are not forwarded.
    assert sent == [("/api/services/oauth/read",
                     {"return_response": True, "provider": "google", "what": "events",
                      "query": " dentist ", "limit": 5})]


@pytest.mark.parametrize("args", [
    {"provider": "evil", "what": "repos"}, {"provider": "github", "what": "events"},
    {"provider": "github", "what": "../../user"}, {"provider": "github", "what": ["repos"]},
    {"provider": "github"}, {"what": "repos"}, {}])
def test_an_unknown_provider_or_read_never_reaches_the_house(monkeypatch, args):
    seen = []
    serve(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
    assert "error" in json.loads(tools.handle_read(args))
    assert seen == []


@pytest.mark.parametrize("limit", [float("inf"), float("nan"), 10 ** 400, [], {}, None, "x"])
def test_an_unreadable_limit_is_left_out_and_the_body_stays_valid_json(monkeypatch, limit):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))  # Infinity would not survive a strict reader
        return httpx.Response(200, json={"service_response": {"count": 0, "items": []}})

    serve(monkeypatch, handler)
    assert json.loads(tools.handle_read({"provider": "discord", "what": "servers", "limit": limit}))["count"] == 0
    assert "limit" not in sent[0]
    assert "NaN" not in json.dumps(sent[0]) and "Infinity" not in json.dumps(sent[0])


def test_a_long_query_is_cut_and_a_non_text_query_is_dropped(monkeypatch):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"service_response": {}})

    serve(monkeypatch, handler)
    tools.handle_read({"provider": "gitlab", "what": "projects", "query": "q" * 5000})
    tools.handle_read({"provider": "gitlab", "what": "projects", "query": {"a": 1}})
    assert len(sent[0]["query"]) <= 200 and "query" not in sent[1]


def test_a_read_when_the_house_is_down_or_refuses_is_an_error_string(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("no")

    serve(monkeypatch, boom)
    assert "could not be reached" in json.loads(tools.handle_read({"provider": "github", "what": "repos"}))["error"]
    serve(monkeypatch, lambda req: httpx.Response(500, text="SECRET-DETAIL"))
    out = tools.handle_read({"provider": "github", "what": "repos"})
    assert "error" in json.loads(out) and "SECRET-DETAIL" not in out


# --- connect_write (T-1370): the first write on a connected account ------------------------
WRITE_CONTRACT = json.loads((Path(__file__).resolve().parents[4] / "tests" / "contracts"
                             / "connect-reads.json").read_text())["writes"]


def test_the_writes_are_exactly_the_contract_and_the_schema_cannot_carry_an_address():
    # jarvis-core's suite reads the same file against its PROVIDERS rows' `writes`.
    assert {p: sorted(n) for p, n in tools.WRITES.items()} == {p: sorted(n) for p, n in WRITE_CONTRACT.items()}
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    params = ctx.tools["connect_write"]["schema"]["parameters"]
    assert params["additionalProperties"] is False
    assert set(params["properties"]) == {"provider", "action", "content", "due", "description"}
    assert params["required"] == ["provider", "action", "content"]
    assert params["properties"]["provider"]["enum"] == ["todoist"]
    assert params["properties"]["action"]["enum"] == ["add_task"]
    # The model is told, in the description it reads, that the owner is asked every time.
    assert "asks the owner every time" in ctx.tools["connect_write"]["schema"]["description"].lower()


def test_connect_write_asks_the_owner_and_is_closed_to_a_guest():
    from _shared import _taint
    decision = _matrix.decide("connect_write", {"provider": "todoist", "action": "add_task", "content": "x"})
    assert decision.verdict == "ask" and not decision.free
    assert not _matrix.guest_may("connect_write")
    assert not _taint._reads_outside("connect_write")  # it sends text, it does not bring any back


def test_a_write_posts_only_the_named_fields_and_returns_the_house_answer(monkeypatch):
    sent = []

    def handler(req):
        sent.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"service_response": {"ok": True, "provider": "todoist",
                                                              "action": "add_task",
                                                              "added": {"content": "Buy milk"}}})

    serve(monkeypatch, handler)
    out = json.loads(tools.handle_write({
        "provider": "Todoist", "action": "add_task", "content": "Buy milk", "due": "tomorrow",
        "description": "oat", "url": "https://evil.test/", "method": "DELETE", "headers": {"a": "b"},
        "path": "/x", "project_id": "9"}))
    assert out["ok"] is True
    # Nothing the model invented rides along: no address, method, header, path or project.
    assert sent == [("/api/services/oauth/write",
                     {"return_response": True, "provider": "todoist", "action": "add_task",
                      "content": "Buy milk", "due": "tomorrow", "description": "oat"})]


def test_a_write_with_only_content_sends_only_content(monkeypatch):
    sent = []
    serve(monkeypatch, lambda req: sent.append(json.loads(req.content)) or httpx.Response(
        200, json={"service_response": {"ok": True}}))
    tools.handle_write({"provider": "todoist", "action": "add_task", "content": "  Buy milk  ",
                        "due": None, "description": ""})
    assert sent == [{"return_response": True, "provider": "todoist", "action": "add_task",
                     "content": "  Buy milk  "}]


@pytest.mark.parametrize("args", [
    {"provider": "evil", "action": "add_task", "content": "x"},
    {"provider": "github", "action": "add_task", "content": "x"},
    {"provider": "todoist", "action": "delete_task", "content": "x"},
    {"provider": "todoist", "action": "../tasks", "content": "x"},
    {"provider": "todoist", "action": ["add_task"], "content": "x"},
    {"provider": "todoist", "content": "x"}, {"action": "add_task", "content": "x"},
    {"provider": "todoist", "action": "add_task"},
    {"provider": "todoist", "action": "add_task", "content": ""},
    {"provider": "todoist", "action": "add_task", "content": "   "},
    {"provider": "todoist", "action": "add_task", "content": 5},
    {"provider": "todoist", "action": "add_task", "content": ["a"]},
    {"provider": "todoist", "action": "add_task", "content": {"a": 1}},
    {"provider": "todoist", "action": "add_task", "content": None}, {}])
def test_an_unknown_write_or_a_missing_title_never_reaches_the_house(monkeypatch, args):
    seen = []
    serve(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
    assert list(json.loads(tools.handle_write(args))) == ["error"]
    assert seen == []


def test_a_write_that_is_not_a_dict_is_an_error_not_a_raise(monkeypatch):
    seen = []
    serve(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
    assert list(json.loads(tools.handle_write(None))) == ["error"]  # type: ignore[arg-type]
    assert seen == []


def test_a_non_text_optional_field_is_left_out_and_a_huge_one_is_cut(monkeypatch):
    sent = []
    serve(monkeypatch, lambda req: sent.append(json.loads(req.content)) or httpx.Response(
        200, json={"service_response": {}}))
    tools.handle_write({"provider": "todoist", "action": "add_task", "content": "x", "due": 5,
                        "description": {"a": 1}})
    tools.handle_write({"provider": "todoist", "action": "add_task", "content": "x" * 99999})
    assert "due" not in sent[0] and "description" not in sent[0]
    assert len(sent[1]["content"]) <= 2000  # bounded here; the house refuses what is over its limit


def test_the_old_scope_answer_from_the_house_reaches_the_model_unchanged(monkeypatch):
    sentence = "Todoist was connected for reading only: reconnect Todoist to allow adding tasks"
    serve(monkeypatch, lambda req: httpx.Response(200, json={"service_response": {"error": sentence}}))
    out = json.loads(tools.handle_write({"provider": "todoist", "action": "add_task", "content": "x"}))
    assert out == {"error": sentence}


def test_a_write_when_the_house_is_down_or_refuses_is_a_fixed_error_string(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("SECRET-ADDRESS")

    serve(monkeypatch, boom)
    out = tools.handle_write({"provider": "todoist", "action": "add_task", "content": "x"})
    assert "SECRET-ADDRESS" not in out
    serve(monkeypatch, lambda req: httpx.Response(404, text="SECRET-DETAIL"))
    out = tools.handle_write({"provider": "todoist", "action": "add_task", "content": "x"})
    # A refusal before the house acted is plainly a no: nothing was added.
    assert "error" in json.loads(out) and "SECRET-DETAIL" not in out
    assert "may or may not" not in out


MAYBE_ADDED = "the task may or may not have been added: check the list before trying again"


@pytest.mark.parametrize("fault", [httpx.ReadTimeout("SECRET-ADDRESS"), httpx.ConnectError("SECRET-ADDRESS"),
                                   httpx.RemoteProtocolError("SECRET-ADDRESS")])
def test_a_write_that_loses_the_house_does_not_claim_it_failed(monkeypatch, fault):
    """The house may have sent the task to the provider before the timeout: answering "the
    house could not be reached" would invite a retry and a duplicate (T-1370 review)."""
    def boom(req):
        raise fault

    serve(monkeypatch, boom)
    out = json.loads(tools.handle_write({"provider": "todoist", "action": "add_task", "content": "x"}))
    assert out == {"error": MAYBE_ADDED}


def test_a_write_the_house_answers_with_a_server_error_is_also_uncertain(monkeypatch):
    serve(monkeypatch, lambda req: httpx.Response(502, text="SECRET-DETAIL"))
    out = tools.handle_write({"provider": "todoist", "action": "add_task", "content": "x"})
    assert json.loads(out) == {"error": MAYBE_ADDED}


def test_a_read_that_loses_the_house_still_says_it_could_not_be_reached(monkeypatch):
    def boom(req):
        raise httpx.ReadTimeout("t")

    serve(monkeypatch, boom)
    assert "could not be reached" in json.loads(tools.handle_read({"provider": "github", "what": "repos"}))["error"]


def test_oauth_house_down_is_an_error_string(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("no")

    serve(monkeypatch, boom)
    assert "could not be reached" in json.loads(tools.handle_start({"provider": "github"}))["error"]
