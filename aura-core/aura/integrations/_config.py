"""Turning a config value into a list of dict blocks.

A domain's YAML block can be a single dict, a list of dicts, or absent. Several
integrations normalise it the same way before setting entities up; this holds
that one operation so they do not each keep a copy. ``template`` keeps its own
richer entries normaliser (it understands the ``sensor: {slug: {...}}``
nesting) and uses :func:`as_blocks` only for the outer list.
"""

from __future__ import annotations

from typing import Any


def as_blocks(config: Any) -> list[dict[str, Any]]:
    """``config`` as a list of dict blocks: one dict, a list of them, or none."""
    if config is None:
        return []
    if isinstance(config, dict):
        return [config]
    return [block for block in config if isinstance(block, dict)]


#: The same normalisation, named for a nested "entries" value at the call site.
as_entries = as_blocks
