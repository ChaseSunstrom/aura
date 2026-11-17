"""The agent's way into the artifact store.

The media brief asks for an artifact tool so that *"tools that produce media
(ComfyUI generation, downloads, screenshots, charts, document conversion) publish
through it rather than leaving files in a working directory"*, and it is explicit
about what that replaces: *"Retire the 'here's a path on the container' pattern
entirely."* A path is an answer only to someone with a shell on that container;
an artifact id and a house URL are an answer to the phone, the desktop and the
console as well.

This is a client, not a second store. The store lives in the adapter, which is
the one service that already terminates the bearer key, binds to the house network and
mounts the volume — so a plugin that reimplemented it would be a second copy of
the retention rules and the size caps, drifting from the first.
"""
from __future__ import annotations

import json
import logging
# `os` for fstat and for the environment the fetch script inherits -- settings
# come from aura_schema, never from os.environ.
import os
import re
import shutil
import stat as stat_module
import subprocess
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from aura_schema import from_env

from . import deliver, surfaces

_LOGGER = logging.getLogger(__name__)

#: Read through the one settings schema, never from the environment.
#:
#: `aura-schema/tests/test_plugin_env_conformance.py` enforces this across
#: every plugin, and it is not bureaucracy: a plugin that reads `os.environ`
#: directly gets a different answer from the rest of the system the moment a
#: setting is renamed, defaulted or validated, and nothing tells anybody.
def _settings():
    return from_env()


def _base() -> str:
    return str(_settings().adapter_url or "http://127.0.0.1:8643").rstrip("/")

#: Refuse to read a file larger than this into memory to publish it. The store
#: accepts far more; this is the bound on doing it through a tool call, where the
#: bytes pass through this process.
MAX_PUBLISH_BYTES = 256 * 1024 * 1024

_TIMEOUT = 30

#: An artifact id is a SHA-256 hex digest. The adapter enforces this too; the
#: guard is repeated here because an id from a tool argument is interpolated
#: into a URL *before* it reaches the adapter, and `abc/../../health?x=` would
#: build a request to somewhere else entirely. A sibling guard on both sides of
#: a boundary is not duplication -- each one protects a different step.
_ID = re.compile(r"^[0-9a-f]{64}$")


def _bad_id(ident: str) -> dict | None:
    if _ID.match(ident or ""):
        return None
    return {"ok": False, "error": f"not an artifact id: {ident!r}"}


def _key() -> str:
    return str(_settings().adapter_key or "")


class _NoRedirect(request.HTTPRedirectHandler):
    """urllib follows a redirect with the Authorization header still on it, so a store (or
    anything answering as it) that said "go to host B" would receive the bearer key. The
    adapter never redirects these calls: any redirect surfaces as an HTTPError."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


#: No redirects and no proxy: HTTP_PROXY in the gateway's environment would carry the key and
#: every published file to the proxy. The adapter is on this machine, reached directly or not
#: at all. Not covered: the adapter URL itself, which is the owner's setting.
_OPENER = request.build_opener(_NoRedirect, request.ProxyHandler({}))


def _open(req: request.Request, timeout: float):
    return _OPENER.open(req, timeout=timeout)


def _call(method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None,
          raw: bool = False) -> Any:
    key = _key()
    if not key:
        return {"ok": False, "error": "AURA_API_KEY is not set, so the artifact store is unreachable"}
    req = request.Request(f"{_base()}{path}", method=method, data=body)
    req.add_header("Authorization", f"Bearer {key}")
    for k, v in (headers or {}).items():
        if v is not None:
            req.add_header(k, v)
    try:
        with _open(req, _TIMEOUT) as resp:
            data = resp.read()
        if raw:
            return data.decode("utf-8", "replace")
        return json.loads(data) if data else {"ok": True}
    except error.HTTPError as exc:
        # The store's own message is more useful than "HTTP 413": it names the
        # cap that was hit.
        detail = ""
        try:
            detail = json.loads(exc.read()).get("detail", "")
        except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
            pass
        return {"ok": False, "error": detail or f"the artifact store answered {exc.code}"}
    except (error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "error": f"the artifact store is unreachable: {exc}"}


def url_for(ident: str) -> str:
    """The house URL a person or another surface can open."""
    check = _bad_id(ident)
    if check:
        return ""
    public = str(_settings().network_public_url or _base()).rstrip("/")
    return f"{public}/api/artifacts/{ident}"


#: Where the skills and Hermes put what they make, under the Hermes home: `work` (every
#: office, audio, image and media skill writes there), `files` (downloads and recordings) and
#: the media Hermes itself caches (screenshots, images, audio, documents). The test reads the
#: skills against this, so a skill that moves its output folder fails there rather than being
#: refused here in a turn. Not the whole `cache` (model caches and delegation state sit there)
#: and not the system temp dir (every process on the box writes there).
_HOME_OUTPUT_DIRS = ("work", "files", "cache/screenshots", "cache/images", "cache/audio", "cache/documents",
                     "browser_screenshots")

#: A name that holds a credential, refused even inside an output folder: a download or a
#: script's output can land next to one. Matched on the lowercased file name.
_CREDENTIAL_NAME = re.compile(
    r"^(\.env.*|.*\.(pem|key|p12|pfx|kdbx)|id_(rsa|dsa|ecdsa|ed25519).*|config\.yaml"
    r"|(auth|credentials?|secrets?)[^/]*\.json|[^/]*token[^/]*\.json)$"
)


def _publish_roots() -> list[Path]:
    # HERMES_HOME is Hermes' plumbing, not a Jarvis setting (the schema's conformance test allows it).
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return [(home / d).resolve() for d in _HOME_OUTPUT_DIRS]


def _refusal(path: Path) -> str | None:
    """Why this path may not be published, or None. Judged on the resolved path, so a
    `..` or a linked parent folder cannot lead out of an output folder.

    Not covered: a secret copied into an output folder under an innocent name; the folder
    rule bounds where the agent reads, not what it was told to put there."""
    real = path.resolve()
    if ".ssh" in real.parts or _CREDENTIAL_NAME.match(path.name.lower()) or _CREDENTIAL_NAME.match(real.name.lower()):
        return f"{path.name} looks like a credential and is never published"
    if not any(root == real or root in real.parents for root in _publish_roots()):
        return f"{path} is outside the output folders ({', '.join(str(r) for r in _publish_roots())})"
    return None


def handle_publish(params: dict, **kwargs: Any) -> str:
    path = Path(str(params.get("path", "")))
    # The agent names this path and reads web pages: a planted instruction must not be able
    # to publish the stack's own secrets or a system file to a URL.
    refused = _refusal(path)
    if refused:
        return json.dumps({"ok": False, "error": refused})
    # A symlink is refused rather than followed. The agent chooses this path, and
    # the agent reads web pages: a link planted at a predictable name pointing at
    # /etc/shadow would otherwise publish it to a URL, which is the whole point
    # of the store working.
    if path.is_symlink():
        return json.dumps({"ok": False, "error": f"{path} is a symlink; publish the real file"})
    try:
        # Opened once and measured through the handle, so the file that is sized
        # is the file that is read. Checking `is_file()` and `stat()` and then
        # reading is three chances for the path to become something else in
        # between -- and the third read is the one that leaves the container.
        with path.open("rb") as fh:
            info = os.fstat(fh.fileno())
            if not stat_module.S_ISREG(info.st_mode):
                return json.dumps({"ok": False, "error": f"{path} is not a regular file"})
            size = info.st_size
            if size > MAX_PUBLISH_BYTES:
                return json.dumps({
                    "ok": False,
                    "error": f"{size} bytes is over the {MAX_PUBLISH_BYTES} byte tool limit; "
                             "publish it from the service that produced it instead",
                })
            body = fh.read(MAX_PUBLISH_BYTES + 1)
    except OSError as exc:
        return json.dumps({"ok": False, "error": f"cannot read {path}: {exc.strerror or exc}"})
    if len(body) > MAX_PUBLISH_BYTES:
        # It grew between the fstat and the read.
        return json.dumps({"ok": False, "error": "the file grew past the tool limit while reading"})
    ttl = params.get("ttl")
    out = _call(
        "POST",
        "/api/artifacts",
        body=body,
        # Only the headers that have a value: `_call` sends what it is given, and
        # a None here becomes the literal string "None" on the wire.
        headers={
            k: v
            for k, v in {
                "x-artifact-mime": str(params.get("mime") or "application/octet-stream"),
                "x-artifact-title": str(params.get("title") or path.name),
                "x-artifact-ttl": "never" if ttl == "never" else (str(ttl) if ttl else ""),
                "x-artifact-session": str(params.get("session") or ""),
                "x-artifact-tool": str(params.get("tool") or ""),
                "x-artifact-source-url": str(params.get("source_url") or ""),
            }.items()
            if v
        },
    )
    if out.get("ok") is False:
        return json.dumps(out)
    out["url"] = url_for(out["id"])
    # The publish is done by here; showing it is best effort and reports itself
    # through `delivered`, never through `ok` (see deliver.py).
    out.update(deliver._deliver(kwargs.get("session_id"), out, panel_id=kwargs.get("panel_id")))
    return json.dumps(out)


#: The one panel a session's browser receipt lives in: the next look replaces
#: it, so a long task's ten looks are one panel, not ten.
RECEIPT_PANEL_ID = "browser-receipt"


def _screenshot_path(result: Any) -> str:
    """The ``screenshot_path`` a browser_vision result names: in a multimodal
    envelope's meta, or in its JSON text."""
    if isinstance(result, dict):
        meta = result.get("meta")
        data: Any = meta if isinstance(meta, dict) else result
    else:
        try:
            data = json.loads(result)
        except (TypeError, ValueError):
            return ""
    path = data.get("screenshot_path") if isinstance(data, dict) else None
    return path if isinstance(path, str) else ""


def browser_receipt(tool_name: str = "", result: Any = None, session_id: str = "", **_: Any) -> None:
    """``post_tool_call``: browser_vision's screenshot stays on the HUD as the task's receipt.

    The live activity tile goes five seconds after a run ends, which left "done"
    as the only proof. Only Hermes' own shot is published -- a file named
    ``browser_screenshot_*.png`` under HERMES_HOME (handle_publish refuses a
    symlink) -- kept a day. Never raises. Not covered: whether the model looks
    at the end (the skill asks it to); a session with no HUD client sees
    nothing, though the artifact is stored; a wedged adapter holds the turn for
    the store's own timeout.
    """
    if tool_name != "browser_vision":
        return
    try:
        raw = _screenshot_path(result)
        if not raw:
            return
        path = Path(raw)
        # HERMES_HOME is Hermes' plumbing, not a Jarvis setting (the schema's conformance test allows it).
        home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
        if not (path.name.startswith("browser_screenshot_") and path.suffix == ".png"):
            return
        if home not in path.resolve().parents:
            return
        handle_publish(
            {"path": raw, "mime": "image/png", "title": "Browser receipt",
             "tool": "browser_vision", "ttl": "86400"},
            session_id=session_id, panel_id=RECEIPT_PANEL_ID,
        )
    except Exception:  # noqa: BLE001 - a receipt that cannot be shown must not fail the turn
        _LOGGER.debug("browser receipt failed", exc_info=True)


#: A download's wall-clock cap, in seconds. The plugin's `_TIMEOUT` is for one
#: HTTP round trip to the store and is far too short for a fetch; this matches
#: the benchmark's budget for a media task (`imagegen-006`).
MEDIA_FETCH_TIMEOUT = 600

#: What each of the script's exit codes means, in the words the skill uses, so
#: the model acts on the code rather than re-reading stderr.
_FETCH_EXIT: dict[int, str] = {
    1: "the arguments were wrong; fix them rather than retrying as-is",
    2: "a tool is missing from the image; file a capability gap, never install it at runtime",
    3: "the download failed (the URL, the site or the network); one retry is reasonable",
    4: "it downloaded something that is not a video; try another quality or source",
    5: "publishing to the artifact store failed; check the adapter is up",
}


def _fetch_script() -> str | None:
    """`fetch-video.sh`: on PATH in the image (deploy/Dockerfile), the repo copy under test."""
    repo = Path(__file__).resolve().parents[3] / "scripts" / "media" / "fetch-video.sh"
    return shutil.which("fetch-video.sh") or (str(repo) if repo.is_file() else None)


def handle_media_fetch(params: dict, **_: Any) -> str:
    """Run `fetch-video.sh` here, in the gateway, which has the network the shell lacks.

    The `terminal` tool is a bubblewrap sandbox with `--unshare-net` (ADR-0045),
    so the script exits 3 there whatever the URL. `subprocess.run` from this
    process is the one place its yt-dlp and its own curl to the store both work.
    Not covered: a download that finishes inside the cap but past the model's
    patience -- the script has no progress channel back to the tool call.
    """
    url = str(params.get("url") or "").strip()
    search = str(params.get("search") or "").strip()
    if bool(url) == bool(search):
        return json.dumps({"ok": False, "error": "give exactly one of url or search"})
    # A value that starts with `-` would be read as a flag by yt-dlp, which the
    # script hands the URL to positionally; the model chooses this string.
    if url and not url.startswith(("http://", "https://")):
        return json.dumps({"ok": False, "error": f"not an http(s) URL: {url!r}"})
    try:
        quality = int(params.get("quality") or 720)
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "error": "quality must be a number of pixels, e.g. 720"})
    if quality <= 0:
        return json.dumps({"ok": False, "error": "quality must be a positive number of pixels"})
    script = _fetch_script()
    if not script:
        return json.dumps({"ok": False, "error": "fetch-video.sh is not in this image; file a capability gap"})
    argv = [script, "--url", url] if url else [script, "--search", search]
    argv += ["--quality", str(quality)]
    if params.get("title"):
        argv += ["--title", str(params["title"])]
    ttl = params.get("ttl")
    if ttl:
        argv += ["--ttl", "never" if ttl == "never" else str(ttl)]
    # The script reads the store's address and key from its environment; they are
    # set here from the schema so the script and the tool agree on the store even
    # when the gateway's environment spells them differently.
    env = {
        **os.environ,
        "AURA_API_KEY": _key(),
        "AURA_ADAPTER_BASE": _base(),
        "AURA_PUBLIC_URL": str(_settings().network_public_url or _base()).rstrip("/"),
    }
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, env=env,
            timeout=MEDIA_FETCH_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({
            "ok": False,
            "error": f"fetch-video.sh ran past {MEDIA_FETCH_TIMEOUT}s and was stopped; "
                     "nothing was published",
        })
    except OSError as exc:
        return json.dumps({"ok": False, "error": f"cannot run fetch-video.sh: {exc.strerror or exc}"})
    stderr_tail = " ".join((proc.stderr or "").strip().splitlines()[-3:])
    if proc.returncode != 0:
        meaning = _FETCH_EXIT.get(proc.returncode, "fetch-video.sh failed")
        return json.dumps({
            "ok": False, "exit_code": proc.returncode,
            "error": f"{meaning}: {stderr_tail}" if stderr_tail else meaning,
        })
    # The script's contract is one JSON line on stdout, last; anything before it
    # would be a stray print, so only the last line is read.
    try:
        out = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (IndexError, ValueError):
        return json.dumps({"ok": False, "error": "fetch-video.sh exited 0 without its JSON line"})
    return json.dumps(out)


def handle_list(params: dict, **_: Any) -> str:
    # Quoted: an unescaped filter would let `x&pinned=true` or a `#` change the
    # request rather than filter it.
    kind = parse.quote(str(params.get("kind") or ""), safe="")
    out = _call("GET", f"/api/artifacts?kind={kind}")
    if isinstance(out, dict) and out.get("ok") is False:
        return json.dumps(out)
    rows = out.get("artifacts", []) if isinstance(out, dict) else []
    for row in rows:
        row["url"] = url_for(row["id"])
    return json.dumps({"ok": True, "artifacts": rows, "count": len(rows)})


def _find(ident: str) -> dict:
    """One artifact's row (with its ``url``) by id, or the store's error, or ``no artifact``."""
    # ponytail: scans the listing because the store's GET /{id} returns the blob,
    # not the metadata. Fine while the store holds hundreds; add a metadata route
    # (or ?meta=1) if a listing ever costs enough to notice.
    out = _call("GET", "/api/artifacts?kind=")
    if isinstance(out, dict) and out.get("ok") is False:
        return out
    rows = out.get("artifacts", []) if isinstance(out, dict) else []
    for row in rows:
        if row["id"] == ident:
            row["url"] = url_for(ident)
            return {"ok": True, "artifact": row}
    return {"ok": False, "error": f"no artifact {ident}"}


#: How much of the newest car log `jarvis_health` hands the model: its end, where a
#: drive's failure is, without spending the context on the whole drive.
CAR_LOG_TAIL = 6000


def handle_health(params: dict, **_: Any) -> str:
    """Jarvis's own state in one call: the HUD's status (model, Hermes, kill switch)
    and the tail of the newest car log the phone uploaded (`CarLogUpload`).

    Before this the model could not answer "is anything broken?" without a runbook
    of tool calls, nor "why was the car blank?" at all: `artifact_get` returns a
    row, not the text. Does not cover the house service or the GPUs; those have
    their own tools.
    """
    status = _call("GET", "/api/status")
    listing = _call("GET", "/api/artifacts?kind=")
    rows = listing.get("artifacts", []) if isinstance(listing, dict) else []
    logs = [r for r in rows if (r.get("provenance") or {}).get("tool") == "car-log"]
    car = None
    if logs:
        newest = max(logs, key=lambda r: r.get("created") or 0)
        text = _call("GET", f"/api/artifacts/{newest['id']}", raw=True)
        car = {"title": newest.get("title"), "created": newest.get("created"), "drives_kept": len(logs),
               "tail": text[-CAR_LOG_TAIL:] if isinstance(text, str) else text}
    return json.dumps({"ok": True, "status": status, "car_log": car})


def handle_get(params: dict, **_: Any) -> str:
    ident = str(params.get("id", ""))
    if bad := _bad_id(ident):
        return json.dumps(bad)
    return json.dumps(_find(ident))


def reported_surface(session_id: str | None) -> dict | None:
    """What the session's client said its screen can show, or ``None`` when it never said.

    The client reports on ``prompt.submit`` under the live id it holds; a tool
    handler and the prompt builder are given the durable one (``hud._state_key``
    says why they differ), so the durable id is tried as given and then mapped to
    the live one through jarvis-hud, which already keeps that table. ``None`` is
    the honest answer for the api_server platform and a cron turn: nobody is on
    a screen, and the caller falls back to the poorest profile.
    """
    sid = str(session_id or "")
    if not sid or not _key():
        return None
    candidates = [sid]
    try:
        hud = deliver._hud_module()
        live = hud._resolve_tui_sid(sid) if hud is not None else None
    except Exception:  # noqa: BLE001 - a broken sibling plugin must not cost the prompt line
        live = None
    if live and live not in candidates:
        candidates.append(live)
    for candidate in candidates:
        out = _call("GET", "/api/surface/current?" + parse.urlencode({"session": candidate}))
        if isinstance(out, dict) and isinstance(out.get("surface"), dict):
            return out["surface"]
    return None


def render_surface_section(session_info: Any = None) -> str:
    """The one prompt line: what the surface this session is on can show.

    Rendered once, when the session's prompt is built (Hermes freezes sections
    into it); a session resumed on another screen keeps the line it started
    with, and `surface_capabilities` is the live answer for that turn.
    """
    # ponytail: no pre_llm_call refresh for a session that moves screens; add one
    # the way jarvis-personas does if a stale line ever misroutes a handoff.
    try:
        sid = str((session_info or {}).get("session_id") or "")
    except Exception:  # noqa: BLE001 - a read-only mapping of unknown shape must not cost the prompt
        sid = ""
    reported = reported_surface(sid)
    return surfaces.summary(str((reported or {}).get("name") or "unknown"), reported)


def handle_send(params: dict, **_: Any) -> str:
    """Show an artifact on the newest session of another surface.

    The same delivery as a publish (`deliver._deliver`), aimed at a session the
    adapter says reported that surface. Not guaranteed: that the person is looking
    at that screen — only that a live client of it was routed the panel. When no
    session of that surface is connected the answer is ``ok: false`` with the
    ``url``, so the model gives the link rather than claiming a handoff.
    """
    ident = str(params.get("id", ""))
    if bad := _bad_id(ident):
        return json.dumps(bad)
    to = str(params.get("to") or "").strip().lower()
    if to not in surfaces.REGISTRY:
        return json.dumps({"ok": False,
                           "error": f"no surface called {to!r}; one of {', '.join(surfaces.REGISTRY)}"})
    found = _find(ident)
    if found.get("ok") is False:
        return json.dumps(found)
    row = found["artifact"]
    mime = str(row.get("mime") or "")
    target = surfaces.surface_for(to)
    need = surfaces.capability_for(mime)
    if not target.can(need) or int(row.get("size") or 0) > target.max_bytes:
        return json.dumps({"ok": False, "url": row["url"],
                           "error": f"the {to} cannot show {mime or 'that'} at {row.get('size', 0)} bytes; "
                                    "give the link instead"})
    on = _call("GET", "/api/surface/sessions?" + parse.urlencode({"surface": to}))
    sessions = on.get("sessions") if isinstance(on, dict) and on.get("ok") is not False else []
    if not sessions:
        return json.dumps({"ok": False, "url": row["url"],
                           "error": f"no {to} has reported in; open it and ask again, or give the link"})
    for sid in sessions:
        shown = deliver._deliver(str(sid), row)
        if shown.get("delivered"):
            return json.dumps({"ok": True, "to": to, "session_id": sid, "panel_id": shown["panel_id"],
                               "url": row["url"], "say": f"I have put it on the {to}."})
    return json.dumps({"ok": False, "url": row["url"],
                       "error": f"the {to} reported in earlier but none of its clients is connected now; "
                                "give the link instead"})


def handle_pin(params: dict, **_: Any) -> str:
    ident = str(params.get("id", ""))
    if bad := _bad_id(ident):
        return json.dumps(bad)
    pinned = bool(params.get("pinned", True))
    out = _call("POST", f"/api/artifacts/{ident}/pin?pinned={'true' if pinned else 'false'}")
    return json.dumps(out if out.get("ok") is False else {"ok": True, "artifact": out})


def handle_delete(params: dict, **_: Any) -> str:
    ident = str(params.get("id", ""))
    if bad := _bad_id(ident):
        return json.dumps(bad)
    return json.dumps(_call("DELETE", f"/api/artifacts/{ident}"))


def build_approval(name: str, params: dict) -> dict | None:
    """Only deletion is outward-facing enough to gate.

    Publishing and pinning add or keep; deleting takes something away that a
    person may be looking at on another surface, and it cannot be undone because
    the blob is gone.

    Returns the directive the hook consumes -- `action`, `message`, `rule_key` --
    not a bare string. An earlier version returned the message alone, which type-
    checks nowhere and would have let every delete straight through: the gate
    reads `.get("action")` off what it is handed.
    """
    if name != "artifact_delete":
        return None
    ident = str(params.get("id") or "?")
    return {
        "action": "approve",
        "message": f"Delete artifact {ident} from the store. The bytes are gone afterwards and "
                   "it may be on screen on another surface.",
        # Per artifact, so approving one deletion is not approving the store.
        "rule_key": f"artifacts:delete:{ident}",
    }


def handle_surface_capabilities(params: dict, **kwargs: Any) -> str:
    """Answer from the table rather than from exploration.

    The brief: "The reasoning budget spent deciding that is itself the bug." So
    this is a lookup, and the summary line is the same one the system prompt
    carries, to keep the tool and the prompt from disagreeing.
    """
    # No environment fallback for the active surface: it is not a setting, it
    # is a property of whoever is asking, and guessing it is how a terminal gets
    # told it can play video. Unnamed, it is the surface this session's client
    # reported on its prompt, narrowed by that report; with no report, the
    # conservative profile.
    named = str(params.get("surface") or "")
    reported = None if named else reported_surface(kwargs.get("session_id"))
    name = named or str((reported or {}).get("name") or "unknown")
    s = surfaces.surface_for(name, reported)
    return json.dumps({
        "ok": True,
        "surface": s.name,
        "reported": reported is not None,
        "can": {
            k: s.can(k)
            for k in ("images", "video", "audio", "documents", "interactive", "downloads", "notifications")
        },
        "max_bytes": s.max_bytes,
        "summary": surfaces.summary(name, reported),
    })


def handle_toolchain_check(params: dict, **_: Any) -> str:
    """What the image carries, so nothing probes with `command -v` in a reasoning block."""
    from . import toolchain

    found = toolchain.check()
    gaps = toolchain.missing(found)
    return json.dumps({
        "ok": not gaps,
        "tools": [
            {"name": f.name, "present": f.present, "version": f.version,
             "purpose": f.purpose, "required": f.required}
            for f in found
        ],
        "missing": [f.name for f in gaps],
        "advice": toolchain.gap_report(found),
    })
