from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("grafana-dashboards")
tools = plugin.tools

SEARCH_HITS = [
    {"uid": "homelab", "title": "Homelab", "folderTitle": "Infrastructure", "tags": ["lan", "cpu"],
     "url": "/d/homelab/homelab", "type": "dash-db"},
    {"uid": "media", "title": "Media", "folderTitle": "Apps", "tags": ["jellyfin"],
     "url": "/d/media/media", "type": "dash-db"},
]

#: A board shaped the way Grafana actually stores one: a current panel with a
#: uid datasource, a stat panel with no query, a legacy panel naming its
#: datasource by display name, and a collapsed row hiding a panel inside itself.
DASHBOARD = {
    "meta": {"folderTitle": "Infrastructure", "url": "/d/homelab/homelab"},
    "dashboard": {
        "uid": "homelab", "title": "Homelab", "tags": ["lan", "cpu"],
        "panels": [
            {"id": 1, "title": "CPU", "type": "timeseries",
             "datasource": {"type": "influxdb", "uid": "influx-uid"},
             "targets": [{"refId": "A", "rawQuery": True,
                          "query": 'from(bucket: "proxmox") |> range(start: -1h)'},
                         {"refId": "B", "hide": True, "query": "unused"}]},
            {"id": 2, "title": "Uptime", "type": "stat",
             "datasource": {"type": "influxdb", "uid": "influx-uid"}, "targets": []},
            {"id": 3, "type": "row", "title": "Details", "collapsed": True, "panels": [
                {"id": 4, "title": "Load", "type": "timeseries", "datasource": "Prometheus",
                 "targets": [{"refId": "A", "expr": "rate(node_cpu_seconds_total[5m])"}]},
            ]},
        ],
    },
}

DATASOURCES = [
    {"uid": "influx-uid", "name": "InfluxDB", "type": "influxdb", "isDefault": True},
    {"uid": "prom-uid", "name": "Prometheus", "type": "prometheus"},
]

FRAMES = {"results": {"A": {"frames": [
    {"schema": {"fields": [{"name": "Time"}, {"name": "Value", "labels": {"host": "pve"}}]},
     "data": {"values": [[1756770000000, 1756770060000], [42.0, 43.5]]}},
]}}}


def _mount(monkeypatch, handler) -> None:
    """Route every httpx.Client the shared Grafana layer opens at ``handler``."""
    Orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: Orig(base_url=kw.get("base_url", ""),
                                                           headers=kw.get("headers"),
                                                           transport=httpx.MockTransport(handler)))


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("GRAFANA_URL", "https://monitor.test")
    monkeypatch.setenv("GRAFANA_TOKEN", "glsa_token")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query.decode()),
                     request.headers.get("authorization")))
        if request.url.path == "/api/search":
            return httpx.Response(200, json=SEARCH_HITS)
        if request.url.path == "/api/dashboards/uid/homelab":
            return httpx.Response(200, json=DASHBOARD)
        if request.url.path == "/api/datasources":
            return httpx.Response(200, json=DATASOURCES)
        if request.url.path == "/api/ds/query":
            seen.append(("BODY", json.loads(request.content), "", ""))
            return httpx.Response(200, json=FRAMES)
        return httpx.Response(404, text="nope")

    _mount(monkeypatch, handler)
    return seen


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def test_search_is_parsed_into_rows(fake):
    out = json.loads(tools.handle_search_dashboards({"query": "home", "tag": "cpu", "limit": 10}))
    method, path, query, auth = next(s for s in fake if s[1] == "/api/search")
    assert (method, auth) == ("GET", "Bearer glsa_token")
    assert "type=dash-db" in query and "query=home" in query and "tag=cpu" in query and "limit=10" in query
    assert out["ok"] and out["count"] == 2
    assert out["dashboards"][0] == {"uid": "homelab", "title": "Homelab", "folder": "Infrastructure",
                                    "tags": ["lan", "cpu"], "url": "/d/homelab/homelab"}


def test_search_filters_by_folder_on_the_results(fake):
    # Grafana ignores a parameter it does not know, so the folder narrowing is
    # done here and the result says it only narrowed the page it fetched.
    out = json.loads(tools.handle_search_dashboards({"folder": "apps"}))
    assert [d["uid"] for d in out["dashboards"]] == ["media"]
    assert "not to every folder" in out["folder_filter"]


def test_the_command_lists_titles(fake):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    text = ctx.commands["dashboards"]("")
    assert "Homelab (Infrastructure) — uid homelab" in text and "Media (Apps)" in text


# --------------------------------------------------------------------------- #
# One dashboard
# --------------------------------------------------------------------------- #
def test_panels_come_back_with_their_queries(fake):
    out = json.loads(tools.handle_get_dashboard({"uid": "homelab"}))
    assert out["ok"] and out["title"] == "Homelab" and out["folder"] == "Infrastructure"
    assert out["tags"] == ["lan", "cpu"] and out["panel_count"] == 3
    by_id = {p["id"]: p for p in out["panels"]}
    assert set(by_id) == {1, 2, 4}  # the row itself is not a panel; its child is
    assert by_id[1]["type"] == "timeseries" and by_id[1]["datasource"] == "influx-uid"
    assert by_id[1]["queries"] == ['from(bucket: "proxmox") |> range(start: -1h)', "unused"]
    assert by_id[2]["queries"] == []
    assert by_id[4]["datasource"] == "Prometheus"
    assert by_id[4]["queries"] == ["rate(node_cpu_seconds_total[5m])"]
    assert "truncated" not in out
    assert json.loads(tools.handle_get_dashboard({}))["ok"] is False


def test_a_builder_mode_target_reports_its_fields_rather_than_an_empty_query():
    # An InfluxQL panel built in the UI stores no query string at all; saying it
    # has no query would be a lie about a panel that plots fine.
    text = tools.query_text({"refId": "A", "measurement": "cpu", "select": [[{"type": "field"}]]})
    assert "measurement" in text and "refId" not in text
    assert tools.query_text({"refId": "A", "datasource": {"uid": "x"}}) == ""


def test_get_dashboard_truncation_names_its_cap(monkeypatch):
    """Over the cap the queries go first, then whole panels — and both levels
    have to say the number they were cut at."""
    monkeypatch.setenv("GRAFANA_URL", "https://monitor.test")
    fat = {"meta": {"folderTitle": "Infrastructure"},
           "dashboard": {"uid": "big", "title": "Big", "tags": [], "panels": [
               {"id": i, "title": f"panel {i}", "type": "timeseries",
                "datasource": {"uid": "influx-uid"},
                "targets": [{"refId": "A", "query": "x" * 800}]} for i in range(20)]}}
    _mount(monkeypatch, lambda r: httpx.Response(200, json=fat))

    out = json.loads(tools.handle_get_dashboard({"uid": "big"}))
    assert f"{tools.MAX_DASHBOARD_CHARS}-character cap" in out["truncated"]
    assert out["panel_count"] == 20 and len(out["panels"]) == 20
    assert all("queries" not in p for p in out["panels"])

    tiny = tools.get_dashboard("big", max_chars=500)
    assert "500-character cap" in tiny["truncated"]
    assert tiny["panel_count"] == 20 and 0 < len(tiny["panels"]) < 20
    assert len(json.dumps(tiny)) <= 500


# --------------------------------------------------------------------------- #
# Running a panel's query
# --------------------------------------------------------------------------- #
def test_panel_query_returns_rows(fake):
    out = json.loads(tools.handle_panel_query({"uid": "homelab", "panel_id": 1, "from": "now-2h"}))
    body = next(s[1] for s in fake if s[0] == "BODY")
    assert body["from"] == "now-2h" and body["to"] == "now"
    # The hidden target is not run: Grafana does not draw it, so its rows would
    # answer a different question from the graph on the board.
    assert len(body["queries"]) == 1
    assert body["queries"][0]["refId"] == "A"
    assert body["queries"][0]["datasource"] == {"uid": "influx-uid"}
    assert body["queries"][0]["query"].startswith('from(bucket: "proxmox")')
    assert out["ok"] and out["count"] == 2
    assert out["rows"][0] == {"host": "pve", "_time": 1756770000000, "_value": 42.0}
    assert out["panel"] == {"id": 1, "title": "CPU", "type": "timeseries"}


def test_panel_query_resolves_a_datasource_named_the_old_way(fake):
    out = json.loads(tools.handle_panel_query({"uid": "homelab", "panel_id": 4}))
    assert ("GET", "/api/datasources", "", "Bearer glsa_token") in fake
    body = next(s[1] for s in fake if s[0] == "BODY")
    assert body["queries"][0]["datasource"] == {"uid": "prom-uid"}
    assert body["queries"][0]["expr"] == "rate(node_cpu_seconds_total[5m])"
    assert body["from"] == "now-1h" and out["ok"]


def test_panel_query_says_which_panels_exist_when_the_id_is_wrong(fake):
    out = json.loads(tools.handle_panel_query({"uid": "homelab", "panel_id": 99}))
    assert out["ok"] is False and "no panel 99" in out["error"]
    assert {p["id"] for p in out["panels"]} == {1, 2, 4}
    assert json.loads(tools.handle_panel_query({"uid": "homelab", "panel_id": 2}))["ok"] is False
    assert json.loads(tools.handle_panel_query({"uid": "homelab"}))["ok"] is False


def test_a_template_variable_datasource_is_reported_not_guessed(fake):
    out = json.loads(tools.handle_panel_query({"uid": "homelab", "panel_id": 1}))
    assert out["ok"]  # sanity: the same call works with a real uid
    with pytest.raises(RuntimeError, match="template variable"):
        tools.resolve_datasource({"uid": "${DS_INFLUX}"})
    with pytest.raises(RuntimeError, match="no Grafana datasource"):
        tools.resolve_datasource("Nothing")
    # No datasource at all falls back to Grafana's default, as Grafana does.
    assert tools.resolve_datasource(None) == "influx-uid"


# --------------------------------------------------------------------------- #
# Registration and credentials
# --------------------------------------------------------------------------- #
def test_register_has_no_approval_hook(monkeypatch):
    monkeypatch.setenv("GRAFANA_URL", "https://monitor.test")
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"grafana_search_dashboards", "grafana_get_dashboard", "grafana_panel_query"}
    # Read-only: a gate here would be a prompt the owner learns to dismiss.
    assert "pre_tool_call" not in ctx.hooks
    assert "dashboards" in ctx.commands
    assert all(ctx.available(name) for name in ctx.tools)
    monkeypatch.setenv("GRAFANA_URL", "")
    assert not any(ctx.available(name) for name in ctx.tools)


def test_tools_say_so_when_grafana_is_not_configured(monkeypatch):
    monkeypatch.setenv("GRAFANA_URL", "")
    for raw in (tools.handle_search_dashboards({}), tools.handle_get_dashboard({"uid": "x"}),
                tools.handle_panel_query({"uid": "x", "panel_id": 1})):
        out = json.loads(raw)
        assert out["ok"] is False and "GRAFANA_URL" in out["error"]


def test_no_credential_reaches_a_tool_result(monkeypatch):
    """httpx echoes the request URL in every error it raises, and that URL carries
    the userinfo when GRAFANA_URL was written as https://user:pw@host."""
    monkeypatch.setenv("GRAFANA_URL", "https://ops:s3cr3tpw@monitor.test")
    monkeypatch.setenv("GRAFANA_TOKEN", "glsa_SECRET_TOKEN_123")
    _mount(monkeypatch, lambda r: httpx.Response(401, text="token glsa_SECRET_TOKEN_123 is not valid"))

    for raw in (tools.handle_search_dashboards({}), tools.handle_get_dashboard({"uid": "homelab"}),
                tools.handle_panel_query({"uid": "homelab", "panel_id": 1})):
        assert json.loads(raw)["ok"] is False
        for secret in ("glsa_SECRET_TOKEN_123", "s3cr3tpw", "ops:s3cr3tpw"):
            assert secret not in raw, raw


def test_the_token_is_not_in_a_successful_payload(fake):
    for raw in (tools.handle_search_dashboards({}), tools.handle_get_dashboard({"uid": "homelab"}),
                tools.handle_panel_query({"uid": "homelab", "panel_id": 1}),
                tools.handle_command("")):
        assert "glsa_token" not in raw and "Bearer" not in raw
