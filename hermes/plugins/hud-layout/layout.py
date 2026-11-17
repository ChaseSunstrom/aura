"""JARVIS's hands on the HUD's window manager — tree operations, undo, the lock,
manual-wins, presets, preferences, the tools and the ``/hud_layout`` command.

A *layout* is the settings schema's ``LayoutDocument`` (``aura_schema``:
``{mode, tree, panels}`` — a tiling tree of panel ids over a panel list). This
module never defines its own document shape: every layout it emits or stores
passes ``LayoutDocument.model_validate`` first, so the HUD can never be handed a
tree that names a panel it does not hold. The saved arrangements
(``hud.layouts``), the mode (``hud.mode``) and the lock (``hud.lock_layout``)
are read and written through the schema and Hermes's config writer — the same
path ``jarvis-settings`` uses — never a private store.

Three rules the brief fixes, and where each lives:

* **Lock layout** — every change JARVIS makes reads ``hud.lock_layout`` first
  (:meth:`LayoutStore._guard`) and answers ``refused: lock_layout`` while it is
  on. ``highlight`` and ``list_panels`` are not changes and stay allowed.
* **Manual wins, and is remembered** — the HUD reports a person's own
  arrangement with ``hud_layout @<sid> user …`` (the ``hud.layout.user`` shape);
  it is saved under ``hud.layouts[surface][activity]`` and from then on
  ``arrange`` for that activity applies *the person's* layout and says
  "kept your arrangement" — unless ``force``. A person's undo of a JARVIS
  arrangement is remembered too (``declined``): JARVIS leaves that activity
  alone until ``force`` or ``forget``.
* **Reversible** — every JARVIS change pushes the layout it replaced on a
  per-session undo stack (:data:`MAX_UNDO` deep); ``undo`` re-applies it as
  op ``undo``. A person's manual arrangement clears the stack, because undoing
  an older JARVIS change would clobber what the person just did.

JARVIS's own per-activity preferences (the tree it chose with ``remember``)
persist in Hermes's plugin-state API (``ctx.state``, a quota-bounded JSON file
under ``<HERMES_HOME>/plugin-data/``), handed in by ``register()``; without a
Hermes context (tests, docs generation) they live in memory for the process.

Events go out on the HUD channel :mod:`channel` uses (``hud.layout.apply`` and
``hud.layout.highlight``); the wire shapes, the tool argument shapes and the
presets are pinned by ``tests/contracts/hud-layout.json``, whose layout document
is the settings contract's (``hermes-settings.json#/hud/layout_document``) by
reference.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union

from pydantic import ValidationError

from aura_schema import SETTINGS_BY_KEY, LayoutDocument, coerce
from aura_schema import SettingsError as _SchemaError
from aura_schema.settings import HUD_MODES, SURFACES

from _shared._approval_hook import split_session

from . import channel

_LOGGER = logging.getLogger(__name__)

#: The two layout events, in the order the contract lists them.
EVENTS: list[str] = ["hud.layout.apply", "hud.layout.highlight", "hud.layout.camera"]
#: What an ``apply`` frame's ``op`` may be. ``user`` is the HUD reporting a
#: manual arrangement back (never emitted by JARVIS, listed so a renderer that
#: switches on op is exhaustive).
OPS: list[str] = ["open", "close", "focus", "move", "resize", "arrange", "pin", "set_mode", "undo", "band", "user"]

#: What a ``hud.layout.camera`` frame's ``op`` may be (HUD.md §6.4). The camera is
#: not the document: flying somewhere changes nothing about what is open, so it
#: is a frame of its own and is not on the undo stack.
CAMERA_OPS: list[str] = ["fly", "fit", "reactor"]
#: The eleven tools, in the brief's order, under the plugin's prefix.
TOOLS: list[str] = [
    "hud_list_panels", "hud_open", "hud_close", "hud_focus", "hud_move", "hud_resize",
    "hud_arrange", "hud_highlight", "hud_pin", "hud_set_mode", "hud_undo",
    # v3, the map's own verbs (HUD.md §6.4): where the camera looks, and which
    # band a panel sits on.
    "hud_fly_to", "hud_zoom_to_fit", "hud_return_to_reactor", "hud_band",
]
#: Toolset the tools live under (a plugin toolset key; enabled by default).
TOOLSET: str = "hud_layout"
#: The slash command the HUD dispatches (``command.dispatch {name:"hud_layout"}``).
COMMAND_NAME: str = "hud_layout"
#: The schema keys this plugin reads and writes.
LOCK_KEY: str = "hud.lock_layout"
LAYOUTS_KEY: str = "hud.layouts"
MODE_KEY: str = "hud.mode"
#: The surface a tool call means when it names none: the console is where the
#: choreography is designed (§9.2); the phone maps a tree by leaf order.
DEFAULT_SURFACE: str = "web"
#: A highlight is a rim pulse of this length, not a scroll or a modal (§9.8).
HIGHLIGHT_MS: int = 1200
#: Undo depth per session. Deeper than a person will step back, shallow enough
#: that a runaway loop of arranges cannot grow memory without bound.
MAX_UNDO: int = 32
#: Where a moved/opened panel lands relative to its target in the tiling tree.
EDGES: list[str] = ["left", "right", "top", "bottom"]
#: The panel types of GLASS_HUD.md §9.1 — a hint for ``open``'s ``type``; the
#: schema accepts any non-empty type so a later phase can add one without a
#: plugin release.
PANEL_TYPES: list[str] = [
    "chat", "timeline", "approvals", "goals", "kanban", "task", "memory", "model", "voice",
    "audit", "settings", "image", "browser", "document", "telemetry", "notifications",
    "house", "automations", "integrations", "jobs", "pairing", "gallery",
    # The media panels (hud-panels.json's video/audio kinds). Appended: the
    # Android envelope table is compared to this list in order.
    "video", "audio",
]
#: The three orbital bands a panel may be moved to (HUD.md §6.2).
BANDS: list[str] = ["inner", "middle", "outer"]
#: Audit log the lock and layout writes append to — the settings audit, because
#: they are settings writes (same file as ``jarvis-settings``).
AUDIT_REL: str = "logs/settings.jsonl"

#: The choreography of the brief as layout trees, plus ``working`` (§9.2, the
#: default). Each preset names the activity it serves so ``arrange`` can look
#: the person's saved arrangement and JARVIS's remembered preference up.
PRESETS: dict[str, dict[str, Any]] = {
    "working": {
        "activity": "default",
        "layout": {
            "mode": "task",
            "tree": {"split": "row", "ratio": 0.5, "children": [
                "chat",
                {"split": "column", "ratio": 0.6, "children": [
                    "timeline",
                    {"split": "row", "ratio": 0.5, "children": ["goals", "model"]},
                ]},
            ]},
            "panels": [
                {"id": "chat", "type": "chat"}, {"id": "timeline", "type": "timeline"},
                {"id": "goals", "type": "goals"}, {"id": "model", "type": "model"},
            ],
        },
    },
    "goal-start": {
        "activity": "goal",
        "layout": {
            "mode": "task",
            "tree": {"split": "row", "ratio": 0.55, "children": [
                "chat",
                {"split": "column", "ratio": 0.5, "children": ["goals", "timeline"]},
            ]},
            "panels": [
                {"id": "chat", "type": "chat"}, {"id": "goals", "type": "goals"},
                {"id": "timeline", "type": "timeline"},
            ],
        },
    },
    "image-open": {
        "activity": "image",
        "layout": {
            "mode": "task",
            "tree": {"split": "row", "ratio": 0.5, "children": ["chat", "image"]},
            "panels": [{"id": "chat", "type": "chat"}, {"id": "image", "type": "image"}],
        },
    },
    "approval-focus": {
        "activity": "approval",
        "layout": {
            "mode": "focus",
            "tree": "approvals",
            "panels": [
                {"id": "approvals", "type": "approvals"}, {"id": "chat", "type": "chat"},
                {"id": "timeline", "type": "timeline"},
            ],
        },
    },
    "model-warming": {
        "activity": "model",
        "layout": {
            "mode": "task",
            "tree": {"split": "row", "ratio": 0.6, "children": [
                "chat",
                {"split": "column", "ratio": 0.4, "children": ["model", "timeline"]},
            ]},
            "panels": [
                {"id": "chat", "type": "chat"}, {"id": "model", "type": "model"},
                {"id": "timeline", "type": "timeline"},
            ],
        },
    },
    "voice-ambient": {
        "activity": "voice",
        "layout": {"mode": "ambient", "tree": None, "panels": [{"id": "voice", "type": "voice"}]},
    },
    "browser-task": {
        "activity": "browser",
        "layout": {
            "mode": "task",
            "tree": {"split": "row", "ratio": 0.3, "children": [
                "chat",
                {"split": "row", "ratio": 0.6, "children": ["browser", "timeline"]},
            ]},
            "panels": [
                {"id": "chat", "type": "chat"}, {"id": "browser", "type": "browser"},
                {"id": "timeline", "type": "timeline"},
            ],
        },
    },
}

#: What a person is told when the lock refuses a change; pinned by the contract
#: so the apps and the bench recognise the refusal by text.
LOCK_MESSAGE: str = (
    "Lock layout is on: JARVIS may not open, close, move, resize or arrange panels until "
    "you unlock it (the Lock layout toggle, alt-k, or hud_layout unlock)."
)

Tree = Union[str, dict[str, Any], None]


class LayoutError(ValueError):
    """A change that cannot be made as asked (unknown panel, bad tree, bad mode …)."""


class Locked(LayoutError):
    """Lock layout is on; JARVIS's change was refused."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# The tiling tree (plain JSON, the schema's shape)
# --------------------------------------------------------------------------- #
def leaves(tree: Tree) -> list[str]:
    """Panel ids in the tree, left-to-right / top-to-bottom (the phone's stack order)."""
    if tree is None:
        return []
    if isinstance(tree, str):
        return [tree]
    return [leaf for child in tree["children"] for leaf in leaves(child)]


def remove_leaf(tree: Tree, pid: str) -> tuple[Tree, bool]:
    """The tree without ``pid``, its parent split collapsed onto the sibling."""
    if tree is None:
        return None, False
    if isinstance(tree, str):
        return (None, True) if tree == pid else (tree, False)
    first, second = tree["children"]
    if first == pid:
        return second, True
    if second == pid:
        return first, True
    new_first, done = remove_leaf(first, pid)
    if done:
        return {**tree, "children": [new_first, second]}, True
    new_second, done = remove_leaf(second, pid)
    if done:
        return {**tree, "children": [first, new_second]}, True
    return tree, False


def split_leaf(target: str, pid: str, edge: str, share: float) -> dict[str, Any]:
    """A split putting ``pid`` on ``edge`` of ``target`` with ``share`` of the space."""
    if edge not in EDGES:
        raise LayoutError(f"unknown edge {edge!r}; one of {', '.join(EDGES)}")
    first_is_new = edge in ("left", "top")
    return {
        "split": "row" if edge in ("left", "right") else "column",
        "ratio": share if first_is_new else 1 - share,
        "children": [pid, target] if first_is_new else [target, pid],
    }


def insert_beside(tree: Tree, target: str, pid: str, edge: str, share: float) -> tuple[Tree, bool]:
    """``tree`` with the leaf ``target`` replaced by a split of ``target`` and ``pid``."""
    if tree is None:
        return None, False
    if isinstance(tree, str):
        return (split_leaf(target, pid, edge, share), True) if tree == target else (tree, False)
    children = list(tree["children"])
    for i, child in enumerate(children):
        new_child, done = insert_beside(child, target, pid, edge, share)
        if done:
            children[i] = new_child
            return {**tree, "children": children}, True
    return tree, False


def set_share(tree: Tree, pid: str, share: float) -> tuple[Tree, bool]:
    """Give the leaf ``pid`` ``share`` of its parent split (its sibling gets the rest)."""
    if tree is None or isinstance(tree, str):
        return tree, False
    first, second = tree["children"]
    if first == pid:
        return {**tree, "ratio": share}, True
    if second == pid:
        return {**tree, "ratio": 1 - share}, True
    for i, child in enumerate((first, second)):
        new_child, done = set_share(child, pid, share)
        if done:
            children = [first, second]
            children[i] = new_child
            return {**tree, "children": children}, True
    return tree, False


def _share(value: Any, *, name: str = "share") -> float:
    try:
        share = float(value)
    except (TypeError, ValueError):
        raise LayoutError(f"{name} must be a number between 0 and 1 (exclusive)") from None
    if not 0 < share < 1:
        raise LayoutError(f"{name} must be between 0 and 1 (exclusive), got {share}")
    return share


# --------------------------------------------------------------------------- #
# Layout documents
# --------------------------------------------------------------------------- #
def validate_layout(doc: Any) -> dict[str, Any]:
    """``doc`` normalised through the schema's ``LayoutDocument``, or :class:`LayoutError`."""
    if not isinstance(doc, dict):
        raise LayoutError("a layout must be a JSON object {mode, tree, panels}")
    try:
        return LayoutDocument.model_validate(doc).model_dump(exclude_none=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise LayoutError(f"invalid layout: {first.get('msg')}" + (f" (at {where})" if where else "")) from None


def _panel_index(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in doc.get("panels", [])}


def _need(doc: dict[str, Any], pid: str) -> dict[str, Any]:
    panel = _panel_index(doc).get(pid)
    if panel is None:
        raise LayoutError(f"no panel {pid!r} on this layout; open: {', '.join(_panel_index(doc)) or 'none'}")
    return panel


def _default_rect(index: int) -> dict[str, float]:
    """A staggered placement for a panel that was given none."""
    step = 0.06 * (index % 5)
    return {"x": 0.1 + step, "y": 0.1 + step, "w": 0.45, "h": 0.55}


def _retile(doc: dict[str, Any]) -> dict[str, Any]:
    """Put every panel the tree does not hold into it (row splits at the root), so
    a switch back to Tiled never leaves a panel the manager cannot place."""
    tree: Tree = doc.get("tree")
    held = set(leaves(tree))
    for panel in doc.get("panels", []):
        if panel["id"] in held:
            continue
        tree = panel["id"] if tree is None else {"split": "row", "ratio": 0.5, "children": [tree, panel["id"]]}
        held.add(panel["id"])
    return {**doc, "tree": tree}


def op_open(doc: dict[str, Any], pid: str, ptype: str, *, beside: Optional[str] = None,
            edge: str = "right", share: float = 0.5, rect: Optional[dict] = None,
            pinned: bool = False) -> dict[str, Any]:
    """Add a panel: split beside a target (Tiled), place it (Floating), or add it
    to the rail (Focus) / the hidden set (Ambient)."""
    if not pid or not ptype:
        raise LayoutError("open needs a panel id and a type")
    if pid in _panel_index(doc):
        raise LayoutError(f"panel {pid!r} is already open")
    panel: dict[str, Any] = {"id": pid, "type": ptype, "pinned": bool(pinned)}
    if rect is not None:
        panel["rect"] = rect
    mode = doc["mode"]
    tree: Tree = doc.get("tree")
    if mode == "task":
        if tree is None:
            tree = pid
        elif beside:
            _need(doc, beside)
            tree, done = insert_beside(tree, beside, pid, edge, share)
            if not done:
                raise LayoutError(f"panel {beside!r} is not in the tiling tree")
        else:
            wrapped = split_leaf("__root__", pid, edge, share)
            wrapped["children"] = [tree if c == "__root__" else c for c in wrapped["children"]]
            tree = wrapped
    elif mode == "overview" and rect is None:
        panel["rect"] = _default_rect(len(doc.get("panels", [])))
    elif mode == "focus" and tree is None:
        tree = pid
    return {**doc, "tree": tree, "panels": [*doc.get("panels", []), panel]}


def op_close(doc: dict[str, Any], pid: str, *, force: bool = False) -> dict[str, Any]:
    """Remove a panel from the list and the tree. A pinned panel is the person's
    choice and refuses unless ``force``."""
    panel = _need(doc, pid)
    if panel.get("pinned") and not force:
        raise LayoutError(f"panel {pid!r} is pinned by you; pass force to close it anyway")
    tree, _ = remove_leaf(doc.get("tree"), pid)
    return {**doc, "tree": tree, "panels": [p for p in doc.get("panels", []) if p["id"] != pid]}


def op_focus(doc: dict[str, Any], pid: str) -> dict[str, Any]:
    """Focus mode with ``pid`` large; every other panel becomes the rail."""
    _need(doc, pid)
    return {**doc, "mode": "focus", "tree": pid}


def op_move(doc: dict[str, Any], pid: str, *, target: Optional[str] = None, edge: str = "right",
            share: float = 0.5, rect: Optional[dict] = None) -> dict[str, Any]:
    """Re-seat a panel: beside ``target`` on ``edge`` (Tiled), or at ``rect`` (Floating)."""
    _need(doc, pid)
    if rect is not None:
        panels = [{**p, "rect": rect} if p["id"] == pid else p for p in doc.get("panels", [])]
        return {**doc, "panels": panels}
    if not target:
        raise LayoutError("move needs a target panel (and an edge) or a rect")
    if target == pid:
        raise LayoutError("a panel cannot be moved beside itself")
    _need(doc, target)
    tree, _ = remove_leaf(doc.get("tree"), pid)
    tree, done = insert_beside(tree, target, pid, edge, share)
    if not done:
        raise LayoutError(f"panel {target!r} is not in the tiling tree")
    return {**doc, "tree": tree}


def op_resize(doc: dict[str, Any], pid: str, *, share: Optional[float] = None,
              rect: Optional[dict] = None) -> dict[str, Any]:
    """Give a panel ``share`` of its parent split, or a new ``rect`` (Floating)."""
    _need(doc, pid)
    if rect is not None:
        panels = [{**p, "rect": rect} if p["id"] == pid else p for p in doc.get("panels", [])]
        return {**doc, "panels": panels}
    if share is None:
        raise LayoutError("resize needs a share (0..1 of the parent split) or a rect")
    tree, done = set_share(doc.get("tree"), pid, share)
    if not done:
        raise LayoutError(f"panel {pid!r} fills the surface or is not in the tiling tree; nothing to resize")
    return {**doc, "tree": tree}


def op_pin(doc: dict[str, Any], pid: str, pinned: bool) -> dict[str, Any]:
    _need(doc, pid)
    return {**doc, "panels": [{**p, "pinned": bool(pinned)} if p["id"] == pid else p
                              for p in doc.get("panels", [])]}


def op_band(doc: dict[str, Any], pids: list[str], band: str) -> dict[str, Any]:
    """Put each named panel on ``band``.

    This is "bring these panels in for the task I am starting" and "push the
    finished work outward" — the two arrangement moves the brief asks JARVIS to
    learn — as one verb, because they are the same operation in two directions.
    A panel's band is otherwise its type's, so clearing it is what returns it.
    """
    if band not in BANDS:
        raise LayoutError(f"unknown band {band!r}; one of {', '.join(BANDS)}")
    if not pids:
        raise LayoutError("band needs at least one panel id")
    for pid in pids:
        _need(doc, pid)
    wanted = set(pids)
    panels = [{**p, "band": band} if p["id"] in wanted else p for p in doc.get("panels", [])]
    return {**doc, "panels": panels}


def op_set_mode(doc: dict[str, Any], mode: str) -> dict[str, Any]:
    """Switch preset, keeping every panel (HUD.md §6.5).

    Task opens the current work's panels, Overview draws every panel as a glyph,
    Focus keeps one open and Ambient shows the reactor and a strip. The names
    changed in v3 with the geometry: `tiled` and `floating` described a split
    tree and a z-order, and ADR-0036 retired both."""
    if mode not in HUD_MODES:
        raise LayoutError(f"unknown mode {mode!r}; one of {', '.join(HUD_MODES)}")
    out = {**doc, "mode": mode}
    if mode == "task":
        return _retile(out)
    if mode == "overview":
        panels = [p if p.get("rect") else {**p, "rect": _default_rect(i)}
                  for i, p in enumerate(out.get("panels", []))]
        return {**out, "panels": panels}
    if mode == "focus":
        first = leaves(out.get("tree"))
        return {**out, "tree": first[0] if first else None}
    return out


# --------------------------------------------------------------------------- #
# Settings-backed state: the lock and the person's saved arrangements
# --------------------------------------------------------------------------- #
def _dig(data: Any, dotted: str) -> Any:
    for seg in dotted.split("."):
        if not isinstance(data, dict) or seg not in data:
            return None
        data = data[seg]
    return data


class HermesConfig:
    """``hud.lock_layout`` / ``hud.layouts`` through Hermes's config reader and writer.

    Every write is coerced by the schema first (``strict``: the values are JSON,
    already typed) so a layout that would not validate under ``jsettings set``
    never lands in ``config.yaml`` from here either, and audit-logged to the
    settings audit. Imports are lazy so this module loads without Hermes.
    """

    def _resolved(self) -> dict:
        # A config that cannot be read is reported, never treated as "unlocked":
        # the failure the lock exists for is JARVIS moving things it was told not to.
        try:
            from hermes_cli.config import load_config
        except ImportError as exc:
            raise LayoutError(f"Hermes config is not readable here ({exc}); cannot check the lock") from None
        return load_config()

    def _write(self, key: str, value: Any) -> None:
        try:
            from tui_gateway.server import _write_config_key as writer
        except Exception:
            writer = None
        if writer is not None:
            writer(key, value)
            return
        # Off the gateway (a CLI run): the CLI's raw read → set → atomic save.
        from hermes_cli import config as hc
        from utils import atomic_yaml_write

        path = hc.get_config_path()
        raw = hc.read_user_config_raw(path) if os.path.exists(path) else {}
        hc._set_nested(raw, key, value)
        hc.ensure_hermes_home()
        atomic_yaml_write(path, raw, sort_keys=False)

    def _audit(self, key: str, before: Any, after: Any, *, session: str) -> None:
        line = json.dumps({"ts": _now_iso(), "actor": "hud_layout", "session": session,
                           "key": key, "before": before, "after": after})
        try:
            from hermes_cli.config import get_config_path

            home = os.path.dirname(str(get_config_path()))
        except Exception:
            home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
        path = os.path.join(home, AUDIT_REL)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:  # the value is already written; a full disk must not undo that
            _LOGGER.exception("hud layout audit append failed: key=%s", key)

    def lock_layout(self) -> bool:
        value = _dig(self._resolved(), LOCK_KEY)
        return value if isinstance(value, bool) else False

    def set_lock(self, on: bool, *, session: str = "") -> None:
        before = self.lock_layout()
        value = coerce(SETTINGS_BY_KEY[LOCK_KEY], on, strict=True)
        self._write(LOCK_KEY, value)
        self._audit(LOCK_KEY, before, value, session=session)

    def layouts(self) -> dict[str, dict[str, Any]]:
        value = _dig(self._resolved(), LAYOUTS_KEY)
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def saved_layout(self, surface: str, activity: str) -> Optional[dict[str, Any]]:
        doc = self.layouts().get(surface, {}).get(activity)
        return doc if isinstance(doc, dict) else None

    def save_user_layout(self, surface: str, activity: str, doc: dict[str, Any], *, session: str = "") -> None:
        before = self.layouts()
        after = copy.deepcopy(before)
        after.setdefault(surface, {})[activity] = doc
        try:
            value = coerce(SETTINGS_BY_KEY[LAYOUTS_KEY], after, strict=True)
        except _SchemaError as exc:
            raise LayoutError(str(exc)) from None
        self._write(LAYOUTS_KEY, value)
        self._audit(LAYOUTS_KEY, before, value, session=session)


class MemoryConfig(HermesConfig):
    """The same surface over a dict — the tests' and the docs generator's config."""

    def __init__(self, data: Optional[dict] = None) -> None:
        self.data: dict[str, Any] = data if data is not None else {}
        self.writes: list[tuple[str, Any]] = []

    def _resolved(self) -> dict:
        return self.data

    def _write(self, key: str, value: Any) -> None:
        cur = self.data
        *parents, last = key.split(".")
        for seg in parents:
            cur = cur.setdefault(seg, {})
        cur[last] = value
        self.writes.append((key, value))

    def _audit(self, key: str, before: Any, after: Any, *, session: str) -> None:
        del key, before, after, session


# --------------------------------------------------------------------------- #
# JARVIS's own preferences (Hermes plugin state, or memory)
# --------------------------------------------------------------------------- #
class PrefStore:
    """Per-activity layout preferences and the activities the person declined.

    ``backend`` is Hermes's ``PluginState`` (``get(key, default)`` / ``set(key,
    value)``: one JSON file, locked, quota-bounded) or None for an in-process
    dict. One key, ``preferences``, holds ``{"layouts": {activity: doc},
    "declined": {activity: count}}`` so a read-modify-write is one locked set.
    """

    KEY = "preferences"

    def __init__(self, backend: Optional[Any] = None) -> None:
        self._backend = backend
        self._memory: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        raw = self._backend.get(self.KEY, None) if self._backend is not None else self._memory
        data = raw if isinstance(raw, dict) else {}
        return {"layouts": dict(data.get("layouts") or {}), "declined": dict(data.get("declined") or {})}

    def _store(self, data: dict[str, Any]) -> None:
        if self._backend is not None:
            self._backend.set(self.KEY, data)
        else:
            self._memory = data

    def get(self, activity: str) -> Optional[dict[str, Any]]:
        doc = self._read()["layouts"].get(activity)
        return copy.deepcopy(doc) if isinstance(doc, dict) else None

    def set(self, activity: str, doc: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            data["layouts"][activity] = doc
            self._store(data)

    def declined(self, activity: str) -> int:
        return int(self._read()["declined"].get(activity, 0) or 0)

    def decline(self, activity: str) -> int:
        with self._lock:
            data = self._read()
            data["declined"][activity] = int(data["declined"].get(activity, 0) or 0) + 1
            self._store(data)
            return data["declined"][activity]

    def forget(self, activity: Optional[str] = None) -> list[str]:
        """Drop the preference and the decline count for one activity, or all."""
        with self._lock:
            data = self._read()
            names = sorted(set(data["layouts"]) | set(data["declined"]))
            if activity is not None:
                names = [n for n in names if n == activity]
            for name in names:
                data["layouts"].pop(name, None)
                data["declined"].pop(name, None)
            self._store(data)
            return names

    def summary(self) -> dict[str, Any]:
        data = self._read()
        return {"preferences": sorted(data["layouts"]), "declined": sorted(data["declined"])}


# --------------------------------------------------------------------------- #
# The store: per-session layouts, undo, events
# --------------------------------------------------------------------------- #
class _Session:
    def __init__(self) -> None:
        self.layouts: dict[str, dict[str, Any]] = {}
        self.activity: str = PRESETS["working"]["activity"]
        self.undo: list[dict[str, Any]] = []
        self.rev: int = 0


class LayoutStore:
    """Per-session layout state behind one lock; events through ``emitter``.

    ``emitter(state_key, event, payload) -> bool``, ``config`` and ``prefs`` are
    injected so the tests drive everything without a gateway or Hermes; the
    module-level :data:`STORE` uses the gateway channel and Hermes's config.
    """

    def __init__(
        self,
        *,
        emitter: Optional[Callable[[str, str, dict], bool]] = None,
        config: Optional[HermesConfig] = None,
        prefs: Optional[PrefStore] = None,
        max_undo: int = MAX_UNDO,
    ) -> None:
        self._emit = emitter or channel.emit
        self.config = config or HermesConfig()
        self.prefs = prefs or PrefStore()
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()
        self._max_undo = max_undo

    # -- helpers ------------------------------------------------------------ #
    def _session(self, key: str) -> _Session:
        return self._sessions.setdefault(key, _Session())

    @staticmethod
    def _surface(surface: Optional[str]) -> str:
        surface = surface or DEFAULT_SURFACE
        if surface not in SURFACES:
            raise LayoutError(f"unknown surface {surface!r}; one of {', '.join(SURFACES)}")
        return surface

    def current(self, key: str, surface: Optional[str] = None) -> dict[str, Any]:
        """The layout this session's surface is showing as far as the plugin knows.

        Before anything was applied or reported, that is the person's saved
        layout for the session's activity, else the ``working`` preset — the HUD
        starts on the same default (§9.2), so the first undo has a real target.
        """
        surface = self._surface(surface)
        with self._lock:
            sess = self._session(key)
            doc = sess.layouts.get(surface)
            if doc is None:
                doc = self.config.saved_layout(surface, sess.activity) or PRESETS["working"]["layout"]
                doc = validate_layout(doc)
                sess.layouts[surface] = doc
            return copy.deepcopy(doc)

    def _guard(self) -> None:
        if self.config.lock_layout():
            raise Locked(LOCK_MESSAGE)

    def _apply(self, key: str, surface: str, op: str, doc: dict[str, Any], *, reason: str,
               reversible: bool = True, activity: Optional[str] = None) -> dict[str, Any]:
        """Validate, record for undo, make current, emit; the tool's result record."""
        doc = validate_layout(doc)
        with self._lock:
            sess = self._session(key)
            before = self.current(key, surface)
            if reversible:
                # `activity` is what the layout was for (what an undo declines);
                # `activity_before` is what comes back with the old layout.
                sess.undo.append({"op": op, "surface": surface, "layout": before,
                                  "activity": activity or sess.activity, "activity_before": sess.activity,
                                  "reason": reason, "at": _now_iso()})
                del sess.undo[:-self._max_undo]
            if activity:
                sess.activity = activity
            sess.layouts[surface] = doc
            sess.rev += 1
            frame = {
                "op": op, "surface": surface, "activity": sess.activity, "layout": copy.deepcopy(doc),
                "animate": True, "reversible": reversible, "reason": reason,
                "rev": sess.rev, "undo_depth": len(sess.undo),
            }
        delivered = self._emit(key, "hud.layout.apply", frame)
        return {**frame, "ok": True, "delivered": delivered}

    # -- reads -------------------------------------------------------------- #
    def list_panels(self, key: str, surface: Optional[str] = None) -> dict[str, Any]:
        surface = self._surface(surface)
        doc = self.current(key, surface)
        order = leaves(doc.get("tree"))
        with self._lock:
            sess = self._session(key)
            return {
                "ok": True, "surface": surface, "mode": doc["mode"], "activity": sess.activity,
                "focused": order[0] if doc["mode"] == "focus" and order else None,
                "order": order, "panels": doc.get("panels", []), "locked": self.config.lock_layout(),
                "undo_depth": len(sess.undo),
            }

    def get(self, key: str) -> dict[str, Any]:
        """Everything a HUD needs to draw the manager's state line (``/hud_layout get``)."""
        saved = self.config.layouts()
        with self._lock:
            sess = self._session(key)
            return {
                "ok": True, "locked": self.config.lock_layout(), "activity": sess.activity,
                "layouts": {s: copy.deepcopy(d) for s, d in sess.layouts.items()},
                "undo_depth": len(sess.undo),
                "user_layouts": {s: sorted(a) for s, a in saved.items() if isinstance(a, dict)},
                "presets": sorted(PRESETS), **self.prefs.summary(),
            }

    # -- JARVIS's changes (all guarded by the lock) ----------------------------- #
    def open(self, key: str, pid: str, ptype: str, *, surface: Optional[str] = None, reason: str = "",
             **kw: Any) -> dict[str, Any]:
        self._guard()
        surface = self._surface(surface)
        return self._apply(key, surface, "open", op_open(self.current(key, surface), pid, ptype, **kw),
                           reason=reason or f"opened {pid}")

    def close(self, key: str, pid: str, *, surface: Optional[str] = None, reason: str = "",
              force: bool = False) -> dict[str, Any]:
        self._guard()
        surface = self._surface(surface)
        return self._apply(key, surface, "close", op_close(self.current(key, surface), pid, force=force),
                           reason=reason or f"closed {pid}")

    def focus(self, key: str, pid: str, *, surface: Optional[str] = None, reason: str = "") -> dict[str, Any]:
        self._guard()
        surface = self._surface(surface)
        return self._apply(key, surface, "focus", op_focus(self.current(key, surface), pid),
                           reason=reason or f"focused {pid}")

    def move(self, key: str, pid: str, *, surface: Optional[str] = None, reason: str = "",
             **kw: Any) -> dict[str, Any]:
        self._guard()
        surface = self._surface(surface)
        return self._apply(key, surface, "move", op_move(self.current(key, surface), pid, **kw),
                           reason=reason or f"moved {pid}")

    def resize(self, key: str, pid: str, *, surface: Optional[str] = None, reason: str = "",
               **kw: Any) -> dict[str, Any]:
        self._guard()
        surface = self._surface(surface)
        return self._apply(key, surface, "resize", op_resize(self.current(key, surface), pid, **kw),
                           reason=reason or f"resized {pid}")

    def pin(self, key: str, pid: str, pinned: bool = True, *, surface: Optional[str] = None,
            reason: str = "") -> dict[str, Any]:
        self._guard()
        surface = self._surface(surface)
        return self._apply(key, surface, "pin", op_pin(self.current(key, surface), pid, pinned),
                           reason=reason or (f"pinned {pid}" if pinned else f"unpinned {pid}"))

    def set_mode(self, key: str, mode: str, *, surface: Optional[str] = None, reason: str = "") -> dict[str, Any]:
        self._guard()
        surface = self._surface(surface)
        return self._apply(key, surface, "set_mode", op_set_mode(self.current(key, surface), mode),
                           reason=reason or f"mode {mode}")

    def arrange(self, key: str, *, preset: Optional[str] = None, layout: Optional[dict] = None,
                activity: Optional[str] = None, surface: Optional[str] = None, force: bool = False,
                remember: bool = False, reason: str = "") -> dict[str, Any]:
        """Apply a preset or a layout tree for an activity — unless the person's
        arrangement for that activity wins (see the module doc)."""
        self._guard()
        surface = self._surface(surface)
        if preset is not None and preset not in PRESETS:
            raise LayoutError(f"unknown preset {preset!r}; one of {', '.join(sorted(PRESETS))}")
        if preset is None and layout is None:
            raise LayoutError("arrange needs a preset name or a layout {mode, tree, panels}")
        chosen = validate_layout(layout) if layout is not None else validate_layout(PRESETS[str(preset)]["layout"])
        activity = activity or (PRESETS[str(preset)]["activity"] if preset else None) or self._session(key).activity
        if not force:
            saved = self.config.saved_layout(surface, activity)
            if saved is not None:
                out = self._apply(key, surface, "arrange", saved, activity=activity,
                                  reason=f"kept your arrangement for {activity}")
                return {**out, "kept": "user", "message": f"kept your arrangement for {activity}"}
            if self.prefs.declined(activity):
                return {"ok": True, "applied": False, "kept": "declined", "surface": surface,
                        "activity": activity, "delivered": False,
                        "message": f"left the layout alone: you undid my last arrangement for {activity} "
                                   "(pass force to arrange anyway, or hud_layout forget to clear that)"}
            if layout is None:
                chosen = self.prefs.get(activity) or chosen
        if remember and layout is not None:
            self.prefs.set(activity, chosen)
        out = self._apply(key, surface, "arrange", chosen, activity=activity,
                          reason=reason or (f"preset {preset}" if preset else f"arranged for {activity}"))
        return {**out, "preset": preset, "remembered": bool(remember and layout is not None)}

    def band(self, key: str, pids: list[str], band: str, *, surface: Optional[str] = None,
             reason: str = "") -> dict[str, Any]:
        """Move panels to a band: bring them in for a task, push them out when done."""
        self._guard()
        surface = self._surface(surface)
        return self._apply(key, surface, "band", op_band(self.current(key, surface), pids, band),
                           reason=reason or f"{', '.join(pids)} to the {band} band")

    def camera(self, key: str, op: str, *, panel: Optional[str] = None,
               surface: Optional[str] = None, reason: str = "") -> dict[str, Any]:
        """Move the camera. Not a layout change and not on the undo stack.

        Flying somewhere changes nothing about what is open, so the lock does not
        stop it either: "Lock layout" is the person saying *do not rearrange my
        panels*, and looking at one of them is not rearranging it. The way back is
        always one keystroke and one tool call (`hud_return_to_reactor`).
        """
        if op not in CAMERA_OPS:
            raise LayoutError(f"unknown camera op {op!r}; one of {', '.join(CAMERA_OPS)}")
        surface = self._surface(surface)
        if op == "fly":
            if not panel:
                raise LayoutError("fly needs a panel id")
            _need(self.current(key, surface), panel)
        frame = {"op": op, "panel": panel, "surface": surface, "reason": reason}
        delivered = self._emit(key, "hud.layout.camera", frame)
        return {**frame, "ok": True, "delivered": delivered}

    def highlight(self, key: str, pid: str, *, surface: Optional[str] = None, reason: str = "") -> dict[str, Any]:
        """A rim pulse on a panel — not a change, so allowed under the lock."""
        surface = self._surface(surface)
        _need(self.current(key, surface), pid)
        frame = {"panel": pid, "surface": surface, "duration_ms": HIGHLIGHT_MS, "reason": reason}
        delivered = self._emit(key, "hud.layout.highlight", frame)
        return {**frame, "ok": True, "delivered": delivered}

    def undo(self, key: str, *, origin: str = "agent") -> dict[str, Any]:
        """Re-apply the layout the last JARVIS change replaced. Any undo counts as
        the person declining that arrangement for its activity (``origin`` is
        recorded for the audit trail, both mean "not that")."""
        self._guard()
        with self._lock:
            sess = self._session(key)
            if not sess.undo:
                return {"ok": False, "error": "nothing to undo: JARVIS has not changed this layout",
                        "undo_depth": 0}
            entry = sess.undo.pop()
            sess.activity = entry["activity_before"]
        declined = self.prefs.decline(entry["activity"])
        out = self._apply(key, entry["surface"], "undo", entry["layout"], reversible=False,
                          reason=f"undid {entry['op']} ({entry['reason']})")
        return {**out, "undid": entry["op"], "origin": origin, "declined": declined}

    # -- the person's side --------------------------------------------------- #
    def user_layout(self, key: str, surface: Optional[str], activity: Optional[str],
                    doc: dict[str, Any]) -> dict[str, Any]:
        """The HUD reporting a manual arrangement (``hud.layout.user``): saved to
        ``hud.layouts`` for the activity, made current, and the undo stack cleared."""
        surface = self._surface(surface)
        doc = validate_layout(doc)
        with self._lock:
            sess = self._session(key)
            activity = activity or sess.activity
            self.config.save_user_layout(surface, activity, doc, session=key)
            sess.layouts[surface] = doc
            sess.activity = activity
            sess.undo.clear()
            sess.rev += 1
            rev = sess.rev
        return {"ok": True, "op": "user", "surface": surface, "activity": activity, "layout": doc,
                "rev": rev, "undo_depth": 0, "saved": f"{LAYOUTS_KEY}.{surface}.{activity}"}

    def lock(self, key: str, on: bool) -> dict[str, Any]:
        self.config.set_lock(on, session=key)
        return {"ok": True, "locked": on}

    def forget(self, activity: Optional[str] = None) -> dict[str, Any]:
        return {"ok": True, "forgot": self.prefs.forget(activity)}


#: The single process-wide store the tools and the command share.
STORE = LayoutStore()


def attach_state(state: Any) -> None:
    """Persist preferences in Hermes's plugin state when ``register()`` is given one.

    A stand-in ctx (the docs generator's, the tests') has no real ``state``; only
    an object with ``get``/``set`` callables is trusted, anything else keeps the
    in-memory store so a docs build never writes a file.
    """
    if callable(getattr(state, "get", None)) and callable(getattr(state, "set", None)):
        STORE.prefs = PrefStore(state)


# --------------------------------------------------------------------------- #
# Tool handlers  (registry calls handler(args, session_id=…, task_id=…, …))
# --------------------------------------------------------------------------- #
def _refused(exc: Locked) -> str:
    return json.dumps({"ok": False, "refused": "lock_layout", "error": str(exc)})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _run(fn: Callable[[], dict[str, Any]]) -> str:
    try:
        return json.dumps(fn())
    except Locked as exc:
        return _refused(exc)
    except LayoutError as exc:
        return _err(str(exc))


def _pid(args: dict, tool: str) -> str:
    pid = str(args.get("id") or "").strip()
    if not pid:
        raise LayoutError(f"{tool} needs the panel id")
    return pid


def _opt_share(args: dict) -> Optional[float]:
    return None if args.get("share") is None else _share(args.get("share"))


def handle_list_panels(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    return _run(lambda: STORE.list_panels(key, (args or {}).get("surface")))


def handle_open(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.open(
        key, _pid(a, "hud_open"), str(a.get("type") or ""), surface=a.get("surface"),
        reason=str(a.get("reason") or ""), beside=a.get("beside"), edge=str(a.get("edge") or "right"),
        share=_share(a.get("share", 0.5)), rect=a.get("rect"), pinned=bool(a.get("pinned", False))))


def handle_close(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.close(key, _pid(a, "hud_close"), surface=a.get("surface"),
                                    reason=str(a.get("reason") or ""), force=bool(a.get("force", False))))


def handle_focus(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.focus(key, _pid(a, "hud_focus"), surface=a.get("surface"),
                                    reason=str(a.get("reason") or "")))


def handle_move(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.move(
        key, _pid(a, "hud_move"), surface=a.get("surface"), reason=str(a.get("reason") or ""),
        target=a.get("target"), edge=str(a.get("edge") or "right"), share=_share(a.get("share", 0.5)),
        rect=a.get("rect")))


def handle_resize(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.resize(key, _pid(a, "hud_resize"), surface=a.get("surface"),
                                     reason=str(a.get("reason") or ""), share=_opt_share(a), rect=a.get("rect")))


def handle_arrange(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.arrange(
        key, preset=a.get("preset"), layout=a.get("layout"), activity=a.get("activity"),
        surface=a.get("surface"), force=bool(a.get("force", False)), remember=bool(a.get("remember", False)),
        reason=str(a.get("reason") or "")))


def handle_highlight(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.highlight(key, _pid(a, "hud_highlight"), surface=a.get("surface"),
                                        reason=str(a.get("reason") or "")))


def handle_pin(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.pin(key, _pid(a, "hud_pin"), bool(a.get("pinned", True)),
                                  surface=a.get("surface"), reason=str(a.get("reason") or "")))


def handle_band(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    raw = a.get("panels")
    pids = [str(p) for p in raw] if isinstance(raw, list) else ([str(raw)] if raw else [])
    return _run(lambda: STORE.band(key, pids, str(a.get("band") or ""), surface=a.get("surface"),
                                   reason=str(a.get("reason") or "")))


def handle_fly_to(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.camera(key, "fly", panel=_pid(a, "hud_fly_to"), surface=a.get("surface"),
                                     reason=str(a.get("reason") or "")))


def handle_zoom_to_fit(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.camera(key, "fit", surface=a.get("surface"),
                                     reason=str(a.get("reason") or "")))


def handle_return_to_reactor(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.camera(key, "reactor", surface=a.get("surface"),
                                     reason=str(a.get("reason") or "")))


def handle_set_mode(args: dict, **kwargs: Any) -> str:
    key = channel.canonical_key(kwargs.get("session_id"))
    a = args or {}
    return _run(lambda: STORE.set_mode(key, str(a.get("mode") or ""), surface=a.get("surface"),
                                       reason=str(a.get("reason") or "")))


def handle_undo(args: dict, **kwargs: Any) -> str:
    del args
    key = channel.canonical_key(kwargs.get("session_id"))
    return _run(lambda: STORE.undo(key, origin="agent"))


#: Tool name → handler, in :data:`TOOLS` order (``register()`` walks this).
HANDLERS: dict[str, Callable[..., str]] = {
    "hud_list_panels": handle_list_panels, "hud_open": handle_open, "hud_close": handle_close,
    "hud_focus": handle_focus, "hud_move": handle_move, "hud_resize": handle_resize,
    "hud_arrange": handle_arrange, "hud_highlight": handle_highlight, "hud_pin": handle_pin,
    "hud_set_mode": handle_set_mode, "hud_undo": handle_undo,
    "hud_fly_to": handle_fly_to, "hud_zoom_to_fit": handle_zoom_to_fit,
    "hud_return_to_reactor": handle_return_to_reactor, "hud_band": handle_band,
}


# --------------------------------------------------------------------------- #
# Slash command  /hud_layout @<sid> get | arrange <preset> | undo | lock | unlock |
#                user <surface> <activity> <json> | forget [activity] | presets
# --------------------------------------------------------------------------- #
USAGE = (
    "usage: /hud_layout @<sid> get | arrange <preset> | undo | lock | unlock | "
    "user <surface> <activity> <json layout> | forget [activity] | presets"
)


def handle_command(raw_args: str) -> str:
    # `@<sid> <rest>` — the app prefixes the arg with the session id session.create
    # returned, because plugin command handlers are never handed the session.
    sid, rest = split_session(raw_args)
    if sid is None:
        return USAGE + " (prefix the argument with @<session_id>)"
    key = channel.canonical_key(sid)
    head, _, tail = rest.partition(" ")
    head = head.lower()
    tail = tail.strip()

    if head in ("", "get"):
        return json.dumps(STORE.get(key))
    if head == "presets":
        return json.dumps({"ok": True, "presets": PRESETS})
    if head == "arrange":
        if not tail:
            return _err("usage: /hud_layout @<sid> arrange <preset>")
        preset = tail.split()[0]
        # The person asked for it from the HUD: their own choice never counts as
        # JARVIS fighting them, so it bypasses manual-wins (force) but not the lock.
        return _run(lambda: STORE.arrange(key, preset=preset, force=True, reason=f"you chose {preset}"))
    if head == "undo":
        return _run(lambda: STORE.undo(key, origin="user"))
    if head in ("lock", "unlock"):
        return _run(lambda: STORE.lock(key, head == "lock"))
    if head == "forget":
        return _run(lambda: STORE.forget(tail.split()[0] if tail else None))
    if head == "user":
        # `user <surface> <activity> <json>`: the layout begins at the first '{'.
        brace = tail.find("{")
        if brace == -1:
            return _err("usage: /hud_layout @<sid> user <surface> <activity> <json layout>")
        words = tail[:brace].split()
        try:
            doc = json.loads(tail[brace:])
        except json.JSONDecodeError as exc:
            return _err(f"layout is not valid JSON ({exc})")
        surface = words[0] if words else None
        activity = words[1] if len(words) > 1 else None
        return _run(lambda: STORE.user_layout(key, surface, activity, doc))
    return _err(USAGE)
