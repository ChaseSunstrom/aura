"""The activity emitter: tool calls in, ``activity.*`` events out, redacted.

The media brief (Part 3) wants to *see* what the browser and the desktop are
doing: the page, the action, a picture, a trail of steps, on a tile that opens
by itself. Hermes gives the pieces to nobody but a plugin's tool-call hooks, so
this is where the view is made — ``pre_tool_call`` opens an activity the first
time a visual tool is called in a session, ``post_tool_call`` turns every call
into a step and, when the tool's own result carries a page or a picture, into a
frame. The wire shape is ``tests/contracts/activity-frames.json``; the constants
below are this plugin's copy of it (the plugin is copied into the image without
the repo) and ``tests/test_activity.py`` fails when they drift.

Three things are decided here and nowhere else:

* **The route.** Frames go out through aura-hud's store (``STORE.emit``), the
  same ``_emit`` that carries ``hud.panel.*`` — one path from a plugin to a
  session's HUD clients, not a second one. No aura-hud, no HUD clients, no
  frame: ``SEND`` answers False and nothing is kept.
* **The throttle.** One frame per ``THROTTLE_MS`` per activity and only when
  its digest changed; a step is never throttled. The brief's "start at one every
  500 ms, and only on change".
* **The redaction.** Every string leaves through :func:`scrub`: the values typed
  into a sensitive field (by the field's label in the last snapshot; an unknown
  field counts as sensitive), the audit log's redactor over the rest, and page
  text with a sensitive field's line cut down to its role, name and ref. The
  gate's wording — a password field's value never leaves the container — is
  ``tests/test_redaction.py``.

The picture. A result that holds one (``browser_vision``, ``desktop_click``'s
after-look, ``desktop_screen``'s file) carries it; after a browser step that
moves the page (:data:`PICTURED`) the page is photographed — the viewport,
through Hermes' own ``screenshot`` command on the same session — because a
text frame (the url, the title and the compact snapshot) was all a plain
``browser_navigate`` gave, and the owner read it as "the browser doesn't
actually show a browser, just html" (2026-09-25). A password field is drawn
masked by the browser itself; its typed value still never leaves as text. Not
covered: a page that paints after the step returns shows as it was then. Nor
does it know a delegated
child's parent: a child's tool calls are keyed by the child's session, which
has HUD clients only when a window opened it.

While one ``browser_exec`` call runs (T-1546). A call can drive a page for
minutes and the tile showed nothing until it returned, so a loop takes the
driven tab's picture through the task's CDP supervisor once a second
(:func:`_live_picture`) into the same frames, throttle and digest. It runs only
when the activity's start reached a HUD client, and stops when the call
returns, when a frame no longer reaches a client, after :data:`LIVE_FAILS`
empty captures in a row, at :data:`CALL_MAX_S`, or when the activity ends. Not
covered: a frame already in flight as the call returns may land just after the
call's own; the picture is of the tab browser_exec drove last, so a script
that opens a tab and never focuses it shows the old one.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from _shared import _audit
from _shared._approval_hook import summarise_args

_LOGGER = logging.getLogger(__name__)

#: The four events, in the contract's order.
EVENTS: list[str] = ["activity.start", "activity.frame", "activity.step", "activity.end"]
SURFACES: list[str] = ["browser", "desktop"]
#: Which tool opens which surface. The names are the approval matrix's
#: (``_shared/_matrix.py``); a tool absent here is not visual and is never seen.
TOOLS: dict[str, list[str]] = {
    "browser": ["browser_navigate", "browser_snapshot", "browser_click", "browser_type",
                "browser_press", "browser_scroll", "browser_back", "browser_dialog",
                "browser_vision", "browser_get_images", "browser_console", "browser_exec",
                "browser_cdp"],
    "desktop": ["computer_use", "desktop_screen", "desktop_windows", "desktop_click",
                "desktop_type", "desktop_key", "desktop_scroll", "desktop_focus",
                "desktop_open", "desktop_run"],
}
#: Seconds without a visual call before the activity ends on its own.
IDLE_END_S: float = 8.0
#: How long a visual call may run before its activity ends anyway. The idle clock starts
#: when a call returns (a vision read or a slow page outlasts IDLE_END_S); this bound is
#: only for a call whose result never comes back, so its tile does not stay live forever.
CALL_MAX_S: float = 300.0
#: The frame throttle: one per this many milliseconds, per activity, on change.
THROTTLE_MS: int = 500
#: Seconds between pictures while a browser_exec call runs (T-1546): one a second at most,
#: and none for a call that returns within the first.
LIVE_EVERY_S: float = 1.0
#: Empty captures in a row, after a first picture, at which the loop gives up on the call
#: (a closed tab, a browser that stopped answering): a dead source is not polled for minutes.
LIVE_FAILS: int = 3
#: Seconds the loop waits for a first picture. A cold browser has no supervisor for its first
#: seconds (found live: three empty captures ended the loop before a seven-second call's first
#: picture); a browser with none at all (a cloud provider's own) is let go after this.
LIVE_START_S: float = 20.0
#: The calls that get pictures while they run: the only browser tool that can run long.
LIVE_TOOLS: frozenset[str] = frozenset({"browser_exec"})
#: Each CDP request's bound in the loop: a wedged browser costs a frame, never the call.
LIVE_CDP_TIMEOUT_S: float = 3.0
#: The reduced capture: longest side, JPEG quality, and the byte cap past which
#: the picture is dropped and the frame goes out text-only.
MAX_SIDE_PX: int = 640
JPEG_QUALITY: int = 60
MAX_BYTES: int = 200_000
#: How much page text a frame carries: the tile reads a summary, not the page.
TEXT_MAX_CHARS: int = 2000
#: The mask, the audit log's, so a value reads the same on the tile and in the log.
MASK: str = _audit.MASK
#: A field whose role, name or snapshot line carries one of these as a whole
#: word is sensitive: what is typed into it is masked, and its line is cut.
SENSITIVE_LABELS: list[str] = ["password", "passcode", "passphrase", "pin", "secret", "token",
                               "otp", "one-time", "cvv", "ssn"]
END_STATUSES: list[str] = ["done", "failed", "stopped"]

_SENSITIVE_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in SENSITIVE_LABELS) + r")\b",
                           re.IGNORECASE)
#: An element ref as agent-browser prints it: ``[ref=@e3]`` or a bare ``@e3``.
_REF_RE = re.compile(r"(@e\d+)")
#: The part of a snapshot line worth keeping: ``- textbox "Password"``.
_ROLE_NAME_RE = re.compile(r'^(\s*[-*]?\s*[A-Za-z][\w-]*(?:\s+"[^"]*")?)')
_PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_PICTURE_MAX_BYTES = 10 * 1024 * 1024

#: One line per tool, from its arguments; ``{}`` fields read as "" when absent.
_ACTIONS: dict[str, str] = {
    "browser_navigate": "open {url}",
    "browser_press": "press {key}",
    "browser_scroll": "scroll {direction}",
    "browser_back": "go back",
    "browser_dialog": "dialog: {action}",
    "browser_snapshot": "read the page",
    "browser_vision": "look at the page",
    "browser_get_images": "list the page's images",
    "browser_console": "read the console",
    "browser_exec": "run browser automation",
    "browser_cdp": "run browser automation",
    "computer_use": "{action}",
    "desktop_screen": "look at the screen",
    "desktop_windows": "list the windows",
    "desktop_click": "click at ({x}, {y})",
    "desktop_key": "press {keys}",
    "desktop_scroll": "scroll",
    "desktop_focus": "focus {window}",
    "desktop_open": "open {app}{url}",
    "desktop_run": "run {command}",
}


class _Args(dict):
    """``format_map`` source: a missing or None field is an empty string."""

    def __missing__(self, key: str) -> str:
        return ""

    def __getitem__(self, key: str) -> str:
        value = super().__getitem__(key) if key in self else ""
        return "" if value is None else str(value)


@dataclass
class Frame:
    """What a tool result gave the view: any of these may be empty."""

    url: str = ""
    title: str = ""
    text: str = ""
    image: bytes = b""


@dataclass
class Activity:
    id: str
    surface: str
    started: float
    seq: int = 0
    steps: int = 0
    last_ok: bool = True
    last_frame_at: float = 0.0
    last_digest: str = ""
    url: str = ""
    title: str = ""
    #: Values typed into a sensitive field: masked wherever they recur.
    secrets: list[str] = field(default_factory=list)
    #: ref -> the snapshot line that named it, from the last page seen.
    refs: dict[str, str] = field(default_factory=dict)
    timer: Optional[threading.Timer] = None
    #: Whether ``activity.start`` reached a HUD client: no client, no live pictures.
    delivered: bool = True
    #: Set to stop the running call's live loop (T-1546); None when no loop runs.
    live: Optional[threading.Event] = None


#: Live activities by the session Hermes hands the hooks.
_LIVE: dict[str, Activity] = {}
_LOCK = threading.RLock()


# --------------------------------------------------------------------------- #
# The route out
# --------------------------------------------------------------------------- #
def _hud_module():
    """aura-hud's loaded ``hud`` module (``hermes_plugins.aura_hud.hud`` in the
    gateway, ``aura_hud.hud`` under the test loader), or None when it is not
    loaded — in which case there is nothing that could be listening."""
    for name, mod in list(sys.modules.items()):
        if name.endswith("aura_hud.hud") and hasattr(mod, "STORE"):
            return mod
    return None


def _send(session_id: str, event: str, payload: dict) -> bool:
    mod = _hud_module()
    if mod is None:
        return False
    try:
        return bool(mod.STORE.emit(mod._state_key(session_id), event, payload))
    except Exception:  # a wedged transport must not fail the tool call
        _LOGGER.exception("activity emit failed: event=%s", event)
        return False


#: The one way out. Looked up per call so a test can record frames without a
#: gateway; production is :func:`_send`, aura-hud's route.
SEND: Callable[[str, str, dict], bool] = _send

#: The browser steps after which the page is photographed: the ones that move
#: or change it. A read (``browser_snapshot``, ``browser_console``) is not.
PICTURED: frozenset[str] = frozenset({
    "browser_navigate", "browser_click", "browser_type", "browser_press",
    "browser_scroll", "browser_back", "browser_dialog",
    # Hermes v0.23 drives the browser only through browser_exec (T-1519); its code may move the
    # page or only read it, and a picture of an unmoved page costs one capture.
    "browser_exec",
})


_WARNED_MOVED = False


def _screenshot(task_id: str, session: str = "") -> bytes:
    """The browser session's viewport as PNG bytes, or ``b""`` when it cannot be had.

    Hermes v0.23 removed the private browser command this used (T-1519), so the picture comes
    from ``browser_exec`` itself, called in-process with its own ``capture_screenshot`` helper on
    the same task and session: it follows that session's route (local, sidecar or cloud) and no
    hook or approval runs for it. Not covered: a session the step ended (the call then starts a
    fresh one and photographs a blank page).
    """
    try:
        # A named import, not an attribute read: a Hermes bump that moves it fails the import
        # probe (scripts/verify/hermes-imports.py) instead of every frame silently (T-0732).
        from tools.browser_use_cli import browser_exec  # Hermes' own; absent outside the container
    except Exception:  # noqa: BLE001
        global _WARNED_MOVED
        if not _WARNED_MOVED:  # once per process, not once per frame
            _WARNED_MOVED = True
            _LOGGER.warning("activity: Hermes' browser_exec moved; frames go out without a picture")
        return b""
    path = Path(tempfile.gettempdir()) / f"jarvis-activity-{uuid.uuid4().hex}.png"
    try:
        browser_exec(f"capture_screenshot({str(path)!r})", session=session, timeout_s=15,
                     task_id=task_id or "default")
        return path.read_bytes() if path.is_file() else b""
    except Exception:  # noqa: BLE001 — a missing picture is a text frame, never a failed step
        _LOGGER.debug("activity: screenshot failed", exc_info=True)
        return b""
    finally:
        path.unlink(missing_ok=True)


#: Replaced by the tests; production is :func:`_screenshot`.
PICTURE: Callable[..., bytes] = _screenshot


def _live_picture(task_id: str) -> bytes:
    """The driven tab's viewport as JPEG bytes, taken mid-call, or ``b""``.

    Not :func:`_screenshot`: that runs a second ``browser_exec`` on the session the running
    call holds. This goes through the task's CDP supervisor, Hermes' public seam for an
    in-process plugin, on a session attached to the tab browser_exec drove last and detached
    again. The driven tab is the supervisor's private ``_driven_target_id``; a Hermes that
    renames it gets the supervisor's default page instead of no picture. Never raises. Not
    covered: a browser with no supervisor (a cloud provider's own), which gets no live frame.
    """
    try:
        # A named import, so a Hermes bump that moves it fails the import probe (T-0732).
        from tools.browser_supervisor import SUPERVISOR_REGISTRY  # Hermes' own
    except Exception:  # noqa: BLE001 — outside the container
        return b""
    key = task_id or "default"  # browser_exec's own key for its supervisor
    attached = ""
    cdp: Any = None
    try:
        sup = SUPERVISOR_REGISTRY.get(key)
        if sup is None:
            return b""
        cdp = SUPERVISOR_REGISTRY.capture(key, timeout=LIVE_CDP_TIMEOUT_S)
        session = str(getattr(cdp, "page_session_id", "") or "")
        target = getattr(sup, "_driven_target_id", None)
        if target:
            reply = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True},
                             timeout=LIVE_CDP_TIMEOUT_S)
            attached = str((reply.get("result") or {}).get("sessionId") or "")
            session = attached or session
        if not session:
            return b""
        shot = cdp.call("Page.captureScreenshot", {"format": "jpeg", "quality": JPEG_QUALITY},
                        session_id=session, timeout=LIVE_CDP_TIMEOUT_S)
        data = str((shot.get("result") or {}).get("data") or "")
        return base64.b64decode(data) if data else b""
    except Exception:  # noqa: BLE001 — a missing picture is a skipped frame
        _LOGGER.debug("activity: live picture failed", exc_info=True)
        return b""
    finally:
        if attached and cdp is not None:
            try:  # an attached session left behind would outlive every frame
                cdp.call("Target.detachFromTarget", {"sessionId": attached},
                         timeout=LIVE_CDP_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("activity: detach failed", exc_info=True)


#: Replaced by the tests; production is :func:`_live_picture`.
LIVE_PICTURE: Callable[[str], bytes] = _live_picture


def enabled() -> bool:
    """``activity.enabled`` (``ACTIVITY_ENABLED``), read per call so a change lands
    without a restart. Off when the settings layer cannot answer: a view that
    streams screenshots must fail closed, not open."""
    try:
        from aura_schema import from_env

        return bool(from_env().activity_enabled)
    except Exception:  # noqa: BLE001 — a missing schema or a bad variable
        _LOGGER.debug("activity: settings unreadable, treating as disabled", exc_info=True)
        return False


def surface_of(tool_name: str) -> str:
    for surface, names in TOOLS.items():
        if tool_name in names:
            return surface
    return ""


def _now_iso(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def redact_page_text(text: str) -> str:
    """Page text with every sensitive field's line cut to its role, name and ref.

    ``- textbox "Password" [ref=@e3] value="hunter2"`` becomes
    ``- textbox "Password" [ref=@e3] [redacted]``: whatever a snapshot format
    puts after the name — a value, a placeholder, a description — is gone, and
    the ref survives so the trail can still name the field. The rest of the
    text passes the audit log's redactor. Not covered: a value the format puts
    on the *next* line; the typed secrets list (:func:`scrub`) is the catch for
    that, since a value the model typed is one it knew.
    """
    out: list[str] = []
    for line in text.splitlines():
        if _SENSITIVE_RE.search(line):
            head = _ROLE_NAME_RE.match(line)
            kept = head.group(1) if head else line.split(":", 1)[0].split("=", 1)[0]
            ref = _REF_RE.search(line)
            line = " ".join(p for p in (kept.rstrip(), f"[ref={ref.group(1)}]" if ref else "", MASK) if p)
        out.append(line)
    return _audit.redact_text("\n".join(out))


def scrub(act: Activity, text: str) -> str:
    """Every string leaves through here: the activity's typed secrets, then the redactor."""
    for secret in act.secrets:
        if secret:
            text = text.replace(secret, MASK)
    return _audit.redact_text(text)


def _learn_refs(act: Activity, text: str) -> None:
    for line in text.splitlines():
        for ref in _REF_RE.findall(line):
            act.refs[ref] = line


def _target(act: Activity, args: dict) -> str:
    """The field a click or a type names: its snapshot name when known, else the ref."""
    ref = str(args.get("ref") or args.get("selector") or "")
    line = act.refs.get(ref, "")
    head = _ROLE_NAME_RE.match(line) if line else None
    if head and '"' in head.group(1):
        return head.group(1).split('"', 1)[1].rsplit('"', 1)[0] or ref
    return ref


def _sensitive_target(act: Activity, args: dict) -> bool:
    """Unknown counts as sensitive: a field whose label was never seen (the
    snapshot went by before this plugin loaded) is one whose value must not
    leave on the guess that it is a search box."""
    ref = str(args.get("ref") or args.get("selector") or "")
    line = act.refs.get(ref)
    if line is None:
        return True
    return bool(_SENSITIVE_RE.search(line))


def describe(act: Activity, tool_name: str, args: dict) -> tuple[str, Optional[str]]:
    """The step's one line, and the typed value to keep as a secret when there is one."""
    if tool_name == "browser_click":
        return f"click {_target(act, args)}".rstrip(), None
    if tool_name == "browser_type":
        text = str(args.get("text") or "")
        if _sensitive_target(act, args):
            return f"type into {_target(act, args)}: {MASK}", text or None
        return f"type into {_target(act, args)}: {_clip(text, 60)}", None
    if tool_name == "desktop_type":
        return f"type: {_clip(str(args.get('text') or ''), 60)}", None
    template = _ACTIONS.get(tool_name)
    if template is None:
        return summarise_args(args) or tool_name, None
    return " ".join(template.format_map(_Args(args)).split()) or tool_name, None


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #
def _decode_data_url(url: str) -> bytes:
    if not url.startswith("data:image/") or "," not in url:
        return b""
    try:
        return base64.b64decode(url.split(",", 1)[1])
    except (ValueError, TypeError):
        return b""


def _read_picture(path: Any) -> bytes:
    """A picture a tool result names, if it is one and lives under HERMES_HOME."""
    if not isinstance(path, str) or not path.lower().endswith(_PICTURE_SUFFIXES):
        return b""
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
    try:
        file = Path(path).resolve()
        if home not in file.parents or file.stat().st_size > _PICTURE_MAX_BYTES:
            return b""
        return file.read_bytes()
    except OSError:
        return b""


def extract_frame(tool_name: str, result: Any) -> Frame:
    """What a tool result gives the view. Only the tool's own words: a multimodal
    envelope's picture, a JSON result's url/title/snapshot and the file it names,
    a plain-text snapshot from the two tools that print one."""
    frame = Frame()
    if isinstance(result, dict) and result.get("_multimodal"):
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "image_url":
                frame.image = _decode_data_url(str((part.get("image_url") or {}).get("url") or ""))
        raw_meta = result.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        if not frame.image:
            frame.image = _read_picture(meta.get("screenshot_path"))
        return frame
    if not isinstance(result, str):
        return frame
    data: Any = None
    if result.lstrip().startswith("{"):
        try:
            data = json.loads(result)
        except ValueError:
            data = None
    if isinstance(data, dict):
        frame.url = str(data.get("url") or "")
        frame.title = str(data.get("title") or "")
        if isinstance(data.get("snapshot"), str):
            frame.text = data["snapshot"]
        frame.image = _read_picture(data.get("screenshot_path") or data.get("path"))
    elif tool_name in ("browser_navigate", "browser_snapshot") and result.strip():
        frame.text = result
    return frame


def shrink(image: bytes) -> Optional[str]:
    """The reduced capture as a JPEG data URL, or None when it cannot be made small.

    None rather than the original: a full-page PNG is what the brief says must
    never stream ("never at full page size"), and a picture that stays over the
    cap at quality 40 is one the tile does without. Pillow is in the image
    (the desktop plugin draws with it); without it every frame is text-only.
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 — Pillow absent
        return None
    try:
        with Image.open(io.BytesIO(image)) as opened:
            picture = opened.convert("RGB")
            picture.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
            for quality in (JPEG_QUALITY, 40):
                buf = io.BytesIO()
                picture.save(buf, "JPEG", quality=quality, optimize=True)
                if buf.tell() <= MAX_BYTES:
                    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 — not a picture after all
        _LOGGER.debug("activity: picture not usable", exc_info=True)
    return None


# --------------------------------------------------------------------------- #
# The activity
# --------------------------------------------------------------------------- #
def _open(session_id: str, surface: str, tool_name: str, args: dict, now: float) -> Activity:
    act = Activity(id="a_" + uuid.uuid4().hex[:8], surface=surface, started=now)
    _LIVE[session_id] = act
    intent, _ = describe(act, tool_name, args)
    act.delivered = bool(SEND(session_id, "activity.start", {
        "id": act.id, "surface": surface, "tool": tool_name,
        "intent": scrub(act, intent), "started_at": _now_iso(now),
    }))
    return act


def _stop_live(act: Activity) -> None:
    if act.live is not None:
        act.live.set()
        act.live = None


def _end(session_id: str, act: Activity, status: str, now: float) -> None:
    _stop_live(act)
    if act.timer is not None:
        act.timer.cancel()
        act.timer = None
    if _LIVE.get(session_id) is act:
        del _LIVE[session_id]
    SEND(session_id, "activity.end", {
        "id": act.id, "steps": act.steps, "status": status, "ended_at": _now_iso(now),
    })


def _idle(session_id: str, act: Activity) -> None:
    with _LOCK:
        if _LIVE.get(session_id) is not act:
            return
        _end(session_id, act, "done" if act.last_ok else "failed", time.time())


def _arm(session_id: str, act: Activity, after_s: float | None = None) -> None:
    if act.timer is not None:
        act.timer.cancel()
    act.timer = threading.Timer(IDLE_END_S if after_s is None else after_s, _idle,
                                args=(session_id, act))
    act.timer.daemon = True
    act.timer.start()


def _maybe_frame(session_id: str, act: Activity, frame: Frame, now: float) -> Optional[bool]:
    """One frame, if the page changed and the throttle allows: None when none went out,
    else whether it reached a HUD client."""
    if not (frame.image or frame.text or frame.url or frame.title):
        return None
    if frame.url:
        act.url = frame.url
    if frame.title:
        act.title = frame.title
    text = scrub(act, redact_page_text(frame.text))[:TEXT_MAX_CHARS] if frame.text else ""
    url, title = scrub(act, act.url), scrub(act, act.title)
    digest = hashlib.sha1(
        "\x1f".join((url, title, text)).encode() + hashlib.sha1(frame.image).digest()
    ).hexdigest()
    if digest == act.last_digest or (now - act.last_frame_at) * 1000 < THROTTLE_MS:
        return None
    act.seq += 1
    act.last_frame_at = now
    act.last_digest = digest
    return bool(SEND(session_id, "activity.frame", {
        "id": act.id, "seq": act.seq, "url": url, "title": title,
        "image": shrink(frame.image) if frame.image else None,
        "text": text or None, "at": _now_iso(now),
    }))


def _live_loop(session_id: str, act: Activity, task_id: str, stop: threading.Event) -> None:
    """Pictures of the driven tab while one call runs; see the module's T-1546 note."""
    started = time.time()
    deadline = started + CALL_MAX_S
    fails = 0
    seen = False
    while not stop.wait(LIVE_EVERY_S) and time.time() < deadline:
        with _LOCK:
            if _LIVE.get(session_id) is not act:
                return
        image = LIVE_PICTURE(task_id)  # outside the lock: other sessions' hooks wait on it
        if not image:
            fails += 1
            if (seen and fails >= LIVE_FAILS) or (not seen and time.time() - started >= LIVE_START_S):
                return
            continue
        fails = 0
        seen = True
        with _LOCK:
            if stop.is_set() or _LIVE.get(session_id) is not act:
                return
            if _maybe_frame(session_id, act, Frame(image=image), time.time()) is False:
                return  # the client went away: stop taking pictures nobody sees


def _start_live(session_id: str, act: Activity, task_id: str) -> None:
    _stop_live(act)
    if not act.delivered:
        return
    act.live = threading.Event()
    threading.Thread(target=_live_loop, args=(session_id, act, task_id, act.live),
                     name=f"activity-live-{act.id}", daemon=True).start()


def pre_tool_call(tool_name: str, args: Any = None, task_id: str = "", **kwargs: Any) -> None:
    """Open the activity as the first visual call starts; never a directive."""
    surface = surface_of(tool_name)
    session_id = str(kwargs.get("session_id") or "")
    if not surface or not session_id or not enabled():
        return None
    shaped = args if isinstance(args, dict) else {}
    with _LOCK:
        now = time.time()
        act = _LIVE.get(session_id)
        if act is not None and act.surface != surface:
            # The desktop after the browser is a new activity, not a step of the old.
            _end(session_id, act, "done" if act.last_ok else "failed", now)
            act = None
        if act is None:
            act = _open(session_id, surface, tool_name, shaped, now)
        # The call is in flight: the idle clock starts when it returns (T-0456).
        _arm(session_id, act, CALL_MAX_S)
        if tool_name in LIVE_TOOLS:
            _start_live(session_id, act, str(task_id or ""))
    return None


def post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, task_id: str = "",
                   session_id: str = "", duration_ms: int = 0, status: str = "",
                   error_type: str = "", error_message: str = "", **kwargs: Any) -> None:
    """A step for every visual call, a frame when its result shows something."""
    del duration_ms, error_type, kwargs
    surface = surface_of(tool_name)
    if not surface or not session_id or not enabled():
        return
    shaped = args if isinstance(args, dict) else {}
    with _LOCK:  # the call returned: its live pictures end before its own picture is taken
        running = _LIVE.get(session_id)
        if running is not None:
            _stop_live(running)
    ok = (status or "ok") == "ok" and not error_message
    frame = extract_frame(tool_name, result)
    # Before the lock: a capture takes a moment, and other sessions' hooks wait on it.
    if ok and not frame.image and tool_name in PICTURED:
        frame.image = PICTURE(task_id, str(shaped.get("session") or ""))
    with _LOCK:
        now = time.time()
        act = _LIVE.get(session_id)
        if act is None or act.surface != surface:
            # No pre hook saw this call (the plugin loaded mid-turn): open now.
            if act is not None:
                _end(session_id, act, "done" if act.last_ok else "failed", now)
            act = _open(session_id, surface, tool_name, shaped, now)
        action, secret = describe(act, tool_name, shaped)
        if secret:
            act.secrets.append(secret)
        act.steps += 1
        act.seq += 1
        act.last_ok = ok
        step = {"id": act.id, "seq": act.seq, "tool": tool_name, "action": scrub(act, action),
                "ok": ok, "at": _now_iso(now)}
        if not ok:
            step["error"] = scrub(act, _clip(str(error_message or "failed"), 200))
        SEND(session_id, "activity.step", step)
        _maybe_frame(session_id, act, frame, now)
        # After the frame: the refs a type call needs come from the page seen
        # before it, and this result's own page names the next call's targets.
        if frame.text:
            _learn_refs(act, frame.text)
        _arm(session_id, act)


def reset() -> None:
    """Forget every live activity (tests)."""
    with _LOCK:
        for act in _LIVE.values():
            _stop_live(act)
            if act.timer is not None:
                act.timer.cancel()
        _LIVE.clear()
