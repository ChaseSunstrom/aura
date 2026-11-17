"""The HUD panel surface — state, validation, gateway emit, tools and command.

A *panel* is a typed card a HUD client (web console, Android) shows, updates and
dismisses. The agent summons panels with the ``hud_*`` tools; a late-joining or
voice/click client drives them through the ``/hud`` slash command. Every client
of a session renders the same surface from the same three events, so the panel
state lives here (per session, in memory) and is replayed to ``list``.

A panel shown with ``keep`` is the owner's: it is saved to
``$HERMES_HOME/plugin-data/jarvis-hud/kept.json``, listed in every session, shown
and updated on every live screen, and outlives ttl, eviction, the wildcard
dismiss and a restart, until it is dismissed by its id.

The prose contract is ``docs/hermes-hud-protocol.md`` ("Panels …"); the machine
contract both this emitter and the app renderers read is
``tests/contracts/hud-panels.json``. The constants below (EVENTS, ORIGINS,
KINDS, REQUIRED_SHOW_FIELDS, DISMISS_ALL_ID) are this plugin's copy of that
contract so the plugin works when copied into ``~/.hermes/plugins/`` away from
the repo; ``tests/test_aura_hud.py`` reads the JSON and fails if they drift.

Routing the events is the one subtle part. A plugin tool handler is called with
the *agent's* ``session_id`` (``registry.dispatch(..., session_id=…)``), which for
a ``hermes serve`` session is the durable ``session_key`` — not the short id the
app got from ``session.create`` and not the key ``tui_gateway.server._sessions``
routes by. ``_emit(event, sid, payload)`` writes to ``_sessions[sid]["transport"]``,
so we reverse-map the durable key to that short id (``_resolve_tui_sid``). The
``/hud`` command instead receives the short id (prefixed ``@<sid>``, because
plugin commands are not handed the session), so both handlers first canonicalise
to the durable key (``_state_key``) — otherwise a panel a tool showed would be
filed under a different key than the one ``/hud @<sid> list`` looks up, and the
list would come back empty.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from _shared import _speaker
from _shared._approval_hook import split_session
from _shared._jsonstore import write_json

_LOGGER = logging.getLogger(__name__)

#: The three panel events, in the order the contract lists them.
EVENTS: list[str] = ["hud.panel.show", "hud.panel.update", "hud.panel.dismiss"]
#: Who summoned a panel: a tool call, a person (voice/click), or an event.
ORIGINS: list[str] = ["agent", "user", "system"]
#: The typed cards a client can render. An unknown kind is rejected, not shown,
#: so a typo never reaches an app that would draw an empty card for it.
KINDS: list[str] = [
    "text", "table", "image", "chart", "camera", "code", "terminal", "web",
    "device", "telemetry", "goal", "approval", "clarify", "notice",
    # The media kinds: a published artifact (aura-artifacts) the app plays or
    # opens by url + mime. Appended, never reordered -- the Android mirror
    # compares the list element-wise against the contract.
    "video", "audio", "document",
]
#: Fields every ``hud.panel.show`` payload carries; a client keys off all of them.
REQUIRED_SHOW_FIELDS: list[str] = ["id", "kind", "title", "payload", "origin", "created_at", "rev"]
#: The id that dismisses every panel of a session at once.
DISMISS_ALL_ID: str = "*"
#: The spoken line (the owner, 2026-09-10: "can you say it" answered with a file
#: path nobody could play). Not a panel and not in the panels contract: it is
#: one event, ``{"text", "voice"}``, that every surface speaks in its own way,
#: whatever mode the person is in -- they asked to hear it.
SAY_EVENT: str = "hud.say"
#: The longest line one ``hud_say`` carries: a paragraph, not an essay. A reply
#: is already spoken by the surface as it streams; this is for the one thing
#: the person asked to hear.
SAY_MAX_CHARS: int = 2000
#: The slash command name.
COMMAND_NAME: str = "hud"
#: Toolset the four tools live under (a plugin toolset key; enabled by default).
TOOLSET: str = "hud"
#: The audience every session of the owner's belongs to (``HudStore.audience``). Not a session
#: id: an id holds no space.
OWNER_SCREENS: str = "the owner's screens"
#: Oldest panels past this count are dismissed to make room — a runaway loop
#: that shows a panel a turn must not grow the surface without bound.
MAX_PANELS: int = 24
#: Panels the owner keeps at once. Each is a tab on every screen, so past a
#: dozen a 13th is refused with the titles to choose from, never dropped silently.
MAX_KEPT: int = 12
#: How often the process with the screens re-reads the kept file. The cron
#: scheduler runs in the ``gateway`` process and the screens hang off the
#: ``dashboard`` one, so a cron update to a kept panel reaches them through the file.
KEPT_POLL_S: float = 5.0

#: Required payload fields per kind, from the doc's "Kinds and payloads" table.
#: ``telemetry`` is special (gpus *or* metrics) and handled in :func:`validate`;
#: ``goal``/``approval``/``clarify`` are system-originated pass-throughs whose
#: shape is owned by the event that raised them, so only "must be an object"
#: is enforced for them here.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "text": ["markdown"],
    "table": ["columns", "rows"],
    "image": ["url", "alt"],
    "chart": ["series"],
    "camera": ["camera", "url", "taken_at"],
    "code": ["language", "text"],
    "terminal": ["command", "output"],
    "web": ["url"],
    "device": ["entity_id", "name", "state", "attributes"],
    "telemetry": [],
    "goal": [],
    "approval": [],
    "clarify": [],
    "notice": ["level", "text"],
    # `mime` is required, not inferred from the url: an artifact url is a
    # digest with no extension, so without it a player would sniff -- and a
    # document body could not tell a PDF it can embed from an HTML file the
    # adapter serves as a download.
    "video": ["url", "mime"],
    "audio": ["url", "mime"],
    "document": ["url", "mime"],
}

#: Light type checks on the required fields whose shape a renderer depends on,
#: so a string handed where a list is drawn is caught here, not in the app.
_TYPE_CHECKS: dict[str, list[tuple[str, type | tuple[type, ...]]]] = {
    "text": [("markdown", str)],
    "table": [("columns", list), ("rows", list)],
    "image": [("url", str), ("alt", str)],
    "chart": [("series", list)],
    "camera": [("camera", str), ("url", str), ("taken_at", str)],
    "code": [("language", str), ("text", str)],
    "terminal": [("command", str), ("output", str)],
    "web": [("url", str)],
    "device": [("entity_id", str), ("name", str), ("state", str), ("attributes", dict)],
    "notice": [("level", str), ("text", str)],
    "video": [("url", str), ("mime", str)],
    "audio": [("url", str), ("mime", str)],
    "document": [("url", str), ("mime", str)],
}

_ID_RE = re.compile(r"^p_[0-9a-f]{8}$")


class ValidationError(ValueError):
    """A panel that cannot be shown as asked (bad kind or missing field)."""


def validate(kind: str, payload: Any) -> None:
    """Raise :class:`ValidationError` unless ``kind``/``payload`` fit the contract."""
    if kind not in KINDS:
        raise ValidationError(
            f"unknown panel kind {kind!r}; valid kinds: {', '.join(KINDS)}"
        )
    if not isinstance(payload, dict):
        raise ValidationError(f"payload for kind {kind!r} must be a JSON object")
    if kind == "telemetry":
        # Two accepted shapes: the gpu_status result (gpus) or generic metrics.
        if not (isinstance(payload.get("gpus"), list) or isinstance(payload.get("metrics"), list)):
            raise ValidationError(
                "telemetry payload needs a 'gpus' list or a 'metrics' list"
            )
        return
    missing = [f for f in REQUIRED_FIELDS.get(kind, []) if payload.get(f) is None]
    if missing:
        raise ValidationError(
            f"payload for kind {kind!r} is missing required field(s): {', '.join(missing)}"
        )
    for name, typ in _TYPE_CHECKS.get(kind, []):
        if name in payload and not isinstance(payload[name], typ):
            want = typ.__name__ if isinstance(typ, type) else "/".join(t.__name__ for t in typ)
            raise ValidationError(f"panel field {name!r} for kind {kind!r} must be {want}")


def _new_id() -> str:
    """A fresh panel id ``p_<8 hex>``."""
    return "p_" + uuid.uuid4().hex[:8]


def _now_iso() -> str:
    """UTC timestamp shaped like the contract's examples (``…Z``, no micros)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Gateway routing
# --------------------------------------------------------------------------- #
def _sessions_table() -> Optional[dict]:
    """The live ``tui_gateway.server._sessions`` map, or None off the gateway.

    Imported lazily so this module (and the tests) load without Hermes on the
    path — the same rule the sibling plugins follow.
    """
    try:
        from tui_gateway.server import _sessions
    except Exception:
        return None
    return _sessions


def _state_key(raw: Optional[str]) -> str:
    """Canonical per-session state key shared by the tools and the command.

    A tool handler is given the durable ``session_key``; the ``/hud`` command is
    given the short id the app holds. Map both to the durable key so a panel one
    surface created is the one the other lists. Off the gateway (tests), the id
    is its own key.
    """
    raw = raw or ""
    sessions = _sessions_table()
    if sessions is not None:
        entry = sessions.get(raw)
        if isinstance(entry, dict) and entry.get("session_key"):
            return str(entry["session_key"])
    return raw


def _resolve_tui_sid(state_key: str) -> Optional[str]:
    """The ``_sessions`` key ``_emit`` routes by, for this session, or None.

    ``_emit(event, sid, payload)`` writes to ``_sessions[sid]["transport"]``, so
    the sid must be the short id, not the durable key the tools carry. Try the
    key directly (already a short id, or a session keyed by its durable id), then
    match a session whose ``session_key`` equals it. None means no live HUD
    client for this session (e.g. the api_server platform without a dashboard).
    """
    sessions = _sessions_table()
    if sessions is None or not state_key:
        return None
    if state_key in sessions:
        return state_key
    for sid, entry in list(sessions.items()):
        if isinstance(entry, dict) and entry.get("session_key") == state_key:
            return sid
    return None


def _live_sids() -> list[str]:
    """Every session with a HUD client right now (the short ids ``_emit`` routes by)."""
    sessions = _sessions_table()
    return list(sessions.keys()) if sessions else []


def _kept_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "plugin-data" / "jarvis-hud" / "kept.json"


def _gateway_emit(state_key: str, event: str, payload: dict) -> bool:
    """Emit one panel event to a session's HUD clients; True when it was routed.

    False (not raised) when there is no TUI session to route to, so the caller
    keeps state and reports ``delivered: false`` rather than failing the tool.
    """
    sid = _resolve_tui_sid(state_key)
    if not sid:
        return False
    try:
        from tui_gateway.server import _emit
    except Exception:
        return False
    try:
        _emit(event, sid, payload)
        return True
    except Exception:  # a wedged transport must not crash the tool call
        _LOGGER.exception("hud emit failed: event=%s", event)
        return False


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class HudStore:
    """Per-session panel state, guarded by one lock.

    ``emitter(state_key, event, payload) -> bool`` is injected so the tests can
    record frames without a gateway; production uses :func:`_gateway_emit`.
    ``live()`` names every session with a screen, for a kept panel's events.
    Without ``kept_path`` kept panels live in memory only. With ``watch`` the
    first ``list`` starts a thread that pushes another process's changes to the
    kept file to this process's screens (:meth:`refresh_kept`).

    Not covered: two processes writing the kept file in the same instant; the
    later write wins.
    """

    def __init__(
        self,
        emitter: Optional[Callable[[str, str, dict], bool]] = None,
        max_panels: int = MAX_PANELS,
        *,
        kept_path: Optional[Path] = None,
        live: Optional[Callable[[], list[str]]] = None,
        owner: Optional[Callable[[str], bool]] = None,
        watch: bool = False,
    ) -> None:
        self._panels: dict[str, "OrderedDict[str, dict]"] = {}
        self._timers: dict[tuple[str, str], threading.Timer] = {}
        self._lock = threading.RLock()
        self._emit = emitter or _gateway_emit
        self._max = max_panels
        self._live = live or _live_sids
        # Late-bound: the module's STORE is built before _owner is defined.
        self._owner = owner or (lambda sid: _owner(sid))
        self._kept_path = kept_path
        self._kept: "OrderedDict[str, dict]" = OrderedDict()
        self._kept_raw: Optional[str] = None
        self._watch = watch
        self._watcher: Optional[threading.Thread] = None
        self._sync_kept()

    def _sync_kept(self) -> Optional[dict]:
        """Re-read the kept file if it changed since we last saw it; the old panels, or None.

        Compared by content, not mtime: two writes inside one coarse filesystem tick share an
        mtime, and the other process's change was missed (the cross-process test caught it)."""
        if self._kept_path is None:
            return None
        try:
            raw = self._kept_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raw = ""
        except OSError:
            return None  # a busy or unreadable file is not "no kept panels": keep them up
        if raw == self._kept_raw:
            return None
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            # Half a file would read as none kept, dismiss every tab and let the next save
            # wipe them; keep ours, and that save rewrites the file whole.
            _LOGGER.warning("hud kept-panel file is not JSON; keeping the panels already up")
            self._kept_raw = raw
            return None
        old, self._kept_raw = self._kept, raw
        self._kept = OrderedDict(
            (r["id"], r) for r in ((data.get("panels") if isinstance(data, dict) else None) or [])
            if isinstance(r, dict) and r.get("kind") in KINDS and isinstance(r.get("id"), str)
        )
        return old

    def _save_kept(self) -> None:
        if self._kept_path is not None:
            try:
                write_json(self._kept_path, {"panels": list(self._kept.values())})
            except OSError:
                # The screens already have the change; only a restart loses it.
                _LOGGER.exception("hud kept-panel save failed")
                return
            try:
                self._kept_raw = self._kept_path.read_text(encoding="utf-8")
            except OSError:
                self._kept_raw = None

    def refresh_kept(self) -> None:
        """Show this process's screens what another process changed in the kept file."""
        with self._lock:
            old = self._sync_kept()
            if old is None:
                return
            shown = [dict(r) for pid, r in self._kept.items() if old.get(pid, {}).get("rev") != r["rev"]]
            gone = [pid for pid in old if pid not in self._kept]
        for record in shown:
            self._broadcast("hud.panel.show", record)
        for pid in gone:
            self._broadcast("hud.panel.dismiss", {"id": pid})

    def _watch_kept(self) -> None:
        while True:
            time.sleep(KEPT_POLL_S)
            try:
                self.refresh_kept()
            except Exception:  # a background poll must never die on one bad read
                _LOGGER.exception("hud kept-panel refresh failed")

    def _broadcast(self, event: str, payload: dict) -> bool:
        """One kept panel's event to every live screen; True when any got it."""
        return any([bool(self._emit(sid, event, payload)) for sid in self._live()])

    def _others(self, state_key: str) -> list[str]:
        """The owner's other screens: every live one but the asker's whose last word was the owner's.

        A phone or car voice turn runs in a session of its own, and the phone's home and the
        car page are the console on sessions of theirs, so a panel sent only to the asker
        never reached the screen the owner was looking at (T-0785). Only owner to owner: a
        member's or a guest's turn reaches nobody else, and their screens never get the
        owner's (a panel may be the owner's mail). A screen nobody has spoken at is the key
        holder's, the owner's (``_speaker``).
        Does not cover: a screen that joins later (it lists its own session's panels only),
        or an adapter too slow to answer, where the lookup fails open to the owner.
        """
        # The adapter records speakers by the short id a screen holds; the store's key is the
        # durable one, which it never saw and so would always read as the owner's.
        asker = _resolve_tui_sid(state_key) or state_key
        if not self._owner(asker):
            return []
        return [sid for sid in self._live() if sid not in {state_key, asker} and self._owner(sid)]

    def audience(self, state_key: str) -> str:
        """Who a frame shared from this session reaches, as one key: OWNER_SCREENS for every
        session whose last word was the owner's, the session's own key for anyone else's.

        For a tool that remembers what it drew: kept per session, a list shown from the voice
        client's session was unknown to a change made from the console's (T-1471). The same lookup
        as ``_others``, with its limits: an adapter that cannot say who spoke reads as the
        owner. Does not cover: a session whose speaker changes later; the key is who spoke
        last when it was asked.
        """
        asker = _resolve_tui_sid(state_key) or state_key
        return OWNER_SCREENS if self._owner(asker) else state_key

    def share(self, state_key: str, event: str, payload: dict) -> bool:
        """One frame to the asking session and the owner's other screens; True when any got it."""
        delivered = [bool(self._emit(state_key, event, payload))]
        delivered += [bool(self._emit(sid, event, payload)) for sid in self._others(state_key)]
        return any(delivered)

    # -- helpers (call with the lock held) --------------------------------- #
    def _cancel_timer(self, key: str, pid: str) -> None:
        timer = self._timers.pop((key, pid), None)
        if timer is not None:
            timer.cancel()

    def _schedule_ttl(self, key: str, pid: str, ttl_s: float) -> None:
        self._cancel_timer(key, pid)
        # daemon so a pending dismiss never holds up interpreter shutdown.
        timer = threading.Timer(ttl_s, self._ttl_fire, args=(key, pid))
        timer.daemon = True
        self._timers[(key, pid)] = timer
        timer.start()

    def _ttl_fire(self, key: str, pid: str) -> None:
        # The panel may already be gone (dismissed, evicted, replaced); dismiss
        # tolerates that and emits nothing for an id it does not hold.
        try:
            self.dismiss(key, pid, origin="system")
        except Exception:  # a background timer must never surface an exception
            _LOGGER.exception("hud ttl dismiss failed: id=%s", pid)

    # -- operations -------------------------------------------------------- #
    def show(
        self,
        state_key: str,
        kind: str,
        title: str,
        payload: dict,
        *,
        ttl_s: float | int | None = None,
        panel_id: str | None = None,
        origin: str = "agent",
        keep: bool = False,
    ) -> tuple[dict, bool]:
        """Create (or replace by id) a panel; return (record, delivered)."""
        validate(kind, payload)
        if origin not in ORIGINS:
            raise ValidationError(f"unknown origin {origin!r}; valid: {', '.join(ORIGINS)}")
        with self._lock:
            self._sync_kept()
            to_kept = keep or bool(panel_id and panel_id in self._kept)
        if to_kept:  # outside the lock: _show_kept broadcasts once it has let go
            return self._show_kept(state_key, kind, title, payload, panel_id, origin)
        ttl = _coerce_ttl(ttl_s)
        evicted: list[str] = []
        with self._lock:
            panels = self._panels.setdefault(state_key, OrderedDict())
            pid = panel_id or _new_id()
            existing = panels.get(pid)
            rev = (existing["rev"] + 1) if existing else 1
            record = {
                "id": pid,
                "kind": kind,
                "title": title,
                "payload": payload,
                "ttl_s": ttl,
                "origin": origin,
                "created_at": existing["created_at"] if existing else _now_iso(),
                "rev": rev,
            }
            panels[pid] = record
            panels.move_to_end(pid)
            # Evict oldest beyond the cap (never the panel we just wrote).
            while len(panels) > self._max:
                old_id, _ = next(iter(panels.items()))
                if old_id == pid:
                    break
                panels.popitem(last=False)
                self._cancel_timer(state_key, old_id)
                evicted.append(old_id)
            if ttl is not None:
                self._schedule_ttl(state_key, pid, ttl)
            else:
                self._cancel_timer(state_key, pid)
            snapshot = dict(record)
        for old_id in evicted:
            self.share(state_key, "hud.panel.dismiss", {"id": old_id})
        return snapshot, self.share(state_key, "hud.panel.show", snapshot)

    def _show_kept(
        self, state_key: str, kind: str, title: str, payload: dict,
        panel_id: str | None, origin: str,
    ) -> tuple[dict, bool]:
        with self._lock:
            pid = panel_id or _new_id()
            if pid not in self._kept and len(self._kept) >= MAX_KEPT:
                titles = ", ".join(r["title"] for r in self._kept.values())
                raise ValidationError(f"{MAX_KEPT} panels are kept already ({titles}); dismiss one first")
            # Kept after it was shown: it moves out of the session, and its ttl goes.
            panels = self._panels.get(state_key)
            existing = self._kept.get(pid) or (panels.pop(pid, None) if panels else None)
            self._cancel_timer(state_key, pid)
            record = {
                "id": pid, "kind": kind, "title": title, "payload": payload, "ttl_s": None,
                "origin": origin,
                "created_at": existing["created_at"] if existing else _now_iso(),
                "rev": (existing["rev"] + 1) if existing else 1,
                "kept": True,
            }
            self._kept[pid] = record
            self._save_kept()
            snapshot = dict(record)
        return snapshot, self._broadcast("hud.panel.show", snapshot)

    def update(
        self,
        state_key: str,
        panel_id: str,
        *,
        payload: dict | None = None,
        title: str | None = None,
    ) -> tuple[dict, bool]:
        """Bump a panel's rev and emit the changed fields; (record, delivered)."""
        with self._lock:
            self._sync_kept()
            panels = self._panels.get(state_key)
            kept = panel_id in self._kept
            record = self._kept[panel_id] if kept else (panels.get(panel_id) if panels else None)
            if record is None:
                raise ValidationError(f"no panel {panel_id!r} to update")
            if payload is not None:
                validate(record["kind"], payload)
                record["payload"] = payload
            if title is not None:
                record["title"] = title
            record["rev"] += 1
            # An update carries only what changed, plus id and the new rev so a
            # client drops it if it already holds a higher rev.
            frame: dict[str, Any] = {"id": panel_id, "rev": record["rev"]}
            if payload is not None:
                frame["payload"] = payload
            if title is not None:
                frame["title"] = title
            snapshot = dict(record)
            if kept:
                self._save_kept()
        if kept:
            return snapshot, self._broadcast("hud.panel.update", frame)
        return snapshot, self.share(state_key, "hud.panel.update", frame)

    def dismiss(
        self, state_key: str, panel_id: str, *, origin: str = "agent"
    ) -> tuple[list[str], bool]:
        """Dismiss one panel or, for ``*``, all of them; (ids, delivered)."""
        del origin  # dismiss carries no origin in its payload; kept for symmetry
        with self._lock:
            self._sync_kept()
            forget = panel_id in self._kept
            if forget:  # the owner closed it: forgotten everywhere
                del self._kept[panel_id]
                self._save_kept()
        if forget:
            return [panel_id], self._broadcast("hud.panel.dismiss", {"id": panel_id})
        with self._lock:
            panels = self._panels.get(state_key)
            kept = [dict(r) for r in self._kept.values()]
            if panel_id == DISMISS_ALL_ID:
                ids = list(panels.keys()) if panels else []
                if panels:
                    for pid in ids:
                        self._cancel_timer(state_key, pid)
                    panels.clear()
            else:
                ids = [panel_id] if (panels and panel_id in panels) else []
                if ids and panels is not None:  # ids is non-empty only when panels held it
                    self._cancel_timer(state_key, panel_id)
                    del panels[panel_id]
        if panel_id == DISMISS_ALL_ID:
            # Emit the single wildcard so late joiners agree, even with nothing
            # currently held — a client may hold panels we evicted from state.
            delivered = self._emit(state_key, "hud.panel.dismiss", {"id": DISMISS_ALL_ID})
            # The other screens lose only this session's panels, not their own.
            for sid in self._others(state_key):
                for pid in ids:
                    self._emit(sid, "hud.panel.dismiss", {"id": pid})
            # The wildcard clears the screen; the owner's kept panels come back.
            for record in kept:
                self._emit(state_key, "hud.panel.show", record)
            return ids, delivered
        if not ids:
            return [], False
        return ids, self.share(state_key, "hud.panel.dismiss", {"id": panel_id})

    def emit(self, state_key: str, event: str, payload: dict) -> bool:
        """One event that is not a panel (``hud.say``), through the same route; True when delivered."""
        return bool(self._emit(state_key, event, payload))

    def is_kept(self, panel_id: str) -> bool:
        with self._lock:
            self._sync_kept()
            return panel_id in self._kept

    def list(self, state_key: str, kept: bool = True) -> list[dict]:
        """The kept panels (unless ``kept`` is False), then every live panel for a session, oldest first."""
        with self._lock:
            self._sync_kept()
            if self._watch and self._watcher is None:
                # daemon so the poll never holds up interpreter shutdown.
                self._watcher = threading.Thread(target=self._watch_kept, name="hud-kept", daemon=True)
                self._watcher.start()
            panels = self._panels.get(state_key)
            return [dict(rec) for rec in (self._kept.values() if kept else ())] + (
                [dict(rec) for rec in panels.values()] if panels else [])


def _coerce_ttl(ttl_s: Any) -> Optional[float]:
    """A positive number of seconds, or None (no auto-dismiss)."""
    if ttl_s is None:
        return None
    try:
        ttl = float(ttl_s)
    except (TypeError, ValueError):
        return None
    return ttl if ttl > 0 else None


#: The single process-wide store the tools and the command share.
STORE = HudStore(kept_path=_kept_path(), watch=True)


# --------------------------------------------------------------------------- #
# Tool handlers  (registry calls handler(args, session_id=…, task_id=…, …))
# --------------------------------------------------------------------------- #
def _ok(record: dict, delivered: bool) -> str:
    out = dict(record)
    out["ok"] = True
    out["delivered"] = delivered
    return json.dumps(out)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _guest(session_id: Any) -> bool:
    return _speaker.level_of(str(session_id or "")) == "guest"


def _owner(session_id: Any) -> bool:
    return _speaker.level_of(str(session_id or "")) == "owner"


#: hud_* are free for a guest, but kept panels are the owner's on every screen: a guest's
#: turn (or a fetched page talking the model into it) may not pin, rewrite, drop or read them.
GUEST_KEPT = "a guest may not change the owner's kept panels"


def handle_hud_show(args: dict, **kwargs: Any) -> str:
    key = _state_key(kwargs.get("session_id"))
    guest = _guest(kwargs.get("session_id"))
    if guest and args.get("id") and STORE.is_kept(str(args.get("id"))):
        return _err(GUEST_KEPT)
    try:
        record, delivered = STORE.show(
            key,
            str(args.get("kind", "")),
            str(args.get("title", "")),
            args.get("payload") or {},  # show() stores a dict body; an absent payload is empty
            ttl_s=args.get("ttl_s"),
            panel_id=args.get("id"),
            origin="agent",
            keep=args.get("keep") is True and not guest,
        )
    except ValidationError as exc:
        return _err(str(exc))
    return _ok(record, delivered)


def handle_hud_update(args: dict, **kwargs: Any) -> str:
    key = _state_key(kwargs.get("session_id"))
    pid = str(args.get("id", ""))
    if not pid:
        return _err("hud_update needs the panel id")
    if STORE.is_kept(pid) and _guest(kwargs.get("session_id")):
        return _err(GUEST_KEPT)
    try:
        record, delivered = STORE.update(
            key, pid, payload=args.get("payload"), title=args.get("title")
        )
    except ValidationError as exc:
        return _err(str(exc))
    return _ok(record, delivered)


def handle_hud_dismiss(args: dict, **kwargs: Any) -> str:
    key = _state_key(kwargs.get("session_id"))
    pid = str(args.get("id", ""))
    if not pid:
        return _err("hud_dismiss needs a panel id or '*'")
    if STORE.is_kept(pid) and _guest(kwargs.get("session_id")):
        return _err(GUEST_KEPT)
    ids, delivered = STORE.dismiss(key, pid, origin="agent")
    return json.dumps({"ok": True, "delivered": delivered, "dismissed": ids})


def handle_hud_say(args: dict, **kwargs: Any) -> str:
    """Speak one line on the person's surface, in `voice` (the primary's when none)."""
    key = _state_key(kwargs.get("session_id"))
    text = str(args.get("text") or "").strip()
    if not text:
        return _err("hud_say needs the words to say")
    if len(text) > SAY_MAX_CHARS:
        return _err(f"hud_say carries at most {SAY_MAX_CHARS} characters; say the gist and offer the rest")
    voice = str(args.get("voice") or "").strip()
    if voice and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", voice):
        return _err("voice must be a short plain name")
    payload = {"text": text, "voice": voice or None}
    delivered = STORE.emit(key, SAY_EVENT, payload)
    return json.dumps({"ok": True, "delivered": delivered, "said": text})


def handle_hud_list(args: dict, **kwargs: Any) -> str:
    del args
    key = _state_key(kwargs.get("session_id"))
    panels = STORE.list(key, kept=not _guest(kwargs.get("session_id")))
    return json.dumps({"ok": True, "count": len(panels), "panels": panels})


# --------------------------------------------------------------------------- #
# Slash command  /hud @<sid> list | dismiss <id|*> | show <kind> <title> <json>
# --------------------------------------------------------------------------- #
USAGE = (
    "usage: /hud @<sid> list | dismiss <id|*> | show <kind> <title> <json payload>"
)


def handle_command(raw_args: str) -> str:
    # `@<sid> <rest>` — the app prefixes the arg with the session id session.create
    # returned, because plugin command handlers are never handed the session.
    sid, rest = split_session(raw_args)
    if sid is None:
        return USAGE + " (prefix the argument with @<session_id>)"
    key = _state_key(sid)
    head, _, tail = rest.partition(" ")
    head = head.lower()
    tail = tail.strip()

    if head in ("", "list"):
        # Every console lists its panels on connect: from then on this process tells the open
        # consoles when a routine's own run adds to what waits to be remembered (T-1506).
        # Imported here: the proposals module reads the matrix, which loads after this one.
        from _shared import _memory_proposals
        _memory_proposals.watch()
        return json.dumps(STORE.list(key))

    if head == "dismiss":
        if not tail:
            return "usage: /hud @<sid> dismiss <id|*>"
        ids, delivered = STORE.dismiss(key, tail.split()[0], origin="user")
        return json.dumps({"ok": True, "delivered": delivered, "dismissed": ids})

    if head == "show":
        # `show <kind> <title> <json>`: the payload begins at the first '{', so a
        # title may hold spaces without a quoting rule.
        kind, _, after = tail.partition(" ")
        kind = kind.strip()
        after = after.strip()
        brace = after.find("{")
        if not kind or brace == -1:
            return "usage: /hud @<sid> show <kind> <title> <json payload>"
        title = after[:brace].strip()
        try:
            payload = json.loads(after[brace:])
        except json.JSONDecodeError as exc:
            return f"hud error: payload is not valid JSON ({exc})"
        try:
            record, delivered = STORE.show(key, kind, title, payload, origin="user")
        except ValidationError as exc:
            return f"hud error: {exc}"
        return _ok(record, delivered)

    return USAGE
