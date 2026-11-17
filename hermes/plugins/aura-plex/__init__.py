"""aura-plex — the household Plex server, readable and nothing more.

Registers the ``plex`` toolset: ``plex_libraries``, ``plex_recent``, ``plex_now_playing`` and
``plex_search``, all read-only GETs. There is no playback tool, no hook and no slash command. The
HTTP work lives in :mod:`tools`.

Every tool needs the token as well as the address, so one ``check_fn`` covers all four: an address
with no token would offer tools that can only ever answer 401.
"""
from __future__ import annotations

from aura_schema import from_env

from . import schemas, tools

TOOLSET = "plex"


def _available() -> bool:
    cfg = from_env()
    return bool(cfg.plex_url and cfg.plex_token)


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped PLEX_* value names itself at load
    for name, schema, handler, emoji in (
        ("plex_libraries", schemas.PLEX_LIBRARIES, tools.handle_libraries, "🎞️"),
        ("plex_recent", schemas.PLEX_RECENT, tools.handle_recent, "🆕"),
        ("plex_now_playing", schemas.PLEX_NOW_PLAYING, tools.handle_now_playing, "📺"),
        ("plex_search", schemas.PLEX_SEARCH, tools.handle_search, "🔎"),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_available, emoji=emoji)
