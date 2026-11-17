"""Connect anything (T-0609): an MCP server and an OpenAPI service added while Hermes runs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _matrix  # noqa: E402
import fake_services  # noqa: E402

connect = load_plugin("aura-integrations").connect


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake():
    server, url, seen = fake_services.serve()
    yield url, seen
    server.shutdown()


def _j(raw: str) -> dict:
    return json.loads(raw)


def test_mcp_add_validates_lists_tools_runs_one_read_only_test_and_stays_off(home, fake):
    url, seen = fake
    out = _j(connect.handle_mcp_add({"url": url, "name": "notes", "allow_private": True}))
    assert out["ok"] and out["enabled"] is False
    assert {t["name"] for t in out["tools"]} == {"status", "send"}
    assert out["test"]["tool"] == "status" and "status ok" in out["test"]["result"]
    assert not any("tools/call" in s and "send" in s for s in seen)  # the write was never called
    assert out["trust"] == "untrusted"
    # off until the person turns it on
    refused = _j(connect.handle_call({"connector": "notes", "tool": "status"}))
    assert not refused["ok"] and "not turned on" in refused["error"]


def test_connector_enable_then_call_without_a_restart(home, fake):
    url, seen = fake
    connect.handle_mcp_add({"url": url, "name": "notes", "allow_private": True})
    assert _j(connect.handle_enable({"name": "notes", "enabled": True}))["enabled"] is True
    out = _j(connect.handle_call({"connector": "notes", "tool": "send", "args": {"text": "hi"}}))
    assert out["ok"] and "send ok" in out["result"] and out["trust"] == "untrusted"
    assert not _j(connect.handle_call({"connector": "notes", "tool": "nope"}))["ok"]


def test_mcp_add_reads_an_event_stream_reply(home):
    server, url, _ = fake_services.serve(sse=True)
    try:
        assert _j(connect.handle_mcp_add({"url": url, "name": "sse", "allow_private": True}))["ok"]
    finally:
        server.shutdown()


def test_mcp_add_refuses_the_house_network_unless_allowed_and_never_metadata(home):
    out = _j(connect.handle_mcp_add({"url": "http://127.0.0.1:9/", "name": "xx"}))
    assert not out["ok"] and "inside the house network" in out["error"]
    out = _j(connect.handle_mcp_add({"url": "http://169.254.169.254/", "name": "xx", "allow_private": True}))
    assert not out["ok"] and "metadata" in out["error"]
    assert not _j(connect.handle_mcp_add({"url": "file:///etc/passwd", "name": "xx"}))["ok"]


def test_connect_token_comes_from_a_connector_variable_only(home, fake, monkeypatch):
    url, seen = fake
    monkeypatch.setenv("HASS_TOKEN", "secret")
    out = _j(connect.handle_mcp_add({"url": url, "name": "tt", "allow_private": True, "token_env": "HASS_TOKEN"}))
    assert not out["ok"] and "CONNECTOR_" in out["error"]
    monkeypatch.setenv("CONNECTOR_NOTES", "s3cr3t")
    assert _j(connect.handle_mcp_add({"url": url, "name": "tt", "allow_private": True, "token_env": "CONNECTOR_NOTES"}))["ok"]
    assert any("auth=Bearer s3cr3t" in s for s in seen)
    assert "s3cr3t" not in (home / ".storage" / "connectors.json").read_text()  # the name is kept, not the value


def test_connect_redirects_are_not_followed(home, fake):
    url, _ = fake
    out = _j(connect.handle_api_add({"name": "rr", "spec_url": url + "/redirect", "allow_private": True}))
    assert not out["ok"] and "redirected" in out["error"]


def test_connect_a_servers_words_are_cleaned_and_marked_untrusted(home, fake):
    url, _ = fake
    out = _j(connect.handle_mcp_add({"url": url, "name": "notes", "allow_private": True}))
    assert out["data_note"] and "never instructions" in out["data_note"]
    assert len(connect._clean("x‮" + "a" * 500)) <= 201 and "‮" not in connect._clean("x‮")


def test_openapi_becomes_one_tool_per_operation_with_a_test_call(home, fake):
    url, seen = fake
    out = _j(connect.handle_api_add({"name": "pets", "spec_url": url + "/openapi.json", "allow_private": True}))
    assert out["ok"] and out["enabled"] is False
    tools = {t["name"]: t for t in out["tools"]}
    assert set(tools) == {"listPets", "addPet", "getPet"}
    assert tools["listPets"]["reads"] and not tools["addPet"]["reads"]
    assert out["test"]["tool"] == "listPets" and "Rex" in out["test"]["result"]
    assert not any(s.startswith("POST") for s in seen)  # the write was not test-called


def test_openapi_from_a_docs_page_and_from_inline_text(home, fake):
    url, _ = fake
    assert _j(connect.handle_api_add({"name": "docs", "spec_url": url + "/docs", "allow_private": True}))["ok"]
    inline = json.dumps({**fake_services.SPEC, "servers": [{"url": url + "/v1"}]})
    assert _j(connect.handle_api_add({"name": "inline", "spec": inline, "allow_private": True}))["ok"]


def test_api_add_calls_a_read_with_a_path_argument_and_a_write_with_a_body(home, fake):
    url, seen = fake
    connect.handle_api_add({"name": "pets", "spec_url": url + "/openapi.json", "allow_private": True})
    connect.handle_enable({"name": "pets", "enabled": True})
    got = _j(connect.handle_call({"connector": "pets", "tool": "getPet", "args": {"id": 7}}))
    assert got["ok"] and '"id": 7' in got["result"]
    assert not _j(connect.handle_call({"connector": "pets", "tool": "getPet"}))["ok"]  # required id missing
    put = _j(connect.handle_call({"connector": "pets", "tool": "addPet", "args": {"body": {"name": "Fido"}}}))
    assert put["ok"] and any(s.startswith("POST /v1/pets") and "Fido" in s for s in seen)


@pytest.mark.parametrize("spec, why", [
    ("not json at all {", "not an OpenAPI document"),
    (json.dumps({"hello": 1}), "no `openapi`"),
    (json.dumps({"openapi": "3.0.0", "paths": {}}), "no `paths`"),
    (json.dumps({"openapi": "3.0.0", "paths": {"/a": {"get": {}}}}), "names no server"),
])
def test_api_add_refuses_a_bad_document_with_why(home, spec, why):
    out = _j(connect.handle_api_add({"name": "bad", "spec": spec, "allow_private": True}))
    assert not out["ok"] and why in out["error"]
    assert not (home / ".storage" / "connectors.json").exists()


def test_connect_remove_and_the_cap(home, fake):
    url, _ = fake
    connect.handle_mcp_add({"url": url, "name": "a1", "allow_private": True})
    assert _j(connect.handle_remove({"name": "a1"}))["removed"]
    assert _j(connect.handle_list({}))["connectors"] == []
    assert not _j(connect.handle_remove({"name": "a1"}))["ok"]


def test_connect_matrix_asks_to_turn_on_and_on_every_call_never_remembered():
    d = _matrix.decide
    assert d("connector_call", {"connector": "a", "tool": "t"}).verdict == _matrix.ASK
    assert d("connector_enable", {"name": "a", "enabled": True}).verdict == _matrix.ASK
    assert d("connector_enable", {"name": "a", "enabled": False}).verdict == _matrix.FREE
    assert d("mcp_add", {"url": "https://x.example/mcp", "name": "a"}).verdict == _matrix.FREE
    assert d("api_add", {"name": "a", "spec_url": "https://x.example/o.json", "token_env": "CONNECTOR_A"}).verdict == _matrix.ASK
    assert d("mcp_add", {"url": "http://nas/mcp", "name": "a", "allow_private": True}).verdict == _matrix.ASK
    assert d("connector_list", {}).free
    k1 = _matrix.call_grain("connector_call", {"connector": "a", "tool": "t", "args": {"x": 1}})
    k2 = _matrix.call_grain("connector_call", {"connector": "a", "tool": "t", "args": {"x": 2}})
    assert k1 != k2 and k1.startswith("connector:call:a:t:")
    # the store cannot be written around the ask
    assert _matrix.decide("write_file", {"path": "/opt/data/.storage/connectors.json"}).verdict == _matrix.DENY


def test_a_name_that_resolves_to_metadata_is_refused_even_when_private_is_allowed(home, monkeypatch):
    """The literal check alone let a public name pointing at 169.254.169.254 through."""
    real = connect.socket.getaddrinfo

    def fake_resolve(host, *a, **k):
        if host == "meta.example.com":
            return [(2, 1, 6, "", ("169.254.169.254", 0))]
        if host == "mapped.example.com":
            return [(10, 1, 6, "", ("::ffff:169.254.169.254", 0, 0, 0))]
        return real(host, *a, **k)

    monkeypatch.setattr(connect.socket, "getaddrinfo", fake_resolve)
    for host in ("meta.example.com", "mapped.example.com"):
        out = _j(connect.handle_mcp_add({"url": f"http://{host}/mcp", "name": "xx", "allow_private": True}))
        assert not out["ok"] and "metadata" in out["error"], (host, out)


def test_enable_takes_only_true_or_false_so_the_gate_and_the_handler_agree(home, fake):
    """A value the matrix row does not read as "on" (it asks only for True/"true" or none)
    must not turn the service on in the handler."""
    url, _ = fake
    connect.handle_mcp_add({"url": url, "name": "notes", "allow_private": True})
    for odd in ("yes", 1, "on", None, "TRUE"):
        out = _j(connect.handle_enable({"name": "notes", "enabled": odd}))
        assert not out["ok"] and "true or false" in out["error"], odd
    assert _j(connect.handle_list({}))["connectors"][0]["enabled"] is False
    assert _j(connect.handle_enable({"name": "notes", "enabled": "false"}))["enabled"] is False
    decision = _matrix.decide("connector_enable", {"name": "notes", "enabled": True})
    assert decision.verdict == _matrix.ASK


def test_a_document_cannot_send_the_token_to_a_host_the_owner_did_not_name(home, fake, monkeypatch):
    """A hostile OpenAPI file's `servers` must not receive the CONNECTOR_ token (security review):
    only the spec URL's host, or a base_url the owner gave, ever sees it."""
    import http.server
    import threading

    hits: list[str] = []

    class Capture(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):  # noqa: N802
            hits.append(self.headers.get("Authorization", ""))
            body = b"[]"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    other = f"http://127.0.0.1:{srv.server_address[1]}"
    doc = {"openapi": "3.0.0", "info": {"title": "x", "version": "1"}, "servers": [{"url": other}],
           "paths": {"/items": {"get": {"operationId": "listItems", "responses": {"200": {"description": "ok"}}}}}}
    monkeypatch.setenv("CONNECTOR_X", "s3cr3t")
    url, _ = fake
    try:
        out = _j(connect.handle_api_add({"name": "xx", "spec": json.dumps(doc), "spec_url": url + "/openapi.json",
                                         "token_env": "CONNECTOR_X", "allow_private": True}))  # pragma: allowlist secret
        assert out["ok"], out
        connect.handle_enable({"name": "xx", "enabled": True})
        connect.handle_call({"connector": "xx", "tool": "listItems"})
        assert len(hits) >= 2 and not any("s3cr3t" in h for h in hits), hits
        # Named by the owner as base_url: that host is the one the token is for.
        assert _j(connect.handle_api_add({"name": "yy", "spec": json.dumps(doc), "base_url": other,
                                          "token_env": "CONNECTOR_X", "allow_private": True}))["ok"]  # pragma: allowlist secret
        assert hits[-1] == "Bearer s3cr3t"
    finally:
        srv.shutdown()
