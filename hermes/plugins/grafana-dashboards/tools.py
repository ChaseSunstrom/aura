"""Reading Grafana's dashboards, over the shared ``_shared._grafana`` layer.

Three questions, in the order they get asked: which boards exist
(:func:`search_dashboards`), what is on one of them (:func:`get_dashboard`),
and what does one panel actually say right now (:func:`panel_query`) — the last
by sending the panel's own stored query back through Grafana's datasource proxy.

Nothing here writes. Grafana's dashboard API is a whole-document PUT: an update
carries the entire board, so a tool that got one field wrong would silently
replace someone's layout, alert thresholds and variables with whatever the model
reconstructed. The blast radius is not worth "JARVIS can move a panel", so the
plugin is read-only and the gateway registers no approval hook for it.

What the panel reader does *not* cover:

* Template variables. Grafana substitutes ``$server``, ``$__interval`` and the
  rest in its frontend before the query leaves the browser; the stored query
  still holds the placeholders, and this sends the query as stored. A panel that
  depends on a dashboard variable therefore comes back as the datasource's own
  complaint about an unresolved token rather than a silently wrong number.
* Panel transformations, field overrides, unit formatting and value mappings.
  Those live outside the query, so the rows here are the datasource's answer,
  not the numbers rendered on the panel.
* Mixed-datasource panels beyond the per-target datasource each target names,
  and library panels, whose definition lives outside the dashboard document.
"""
from __future__ import annotations

import json
from typing import Any

from _shared import _grafana

#: How many characters of a dashboard ``grafana_get_dashboard`` will return. A
#: real board's JSON runs to tens of thousands of characters — field configs,
#: thresholds, legends, none of it useful to a model — so the panel list is
#: trimmed past this and the result says at which number it was cut.
MAX_DASHBOARD_CHARS: int = 12000

#: Rows one panel query may return. Matches gpu-telemetry's raw-query cap: past
#: a couple of hundred points a model is summarising noise, and the payload
#: crowds out the turn.
MAX_ROWS: int = 200

#: Dashboards a search returns when the caller names no limit.
DEFAULT_SEARCH_LIMIT: int = 50

#: Where each datasource type keeps its query text in a stored panel target:
#: InfluxDB/Flux ``query``, Prometheus and Loki ``expr``, the SQL sources
#: ``rawSql``, Graphite ``target``, Elasticsearch ``queryText``.
QUERY_KEYS: tuple[str, ...] = ("query", "expr", "rawSql", "target", "queryText")

#: Longest stringified structured target reported in place of query text.
MAX_TARGET_CHARS: int = 300


# --------------------------------------------------------------------------- #
# Reading the dashboard document
# --------------------------------------------------------------------------- #
def flatten_panels(panels: list[Any]) -> list[dict[str, Any]]:
    """Every real panel on a board, rows resolved.

    A collapsed row keeps its children inside its own ``panels`` list while an
    expanded row leaves them as siblings, so a reader that does not descend into
    rows reports half a dashboard on one visit and all of it on the next,
    depending on how the last person left the board.
    """
    out: list[dict[str, Any]] = []
    for panel in panels:
        if not isinstance(panel, dict):
            continue
        if panel.get("type") == "row":
            out.extend(flatten_panels(panel.get("panels") or []))
            continue
        out.append(panel)
    return out


def datasource_label(ref: Any) -> str | None:
    """How a panel names its datasource: a uid (current) or a name (pre-Grafana-9)."""
    if isinstance(ref, dict):
        return ref.get("uid") or ref.get("type")
    if isinstance(ref, str):
        return ref
    return None


def query_text(target: dict[str, Any]) -> str:
    """The query as stored, whatever key this datasource type wrote it under.

    A target with no text at all is a builder-mode query (InfluxQL's
    measurement/select/groupBy form, or an Elasticsearch bucket spec); its
    fields are reported as compact JSON rather than an empty string, because
    "this panel has no query" would be a lie about a panel that plots fine.
    """
    for key in QUERY_KEYS:
        value = target.get(key)
        if isinstance(value, str) and value.strip():
            return value
    structural = {k: v for k, v in target.items()
                  if k not in ("refId", "datasource", "hide", "key", "queryType")}
    if not structural:
        return ""
    return json.dumps(structural)[:MAX_TARGET_CHARS]


def panel_summary(panel: dict[str, Any]) -> dict[str, Any]:
    targets = [t for t in (panel.get("targets") or []) if isinstance(t, dict)]
    return {"id": panel.get("id"), "title": panel.get("title") or "", "type": panel.get("type"),
            "datasource": datasource_label(panel.get("datasource")),
            "queries": [query_text(t) for t in targets]}


def _trim_dashboard(head: dict[str, Any], panels: list[dict[str, Any]],
                    max_chars: int) -> dict[str, Any]:
    """``head`` plus as many panels as fit in ``max_chars``, saying so when it cuts.

    The query text is dropped before whole panels are: knowing a board has a
    "CPU" timeseries panel with id 4 is enough to ask :func:`panel_query` for
    it, whereas a panel that never appears cannot be asked about at all.
    """
    full = {**head, "panels": panels}
    if len(json.dumps(full)) <= max_chars:
        return full

    without_queries = [{k: v for k, v in p.items() if k != "queries"} for p in panels]
    note = (f"the panel list exceeded the {max_chars}-character cap, so the stored queries were "
            "dropped; ask grafana_panel_query for one panel to see and run its query")
    trimmed = {**head, "panels": without_queries, "truncated": note}
    if len(json.dumps(trimmed)) <= max_chars:
        return trimmed

    note = (f"the panel list exceeded the {max_chars}-character cap even without the stored "
            "queries; only the first panels are listed")
    keep = len(without_queries)
    while keep and len(json.dumps({**head, "panels": without_queries[:keep],
                                   "truncated": note})) > max_chars:
        keep -= 1
    return {**head, "panels": without_queries[:keep], "truncated": note}


# --------------------------------------------------------------------------- #
# Tool bodies
# --------------------------------------------------------------------------- #
def search_dashboards(params: dict) -> dict[str, Any]:
    if not _grafana.available():
        return _grafana.not_configured()
    tag = params.get("tag")
    if isinstance(tag, str) and tag.strip():
        tags = [tag]
    elif isinstance(tag, list):
        tags = [str(t) for t in tag]
    else:
        tags = []
    limit = max(1, min(int(params.get("limit") or DEFAULT_SEARCH_LIMIT), 500))
    query = str(params.get("query") or "").strip()

    api_params: dict[str, Any] = {"type": "dash-db", "limit": limit}
    if query:
        api_params["query"] = query
    if tags:
        api_params["tag"] = tags
    hits = _grafana.get_json("/api/search", api_params)
    rows = [{"uid": h.get("uid"), "title": h.get("title"),
             "folder": h.get("folderTitle") or "General",
             "tags": h.get("tags") or [], "url": h.get("url")}
            for h in (hits if isinstance(hits, list) else []) if isinstance(h, dict)]

    # Folder is filtered here rather than passed to /api/search: the parameter
    # that carries it changed name across Grafana versions (folderIds ->
    # folderUIDs) and Grafana ignores a parameter it does not know, which would
    # turn a mistyped filter into "every dashboard matched". The hit already
    # carries folderTitle, so matching locally cannot silently do nothing — at
    # the cost of narrowing the page rather than searching every folder.
    folder = str(params.get("folder") or "").strip().lower()
    if folder:
        rows = [r for r in rows if folder in str(r["folder"]).lower()]
    out: dict[str, Any] = {"ok": True, "dashboards": rows, "count": len(rows)}
    if folder:
        out["folder_filter"] = ("applied to the first %d search hits, not to every folder" % limit)
    return out


def get_dashboard(uid: str, max_chars: int = MAX_DASHBOARD_CHARS) -> dict[str, Any]:
    if not _grafana.available():
        return _grafana.not_configured()
    if not uid:
        return {"ok": False, "error": "uid is required (grafana_search_dashboards reports it)"}
    payload = _grafana.get_json(f"/api/dashboards/uid/{uid}")
    meta = payload.get("meta") or {}
    dashboard = payload.get("dashboard") or {}
    panels = [panel_summary(p) for p in flatten_panels(dashboard.get("panels") or [])]
    head = {"ok": True, "uid": dashboard.get("uid") or uid, "title": dashboard.get("title"),
            "folder": meta.get("folderTitle") or "General", "tags": dashboard.get("tags") or [],
            "url": meta.get("url"), "panel_count": len(panels)}
    return _trim_dashboard(head, panels, max_chars)


def resolve_datasource(ref: Any) -> str:
    """The uid ``/api/ds/query`` needs, from however a panel names its datasource.

    Raises ``RuntimeError`` with what the caller can do about it, because every
    failure here is a dashboard the model can still read — it just cannot run
    this panel — and "KeyError: uid" would not say that.
    """
    if isinstance(ref, dict):
        uid = str(ref.get("uid") or "")
        if uid and not uid.startswith("$"):
            return uid
        if uid:
            raise RuntimeError(f"panel datasource {uid} is a dashboard variable; "
                               "this plugin does not resolve template variables")
        name = str(ref.get("type") or "")
        ref = name
    if isinstance(ref, str) and ref:
        if ref.startswith("$"):
            raise RuntimeError(f"panel datasource {ref} is a dashboard variable; "
                               "this plugin does not resolve template variables")
        found = _grafana.datasource_by_name(ref)
        if found:
            return str(found["uid"])
        raise RuntimeError(f"no Grafana datasource called {ref!r}")
    # No datasource on the panel or the target: Grafana falls back to the
    # instance default, and so must this, or every panel on an older board
    # (which stored no datasource at all) is unqueryable.
    default = _grafana.default_datasource()
    if default:
        return str(default["uid"])
    raise RuntimeError("the panel names no datasource and Grafana has no default one")


def panel_query(params: dict) -> dict[str, Any]:
    if not _grafana.available():
        return _grafana.not_configured()
    uid = str(params.get("uid") or "").strip()
    panel_id = str(params.get("panel_id") if params.get("panel_id") is not None else "").strip()
    if not uid or not panel_id:
        return {"ok": False,
                "error": "uid and panel_id are required (grafana_get_dashboard reports both)"}
    time_from = str(params.get("from") or "now-1h")
    time_to = str(params.get("to") or "now")

    payload = _grafana.get_json(f"/api/dashboards/uid/{uid}")
    panels = flatten_panels((payload.get("dashboard") or {}).get("panels") or [])
    panel = next((p for p in panels if str(p.get("id")) == panel_id), None)
    if panel is None:
        return {"ok": False, "error": f"dashboard {uid} has no panel {panel_id}",
                "panels": [{"id": p.get("id"), "title": p.get("title")} for p in panels]}

    # A hidden target is one Grafana does not draw; running it would answer a
    # different question from the one the panel on screen answers.
    targets = [t for t in (panel.get("targets") or []) if isinstance(t, dict) and not t.get("hide")]
    if not targets:
        return {"ok": False,
                "error": f"panel {panel_id} ({panel.get('title')!r}) has no visible query"}

    queries: list[dict[str, Any]] = []
    for index, target in enumerate(targets):
        query = {k: v for k, v in target.items() if k != "datasource"}
        query["refId"] = target.get("refId") or chr(ord("A") + index)
        query["datasource"] = {"uid": resolve_datasource(target.get("datasource")
                                                         or panel.get("datasource"))}
        queries.append(query)

    rows = _grafana.frames_to_rows(_grafana.ds_query(queries, time_from=time_from, time_to=time_to))
    out: dict[str, Any] = {
        "ok": True, "uid": uid, "panel": {"id": panel.get("id"), "title": panel.get("title"),
                                          "type": panel.get("type")},
        "from": time_from, "to": time_to,
        "queries": [query_text(t) for t in targets],
        "rows": rows[:MAX_ROWS], "count": min(len(rows), MAX_ROWS),
    }
    if len(rows) > MAX_ROWS:
        out["truncated"] = (f"the datasource returned {len(rows)} rows; the first {MAX_ROWS} "
                            "are here — narrow the window with `from`/`to`")
    return out


# --------------------------------------------------------------------------- #
# Handlers and the slash command
# --------------------------------------------------------------------------- #
handle_search_dashboards = _grafana.safe_json(search_dashboards)
handle_get_dashboard = _grafana.safe_json(lambda p: get_dashboard(str(p.get("uid") or "").strip()))
handle_panel_query = _grafana.safe_json(panel_query)


def handle_command(raw_args: str) -> str:
    """``/dashboards [text]`` — the dashboard titles, optionally filtered by title.

    A slash command answers a person, so this is lines of text rather than the
    JSON the tools return; the uid rides along because acting on one of these
    titles means passing it to ``grafana_get_dashboard``.
    """
    result = _grafana.safe_result(search_dashboards, {"query": (raw_args or "").strip()})
    if not result.get("ok"):
        return f"dashboards: {result.get('error')}"
    rows = result["dashboards"]
    if not rows:
        return "No Grafana dashboards matched."
    lines = [f"{r['title']} ({r['folder']}) — uid {r['uid']}" for r in rows]
    return "\n".join([f"{len(rows)} dashboard(s):", *lines])
