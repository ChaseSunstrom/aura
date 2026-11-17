"""Tool schemas for the ``plex`` toolset. All four are read-only; none can play or change anything."""
from __future__ import annotations

_LIMIT = {"type": "integer", "description": "Rows to return. Default %d, max 50."}

PLEX_LIBRARIES = {
    "name": "plex_libraries",
    "description": "List the libraries (Movies, Shows, Music …) on the Plex server. Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

PLEX_RECENT = {
    "name": "plex_recent",
    "description": "What was added to the Plex server lately, newest first: title, type, year, "
                   "the show or artist it belongs to, the library and the date added. Read-only.",
    "parameters": {"type": "object",
                   "properties": {"limit": {**_LIMIT, "description": _LIMIT["description"] % 12}},
                   "required": []},
}

PLEX_NOW_PLAYING = {
    "name": "plex_now_playing",
    "description": "What is playing on the Plex server right now: the title, who is watching, on "
                   "which device, whether it is paused and how many seconds in. An empty list "
                   "means nothing is playing. Read-only; it cannot control playback.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

PLEX_SEARCH = {
    "name": "plex_search",
    "description": "Search the Plex libraries by name and return title, type, year, and the show "
                   "or artist for episodes and tracks. Read-only. Matches names, not subtitles "
                   "or plot summaries.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Title or part of a title to look for."},
            "limit": {**_LIMIT, "description": _LIMIT["description"] % 20},
        },
        "required": ["query"],
    },
}
