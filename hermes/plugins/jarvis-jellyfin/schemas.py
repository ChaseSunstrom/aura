"""Tool schemas for the ``jellyfin`` toolset.

``jellyfin_play`` carries ``item_name`` alongside ``item_id`` for one reason: the
approval prompt is built from the arguments, and an owner asked to approve
``'a1b2c3…'`` on ``'Living Room TV'`` cannot tell what is about to start.
"""
from __future__ import annotations

from .tools import ITEM_TYPES

JELLYFIN_LIBRARIES = {
    "name": "jellyfin_libraries",
    "description": "List the Jellyfin media libraries (Movies, Shows, Music …) the user can see. "
                   "Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

JELLYFIN_SEARCH = {
    "name": "jellyfin_search",
    "description": (
        "Search the Jellyfin libraries by name and return rows of name, type, year and id, plus "
        "series/season/episode for episodes. Read-only. Use the row's id with jellyfin_play. "
        "Matches names, not subtitles or plot summaries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Title or part of a title to look for."},
            "item_types": {"type": "array", "items": {"type": "string", "enum": list(ITEM_TYPES)},
                           "description": "Restrict to these item types (optional)."},
            "limit": {"type": "integer", "description": "Rows to return. Default 20, max 100."},
        },
        "required": ["query"],
    },
}

JELLYFIN_RESUME = {
    "name": "jellyfin_resume",
    "description": "What the user can continue watching, with how many seconds in they are and "
                   "the percentage played. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "Rows to return. Default 12."}},
        "required": [],
    },
}

JELLYFIN_SESSIONS = {
    "name": "jellyfin_sessions",
    "description": (
        "The Jellyfin clients connected right now: device, client, user, whether it can be "
        "remote-controlled, and — when it is playing — the item, the position in seconds and "
        "whether it is paused. Idle sessions are listed too, because a device must have a "
        "session before jellyfin_play can target it. Read-only."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

JELLYFIN_PLAY = {
    "name": "jellyfin_play",
    "description": (
        "Start playing an item on a named Jellyfin device. Something begins playing in a room, "
        "so this needs approval and the owner is shown the item and the device first. `device` is "
        "matched against the device, client or user name from jellyfin_sessions. Pass `item_name` "
        "as well as `item_id` so the approval prompt can name what will play."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "item_id": {"type": "string",
                        "description": "Item id from jellyfin_search or jellyfin_resume."},
            "item_name": {"type": "string",
                          "description": "Human name of that item, for the approval prompt."},
            "device": {"type": "string",
                       "description": "Device, client or user name from jellyfin_sessions."},
            "mode": {"type": "string", "enum": ["PlayNow", "PlayNext", "PlayLast"],
                     "description": "Default PlayNow."},
            "start_position_s": {"type": "integer",
                                 "description": "Start this many seconds in (optional)."},
        },
        "required": ["item_id", "device"],
    },
}
