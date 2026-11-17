"""The one Grafana HTTP layer under gpu-telemetry and grafana-dashboards.

Both plugins talk to the same Grafana addressed by the ``grafana_*`` settings:
``grafana_url`` is the base, ``grafana_token`` the service-account token sent as
``Authorization: Bearer``, ``grafana_verify_ssl`` whether TLS is checked, and
``grafana_influx_datasource`` names (by uid or by display name) the InfluxDB
datasource the telemetry queries go through.

Why one module rather than a client per plugin:

* One credential path. The token is a Grafana admin-shaped secret; a token or a
  ``https://user:pw@grafana.lan`` userinfo reaching a tool result or a log line
  is the failure this file exists to make impossible, so every error string
  leaves through :func:`redact` and :func:`safe_result`.
* One idea of what Grafana's answer looks like. :func:`frames_to_rows` flattens
  the data-frame shape ``POST /api/ds/query`` returns, and that shaping has
  already been wrong once in the field (see the Grafana-13 comment inside it);
  a second copy would be a second chance to get it wrong.
* One datasource resolution, so "the InfluxDB datasource" means the same thing
  to the GPU tools and to a dashboard panel.

What this module does *not* do: it is not a Grafana API client. It knows
``/api/health``, ``/api/datasources``, ``/api/search``, ``/api/dashboards`` and
``/api/ds/query`` — nothing about alerting, provisioning, users or folders — and
it never writes: no caller here can create, update or delete anything in
Grafana. It also does not interpolate Grafana's template variables (``$server``,
``$__interval``): a query taken out of a dashboard panel is sent as it was
stored, so a panel that depends on a dashboard variable comes back with that
variable unresolved rather than silently guessed.

``_shared/`` is a sibling of every plugin directory and carries no
``plugin.yaml``, so Hermes never loads it as a plugin; each plugin's
``__init__`` puts the plugins root on ``sys.path`` and imports this as
``_shared._grafana`` (the loader gives a plugin no parent package to reach a
sibling through). See ``_approval_hook.py`` for the same register.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

import httpx
from aura_schema import from_env

from . import _egress, _failure

_LOGGER = logging.getLogger(__name__)

#: Seconds a Grafana call may take. A datasource-proxy query over a wide range
#: on a Raspberry-Pi-class InfluxDB is slow; past this it is a hung backend, and
#: a tool call that never returns wedges the whole Hermes turn.
DEFAULT_TIMEOUT: float = 30.0

#: ``https://user:pw@host/`` -> the ``user:pw@`` part, so it can be cut out.
_USERINFO_RE = re.compile(r"(?<=://)[^/@\s]+@")

#: A zero-argument factory returning an open ``httpx.Client``. Every function
#: here takes one so a caller (and a test) can supply its own transport without
#: this module reading a global; :func:`client` is the default.
ClientFactory = Callable[[], httpx.Client]


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def available() -> bool:
    """Whether Grafana is configured at all — the ``check_fn`` of every consumer."""
    return bool(from_env().grafana_url)


def base_url() -> str:
    return from_env().grafana_url.rstrip("/")


def headers() -> dict[str, str]:
    """Auth headers, or an empty dict when no token is set.

    Empty is a meaningful answer, not a failure: Grafana answers ``/api/health``
    anonymously, so a tokenless install can still be probed. Callers test the
    truthiness of this to decide whether to attempt an authenticated call.
    """
    tok = from_env().grafana_token
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """An ``httpx.Client`` bound to ``grafana_url`` carrying the token."""
    cfg = from_env()
    return httpx.Client(base_url=_egress.service_url(cfg.grafana_url, "GRAFANA_URL"),
                        headers=headers(), timeout=timeout, verify=cfg.grafana_verify_ssl)


def _open(open_client: ClientFactory | None) -> httpx.Client:
    return (open_client or client)()


def not_configured() -> dict[str, Any]:
    """The result every tool returns when ``GRAFANA_URL`` is unset.

    A fresh dict each call: callers add keys to it.
    """
    return {"ok": False, "error": "Grafana is not configured — set GRAFANA_URL "
                                  "(and GRAFANA_TOKEN for anything past /api/health)."}


def redact(text: str) -> str:
    """Strip credentials out of a string on its way to a caller or a log.

    Two ways the secret reaches a message: a ``grafana_url`` written with
    userinfo (``https://me:pw@grafana.lan``), which httpx echoes in
    ``exc.request.url``, and a token that some proxy quotes back in an error
    body. Both are cut here, because this is the single funnel every Grafana
    error string goes through.
    """
    out = _USERINFO_RE.sub("", text or "")
    try:
        token = from_env().grafana_token
    except Exception:  # an unreadable environment must not break an error path
        token = ""
    if token and token in out:
        out = out.replace(token, "***")
    return out


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def get_json(path: str, params: dict[str, Any] | None = None, *,
             open_client: ClientFactory | None = None) -> Any:
    """GET ``path`` under ``grafana_url`` and return the decoded body.

    Raises ``httpx.HTTPStatusError`` on any 4xx/5xx — the caller decides how a
    failure reads, and :func:`safe_result` is where it becomes a tool result.
    """
    with _open(open_client) as conn:
        return conn.get(path, params=params or {}).raise_for_status().json()


def datasources(*, open_client: ClientFactory | None = None) -> list[dict[str, Any]]:
    """Every datasource Grafana will admit to. Needs a token."""
    out = get_json("/api/datasources", open_client=open_client)
    return out if isinstance(out, list) else []


def influx_datasource(*, open_client: ClientFactory | None = None) -> dict[str, Any]:
    """The InfluxDB datasource ``grafana_influx_datasource`` names, else the first one.

    Matching accepts either the uid or the display name because a Grafana admin
    reads the name off the UI and the uid out of a URL, and both get typed into
    ``GRAFANA_INFLUX_DS``.
    """
    want = from_env().grafana_influx_datasource
    sources = datasources(open_client=open_client)
    for ds in sources:
        if want and want in (ds.get("uid"), ds.get("name")):
            return ds
    for ds in sources:
        if "influx" in str(ds.get("type", "")).lower():
            return ds
    raise RuntimeError("no InfluxDB datasource in Grafana (set GRAFANA_INFLUX_DS)")


def datasource_by_name(name: str, *,
                       open_client: ClientFactory | None = None) -> dict[str, Any] | None:
    """Look a datasource up by uid or display name; None when nothing matches.

    Old dashboard JSON stores ``"datasource": "InfluxDB"`` (the display name)
    where current Grafana stores ``{"type": …, "uid": …}``, so a panel read out
    of a long-lived board can only be queried after this lookup.
    """
    for ds in datasources(open_client=open_client):
        if name in (ds.get("uid"), ds.get("name")):
            return ds
    return None


def default_datasource(*, open_client: ClientFactory | None = None) -> dict[str, Any] | None:
    """Grafana's default datasource — what a panel with no datasource inherits."""
    for ds in datasources(open_client=open_client):
        if ds.get("isDefault"):
            return ds
    return None


def ds_query(queries: list[dict[str, Any]], *, time_from: str = "now-24h", time_to: str = "now",
             open_client: ClientFactory | None = None) -> dict[str, Any]:
    """POST ``/api/ds/query`` — the datasource proxy — and return the raw payload.

    ``queries`` are Grafana query objects, each already carrying its ``refId``
    and ``datasource``; this adds nothing to them, because what a query object
    must contain is the datasource type's business (Flux wants ``query`` +
    ``rawQuery``, Prometheus wants ``expr``, SQL wants ``rawSql``) and guessing
    here would silently drop the field a type actually reads.
    """
    body = {"queries": queries, "from": time_from, "to": time_to}
    with _open(open_client) as conn:
        return conn.post("/api/ds/query", json=body).raise_for_status().json()


def frames_to_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten Grafana's data-frame response into row dicts (`_time`, `_value`, tags)."""
    rows: list[dict[str, Any]] = []
    for result in (payload.get("results") or {}).values():
        for frame in result.get("frames") or []:
            fields = (frame.get("schema") or {}).get("fields") or []
            values = (frame.get("data") or {}).get("values") or []
            labels: dict[str, Any] = {}
            for f in fields:
                labels.update(f.get("labels") or {})
            n = max((len(v) for v in values), default=0)
            for i in range(n):
                row = dict(labels)
                for f, col in zip(fields, values):
                    row[f.get("name", "?")] = col[i] if i < len(col) else None
                if "Time" in row and "_time" not in row:
                    row["_time"] = row.pop("Time")
                if "Value" in row and "_value" not in row:
                    row["_value"] = row.pop("Value")
                if "_field" not in row and "_value" not in row:
                    # Grafana 13 names the value column after the InfluxDB field
                    # ("memory_used": 22996) with the field name nowhere else; without
                    # this the shaper saw ten rows and no GPU (rows_seen=10, count=0).
                    names = [f.get("name") for f in fields
                             if f.get("name") not in ("Time", "_time")]
                    if len(names) == 1 and names[0] in row:
                        row["_field"] = names[0]
                        row["_value"] = row.pop(names[0])
                rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #
def safe_result(fn: Callable[[dict], Any], params: dict | None = None) -> Any:
    """Run a Grafana call, turning a failure into an ``ok: false`` result.

    httpx puts the request URL in the message of every error it raises, and that
    URL carries the userinfo when ``GRAFANA_URL`` was written as
    ``https://user:pw@host`` — so an unredacted exception message is a password
    in a tool result. Every failure is funnelled through here, and here through
    :func:`redact`, rather than each plugin remembering to.

    Caught: httpx's errors, the ``RuntimeError`` a missing datasource raises,
    and the ValueError/TypeError a bad tool argument produces. Not caught,
    deliberately: ``KeyError``, ``AttributeError``, ``IndexError`` — those are
    bugs in the handler, not conditions of the server, and they should fail
    loudly in the plugin's own tests rather than reach a user as "Grafana
    failed".
    """
    try:
        return fn(params or {})
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:
        _LOGGER.warning("Grafana call failed: %s", exc.__class__.__name__)
        # The shared status-and-hint answer (T-1348): no body, no request address.
        return _failure.of(exc) or {"ok": False, "error": redact(f"{exc.__class__.__name__}: {exc}")}


def safe_json(fn: Callable[[dict], Any]) -> Callable[..., str]:
    """Wrap a tool body so it returns a JSON string and never raises.

    Hermes calls a handler inline, so an exception out of one ends the turn;
    :func:`safe_result` does the catching and the redaction, and this only puts
    a JSON string around it — the shape Hermes requires of a tool handler.
    """

    def handler(params: dict, **kwargs: Any) -> str:
        del kwargs
        return json.dumps(safe_result(fn, params or {}))

    return handler
