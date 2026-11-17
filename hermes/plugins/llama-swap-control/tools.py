"""llama-swap HTTP client and tool handlers.

llama-swap (built from v268 in jarvis-models) exposes, next to the OpenAI-compatible `/v1`:

* ``GET  /running``           — resident models with ``state`` (starting|ready|stopping)
* ``GET  /v1/models``         — every configured model, each with ``status.value`` (loaded|unloaded)
* ``GET  /unload``            — unload every model (verified: returns after the model stops)
* ``GET  /api/version``       — build info

Loading a model is done the way llama-swap intends: send a minimal
``/v1/chat/completions`` for it — llama-swap swaps on demand. That call can
take minutes on a cold start, so it runs with a long timeout and the tool
reports the elapsed time. ``LLAMA_SWAP_URL``/``LLAMA_SWAP_TIMEOUT`` are read
through the one settings schema (``aura_schema.from_env()``); no host is baked in.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from aura_schema import from_env

from _shared import _egress, _failure


def base_url() -> str:
    return from_env().llama_swap_url.rstrip("/")


def _client(timeout: float = 10.0) -> httpx.Client:
    cfg = from_env()
    key = cfg.model_api_key  # llama-swap's apiKeys; unset, it is open
    return httpx.Client(base_url=_egress.service_url(cfg.llama_swap_url, "LLAMA_SWAP_URL"), timeout=timeout,
                        headers={"Authorization": f"Bearer {key}"} if key else {})


#: The routing table's rows, as ``job -> settings attribute``. The chat/default row is
#: ``model.default`` (LLM_MODEL) and is added separately: it is the model every row falls
#: back to when its own setting is empty, which is the deployed position (D21).
ROUTING_JOBS: dict[str, str] = {
    "reasoning": "routing_reasoning_model",
    "coding": "routing_coding_model",
    "vision": "routing_vision_model",
    "auxiliary": "routing_auxiliary_model",
    "delegation": "routing_delegation_model",
    "embedding": "routing_embedding_model",
}


def routing_table() -> dict[str, str]:
    """``job -> model id`` from the one settings schema.

    A row whose setting is empty resolves to the default model, so the table JARVIS reads
    is always complete and never says "unset": the question the agent actually has is
    "which model does this work want", and on this deployment the honest answer for every
    row is the resident one. Only a row that names a *different* model is a swap, which is
    what makes ``needs_swap`` below worth showing.
    """
    cfg = from_env()
    default = cfg.model_default
    table = {"chat": default}
    for job, attr in ROUTING_JOBS.items():
        table[job] = getattr(cfg, attr, "") or default
    return table


def list_models() -> dict[str, Any]:
    with _client() as c:
        models = c.get("/v1/models").raise_for_status().json().get("data", [])
        running = c.get("/running").raise_for_status().json().get("running", [])
    state = {r.get("model"): r.get("state") for r in running if isinstance(r, dict)}
    resident = [r.get("model") for r in running]
    table = routing_table()
    jobs_by_model: dict[str, list[str]] = {}
    for job, model in table.items():
        jobs_by_model.setdefault(model, []).append(job)
    return {
        "models": [
            {
                "id": m.get("id"),
                "status": ((m.get("status") or {}).get("value")),
                "state": state.get(m.get("id"), "unloaded"),
                "routed_jobs": jobs_by_model.get(m.get("id"), []),
            }
            for m in models
        ],
        "resident": resident,
        "routing": table,
        # The jobs whose model is not the one loaded right now: doing one of these means a
        # cold start that evicts the resident model for everyone using jarvis-models.
        "needs_swap": sorted(job for job, model in table.items() if model not in resident),
    }


def running() -> dict[str, Any]:
    with _client() as c:
        data = c.get("/running").raise_for_status().json()
    entries = [
        {"model": r.get("model"), "state": r.get("state"), "cmd": (r.get("cmd") or "").strip()[:160]}
        for r in data.get("running", [])
    ]
    return {"running": entries, "resident_model": entries[0]["model"] if entries else None}


def version() -> dict[str, Any]:
    with _client() as c:
        return c.get("/api/version").raise_for_status().json()


def unload_all() -> dict[str, Any]:
    with _client(timeout=120) as c:
        r = c.get("/unload")
    return {"ok": r.status_code < 400, "http": r.status_code, "running": running()["running"]}


def load_model(model: str) -> dict[str, Any]:
    """Make ``model`` resident by sending it a one-token completion."""
    t0 = time.time()
    with _client(timeout=from_env().llama_swap_timeout) as c:
        r = c.post("/v1/chat/completions", json={
            "model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}],
        })
    elapsed = round(time.time() - t0, 1)
    if r.status_code >= 400:
        return {**_failure.status_answer(r.status_code, r.headers.get("retry-after")), "elapsed_s": elapsed}
    return {"ok": True, "model": model, "elapsed_s": elapsed, "running": running()["running"]}


# ----------------------------------------------------------------- handlers
def _wrap(fn):
    def handler(params: dict, **kwargs):
        del kwargs
        try:
            return json.dumps(fn(**(params or {})))
        except httpx.HTTPError as exc:
            # Status and a fixed hint (T-1348): neither the server's text nor its address.
            return json.dumps(_failure.of(exc))
        except _egress.ServiceUrlError as exc:
            return json.dumps({"ok": False, "error": str(exc)})
        except TypeError as exc:
            return json.dumps({"ok": False, "error": f"bad arguments: {exc}"})
    return handler


def handle_llama_swap_models(params: dict, **kwargs) -> str:
    return _wrap(lambda: list_models())(params, **kwargs)


def handle_llama_swap_status(params: dict, **kwargs) -> str:
    return _wrap(lambda: {**running(), "version": version()})(params, **kwargs)


def handle_llama_swap_load(params: dict, **kwargs) -> str:
    model = str((params or {}).get("model") or "").strip()
    if not model:
        return json.dumps({"ok": False, "error": "model is required"})
    return _wrap(lambda: load_model(model))({}, **kwargs)


def handle_llama_swap_unload(params: dict, **kwargs) -> str:
    return _wrap(lambda: unload_all())({}, **kwargs)


# ------------------------------------------------- the model manager (T-1312)
# The console's Models screen sends these to the adapter's /api/models, which adds the manager's
# own key; the manager never sees the apps' key and nothing in `jarvis` holds its own. So these
# tools go the same way, with the adapter's key, and each sits behind a matrix ASK row. The
# manager checks every body again; the checks below only keep a name that is no name (a path, a
# URL, a flag) off the route and out of the body, and are not a copy of its limits.
_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
_GGUF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*\.gguf")
_ENGINES = ("vllm", "llama.cpp")
SETTING_KEYS = frozenset({"max_model_len", "gpu_memory_utilization", "tensor_parallel_size", "kv_cache_dtype",
                          "quantization", "keep_loaded", "idle_unload_s"})


class _Refused(Exception):
    """A request the tool will not send; the message is what the model is told."""


def _adapter_client(timeout: float = 30.0) -> httpx.Client:
    settings = from_env()
    # The adapter's key rides every request, so its address is held to the same rule as the other
    # service addresses (T-1350); an unset one is the loopback default.
    base = _egress.service_url(settings.adapter_url or "http://127.0.0.1:8643", "AURA_ADAPTER_URL")
    return httpx.Client(base_url=base, timeout=timeout, headers={"Authorization": f"Bearer {settings.adapter_key}"})


def _need(params: Any, key: str, rx: re.Pattern[str], what: str) -> str:
    value = (params or {}).get(key)
    if not isinstance(value, str) or not rx.fullmatch(value):
        raise _Refused(f"{key} must be {what}, not {str(value)[:80]!r}.")
    return value


def _settings_arg(params: Any) -> dict[str, Any]:
    s = (params or {}).get("settings", {})
    if not isinstance(s, dict):
        raise _Refused("settings must be an object.")
    if unknown := sorted(set(s) - SETTING_KEYS):
        raise _Refused(f"Unknown setting: {', '.join(map(str, unknown))}. Known: {', '.join(sorted(SETTING_KEYS))}.")
    return s


def _manager(method: str, build, params: Any) -> str:
    """Validate with ``build(params) -> (path, body)``, send it to the adapter, say what came back."""
    try:
        path, body = build(params)
        if not from_env().adapter_key:
            raise _Refused("The model manager is off: AURA_ADAPTER_KEY is not set, so the adapter cannot be asked.")
    except _Refused as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    try:
        with _adapter_client() as c:
            r = c.request(method, path, json=body)
    except _egress.ServiceUrlError as exc:  # raised before any client exists: nothing was sent
        return json.dumps({"ok": False, "error": str(exc)})
    except httpx.HTTPError as exc:
        return json.dumps(_failure.of(exc))
    try:
        out = r.json()
    except ValueError:
        out = None
    if r.status_code >= 400 or not isinstance(out, dict) or out.get("ok") is False:
        error = out.get("error") if isinstance(out, dict) else None
        if error:  # the manager's own refusal, a sentence it wrote for the model (tests pin it)
            return json.dumps({"ok": False, "http": r.status_code, "error": str(error)})
        return json.dumps(_failure.status_answer(r.status_code, r.headers.get("retry-after"))
                          if r.status_code >= 400 else {"ok": False, "error": "no answer"})
    return json.dumps(out)


def handle_model_download(params: dict, **kwargs) -> str:
    del kwargs

    def build(p):
        body = {"repo": _need(p, "repo", _REPO_RE, "a Hugging Face repo, owner/name")}
        if (p or {}).get("file") is not None:
            body["file"] = _need(p, "file", _GGUF_RE, "one .gguf file of the repo")
        return "/api/models/downloads", body
    return _manager("POST", build, params)


def handle_model_add(params: dict, **kwargs) -> str:
    del kwargs

    def build(p):
        body: dict[str, Any] = {"id": _need(p, "id", _ID_RE, "lowercase letters, digits, dot, dash or underscore"),
                                "repo": _need(p, "repo", _REPO_RE, "a Hugging Face repo, owner/name")}
        engine = (p or {}).get("engine", "vllm")
        if engine not in _ENGINES:
            raise _Refused(f"engine is one of {', '.join(_ENGINES)}.")
        body["engine"] = engine
        if engine == "llama.cpp":
            body["file"] = _need(p, "file", _GGUF_RE, "the .gguf file to serve")
        elif (p or {}).get("file") is not None:
            raise _Refused("file is for llama.cpp models only.")
        body["settings"] = _settings_arg(p)
        return "/api/models", body
    return _manager("POST", build, params)


def handle_model_settings(params: dict, **kwargs) -> str:
    del kwargs

    def build(p):
        model = _need(p, "model", _ID_RE, "a model id")
        settings = _settings_arg(p)
        if not settings:
            raise _Refused("settings: send the settings to change.")
        return f"/api/models/{model}", {"settings": settings}
    return _manager("PATCH", build, params)


def _shown(value: Any) -> str:
    """One setting's value as a person reads it; the manager's null means "its default"."""
    if value is None:
        return "default"
    if isinstance(value, bool):
        return "on" if value else "off"
    return " ".join(str(value).split())[:40]


def model_settings_change(tool_name: str, args: dict, session: str = "") -> dict | None:
    """The approval card's Now and Becomes for ``model_settings`` (T-1329), or None for another tool.

    Reads the model's settings from the manager through the adapter (the list the Models screen
    shows): one read with a 3 second limit, because the approval waits on it. Only the settings the
    call changes are shown, as ``name value`` pairs. ``known`` is false when the call is
    malformed, the adapter or manager does not answer, or no such model is listed: the card then
    shows no values rather than a guess. Never raises. The settings are launch options, never
    credentials. Not claimed: that the manager accepts the change, or that the values are still
    these when the person answers.
    """
    del session
    if tool_name != "model_settings" or not isinstance(args, dict):
        return None
    model = args.get("model")
    new = args.get("settings")
    if not isinstance(model, str) or not _ID_RE.fullmatch(model) or not isinstance(new, dict) or not new \
            or set(new) - SETTING_KEYS:
        return None
    key = f"settings of {model}"
    try:
        with _adapter_client(timeout=3.0) as c:
            listed = c.get("/api/models").raise_for_status().json()
        now = next(m["settings"] for m in listed["models"] if m.get("id") == model)
        names = sorted(new)
        return {"key": key, "before": ", ".join(f"{k} {_shown(now.get(k))}" for k in names),
                "after": ", ".join(f"{k} {_shown(new[k])}" for k in names), "known": True, "secret": False}
    except Exception:
        return {"key": key, "known": False}


def handle_model_delete(params: dict, **kwargs) -> str:
    del kwargs

    def build(p):
        model = _need(p, "model", _ID_RE, "a model id")
        weights = (p or {}).get("weights", False)
        if not isinstance(weights, bool):
            raise _Refused("weights is true or false.")
        return f"/api/models/{model}" + ("?weights=1" if weights else ""), None
    return _manager("DELETE", build, params)
