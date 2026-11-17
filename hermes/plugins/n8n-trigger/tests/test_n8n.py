from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("n8n-trigger")
tools = plugin.tools

#: One workflow as n8n's public API returns it, including the keys the API
#: refuses to take back (`id`, `active`, `tags`, `createdAt`, `updatedAt`).
WORKFLOW = {
    "id": "7",
    "name": "Morning lights",
    "active": False,
    "createdAt": "2026-01-01T00:00:00.000Z",
    "updatedAt": "2026-01-02T00:00:00.000Z",
    "settings": {"executionOrder": "v1"},
    "tags": [{"id": "1", "name": "home"}],
    "nodes": [
        {"name": "Cron", "type": "n8n-nodes-base.cron", "typeVersion": 1, "position": [0, 0],
         "parameters": {"triggerTimes": {"item": [{"hour": 7}]}}},
        {"name": "Lights", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4, "position": [200, 0],
         "parameters": {"url": "http://ha.test/api/services/light/turn_on"}},
    ],
    "connections": {"Cron": {"main": [[{"node": "Lights", "type": "main", "index": 0}]]}},
}


def _mount(monkeypatch, handler) -> None:
    """Route every httpx.Client the plugin opens at ``handler``.

    The lambda keeps only base_url and headers on purpose: MockTransport ignores
    timeout/verify, and dropping them proves no call depends on them.
    """
    Orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: Orig(base_url=kw.get("base_url", ""),
                                                           headers=kw.get("headers"),
                                                           transport=httpx.MockTransport(handler)))


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("N8N_URL", "https://n8n.test")
    monkeypatch.setenv("N8N_API_KEY", "k")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("x-n8n-api-key")))
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/v1/executions/42" and request.method == "GET":
            return httpx.Response(200, json={
                "id": "42", "workflowId": "7", "status": "error", "mode": "webhook",
                "startedAt": "2026-09-10T05:00:00Z", "stoppedAt": "2026-09-10T05:00:01Z",
                "data": {"resultData": {
                    "lastNodeExecuted": "Lights",
                    "error": {"message": "connect ECONNREFUSED ha.test:8080", "node": {"name": "Lights"}},
                    "runData": {
                        "Cron": [{"data": {"main": [[{"json": {"hour": 7}}]]}}],
                        "Lights": [{"error": {"message": "connect ECONNREFUSED ha.test:8080"},
                                    "data": {"main": [[]]}}],
                    }}}})
        if request.url.path == "/api/v1/workflows/7" and request.method == "DELETE":
            return httpx.Response(200, json={"id": "7", "name": "Morning lights"})
        if request.url.path == "/api/v1/credentials/schema/slackApi":
            return httpx.Response(200, json={"type": "object", "properties": {"accessToken": {"type": "string"}},
                                             "required": ["accessToken"]})
        if request.url.path == "/api/v1/credentials" and request.method == "POST":
            assert body["data"]["accessToken"] == "xoxb-secret"
            return httpx.Response(200, json={"id": "3", "name": body["name"], "type": body["type"]})
        if request.url.path == "/api/v1/credentials/3" and request.method == "DELETE":
            return httpx.Response(200, json={})
        if request.url.path == "/api/v1/workflows" and request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "1", "name": "Lights", "active": True,
                                                       "nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": "lights-off"}}]}]})
        if request.url.path == "/api/v1/workflows" and request.method == "POST":
            seen.append(("BODY", body))
            return httpx.Response(200, json={**body, "id": "42", "active": False})
        if request.url.path == "/api/v1/workflows/7" and request.method == "GET":
            return httpx.Response(200, json=WORKFLOW)
        if request.url.path == "/api/v1/workflows/7" and request.method == "PUT":
            seen.append(("BODY", body))
            return httpx.Response(200, json={**WORKFLOW, **body})
        if request.url.path == "/api/v1/workflows/7/activate":
            return httpx.Response(200, json={**WORKFLOW, "active": True})
        if request.url.path == "/api/v1/workflows/7/deactivate":
            return httpx.Response(200, json={**WORKFLOW, "active": False})
        if request.url.path == "/api/v1/executions":
            return httpx.Response(200, json={"data": [{"id": "9", "workflowId": "1", "status": "success"}]})
        if request.url.path == "/webhook/lights-off":
            return httpx.Response(200, json={"done": True, "got": body})
        return httpx.Response(404, text="nope")

    _mount(monkeypatch, handler)
    return seen


def test_webhook_url(monkeypatch):
    monkeypatch.setenv("N8N_URL", "https://n8n.test/")
    assert tools.webhook_url("lights-off") == "https://n8n.test/webhook/lights-off"
    assert tools.webhook_url("/lights-off", test=True) == "https://n8n.test/webhook-test/lights-off"
    assert tools.webhook_url("https://n8n.test/webhook/y") == "https://n8n.test/webhook/y"


# T-1350: the webhook auth header goes only to the configured server. A model-supplied absolute
# address is compared with N8N_URL on scheme, host and port, after parsing, never as text.
OTHER_ORIGINS = [
    "https://evil.example.test/webhook/x",
    "http://n8n.example.test/webhook/x",                      # scheme differs
    "https://n8n.example.test:8443/webhook/x",                # port differs
    "https://n8n.example.test@evil.example.test/webhook/x",   # the host is what follows the @
    "https://evil.example.test@n8n.example.test/webhook/x",   # a login rides along as basic auth
    "https://n8n.example.test:443@evil.example.test/x",
    "https://n8n.example.test.evil.example.test/webhook/x",   # the configured name as a prefix
    "https://evil-n8n.example.test/webhook/x",
    "https://n8n.example.test\\@evil.example.test/webhook/x",  # a backslash some parsers read as a slash
    "ftp://n8n.example.test/webhook/x",
    "https://n8n.example.test /webhook/x",
]


@pytest.mark.parametrize("bad", OTHER_ORIGINS)
def test_webhook_url_refuses_another_origin(monkeypatch, bad):
    monkeypatch.setenv("N8N_URL", "https://n8n.example.test")
    with pytest.raises(ValueError) as err:
        tools.webhook_url(bad)
    assert "evil" not in str(err.value) and "N8N_URL" in str(err.value)


@pytest.mark.parametrize("good", [
    "https://n8n.example.test/webhook/x",
    "https://n8n.example.test:443/webhook/x",                 # the default port, spelled out
    "https://N8N.Example.TEST/webhook/x",
    "HTTPS://n8n.example.test/webhook/x",
    "https://n8n.example.test./webhook/x",                    # the root dot
])
def test_webhook_url_keeps_the_same_origin(monkeypatch, good):
    monkeypatch.setenv("N8N_URL", "https://n8n.example.test")
    assert tools.webhook_url(good) == good


def test_webhook_url_compares_a_plain_http_server_port(monkeypatch):
    monkeypatch.setenv("N8N_URL", "http://10.0.0.9:5678")
    assert tools.webhook_url("http://10.0.0.9:5678/webhook/x") == "http://10.0.0.9:5678/webhook/x"
    for bad in ("http://10.0.0.9/webhook/x", "http://10.0.0.9:80/webhook/x", "https://10.0.0.9:5678/x"):
        with pytest.raises(ValueError):
            tools.webhook_url(bad)


@pytest.mark.parametrize("bad", OTHER_ORIGINS)
def test_trigger_to_another_origin_sends_nothing_and_does_not_echo_it(monkeypatch, bad):
    monkeypatch.setenv("N8N_URL", "https://n8n.example.test")
    monkeypatch.setenv("N8N_WEBHOOK_HEADER", "X-Hook-Key: header-value-1234")
    monkeypatch.setenv("N8N_WEBHOOK_AUTH", "hookuser:basic-value-1234")
    sent: list[httpx.Request] = []

    def _send(self, request, **kwargs):
        sent.append(request)
        raise httpx.ConnectError("no network in this test", request=request)

    monkeypatch.setattr(httpx.Client, "send", _send)
    out = tools.handle_n8n_trigger({"webhook": bad})
    assert json.loads(out)["ok"] is False
    assert sent == []
    assert "evil" not in out and "8443" not in out and "header-value" not in out and "basic-value" not in out


def test_trigger_on_the_configured_server_still_carries_the_header(monkeypatch):
    monkeypatch.setenv("N8N_URL", "https://n8n.example.test")
    monkeypatch.setenv("N8N_WEBHOOK_HEADER", "X-Hook-Key: header-value-1234")
    got: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        got.append(request)
        return httpx.Response(200, json={"done": True})

    _mount(monkeypatch, handler)
    for hook in ("lights-off", "https://n8n.example.test/webhook/lights-off"):
        assert json.loads(tools.handle_n8n_trigger({"webhook": hook}))["ok"] is True
    assert [r.url.host for r in got] == ["n8n.example.test"] * 2
    assert all(r.headers["x-hook-key"] == "header-value-1234" for r in got)


def test_trigger_list_executions_health(fake):
    out = json.loads(tools.handle_n8n_trigger({"webhook": "lights-off", "payload": {"room": "kitchen"}}))
    assert out["ok"] and out["http"] == 200 and out["response"]["got"] == {"room": "kitchen"}
    assert json.loads(tools.handle_n8n_trigger({}))["ok"] is False
    lst = json.loads(tools.handle_n8n_list({}))
    assert lst["workflows"] == [{"id": "1", "name": "Lights", "active": True, "webhooks": ["lights-off"]}]
    assert ("GET", "/api/v1/workflows", "k") in fake
    ex = json.loads(tools.handle_n8n_executions({"workflow_id": "1"}))
    assert ex["executions"][0]["status"] == "success"
    h = json.loads(tools.handle_n8n_health({}))
    assert h["ok"] and h["api_key_ok"] is True


def test_failed_webhook_is_reported(fake):
    out = json.loads(tools.handle_n8n_trigger({"webhook": "missing"}))
    assert out["ok"] is False and out["http"] == 404


# --------------------------------------------------------------------------- #
# Reading a definition
# --------------------------------------------------------------------------- #
def test_get_workflow_returns_the_whole_definition(fake):
    out = json.loads(tools.handle_n8n_get_workflow({"workflow_id": "7"}))
    assert ("GET", "/api/v1/workflows/7", "k") in fake
    assert out["ok"] and out["id"] == "7" and out["name"] == "Morning lights"
    assert out["tags"] == ["home"] and out["settings"] == {"executionOrder": "v1"}
    assert [n["name"] for n in out["nodes"]] == ["Cron", "Lights"]
    assert out["nodes"][0]["parameters"] == {"triggerTimes": {"item": [{"hour": 7}]}}
    assert out["connections"]["Cron"]["main"][0][0]["node"] == "Lights"
    assert "truncated" not in out
    assert json.loads(tools.handle_n8n_get_workflow({}))["ok"] is False


def test_get_workflow_truncation_names_its_cap(monkeypatch):
    """A definition over the cap loses node parameters first, then whole nodes.

    Both levels have to say the number they were cut at: a caller handed a
    silently shortened workflow will edit it and PUT the shortage back.
    """
    monkeypatch.setenv("N8N_URL", "https://n8n.test")
    monkeypatch.setenv("N8N_API_KEY", "k")
    fat = {**WORKFLOW, "nodes": [{"name": f"n{i}", "type": "n8n-nodes-base.code", "typeVersion": 2,
                                  "parameters": {"jsCode": "x" * 900}} for i in range(20)]}
    _mount(monkeypatch, lambda r: httpx.Response(200, json=fat))

    out = json.loads(tools.handle_n8n_get_workflow({"workflow_id": "7"}))
    assert f"{tools.MAX_WORKFLOW_CHARS}-character cap" in out["truncated"]
    assert out["node_count"] == 20 and len(out["nodes"]) == 20
    assert all("parameters" not in n for n in out["nodes"])
    assert out["connections"] == WORKFLOW["connections"]  # the wiring survives level one

    # A cap small enough that even the stripped nodes do not fit drops the
    # connections and lists what it could.
    tiny = tools.get_workflow("7", max_chars=700)
    assert "700-character cap" in tiny["truncated"] and "connections" not in tiny
    assert tiny["node_count"] == 20 and 0 < len(tiny["nodes"]) < 20
    assert len(json.dumps(tiny)) <= 700


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def test_create_workflow_posts_only_the_fields_the_api_accepts(fake):
    out = json.loads(tools.handle_n8n_create_workflow({
        "name": "New board", "nodes": WORKFLOW["nodes"], "connections": WORKFLOW["connections"],
        # The keys recent n8n answers 400 for; they must not reach the wire.
        "id": "99", "active": True, "tags": ["home"], "createdAt": "now",
    }))
    assert ("POST", "/api/v1/workflows", "k") in fake
    body = next(b for tag, b in ((s[0], s[1]) for s in fake if s[0] == "BODY"))
    assert set(body) == {"name", "nodes", "connections", "settings"}
    assert body["settings"] == {"executionOrder": "v1"}
    assert out["ok"] and out["id"] == "42" and out["active"] is False
    assert "n8n_activate_workflow" in out["note"]


def test_create_workflow_rejects_an_empty_graph(fake):
    assert json.loads(tools.handle_n8n_create_workflow({"name": "x"}))["ok"] is False
    assert json.loads(tools.handle_n8n_create_workflow({"name": "", "nodes": [{}], "connections": {}}))["ok"] is False
    assert json.loads(tools.handle_n8n_create_workflow(
        {"name": "x", "nodes": [{}], "connections": []}))["ok"] is False


def test_a_model_written_workflow_is_checked_before_it_is_sent(fake):
    """T-0630: shape and size are ours to check; n8n's 400 is a worse place to learn them."""
    def create(**kw):
        return json.loads(tools.handle_n8n_create_workflow({"name": "x", **kw}))

    ok = [{"name": "A", "type": "t"}, {"name": "B", "type": "t"}]
    assert create(nodes=["not a node"], connections={})["ok"] is False
    bad = create(nodes=ok, connections={"Ghost": {"main": [[{"node": "B", "type": "main", "index": 0}]]}})
    assert bad["ok"] is False and "Ghost" in bad["error"]
    bad = create(nodes=ok, connections={"A": {"main": [[{"node": "Nowhere", "type": "main", "index": 0}]]}})
    assert bad["ok"] is False and "Nowhere" in bad["error"]
    huge = create(nodes=[{"name": "A", "parameters": {"c": "x" * 250_000}}], connections={})
    assert huge["ok"] is False and "too large" in huge["error"]
    assert not [s for s in fake if s[0] == "POST"], "nothing invalid reached n8n"
    good = create(nodes=ok, connections={"A": {"main": [[{"node": "B", "type": "main", "index": 0}]]}})
    assert good["ok"] is True


def test_update_workflow_reads_first_so_a_rename_keeps_the_nodes(fake):
    out = json.loads(tools.handle_n8n_update_workflow({"workflow_id": "7", "name": "Evening lights"}))
    calls = [(m, p) for m, p, _ in (s for s in fake if s[0] != "BODY")]
    assert ("GET", "/api/v1/workflows/7") in calls and ("PUT", "/api/v1/workflows/7") in calls
    assert ("PUT", "/api/v1/workflows/7", "k") in fake
    body = next(b for tag, b in ((s[0], s[1]) for s in fake if s[0] == "BODY"))
    assert set(body) == {"name", "nodes", "connections", "settings"}
    assert body["name"] == "Evening lights"
    assert body["nodes"] == WORKFLOW["nodes"]  # carried over, not blanked
    assert out["ok"] and out["replaced"] == ["name"] and "nodes" in out["kept"]
    assert json.loads(tools.handle_n8n_update_workflow({"workflow_id": "7"}))["ok"] is False
    assert json.loads(tools.handle_n8n_update_workflow({"name": "x"}))["ok"] is False


def test_activate_and_deactivate_hit_their_own_paths(fake):
    on = json.loads(tools.handle_n8n_activate_workflow({"workflow_id": "7", "active": True}))
    assert ("POST", "/api/v1/workflows/7/activate", "k") in fake and on["active"] is True
    off = json.loads(tools.handle_n8n_activate_workflow({"workflow_id": "7", "active": False}))
    assert ("POST", "/api/v1/workflows/7/deactivate", "k") in fake and off["active"] is False
    assert json.loads(tools.handle_n8n_activate_workflow({"active": True}))["ok"] is False


def test_a_400_body_message_is_passed_through_verbatim(monkeypatch):
    """n8n's schema validator names the property it rejected; that sentence is
    the only thing that tells the model what to send instead."""
    monkeypatch.setenv("N8N_URL", "https://n8n.test")
    monkeypatch.setenv("N8N_API_KEY", "k")
    message = "request/body must NOT have additional properties"
    _mount(monkeypatch, lambda r: httpx.Response(400, json={"message": message}))

    for handler, params in (
        (tools.handle_n8n_create_workflow, {"name": "x", "nodes": [{}], "connections": {}}),
        (tools.handle_n8n_update_workflow, {"workflow_id": "7", "name": "x"}),
        (tools.handle_n8n_activate_workflow, {"workflow_id": "7", "active": True}),
        (tools.handle_n8n_get_workflow, {"workflow_id": "7"}),
    ):
        out = json.loads(handler(params))
        assert out == {"ok": False, "http": 400, "error": message}


def test_a_400_without_a_json_message_still_answers(monkeypatch):
    monkeypatch.setenv("N8N_URL", "https://n8n.test")
    monkeypatch.setenv("N8N_API_KEY", "k")
    _mount(monkeypatch, lambda r: httpx.Response(400, text="Bad Request"))
    out = json.loads(tools.handle_n8n_activate_workflow({"workflow_id": "7", "active": True}))
    assert out["ok"] is False and out["error"] == "Bad Request"


# --------------------------------------------------------------------------- #
# The approval gate
# --------------------------------------------------------------------------- #
def test_register_gate():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"n8n_trigger", "n8n_health", "n8n_list_workflows", "n8n_executions",
                              "n8n_get_workflow", "n8n_create_workflow", "n8n_update_workflow",
                              "n8n_activate_workflow", "n8n_execution", "n8n_delete_workflow",
                              "n8n_credential_schema", "n8n_credential_create", "n8n_credential_delete"}
    assert ctx.hooks["pre_tool_call"]("n8n_trigger", {"webhook": "x"}, "")["action"] == "approve"
    assert ctx.hooks["pre_tool_call"]("n8n_health", {}, "") is None


def test_every_writing_tool_is_gated_and_every_reader_is_not():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    gate = ctx.hooks["pre_tool_call"]
    writers = {"n8n_trigger": {"webhook": "x"},
               "n8n_create_workflow": {"name": "New board", "nodes": [{}, {}], "connections": {}},
               "n8n_update_workflow": {"workflow_id": "7", "name": "Morning lights"},
               "n8n_activate_workflow": {"workflow_id": "7", "active": True},
               "n8n_delete_workflow": {"workflow_id": "7"},
               "n8n_credential_create": {"name": "slack", "type": "slackApi", "data": {"accessToken": "x"}},
               "n8n_credential_delete": {"credential_id": "3"}}
    assert set(writers) == plugin.schemas.DESTRUCTIVE  # DESTRUCTIVE is the one list
    for name, args in writers.items():
        directive = gate(name, args, "")
        assert directive["action"] == "approve", name
        assert directive["message"] and directive["rule_key"], name
    # A delete says so in capitals and names the workflow; a credential's secret never appears.
    assert "DELETED" in gate("n8n_delete_workflow", {"workflow_id": "7"}, "")["message"]
    assert "x" not in gate("n8n_credential_create", writers["n8n_credential_create"], "")["message"].split("'slack'")[1]
    for name in ("n8n_health", "n8n_list_workflows", "n8n_executions", "n8n_get_workflow",
                 "n8n_execution", "n8n_credential_schema"):
        assert gate(name, {"workflow_id": "7"}, "") is None, name
    # Hermes passes (tool_name, args, task_id, **extra); approval_gate drops the rest.
    assert gate("n8n_trigger", {"webhook": "x"}, "task-1", extra=1)["rule_key"].rsplit(":g", 1)[0] == "n8n:x"


def test_the_update_message_names_the_workflow_by_id_and_name():
    """"update workflow 7" tells the owner nothing about what they are approving."""
    directive = tools.build_approval("n8n_update_workflow",
                                     {"workflow_id": "7", "name": "Morning lights", "nodes": [{}]})
    assert "7" in directive["message"] and "Morning lights" in directive["message"]
    assert directive["rule_key"] == "n8n:update_workflow:7"
    assert "nodes" in directive["message"]


def test_activation_and_deactivation_do_not_share_a_rule_key():
    # Remembering "yes, stop this one" must not pre-approve starting it again.
    on = tools.build_approval("n8n_activate_workflow", {"workflow_id": "7", "active": True})
    off = tools.build_approval("n8n_activate_workflow", {"workflow_id": "7", "active": False})
    assert on["rule_key"] != off["rule_key"]
    assert "starts firing" in on["message"] and "stops firing" in off["message"]


def test_the_trigger_directive_is_unchanged():
    # The pre-existing behaviour other things may already have rules for.
    assert tools.build_approval("n8n_trigger", {"webhook": "lights-off"}) == {
        "action": "approve", "message": "n8n workflow 'lights-off' will run with side effects",
        "rule_key": "n8n:lights-off"}
    assert tools.build_approval("n8n_trigger", {})["rule_key"] == "n8n:?"


def test_the_command_still_answers_health_or_list(fake):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert json.loads(ctx.commands["n8n"](""))["ok"] is True
    assert "workflows" in json.loads(ctx.commands["n8n"]("list"))


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def test_no_credential_reaches_a_tool_result(monkeypatch):
    """httpx echoes the request URL in every error it raises, and that URL carries
    the userinfo when N8N_URL was written as https://user:pw@host."""
    monkeypatch.setenv("N8N_URL", "https://bob:hunter2@n8n.test")
    monkeypatch.setenv("N8N_API_KEY", "n8napi-SECRET-123")
    monkeypatch.setenv("N8N_WEBHOOK_AUTH", "hookuser:hookpass")
    _mount(monkeypatch, lambda r: httpx.Response(500, text="upstream said n8napi-SECRET-123 is bad"))

    payloads = [
        tools.handle_n8n_list({}),
        tools.handle_n8n_executions({}),
        tools.handle_n8n_get_workflow({"workflow_id": "7"}),
        tools.handle_n8n_create_workflow({"name": "x", "nodes": [{}], "connections": {}}),
        tools.handle_n8n_update_workflow({"workflow_id": "7", "name": "x"}),
        tools.handle_n8n_activate_workflow({"workflow_id": "7", "active": True}),
        tools.handle_n8n_trigger({"webhook": "lights-off"}),
        tools.handle_n8n_health({}),
    ]
    for raw in payloads:
        assert json.loads(raw)["ok"] is False
        for secret in ("n8napi-SECRET-123", "hunter2", "bob:hunter2", "hookpass"):
            assert secret not in raw, raw


# --------------------------------------------------------------------------- #
# Managing: one execution's error, delete, credentials (2026-09-10)
# --------------------------------------------------------------------------- #
def test_an_execution_comes_back_with_its_error_and_the_node_it_stopped_on(fake):
    out = json.loads(tools.handle_n8n_execution({"execution_id": "42"}))
    assert ("GET", "/api/v1/executions/42", "k") in fake
    assert out["ok"] and out["status"] == "error" and out["lastNodeExecuted"] == "Lights"
    assert out["error"] == {"message": "connect ECONNREFUSED ha.test:8080", "node": "Lights"}
    assert out["nodes"]["Cron"] == {"items": 1, "last": {"hour": 7}, "error": None}
    assert out["nodes"]["Lights"]["error"] == "connect ECONNREFUSED ha.test:8080"
    assert json.loads(tools.handle_n8n_execution({}))["ok"] is False


def test_delete_and_credentials_go_through_the_api_and_never_echo_a_secret(fake):
    gone = json.loads(tools.handle_n8n_delete_workflow({"workflow_id": "7"}))
    assert gone == {"ok": True, "id": "7", "name": "Morning lights", "deleted": True}
    assert ("DELETE", "/api/v1/workflows/7", "k") in fake
    schema = json.loads(tools.handle_n8n_credential_schema({"type": "slackApi"}))
    assert schema["fields"] == ["accessToken"] and schema["required"] == ["accessToken"]
    made = tools.handle_n8n_credential_create({"name": "slack", "type": "slackApi", "data": {"accessToken": "xoxb-secret"}})
    assert json.loads(made) == {"ok": True, "id": "3", "name": "slack", "type": "slackApi"}
    assert "xoxb-secret" not in made
    assert json.loads(tools.handle_n8n_credential_delete({"credential_id": "3"}))["deleted"] is True
    assert json.loads(tools.handle_n8n_credential_create({"name": "x", "type": "y"}))["ok"] is False
