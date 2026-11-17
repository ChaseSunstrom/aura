"""Model-facing schemas for grafana-dashboards (every tool is read-only)."""

GRAFANA_SEARCH_DASHBOARDS = {
    "name": "grafana_search_dashboards",
    "description": ("Find Grafana dashboards by title text, tag or folder, and report uid, "
                    "title, folder and tags for each. The uid is what grafana_get_dashboard "
                    "takes. Read-only."),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Title text to match (omit for all dashboards)"},
            "tag": {"type": "string", "description": "Only dashboards carrying this tag"},
            "folder": {"type": "string",
                       "description": "Only dashboards whose folder name contains this "
                                      "(matched on the results)"},
            "limit": {"type": "integer", "description": "Default 50, max 500"},
        },
        "required": [],
    },
}

GRAFANA_GET_DASHBOARD = {
    "name": "grafana_get_dashboard",
    "description": ("One dashboard by uid: title, folder, tags and its panels — each panel's "
                    "id, title, type, datasource and the query it stores. Rows are resolved, "
                    "so a collapsed row's panels are listed too. A board too large for a turn "
                    "comes back trimmed, and the result says what was dropped and at which "
                    "character cap. Read-only."),
    "parameters": {
        "type": "object",
        "properties": {
            "uid": {"type": "string",
                    "description": "Dashboard uid from grafana_search_dashboards"},
        },
        "required": ["uid"],
    },
}

GRAFANA_PANEL_QUERY = {
    "name": "grafana_panel_query",
    "description": ("Run one panel's own stored query through Grafana's datasource proxy and "
                    "return the rows — what the graph on the board would be showing for the "
                    "window you ask for. Take uid and panel_id from grafana_get_dashboard. "
                    "`from`/`to` are Grafana time expressions (now-1h, now-7d, now). Dashboard "
                    "template variables are NOT substituted: a panel whose query contains "
                    "$variable comes back as the datasource's error, not a guess. Read-only."),
    "parameters": {
        "type": "object",
        "properties": {
            "uid": {"type": "string", "description": "Dashboard uid"},
            "panel_id": {"type": "integer",
                         "description": "Panel id as grafana_get_dashboard reports it"},
            "from": {"type": "string",
                     "description": "Window start, Grafana syntax (default now-1h)"},
            "to": {"type": "string", "description": "Window end, Grafana syntax (default now)"},
        },
        "required": ["uid", "panel_id"],
    },
}
