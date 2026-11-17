"""Reading a Hermes approval event: which tool is this prompt about?

Hermes carries **no structured tool field** on an approval. The payload the API
server emits as ``approval.request`` is::

    {"command": "<write_file> (plugin approval rule)",
     "pattern_key": "plugin_rule:file:sealed",
     "pattern_keys": [...], "description": "...", "request_id": "...",
     "allow_permanent": true, "allow_session": true,
     "event": "approval.request", "run_id": "...", "choices": [...]}

The tool's name exists only inside ``command``, rendered by
``tools/approval.py`` as ``<tool_name> (plugin approval rule)``, and — for a
plugin-gated call — as the head of ``pattern_key`` after ``plugin_rule:``.

This mattered more than a formatting detail. ``bench/checkers.py`` read
``approval.get("tool") or approval.get("tool_name")``, neither of which exists,
so every ``approval_gate`` checker carrying a ``tool:`` filter matched zero
approvals however loudly the agent asked: ``safety-house-006`` could not have
passed even with a perfect gate in front of it. Three consumers now need this
parse — the audit log, the HUD's approval card and the benchmark's checker —
and three hand-rolled copies of an undocumented string format is three chances
to disagree about what the agent asked permission for.

The module has no imports beyond ``re`` on purpose: the benchmark runner has to
be able to import it without pulling in ``aura_schema`` or the rest of the
plugin machinery.
"""
from __future__ import annotations

import re
from typing import Any

#: ``<tool_name> (plugin approval rule)`` — ``display_target`` in
#: ``tools/approval.py``, for a call gated by a plugin's ``pre_tool_call`` hook.
TOOL_IN_COMMAND = re.compile(r"^<(?P<tool>[A-Za-z0-9_.:-]+)>\s*\(plugin approval rule\)")

#: ``plugin_rule:<rule_key>`` — the allow-list grain an ``[a]lways`` answer
#: remembers. Only useful when the rule key starts with the tool name, which is
#: why ``command`` is tried first.
_PLUGIN_RULE_PREFIX = "plugin_rule:"


def tool_of(approval: dict[str, Any]) -> str:
    """The tool an approval event is about, or ``""`` for a shell-command prompt.

    Empty is a real answer, not a failure: Hermes' own Tier-2 dangerous-command
    gate puts the literal shell command in ``command``, and that prompt is about
    a command rather than about a tool. A caller filtering by tool name should
    treat empty as "not this tool" rather than as "no approval happened".
    """
    if not isinstance(approval, dict):
        return ""
    # A structured field, if a future Hermes ever adds one. Tried first so this
    # module keeps working when the string format it exists to parse goes away.
    for key in ("tool", "tool_name"):
        value = approval.get(key)
        if isinstance(value, str) and value:
            return value
    return from_strings(str(approval.get("command") or ""),
                        str(approval.get("pattern_key") or ""))


def from_strings(command: str, pattern_key: str = "") -> str:
    """The parse itself, for a caller that has the two strings and not the event."""
    match = TOOL_IN_COMMAND.match((command or "").strip())
    if match:
        return match.group("tool")
    key = (pattern_key or "").strip()
    if key.startswith(_PLUGIN_RULE_PREFIX):
        return key[len(_PLUGIN_RULE_PREFIX):].split(":", 1)[0]
    return ""
