"""`/api/status` — the HUD's one call for "what is JARVIS running on right now".

Combines llama-swap's `/running` (which model is resident and whether it is
`starting` — the cold-start state the UIs render as "warming up model"), the
Hermes API server's `/health`, the `hermes serve` backend's `/api/health`, the
configured model name and the kill switch.

The kill switch is read from the file rather than asked of Hermes, because the
whole point of it is that it works when the gateway does not: a switch that the
HUD could only learn about by asking a wedged gateway would go dark exactly when
it mattered. The adapter mounts the Hermes volume read-only for this one file
(`deploy/docker-compose.yml`), so it can answer even while the gateway is down —
which is the state `reactor.json`'s precedence puts `kill` *above*.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

from .config import Settings

#: Where the gateway keeps the switch. A constant, not a setting and not an
#: environment read: the adapter reads its configuration through ``aura_schema``
#: (D17, pinned by ``test_schema_conformance.py``), and this is not configuration
#: — it is the container path the compose file fixes by setting ``HERMES_HOME``
#: to ``/opt/data`` and mounting the same volume here read-only. The path lives
#: in ``tests/contracts/kill-switch.json`` with the writer's, and the contract
#: test fails if the two stop agreeing. Callers pass an explicit path in tests.
KILL_SWITCH_PATH: str = "/opt/data/state/kill-switch.json"


def read_kill_switch(path: str | None = None) -> dict[str, Any]:
    """The kill switch, for the HUD. Never raises; unreadable reads as engaged.

    Mirrors ``hermes/plugins/_shared/_killswitch.read`` deliberately rather than
    importing it: the adapter is a separate image that does not ship the plugins,
    and the two are held together by ``tests/contracts/kill-switch.json``, which
    both suites read. Failing towards "engaged" matters more here than anywhere:
    a HUD that showed a released switch because it could not read the file would
    tell the owner the assistant was live when it was stopped.
    """
    target = Path(path or KILL_SWITCH_PATH)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"engaged": False}
    except OSError as exc:
        return {"engaged": True, "reason": f"kill-switch file unreadable: {exc.__class__.__name__}"}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"engaged": True, "reason": "kill-switch file is not valid JSON"}
    if not isinstance(data, dict):
        return {"engaged": True, "reason": "kill-switch file is not an object"}
    return {
        "engaged": bool(data.get("engaged")),
        "actor": data.get("actor") or "",
        "reason": data.get("reason") or "",
        "at": data.get("at") or "",
    }


#: Beside the kill switch, written by hermes/plugins/jarvis-personas; the shape is
#: ``tests/contracts/persona.json``, which both readers run as a truth table.
PERSONA_PATH: str = "/opt/data/state/persona.json"
PERSONAS: tuple[str, ...] = ("aura", "jarvis")
#: The theme pack each persona wears (the contract's ``themes``): each has her own (T-0861).
#: A client sets its look from this before its first ``persona.switch``.
THEMES: dict[str, str] = {"aura": "aura", "jarvis": "jarvis"}
DEFAULT_PERSONA: str = "aura"
#: A drafted theme's id, as the personas plugin allows it (drafts.py).
THEME_ID = re.compile(r"[a-z][a-z0-9-]{1,23}")


def read_persona(path: str | None = None) -> dict[str, Any]:
    """Which persona is primary, for a client connecting after a switch. Never raises.

    Everything unreadable is AURA, the default theme's persona (design/AURA/PROMPT.md §2):
    the wrong voice is the worst a bad read can do here -- the opposite of the kill
    switch, which fails towards engaged.
    """
    try:
        with open(path or PERSONA_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    active = str(data.get("active") or "").strip().lower()
    if active not in PERSONAS:
        active = DEFAULT_PERSONA
    # A theme AURA drafted and the person applied is worn over the persona's own (theme_apply).
    custom = str(data.get("theme") or "")
    return {"active": active, "theme": custom if THEME_ID.fullmatch(custom) else THEMES[active],
            "since": str(data.get("since") or ""), "previous": str(data.get("previous") or "")}


def read_theme(theme_id: str, path: str | None = None) -> dict[str, Any] | None:
    """An applied drafted theme's pack (HERMES_HOME/themes/<id>.json), or None. Never raises.

    The shipped packs are generated into every client; only a drafted one is served.
    """
    if not THEME_ID.fullmatch(theme_id or ""):
        return None
    try:
        with (Path(path or PERSONA_PATH).parent.parent / "themes" / f"{theme_id}.json").open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None,
                    timeout: float = 5.0) -> tuple[bool, Any]:
    try:
        r = await client.get(url, headers=headers or {}, timeout=timeout)
        if r.status_code != 200:
            return False, {"http": r.status_code}
        return True, r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return False, {"error": exc.__class__.__name__}


def percentiles(ms: Iterable[float]) -> dict[str, Any]:
    """p50/p95 by nearest rank, so each figure is a timing that happened; None with no samples."""
    xs = sorted(ms)

    def at(p: float) -> int | None:
        return round(xs[max(0, math.ceil(p * len(xs)) - 1)]) if xs else None

    return {"n": len(xs), "p50": at(0.5), "p95": at(0.95)}


def summarize_llama_swap(running: Any, model: str) -> dict[str, Any]:
    """Pure: turn llama-swap's /running payload into the HUD's model block."""
    entries = running.get("running", []) if isinstance(running, dict) else []
    loaded = [e for e in entries if isinstance(e, dict)]
    ours = next((e for e in loaded if e.get("model") == model), None)
    state = "unloaded"
    if ours:
        state = str(ours.get("state") or "unknown")
    elif loaded:
        state = "other-model"
    warming = state in {"starting", "loading"}
    return {
        "model": model,
        "state": state,           # ready | starting | stopping | unloaded | other-model
        "warming_up": warming,
        "loaded": [e.get("model") for e in loaded],
    }


def status_word(*, api: bool, backend: bool, swap: bool, model_state: str, kill: bool) -> dict[str, str]:
    """The one word every surface shows, and why (T-0583, ``tests/contracts/status-words.json``).

    Decided here, once: the console, the phone and the desktop print ``word: reason`` from this
    field and derive nothing. First that holds wins: the backend out of reach is ``down``; the
    kill switch, then the model server out of reach, then a model still loading are ``degraded``.
    An unloaded model at rest is ``ready``: llama-swap unloads it most of the evening, and a
    status that said degraded all night would say nothing.

    Does not cover: the adapter itself being down (nothing answers; each surface then shows the
    table's ``no_answer`` label), or the house (jarvis-core), which this report does not probe."""
    if not (api and backend):
        return {"word": "down", "reason": "backend unreachable"}
    if kill:
        return {"word": "degraded", "reason": "kill switch engaged"}
    if not swap or model_state == "unreachable":
        return {"word": "degraded", "reason": "model server unreachable"}
    if model_state in {"starting", "loading"}:
        return {"word": "degraded", "reason": "model warming"}
    return {"word": "ready", "reason": ""}


async def build_status(client: httpx.AsyncClient, settings: Settings) -> dict[str, Any]:
    t0 = time.time()
    ok_swap, swap = await _get_json(client, f"{settings.llama_swap_url}/running",
                                    {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else None)
    ok_api, api = await _get_json(client, f"{settings.hermes_api_url}/health")
    ok_serve, serve = await _get_json(client, f"{settings.hermes_serve_url}/api/health",
                                      {"X-Hermes-Session-Token": settings.hermes_serve_token})
    model = summarize_llama_swap(swap if ok_swap else {}, settings.model)
    if not ok_swap:
        model["state"] = "unreachable"
        model["warming_up"] = False
    kill = read_kill_switch()
    return {
        # An engaged switch is not "ok": the HUD's own health dot must not read
        # green while JARVIS is refusing every action the owner asks for.
        "ok": ok_api and ok_serve and not kill["engaged"],
        "status": status_word(api=ok_api, backend=ok_serve, swap=ok_swap, model_state=model["state"],
                              kill=bool(kill["engaged"])),
        "kill_switch": kill,
        "persona": read_persona(),
        "model": model,
        "hermes": {
            "api_server": {"ok": ok_api, "version": (api or {}).get("version") if isinstance(api, dict) else None},
            "backend": {"ok": ok_serve, "version": (serve or {}).get("version") if isinstance(serve, dict) else None},
        },
        "llama_swap": {"ok": ok_swap, "url": settings.llama_swap_url},
        "latency_ms": round((time.time() - t0) * 1000),
    }


__all__ = ["build_status", "read_kill_switch", "status_word", "summarize_llama_swap"]
_ = asyncio  # keep the import explicit for readers; status gathering is sequential on purpose
