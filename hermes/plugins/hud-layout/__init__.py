"""hud-layout — JARVIS arranges the HUD (the window manager's second contract).

Registers the ``hud_layout`` toolset — ``hud_list_panels`` / ``hud_open`` /
``hud_close`` / ``hud_focus`` / ``hud_move`` / ``hud_resize`` / ``hud_arrange`` /
``hud_highlight`` / ``hud_pin`` / ``hud_set_mode`` / ``hud_undo`` — and the
``/hud_layout`` slash command the HUD dispatches for its own state line (get,
arrange a preset, undo, lock/unlock, report a manual arrangement, forget). See
:mod:`layout` for the tree operations, the lock, manual-wins, undo, presets and
where preferences persist.

Everything Hermes-specific is imported lazily inside :mod:`layout` and
:mod:`channel`, so this package imports (and its tests run) without Hermes on
the path — the rule the sibling plugins follow.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared._approval_hook import guarded_command  # noqa: E402
from . import layout  # noqa: E402

logger = logging.getLogger(__name__)

_SURFACES = ", ".join(layout.SURFACES)
_MODES = ", ".join(layout.HUD_MODES)
_PRESETS = ", ".join(sorted(layout.PRESETS))
_TYPES = ", ".join(layout.PANEL_TYPES)
# Spelled into every description: the model otherwise guesses a panel id or a
# preset, wastes a turn on the error, and only then reads the schema.
_COMMON = (
    f" surface is one of {_SURFACES} (default web). Every change is animated, goes on the "
    "undo stack, and is REFUSED while the person's Lock layout is on — then say so and stop."
)

_SURFACE = {"type": "string", "enum": list(layout.SURFACES), "description": "Which HUD surface (default web)."}
_REASON = {"type": "string", "description": "One short clause shown to the person as why (e.g. 'a goal started')."}
_ID = {"type": "string", "description": "The panel id (from hud_list_panels)."}
_RECT = {
    "type": "object",
    "description": "Floating placement as fractions of the surface: {x, y, w, h} in 0..1.",
    "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                   "w": {"type": "number"}, "h": {"type": "number"}},
}
_LAYOUT = {
    "type": "object",
    "description": ("A full layout document {mode, tree, panels}: mode one of " + _MODES +
                    "; tree a panel id or {split:'row'|'column', ratio, children:[2]}; "
                    "panels [{id, type, rect?, pinned?}] naming every leaf."),
}

SCHEMAS: dict[str, dict] = {
    "hud_list_panels": {
        "name": "hud_list_panels",
        "description": (
            "What is on the HUD right now: mode, activity, the panels (id, type, pinned, rect) "
            "in tiling order, the focused panel, whether Lock layout is on, and the undo depth. "
            "Call it before moving or closing anything so you use a real panel id."
        ),
        "parameters": {"type": "object", "properties": {"surface": _SURFACE}, "required": []},
    },
    "hud_open": {
        "name": "hud_open",
        "description": (
            f"Open a panel on the HUD. type is one of: {_TYPES}. In Tiled mode it splits beside "
            "the target panel on the given edge (or the whole surface when no target); in "
            "Floating it takes rect (or a default spot); in Focus it joins the rail." + _COMMON
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "A stable id for the new panel (e.g. 'image')."},
                "type": {"type": "string", "description": "The panel type."},
                "beside": {"type": "string", "description": "Panel id to split next to (Tiled; optional)."},
                "edge": {"type": "string", "enum": layout.EDGES, "description": "Side of the target (default right)."},
                "share": {"type": "number", "description": "The new panel's share of the split, 0..1 (default 0.5)."},
                "rect": _RECT,
                "pinned": {"type": "boolean", "description": "Open it pinned (survives rearranges)."},
                "surface": _SURFACE, "reason": _REASON,
            },
            "required": ["id", "type"],
        },
    },
    "hud_close": {
        "name": "hud_close",
        "description": (
            "Close a panel; its split collapses onto the sibling. A panel the person pinned "
            "refuses unless force is true — prefer leaving it." + _COMMON
        ),
        "parameters": {
            "type": "object",
            "properties": {"id": _ID, "force": {"type": "boolean", "description": "Close even if pinned."},
                           "surface": _SURFACE, "reason": _REASON},
            "required": ["id"],
        },
    },
    "hud_focus": {
        "name": "hud_focus",
        "description": (
            "Bring one panel to Focus mode: it fills the surface, the rest become the rail. "
            "Use for an approval or a task the person must look at now." + _COMMON
        ),
        "parameters": {"type": "object", "properties": {"id": _ID, "surface": _SURFACE, "reason": _REASON},
                       "required": ["id"]},
    },
    "hud_move": {
        "name": "hud_move",
        "description": (
            "Re-seat a panel: beside a target panel on an edge (Tiled: target + edge + share), "
            "or to a rect (Floating)." + _COMMON
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": _ID,
                "target": {"type": "string", "description": "Panel id to sit next to (Tiled)."},
                "edge": {"type": "string", "enum": layout.EDGES, "description": "Side of the target (default right)."},
                "share": {"type": "number", "description": "The moved panel's share of the split (default 0.5)."},
                "rect": _RECT, "surface": _SURFACE, "reason": _REASON,
            },
            "required": ["id"],
        },
    },
    "hud_resize": {
        "name": "hud_resize",
        "description": (
            "Resize a panel: share is its part of its parent split, 0..1 (Tiled); rect is its "
            "new placement (Floating)." + _COMMON
        ),
        "parameters": {
            "type": "object",
            "properties": {"id": _ID, "share": {"type": "number", "description": "0..1 of the parent split."},
                           "rect": _RECT, "surface": _SURFACE, "reason": _REASON},
            "required": ["id"],
        },
    },
    "hud_arrange": {
        "name": "hud_arrange",
        "description": (
            f"Arrange the whole HUD for what is happening. preset is one of: {_PRESETS} (goal-start "
            "when a goal begins, image-open when an image arrives, approval-focus when approval is "
            "needed, model-warming on a cold start, voice-ambient while listening, browser-task when "
            "browsing) — or pass a layout tree and an activity. If the person saved their own "
            "arrangement for that activity, THEIRS is applied and the result says kept: 'user'; if "
            "they undid your last one, nothing changes and kept: 'declined' — respect both unless "
            "they ask (force). remember stores a layout you pass as your preference for the activity."
            + _COMMON
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "preset": {"type": "string", "enum": sorted(layout.PRESETS), "description": "A named choreography."},
                "layout": _LAYOUT,
                "activity": {"type": "string", "description": "What the layout is for (goal, image, approval, …)."},
                "force": {"type": "boolean", "description": "Apply even over the person's saved arrangement."},
                "remember": {"type": "boolean", "description": "Keep a passed layout as your preference for the activity."},
                "surface": _SURFACE, "reason": _REASON,
            },
            "required": [],
        },
    },
    "hud_highlight": {
        "name": "hud_highlight",
        "description": (
            "Pulse a panel's rim in the reactor's light for 1.2 s to draw the eye to it (not a "
            "scroll, not a modal). Allowed even while the layout is locked."
        ),
        "parameters": {"type": "object", "properties": {"id": _ID, "surface": _SURFACE, "reason": _REASON},
                       "required": ["id"]},
    },
    "hud_pin": {
        "name": "hud_pin",
        "description": "Pin (or unpin) a panel so later rearrangements keep it in place." + _COMMON,
        "parameters": {
            "type": "object",
            "properties": {"id": _ID, "pinned": {"type": "boolean", "description": "true to pin (default), false to unpin."},
                           "surface": _SURFACE, "reason": _REASON},
            "required": ["id"],
        },
    },
    "hud_set_mode": {
        "name": "hud_set_mode",
        "description": (
            f"Switch the window manager's mode: {_MODES}. Every panel is kept (Ambient hides "
            "them until the next mode change)." + _COMMON
        ),
        "parameters": {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": list(layout.HUD_MODES), "description": "The mode."},
                           "surface": _SURFACE, "reason": _REASON},
            "required": ["mode"],
        },
    },
    "hud_fly_to": {
        "name": "hud_fly_to",
        "description": (
            "Fly the camera to a panel and open it: the map moves rather than cutting, and the "
            "panel it lands on takes a full slot. Not a layout change — nothing opens or closes "
            "that was not asked for, and Lock layout does not stop it." + _COMMON
        ),
        "parameters": {
            "type": "object",
            "properties": {"panel": {"type": "string", "description": "The panel id to fly to."},
                           "surface": _SURFACE, "reason": _REASON},
            "required": ["panel"],
        },
    },
    "hud_zoom_to_fit": {
        "name": "hud_zoom_to_fit",
        "description": (
            "Frame everything: zoom out until every panel on the map is on screen. The way to "
            "show the whole system at once." + _COMMON
        ),
        "parameters": {"type": "object", "properties": {"surface": _SURFACE, "reason": _REASON},
                       "required": []},
    },
    "hud_return_to_reactor": {
        "name": "hud_return_to_reactor",
        "description": (
            "Return the camera to neutral, centred on the reactor at rest. The way back from "
            "anywhere; always available." + _COMMON
        ),
        "parameters": {"type": "object", "properties": {"surface": _SURFACE, "reason": _REASON},
                       "required": []},
    },
    "hud_band": {
        "name": "hud_band",
        "description": (
            "Move panels to an orbital band: inner is what is happening now, middle is context "
            "and tasks, outer is reference and archives. Bring a task's panels inward when it "
            "starts and push them outward when it is done." + _COMMON
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "panels": {"type": "array", "items": {"type": "string"},
                           "description": "The panel ids to move."},
                "band": {"type": "string", "enum": list(layout.BANDS), "description": "The band."},
                "surface": _SURFACE, "reason": _REASON,
            },
            "required": ["panels", "band"],
        },
    },
    "hud_undo": {
        "name": "hud_undo",
        "description": (
            "Undo your last layout change ('undo that', 'put it back'). Re-applies the layout it "
            "replaced and remembers that the person declined it for that activity."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_EMOJI = {
    "hud_list_panels": "🗂️", "hud_open": "🪟", "hud_close": "✖️", "hud_focus": "🎯", "hud_move": "↔️",
    "hud_resize": "📐", "hud_arrange": "🧩", "hud_highlight": "✨", "hud_pin": "📌", "hud_set_mode": "🔀",
    "hud_undo": "↩️", "hud_fly_to": "🛰️", "hud_zoom_to_fit": "🔭",
    "hud_return_to_reactor": "🎯", "hud_band": "🪐",
}


def register(ctx) -> None:
    for name in layout.TOOLS:
        ctx.register_tool(name=name, toolset=layout.TOOLSET, schema=SCHEMAS[name],
                          handler=layout.HANDLERS[name], emoji=_EMOJI[name])

    # JARVIS's per-activity preferences outlive the process through Hermes's
    # plugin state (a quota-bounded JSON file under HERMES_HOME/plugin-data).
    layout.attach_state(getattr(ctx, "state", None))

    ctx.register_command(
        layout.COMMAND_NAME,
        # Looked up per call, not bound at registration: tests swap the handler in place.
        handler=guarded_command(lambda raw: layout.handle_command(raw), "hud_layout", logger=logger),
        description=("HUD layout: get | arrange <preset> | undo | lock | unlock | "
                     "user <surface> <activity> <json> | forget [activity] | presets"),
        args_hint="@<session_id> get | arrange <preset> | undo | lock | unlock | user <surface> <activity> <json>",
    )
