"""Watches: "tell me when n8n is back", "when that page changes", "when the garage opens".

The owner's assistant offered this more than once ("I can set a scheduled job to watch that
URL hourly and ping you the moment the content changes -- shall I?", 2026-09-03) and had
nothing to keep the promise with: a ``cronjob`` asks the owner each time it is made, and
runs a model on every tick to look at a page that has not changed. A watch is a stored
question instead, checked every ten minutes by ``hermes/cron/scripts/watch-check.py``
without a model; the model is woken only when one trips, to say so in a sentence.

The same pass watches the homelab's own services (n8n, Grafana, llama-swap, the house
service), replacing the hourly "Homelab health" job that re-alerted one 502 every hour for
three weeks because each run was a fresh session: a service that goes down is said once,
coming back is said once, and one still down is mentioned again at most once a day.

What a watch can see: an http(s) URL answering (``up``/``down``), the page at one changing
(``change``), or a house entity's state (``entity``). Not covered: anything behind a login
(the probe sends no credentials), or a page whose every load differs (a clock, an ad), which
trips each pass; ``change`` suits a page that is the same until it is not.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from _shared._jsonstore import err as _err, ok as _ok, read_json, write_json

import httpx
from _shared import _egress

KINDS = ("up", "down", "change", "entity")
MAX_WATCHES = 50
PROBE_TIMEOUT_S = 10.0
#: A service still down is said again no sooner than this.
REMIND_AFTER_S = 24 * 3600
_LOCK = threading.Lock()
_URL = re.compile(r"^https?://\S+$")
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def _path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-watches" / "watches.json"


def load() -> dict:
    data = read_json(_path())
    return {"watches": list(data.get("watches") or []), "homelab": dict(data.get("homelab") or {})}


def _save(data: dict) -> None:
    write_json(_path(), data)


# --- the tools ---------------------------------------------------------------------------

def handle_watch_add(args: dict, **_: Any) -> str:
    kind = str(args.get("kind") or "").strip().lower()
    target = str(args.get("target") or "").strip()
    say = " ".join(str(args.get("say") or "").split())[:200]
    if kind not in KINDS:
        return _err(f"kind is one of {', '.join(KINDS)}")
    if kind == "entity":
        if not _ENTITY.match(target):
            return _err("target is a house entity id (\"cover.garage_door\"); find it with the house tools first")
        if not str(args.get("state") or "").strip() and args.get("above") is None and args.get("below") is None:
            return _err("an entity watch needs state (\"open\"), above or below")
    elif not _URL.match(target):
        return _err("target is an http(s) URL")
    if not say:
        return _err("say is what to tell Sir when it trips (\"n8n is back up\")")
    watch: dict[str, Any] = {
        "id": uuid.uuid4().hex[:8], "kind": kind, "target": target, "say": say,
        "once": bool(args.get("once", True)), "created": int(time.time()),
    }
    if kind != "entity":
        # Inside the house only with the owner's yes (the matrix's watch:private row asks
        # before this runs); remembered, so a public watch whose name later resolves
        # inside the house is not probed (see check_all).
        watch["private"] = _egress.private(target)
    for key in ("state", "above", "below"):
        if args.get(key) not in (None, ""):
            watch[key] = args[key] if key != "state" else str(args[key]).strip().lower()
    with _LOCK:
        data = load()
        if len(data["watches"]) >= MAX_WATCHES:
            return _err("too many watches; remove one first")
        data["watches"].append(watch)
        _save(data)
    return _ok(watch=watch, said=f"Watching {target}; checked every ten minutes.")


def handle_watch_list(args: dict, **_: Any) -> str:
    data = load()
    return _ok(watches=data["watches"], homelab=data["homelab"])


def handle_watch_remove(args: dict, **_: Any) -> str:
    wid = str(args.get("id") or "").strip()
    with _LOCK:
        data = load()
        keep = [w for w in data["watches"] if w["id"] != wid]
        if len(keep) == len(data["watches"]):
            return _err(f"no watch {wid!r}; watch_list shows them")
        data["watches"] = keep
        _save(data)
    return _ok(removed=wid)


# --- the pass ----------------------------------------------------------------------------

def step(watch: dict, observed: Any, now: float) -> tuple[dict, Optional[str]]:
    """One check of [watch] against what was [observed]: its new state, and the line to say.

    ``observed`` is a bool for up/down/entity (is the condition true now) and a hash for
    change. A condition trips on becoming true -- already true at the first check counts,
    so "tell me when n8n is back" answers at once if it is -- and a repeating watch re-arms
    only once it has been false again. A change watch's first look is its baseline.
    """
    w = dict(watch)
    last = w.get("last")
    w["last"], w["checked"] = observed, int(now)
    if w["kind"] == "change":
        if last is None or observed is None or observed == last:
            return w, None
        return w, w["say"]
    if observed and not last:
        return w, w["say"]
    return w, None


def homelab_step(entry: dict, up: bool, name: str, detail: str, now: float) -> tuple[dict, Optional[str]]:
    """One service's check: said when it goes down or comes back, and once a day while down."""
    e = dict(entry)
    was = e.get("up")
    if was is None:
        e.update(up=up, since=int(now))
        # The first pass after this ships is a baseline, except for something already down.
        if up:
            return e, None
        e["said"] = int(now)
        return e, f"{name} is down ({detail})."
    if up != was:
        e.update(up=up, since=int(now), said=int(now))
        return e, (f"{name} is back up." if up else f"{name} is down ({detail}).")
    if not up and now - float(e.get("said") or 0) >= REMIND_AFTER_S:
        e["said"] = int(now)
        day = datetime.datetime.fromtimestamp(float(e.get("since") or now)).strftime("%-d %B")
        return e, f"{name} is still down, since {day} ({detail})."
    return e, None


def _probe_url(client: httpx.Client, url: str) -> tuple[bool, str, Optional[str]]:
    """(answering, detail, body hash). Answering is any status below 500: a 401 is a server."""
    try:
        r = client.get(url)
    except httpx.HTTPError as exc:
        return False, exc.__class__.__name__, None
    body = hashlib.sha256(" ".join(r.text.split()).encode("utf-8")).hexdigest()[:16]
    return r.status_code < 500, f"HTTP {r.status_code}", body


def _entity_matches(watch: dict, state: Optional[str]) -> bool:
    if state is None:
        return False
    if watch.get("state"):
        return state.lower() == watch["state"]
    try:
        value = float(state)
    except ValueError:
        return False
    if watch.get("above") is not None and not value > float(watch["above"]):
        return False
    if watch.get("below") is not None and not value < float(watch["below"]):
        return False
    return True


def homelab_targets(cfg: Any) -> list[tuple[str, str, dict]]:
    """(name, URL, headers) for each homelab service this install configures."""
    out: list[tuple[str, str, dict]] = []
    if cfg.n8n_url:
        out.append(("n8n", cfg.n8n_url.rstrip("/") + "/healthz", {}))
    if cfg.grafana_url:
        out.append(("Grafana", cfg.grafana_url.rstrip("/") + "/api/health", {}))
    if cfg.llama_swap_url:
        out.append(("llama-swap", cfg.llama_swap_url.rstrip("/") + "/running",
                    {"Authorization": f"Bearer {cfg.model_api_key}"} if cfg.model_api_key else {}))
    if cfg.house_token:
        out.append(("The house service", cfg.house_url.rstrip("/") + "/api/",
                    {"Authorization": f"Bearer {cfg.house_token}"}))
    if cfg.tts_provider == "openai" and cfg.tts_shim_url:
        base = cfg.tts_shim_url.rstrip("/").removesuffix("/v1")
        out.append((VOICE, base + "/health", {}))
    return out


VOICE = "The voice"


def _voice_health(r: httpx.Response) -> tuple[bool, str]:
    """CosyVoice answers 200 even on the CPU or with no clip; its body says which.

    On the CPU a line takes minutes, so the adapter quietly speaks Piper instead
    (2026-10-06, a host driver update): without this only the next deploy's smoke noticed.
    Does not cover Whisper on the same card; /health does not report it.
    """
    try:
        body = r.json()
    except ValueError:
        return False, "its health answer is not JSON"
    if body.get("status") != "ok":
        return False, "no voice clip is registered"
    if body.get("warming"):
        # The engine says "cpu" until its warm-up has loaded the model; a restart is not an outage.
        return True, "warming"
    if body.get("device") != "cuda":
        return False, f"running on the {str(body.get('device') or 'unknown').upper()}, not the GPU"
    return True, "cuda"


def check_all(now: Optional[float] = None, client: Optional[httpx.Client] = None,
              entity_state: Optional[Callable[[str], Optional[str]]] = None) -> list[str]:
    """Run every watch and the homelab once; the lines to say, in order. Saves the state."""
    from aura_schema import from_env

    cfg = from_env()
    now = time.time() if now is None else now
    own = client is None
    # No redirects: a public page that answers 302 to a LAN address would otherwise take
    # an approval-free watch inside the house.
    client = client or httpx.Client(timeout=PROBE_TIMEOUT_S, follow_redirects=False)
    if entity_state is None:
        def entity_state(entity_id: str) -> Optional[str]:
            try:
                r = client.get(f"{cfg.house_url.rstrip('/')}/api/states/{entity_id}",
                               headers={"Authorization": f"Bearer {cfg.house_token}"})
                return str(r.json().get("state")) if r.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                return None
    said: list[str] = []
    try:
        with _LOCK:
            data = load()
            kept = []
            for w in data["watches"]:
                if w["kind"] == "entity":
                    state = entity_state(w["target"])
                    failed = state is None
                    observed: Any = _entity_matches(w, state)
                elif not w.get("private") and _egress.private(w["target"]):
                    # Added as public, now resolving inside the house: not probed.
                    kept.append(w)
                    continue
                else:
                    up, _detail, body = _probe_url(client, w["target"])
                    # An up/down watch's probe failing is its answer; a change watch has none.
                    failed = w["kind"] == "change" and body is None
                    observed = body if w["kind"] == "change" else (up if w["kind"] == "up" else not up)
                w, line = step(w, observed, now)
                # health.py says a watch that could not be checked for a while.
                w["fails"] = int(w.get("fails") or 0) + 1 if failed else 0
                if line:
                    said.append(line)
                    if w.get("once"):
                        continue
                kept.append(w)
            data["watches"] = kept
            for name, url, headers in homelab_targets(cfg):
                try:
                    r = client.get(url, headers=headers)
                    up, detail = r.status_code < 400, f"HTTP {r.status_code}"
                    if up and name == VOICE:
                        up, detail = _voice_health(r)
                except httpx.HTTPError as exc:
                    up, detail = False, exc.__class__.__name__
                data["homelab"][name], line = homelab_step(data["homelab"].get(name, {}), up, name, detail, now)
                if line:
                    said.append(line)
            _save(data)
    finally:
        if own:
            client.close()
    return said


def main() -> int:
    """The cron gate: lines for the model to say, or the wake gate's "nothing to say"."""
    lines = check_all()
    if lines:
        print("\n".join(f"- {line}" for line in lines))
    else:
        print(json.dumps({"wakeAgent": False}))
    return 0
