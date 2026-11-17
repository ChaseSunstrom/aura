"""Keep the car's hot tools out of the tool-search bridge.

Hermes defers every plugin tool behind ``tool_search`` → ``tool_describe`` →
``tool_call`` and has no setting to keep one eager (``tools.tool_search.defer``
only adds to the deferred set). A drive uses the same ten tools nearly every
turn, and state.db showed each car turn paying one to three lookup round trips
(two to six seconds each) before it acted. Keeping those ten visible costs about
3k tokens of schema; turning the bridge off would cost all 151 (23k).

The hook wraps ``tools.tool_search.is_deferrable_tool_name``, which
``classify_tools`` looks up by module global on every assembly. It is a private
name: if a Hermes bump moves it, :func:`install` logs and returns False, and
every tool defers as before. It does not make a tool callable that was not
already registered, and the approval matrix sees the call the same either way.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

#: The tools a drive reaches for: texts/reminders/calls-out, music, routes,
#: "where is …", "do X when I get to …", and the place/person lookups a routine
#: needs first (the 2026-10-08 drive spent 7-14 s a turn finding these four).
EAGER = frozenset({
    "phone_action", "phone_media", "car_navigate", "place_search", "routine_add",
    "routine_list", "place_remember", "place_list", "people_find", "person_remember",
    "route_eta",
    # Not a car tool: the most asked question in any room, and a deferred one cost the
    # 2026-11-08 "what's the temperature" turn a lookup before it fell back to web_search.
    "weather_forecast",
    # Not a car tool either: production had never called it (0 of 78 HUD draws, 2026-11-08)
    # because it sat behind the bridge while SOUL pointed at hud_show; ~700 tokens of schema.
    "hud_project",
    # Not car tools: both refuse a turn that has read outside text, and the bridge's own
    # tool_describe lookup counts as that, so behind the bridge they were refused every time
    # (seen live 2026-11-12: lookup, then "not filed: this turn read outside text"). The
    # model also never reached for complaint_file unprompted while it saw only its name.
    "complaint_file", "later_add",
})


def install(module=None) -> bool:
    """Wrap ``module.is_deferrable_tool_name`` (default: Hermes' ``tools.tool_search``)."""
    if module is None:
        try:
            import tools.tool_search as module  # type: ignore[no-redef]
        except ImportError:
            _LOGGER.debug("no Hermes tool_search here (tests, docs): nothing to keep eager")
            return False
    original = getattr(module, "is_deferrable_tool_name", None)
    if original is None:
        _LOGGER.warning("car tools stay deferred: Hermes moved tools.tool_search.is_deferrable_tool_name")
        return False
    if getattr(original, "_jarvis_eager", False):
        return True  # a plugin reload must not stack wrappers

    def is_deferrable_tool_name(name, defer_tools=None):
        return name not in EAGER and original(name, defer_tools)

    is_deferrable_tool_name._jarvis_eager = True  # type: ignore[attr-defined]
    module.is_deferrable_tool_name = is_deferrable_tool_name
    return True
