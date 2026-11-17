"""n8n webhook + REST client.

* Webhooks: ``{N8N_URL}/webhook/<path>`` (production) or ``/webhook-test/<path>``;
  optional ``N8N_WEBHOOK_AUTH`` (``user:pass`` for basic auth) or ``N8N_WEBHOOK_HEADER``
  (``Name: value``) when the webhook node is protected.
* REST: ``{N8N_URL}/api/v1/...`` with ``X-N8N-API-KEY: {N8N_API_KEY}`` — reading
  workflows and executions, and writing workflows (create / update / activate).

Every ``N8N_*`` value is read through the one settings schema (``aura_schema.from_env()``).

What the write half does *not* do: it does not validate a node graph. n8n itself
is the only thing that knows whether a node type exists, whether its parameters
are complete or whether the connection map names nodes that are present, so a
bad definition comes back as n8n's own 400 message, passed through verbatim
(:func:`_rest_error`) rather than second-guessed here. Deleting a workflow and
storing or deleting a credential exist since 2026-09-10 (the owner: "fully
capable of running, creating and managing n8n stuff"), each behind the gate
with a message that says what goes.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
from aura_schema import from_env

from _shared import _egress, _failure

from . import schemas

#: The body fields ``POST /api/v1/workflows`` and ``PUT /api/v1/workflows/{id}``
#: accept. Recent n8n validates the body strictly and answers 400 "request/body
#: must NOT have additional properties" for ``active``, ``id``, ``tags``,
#: ``createdAt`` and ``updatedAt`` — exactly the keys a caller gets back from a
#: GET and naturally hands straight back. Anything outside this tuple is dropped
#: before the request instead.
WRITABLE_FIELDS: tuple[str, ...] = ("name", "nodes", "connections", "settings")

#: ``settings`` n8n's public API requires and a created workflow gets when the
#: caller supplies none. ``executionOrder: v1`` matters: a workflow saved
#: without it runs its branches in the legacy v0 order, which is a different
#: workflow from the one the n8n editor would have produced for the same nodes.
DEFAULT_SETTINGS: dict[str, Any] = {"executionOrder": "v1"}

#: How many characters of a workflow definition ``n8n_get_workflow`` will
#: return. A real workflow (a Code node holds a whole script) runs to tens of
#: thousands of characters and would swamp the turn it was asked in; past this
#: the definition is trimmed and the result says so.
MAX_WORKFLOW_CHARS: int = 12000

#: ``https://user:pw@host/`` -> the ``user:pw@`` part, so it can be cut out.
_USERINFO_RE = re.compile(r"(?<=://)[^/@\s]+@")

#: Shortest secret :func:`_redact` will substitute by value. A one- or
#: two-character setting occurs by accident all over ordinary text, and masking
#: it rewrites the payload — an ``N8N_API_KEY`` of ``k`` turned every "k" in a
#: workflow's reply into "***" — while a credential that short is not made safe
#: by masking it anyway. The userinfo strip has no such limit: it matches a
#: position in a URL, not a value.
MIN_REDACTABLE: int = 8


def _redact(text: str) -> str:
    """Strip credentials out of a string on its way to a caller or a log.

    httpx puts the request URL in the message of every error it raises, and that
    URL carries the userinfo when ``N8N_URL`` was written as
    ``https://user:pw@n8n.lan`` — an unredacted exception message is a password
    in a tool result. The API key and the webhook credentials are cut too: they
    travel in headers, not in URLs, but a proxy that quotes the request back in
    an error body would otherwise put them in front of the model.
    """
    out = _USERINFO_RE.sub("", text or "")
    try:
        cfg = from_env()
        secrets = [cfg.n8n_api_key, cfg.n8n_webhook_auth, cfg.n8n_webhook_header]
    except Exception:  # an unreadable environment must not break an error path
        secrets = []
    for secret in secrets:
        if secret and len(secret) >= MIN_REDACTABLE and secret in out:
            out = out.replace(secret, "***")
    return out


def _redact_json(value: Any) -> Any:
    """:func:`_redact` over a decoded JSON body.

    A webhook's answer is a *foreign* server's, and n8n behind a proxy has been
    known to quote the request back — including the header it was given — so the
    body is redacted through its serialisation and read back. Deliberately not
    applied to a workflow definition: that comes back to be edited and PUT
    again, and replacing a value with "***" there would write the mask into the
    workflow.
    """
    text = json.dumps(value)
    clean = _redact(text)
    return value if clean == text else json.loads(clean)


def base_url() -> str:
    return from_env().n8n_url.rstrip("/")


def _client(timeout: float = 30.0) -> httpx.Client:
    cfg = from_env()
    headers = {}
    if cfg.n8n_api_key:
        headers["X-N8N-API-KEY"] = cfg.n8n_api_key
    return httpx.Client(base_url=_egress.service_url(cfg.n8n_url, "N8N_URL"), headers=headers,
                        timeout=timeout, verify=cfg.n8n_verify_ssl)


def webhook_url(webhook: str, test: bool = False) -> str:
    webhook = webhook.strip()
    base = _egress.service_url(from_env().n8n_url, "N8N_URL")
    # Any "scheme://" is an absolute address the model chose. The webhook header and basic auth
    # ride along with it, so it is used only when it names the configured server (T-1350).
    if re.match(r"[a-z][a-z0-9+.-]*://", webhook, re.I):
        if not _same_origin(webhook, base):
            raise ValueError("a webhook address must be a path on N8N_URL or an address on that same "
                             "server (same scheme, host and port); nothing was sent")
        return webhook
    prefix = "webhook-test" if test else "webhook"
    return f"{base}/{prefix}/{webhook.lstrip('/')}"


def _same_origin(candidate: str, configured: str) -> bool:
    """True when ``candidate`` is on ``configured``'s scheme, host and port, as httpx will read it.

    Parsed with httpx itself, since its reading is the one the request uses. A login, a space,
    control or backslash character is refused outright: the parsers disagree about where the host
    ends there. Does not cover a redirect (the client follows none) or a name that resolves elsewhere.
    """
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in candidate):
        return False
    try:
        got, want = httpx.URL(candidate), httpx.URL(configured)
    except httpx.InvalidURL:
        return False
    if got.userinfo:
        return False

    def origin(u: httpx.URL) -> tuple[str, str, int | None]:
        scheme = u.scheme.lower()
        return scheme, u.host.lower().rstrip("."), u.port or {"http": 80, "https": 443}.get(scheme)
    return origin(got) == origin(want)


def _webhook_kwargs() -> dict[str, Any]:
    kw: dict[str, Any] = {}
    cfg = from_env()
    auth = cfg.n8n_webhook_auth
    if auth and ":" in auth:
        user, pw = auth.split(":", 1)
        kw["auth"] = (user, pw)
    header = cfg.n8n_webhook_header
    if header and ":" in header:
        name, value = header.split(":", 1)
        kw["headers"] = {name.strip(): value.strip()}
    return kw


def trigger(webhook: str, payload: dict | None = None, method: str = "POST", test: bool = False) -> dict[str, Any]:
    url = webhook_url(webhook, test)
    with httpx.Client(timeout=120, verify=from_env().n8n_verify_ssl) as c:
        if method.upper() == "GET":
            r = c.get(url, params=payload or {}, **_webhook_kwargs())
        else:
            r = c.post(url, json=payload or {}, **_webhook_kwargs())
    # A workflow's own answer is returned by design, for a 2xx only: any other status is the
    # shared failure shape, with none of the server's text and not the address (T-1348).
    if not 200 <= r.status_code < 300:
        return _failure.status_answer(r.status_code, r.headers.get("retry-after"))
    try:
        body: Any = _redact_json(r.json())
    except ValueError:
        body = _redact(r.text[:1000])
    # The URL is ours and carries the userinfo when N8N_URL was written with it.
    return {"ok": True, "http": r.status_code, "url": _redact(url), "response": body}


def list_workflows(active_only: bool = False) -> dict[str, Any]:
    with _client() as c:
        r = c.get("/api/v1/workflows", params={"active": "true"} if active_only else {}).raise_for_status().json()
    out = []
    for w in r.get("data", []):
        hooks = []
        for node in w.get("nodes", []) or []:
            if node.get("type") == "n8n-nodes-base.webhook":
                hooks.append((node.get("parameters") or {}).get("path"))
        out.append({"id": w.get("id"), "name": w.get("name"), "active": w.get("active"), "webhooks": hooks})
    return {"ok": True, "workflows": out, "count": len(out)}


def executions(workflow_id: str | None = None, limit: int = 10) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": int(limit)}
    if workflow_id:
        params["workflowId"] = workflow_id
    with _client() as c:
        r = c.get("/api/v1/executions", params=params).raise_for_status().json()
    return {"ok": True, "executions": [
        {"id": e.get("id"), "workflowId": e.get("workflowId"), "status": e.get("status"),
         "startedAt": e.get("startedAt"), "stoppedAt": e.get("stoppedAt"), "mode": e.get("mode")}
        for e in r.get("data", [])]}


def health() -> dict[str, Any]:
    with _client(timeout=10) as c:
        r = c.get("/healthz")
        if r.status_code != 200:
            return _failure.status_answer(r.status_code, r.headers.get("retry-after"))
        out: dict[str, Any] = {"ok": r.status_code == 200, "http": r.status_code,
                               "url": _redact(base_url())}
        try:
            out["healthz"] = _redact_json(r.json())
        except ValueError:
            out["healthz"] = _redact(r.text[:100])
        if from_env().n8n_api_key:
            w = c.get("/api/v1/workflows", params={"limit": 1})
            out["api_key_ok"] = w.status_code == 200
    return out


# ------------------------------------------------------------- workflow writes
def _dict(value: Any) -> dict[str, Any]:
    """The value when it is a mapping, else an empty one: n8n's execution
    payload nests optional objects four deep and every level may be null."""
    return value if isinstance(value, dict) else {}


def _rest_error(r: httpx.Response) -> dict[str, Any] | None:
    """n8n's own complaint about a rejected request, or None when the call worked.

    A 400 out of the public API is almost always the schema validator naming the
    offending property ("request/body must NOT have additional properties",
    "must have required property 'settings'"), and that sentence is the only
    thing that tells the model what to send instead. It is therefore passed
    through verbatim in ``error`` rather than folded into a generic "HTTP 400".
    Other statuses carry no such guidance and go to ``raise_for_status()``, which
    :func:`_safe` turns into the shared status-and-hint answer (``_shared/_failure.py``).
    The 400 text is the one remote sentence this plugin repeats, on purpose.
    """
    if r.status_code != 400:
        r.raise_for_status()
        return None
    try:
        body: Any = r.json()
    except ValueError:
        body = None
    message = body.get("message") if isinstance(body, dict) else None
    return {"ok": False, "http": 400, "error": str(message) if message else r.text[:300]}


def _writable(params: dict, base: dict | None = None) -> dict[str, Any]:
    """The request body n8n accepts: ``base`` overlaid with whatever ``params`` sets.

    ``base`` is the workflow as it stands, so a caller updating only the name
    does not blank the nodes — n8n's PUT replaces the whole workflow, and an
    omitted ``nodes`` key means an empty workflow, not an unchanged one.
    """
    body = {k: v for k, v in (base or {}).items() if k in WRITABLE_FIELDS}
    for key in WRITABLE_FIELDS:
        if params.get(key) is not None:
            body[key] = params[key]
    return body


def _trim_workflow(head: dict[str, Any], nodes: list[dict[str, Any]], connections: dict[str, Any],
                   max_chars: int) -> dict[str, Any]:
    """``head`` + as much of the definition as fits in ``max_chars``.

    Three levels, each naming the cap when it bites, because a caller that is
    handed a silently shortened workflow will edit it and PUT the shortage back.
    A node's ``parameters`` is nearly all of its bytes, so dropping those first
    keeps what a definition is usually read for: which nodes exist and how they
    are wired.
    """
    full = {**head, "nodes": nodes, "connections": connections}
    if len(json.dumps(full)) <= max_chars:
        return full

    slim = [{k: v for k, v in n.items() if k != "parameters"} for n in nodes]
    trimmed = {**head, "nodes": slim, "connections": connections, "node_count": len(nodes),
               "truncated": (f"the definition exceeded the {max_chars}-character cap, so every "
                             "node's `parameters` was dropped; node names, types and the "
                             "connections are intact")}
    if len(json.dumps(trimmed)) <= max_chars:
        return trimmed

    # Still over: hundreds of nodes, or a connection map bigger than the cap.
    # Keep the identity of as many nodes as fit and say how many were left out —
    # an honest short answer beats a JSON blob cut mid-object.
    ids = [{"name": n.get("name"), "type": n.get("type")} for n in nodes]
    note = (f"the definition exceeded the {max_chars}-character cap even without node parameters; "
            "the connections were dropped and only some nodes are listed")
    keep = len(ids)
    while keep and len(json.dumps({**head, "nodes": ids[:keep], "node_count": len(nodes),
                                   "truncated": note})) > max_chars:
        keep -= 1
    return {**head, "nodes": ids[:keep], "node_count": len(nodes), "truncated": note}


def get_workflow(workflow_id: str, max_chars: int = MAX_WORKFLOW_CHARS) -> dict[str, Any]:
    with _client() as c:
        r = c.get(f"/api/v1/workflows/{workflow_id}")
        err = _rest_error(r)
        if err:
            return err
        w = r.json()
    head = {"ok": True, "id": w.get("id"), "name": w.get("name"), "active": bool(w.get("active")),
            "createdAt": w.get("createdAt"), "updatedAt": w.get("updatedAt"),
            "settings": w.get("settings") or {},
            "tags": [t.get("name") for t in (w.get("tags") or []) if isinstance(t, dict)]}
    nodes = [n for n in (w.get("nodes") or []) if isinstance(n, dict)]
    connections = w.get("connections") if isinstance(w.get("connections"), dict) else {}
    return _trim_workflow(head, nodes, connections, max_chars)


MAX_DEFINITION_CHARS: int = 200_000


def _check_graph(nodes: Any, connections: Any) -> str | None:
    """Why a model-written graph cannot go to n8n, or None. Shape and size only: n8n still
    judges node types and parameters, and its message comes back verbatim."""
    if len(json.dumps([nodes, connections], default=str)) > MAX_DEFINITION_CHARS:
        return f"the definition is too large (cap {MAX_DEFINITION_CHARS} characters)"
    if not all(isinstance(n, dict) for n in nodes):
        return "every node must be an object"
    names = {n["name"] for n in nodes if n.get("name")}
    if not names:
        return None
    for source, outputs in connections.items():
        if source not in names:
            return f"connections names a node that does not exist: {source!r}"
        for branches in (outputs.get("main") or []) if isinstance(outputs, dict) else []:
            for link in branches or []:
                target = link.get("node") if isinstance(link, dict) else None
                if target not in names:
                    return f"connection from {source!r} goes to a node that does not exist: {target!r}"
    return None


def create_workflow(params: dict) -> dict[str, Any]:
    name = str(params.get("name") or "").strip()
    nodes = params.get("nodes")
    connections = params.get("connections")
    if not name:
        return {"ok": False, "error": "name is required"}
    if not isinstance(nodes, list) or not nodes:
        return {"ok": False, "error": "nodes must be a non-empty array of n8n node objects"}
    if not isinstance(connections, dict):
        return {"ok": False,
                "error": "connections must be an object ({} for a single-node workflow)"}
    problem = _check_graph(nodes, connections)
    if problem:
        return {"ok": False, "error": problem}
    settings = params.get("settings")
    body = {"name": name, "nodes": nodes, "connections": connections,
            "settings": settings if isinstance(settings, dict) else dict(DEFAULT_SETTINGS)}
    with _client() as c:
        r = c.post("/api/v1/workflows", json=body)
        err = _rest_error(r)
        if err:
            return err
        w = r.json()
    return {"ok": True, "id": w.get("id"), "name": w.get("name"), "active": bool(w.get("active")),
            "note": "created inactive — n8n starts firing its trigger only after "
                    "n8n_activate_workflow"}


def update_workflow(params: dict) -> dict[str, Any]:
    workflow_id = str(params.get("workflow_id") or "").strip()
    name = str(params.get("name") or "").strip()
    if not workflow_id:
        return {"ok": False, "error": "workflow_id is required"}
    if not name:
        return {"ok": False, "error": ("name is required — n8n's PUT replaces the whole "
                                       "workflow; pass the current name unless you are "
                                       "renaming it")}
    if isinstance(params.get("nodes"), list) and isinstance(params.get("connections"), dict):
        problem = _check_graph(params["nodes"], params["connections"])
        if problem:
            return {"ok": False, "error": problem}
    with _client() as c:
        r = c.get(f"/api/v1/workflows/{workflow_id}")
        err = _rest_error(r)
        if err:
            return err
        current = r.json()
        body = _writable({**params, "name": name}, current)
        # n8n rejects a body without `settings`; a workflow created before the
        # key existed comes back from the GET without one.
        body.setdefault("settings", dict(DEFAULT_SETTINGS))
        r = c.put(f"/api/v1/workflows/{workflow_id}", json=body)
        err = _rest_error(r)
        if err:
            return err
        w = r.json()
    replaced = [k for k in WRITABLE_FIELDS if params.get(k) is not None]
    return {"ok": True, "id": w.get("id"), "name": w.get("name"), "active": bool(w.get("active")),
            "replaced": replaced,
            "kept": [k for k in WRITABLE_FIELDS if k not in replaced and k in body]}


def set_active(workflow_id: str, active: bool) -> dict[str, Any]:
    if not workflow_id:
        return {"ok": False, "error": "workflow_id is required"}
    verb = "activate" if active else "deactivate"
    with _client() as c:
        r = c.post(f"/api/v1/workflows/{workflow_id}/{verb}")
        err = _rest_error(r)
        if err:
            return err
        w = r.json()
    return {"ok": True, "id": w.get("id"), "name": w.get("name"), "active": bool(w.get("active"))}


# ------------------------------------------------------------- approval
def build_approval(tool_name: str, args: dict) -> dict[str, Any] | None:
    """The ``pre_tool_call`` directive for a gated tool, or None to let it run.

    What this does *not* cover: what the workflow itself does. n8n runs whatever
    nodes it holds, so approving a trigger approves every side effect downstream
    of it, and approving an activation approves every future unattended run.
    The message can only name the workflow, not its blast radius.
    """
    if tool_name not in schemas.DESTRUCTIVE:
        return None
    if tool_name == "n8n_trigger":
        hook = str(args.get("webhook") or "?")
        return {"action": "approve", "message": f"n8n workflow '{hook}' will run with side effects",
                "rule_key": f"n8n:{hook}"}
    if tool_name == "n8n_create_workflow":
        name = str(args.get("name") or "?")
        nodes = args.get("nodes")
        count = len(nodes) if isinstance(nodes, list) else 0
        return {"action": "approve",
                "message": f"a new n8n workflow '{name}' ({count} nodes) will be created, inactive",
                "rule_key": "n8n:create_workflow"}
    if tool_name == "n8n_update_workflow":
        workflow_id = str(args.get("workflow_id") or "?")
        name = str(args.get("name") or "?")
        fields = ", ".join(k for k in WRITABLE_FIELDS if args.get(k) is not None) or "nothing"
        # "update workflow 7" tells the owner nothing — the name is what they
        # recognise, and n8n's PUT overwrites the definition wholesale.
        return {"action": "approve",
                "message": (f"n8n workflow {workflow_id} ('{name}') will be overwritten, "
                            f"replacing {fields}"),
                "rule_key": f"n8n:update_workflow:{workflow_id}"}
    if tool_name == "n8n_activate_workflow":
        workflow_id = str(args.get("workflow_id") or "?")
        active = bool(args.get("active"))
        what = ("activated — it starts firing on its own trigger (schedule, webhook, poll)"
                if active else "deactivated — it stops firing")
        # Separate rule keys per direction: remembering "yes, deactivate this"
        # must not silently pre-approve starting it up again later.
        return {"action": "approve", "message": f"n8n workflow {workflow_id} will be {what}",
                "rule_key": f"n8n:{'activate' if active else 'deactivate'}_workflow:{workflow_id}"}
    if tool_name == "n8n_delete_workflow":
        workflow_id = str(args.get("workflow_id") or "?")
        return {"action": "approve",
                "message": f"n8n workflow {workflow_id} will be DELETED with its execution history; this cannot be undone",
                "rule_key": f"n8n:delete_workflow:{workflow_id}"}
    if tool_name == "n8n_credential_create":
        return {"action": "approve",
                "message": f"a credential '{args.get('name') or '?'}' of type {args.get('type') or '?'} will be stored in n8n",
                "rule_key": "n8n:credential_create"}
    if tool_name == "n8n_credential_delete":
        return {"action": "approve",
                "message": f"n8n credential {args.get('credential_id') or '?'} will be deleted; workflows using it stop working",
                "rule_key": f"n8n:credential_delete:{args.get('credential_id') or '?'}"}
    # A name added to DESTRUCTIVE with no branch above still stops at the gate:
    # a missing message must not be a missing approval.
    return {"action": "approve", "message": f"{tool_name} changes n8n state",
            "rule_key": f"n8n:{tool_name}"}


def _safe(fn):
    def handler(params: dict, **kwargs):
        del kwargs
        try:
            return json.dumps(fn(params or {}))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            # A server failure is the shared status-and-hint answer (T-1348): never the body
            # or the request address.
            return json.dumps(_failure.of(exc)
                              or {"ok": False, "error": _redact(f"{exc.__class__.__name__}: {exc}")})
    return handler


def _trigger(p: dict) -> dict[str, Any]:
    hook = str(p.get("webhook") or "").strip()
    if not hook:
        return {"ok": False, "error": "webhook is required"}
    payload = p.get("payload") if isinstance(p.get("payload"), dict) else None
    return trigger(hook, payload, str(p.get("method") or "POST"), bool(p.get("test")))


handle_n8n_trigger = _safe(_trigger)
handle_n8n_list = _safe(lambda p: list_workflows(bool(p.get("active_only"))))
handle_n8n_executions = _safe(lambda p: executions(p.get("workflow_id"), int(p.get("limit") or 10)))
handle_n8n_health = _safe(lambda p: health())
def _get_workflow(p: dict) -> dict[str, Any]:
    workflow_id = str(p.get("workflow_id") or "").strip()
    if not workflow_id:
        return {"ok": False, "error": "workflow_id is required"}
    return get_workflow(workflow_id)


handle_n8n_get_workflow = _safe(_get_workflow)
handle_n8n_create_workflow = _safe(create_workflow)
handle_n8n_update_workflow = _safe(update_workflow)
handle_n8n_activate_workflow = _safe(
    lambda p: set_active(str(p.get("workflow_id") or "").strip(), bool(p.get("active"))))


#: How much of an execution's per-node output ``n8n_execution`` returns.
MAX_EXECUTION_CHARS: int = 6000


def execution(execution_id: str) -> dict[str, Any]:
    """One execution with its data: status, the failing node and n8n's error.

    The public API returns the whole run data with ``includeData=true`` — every
    item every node produced — which for a real workflow is far more than a turn
    can carry. Kept: the status, the error (message and the node it names), and
    each node's *last* output item, cut at :data:`MAX_EXECUTION_CHARS`.
    """
    if not execution_id:
        return {"ok": False, "error": "execution_id is required"}
    with _client() as c:
        r = c.get(f"/api/v1/executions/{execution_id}", params={"includeData": "true"})
        err = _rest_error(r)
        if err:
            return err
        e = r.json()
    # Bound before the isinstance so mypy narrows what the loop reads (a
    # second .get() is a fresh Any | None to it).
    data: dict[str, Any] = _dict(e.get("data"))
    result: dict[str, Any] = _dict(data.get("resultData"))
    error: dict[str, Any] | None = result.get("error") if isinstance(result.get("error"), dict) else None
    nodes: dict[str, Any] = {}
    run_data: dict[str, Any] = _dict(result.get("runData"))
    for node, runs in run_data.items():
        last = runs[-1] if isinstance(runs, list) and runs else {}
        items = (((last.get("data") or {}).get("main") or [[]])[0]) if isinstance(last, dict) else []
        nodes[node] = {"items": len(items) if isinstance(items, list) else 0,
                       "last": (items[-1].get("json") if isinstance(items, list) and items and isinstance(items[-1], dict) else None),
                       "error": (last.get("error") or {}).get("message") if isinstance(last, dict) and last.get("error") else None}
    out: dict[str, Any] = {"ok": True, "id": e.get("id"), "workflowId": e.get("workflowId"),
                           "status": e.get("status"), "mode": e.get("mode"),
                           "startedAt": e.get("startedAt"), "stoppedAt": e.get("stoppedAt"),
                           "lastNodeExecuted": result.get("lastNodeExecuted"),
                           "error": ({"message": error.get("message"), "node": (error.get("node") or {}).get("name")
                                      if isinstance(error.get("node"), dict) else error.get("node")}
                                     if error else None),
                           "nodes": nodes}
    text = json.dumps(_redact_json(out))
    if len(text) > MAX_EXECUTION_CHARS:
        # Drop the per-node outputs first, keeping the shape and the error.
        for node in nodes.values():
            node["last"] = "(trimmed)"
        out["truncated"] = True
    return out


def delete_workflow(workflow_id: str) -> dict[str, Any]:
    if not workflow_id:
        return {"ok": False, "error": "workflow_id is required"}
    with _client() as c:
        r = c.delete(f"/api/v1/workflows/{workflow_id}")
        err = _rest_error(r)
        if err:
            return err
        w = r.json() if r.content else {}
    return {"ok": True, "id": w.get("id", workflow_id), "name": w.get("name"), "deleted": True}


def credential_schema(type_name: str) -> dict[str, Any]:
    if not type_name:
        return {"ok": False, "error": "type is required"}
    with _client() as c:
        r = c.get(f"/api/v1/credentials/schema/{type_name}")
        err = _rest_error(r)
        if err:
            return err
        schema = r.json()
    props = schema.get("properties") if isinstance(schema, dict) else None
    return {"ok": True, "type": type_name,
            "fields": sorted(props) if isinstance(props, dict) else [],
            "required": schema.get("required", []) if isinstance(schema, dict) else []}


def credential_create(params: dict) -> dict[str, Any]:
    name = str(params.get("name") or "").strip()
    type_name = str(params.get("type") or "").strip()
    data = params.get("data")
    if not name or not type_name or not isinstance(data, dict):
        return {"ok": False, "error": "name, type and data (an object) are required"}
    with _client() as c:
        r = c.post("/api/v1/credentials", json={"name": name, "type": type_name, "data": data})
        err = _rest_error(r)
        if err:
            return err
        cred = r.json()
    # Never the data back: the point of storing it in n8n is that it is not in a transcript.
    return {"ok": True, "id": cred.get("id"), "name": cred.get("name"), "type": cred.get("type")}


def credential_delete(credential_id: str) -> dict[str, Any]:
    if not credential_id:
        return {"ok": False, "error": "credential_id is required"}
    with _client() as c:
        r = c.delete(f"/api/v1/credentials/{credential_id}")
        err = _rest_error(r)
        if err:
            return err
    return {"ok": True, "id": credential_id, "deleted": True}


handle_n8n_execution = _safe(lambda p: execution(str(p.get("execution_id") or "").strip()))
handle_n8n_delete_workflow = _safe(lambda p: delete_workflow(str(p.get("workflow_id") or "").strip()))
handle_n8n_credential_schema = _safe(lambda p: credential_schema(str(p.get("type") or "").strip()))
handle_n8n_credential_create = _safe(credential_create)
handle_n8n_credential_delete = _safe(lambda p: credential_delete(str(p.get("credential_id") or "").strip()))


def handle_command(raw_args: str) -> str:
    """``/n8n`` — health with no argument, the workflow list with any argument.

    Kept as the plugin's own function rather than a lambda in ``register`` so
    ``guarded_command`` has something to wrap and a test something to call.
    """
    return handle_n8n_health({}) if not (raw_args or "").strip() else handle_n8n_list({})
