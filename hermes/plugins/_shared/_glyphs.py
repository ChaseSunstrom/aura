# @generated from design/tokens.json and tests/contracts/reactor.json — DO NOT EDIT. Run `python3 design/build.py`.
"""The reactor's eleven states as a chat platform can carry them (GLASS_HUD §10).

``STATES[name]`` is the state's static glyph (the reduced-motion column of the
table), its ASCII stand-in, its word, what the glyph depicts and its colour C as
hex. ``PRECEDENCE`` is the contract's order, highest first; ``goal`` is not in it
because it is the overlay, worn on top of whichever state won.

``_messages.py`` is the only intended reader. Nothing else in a plugin should
need a glyph: if it does, it wants a template.
"""
from __future__ import annotations

#: state -> glyph, ascii, word, meaning, color.
STATES: dict[str, dict[str, str]] = {
    "kill": {"glyph": "◯", "ascii": "X", "word": "Kill switch", "meaning": "single red ring", "color": "#e0344b"},
    "offline": {"glyph": "◌", "ascii": ".", "word": "Offline", "meaning": "hollow ring", "color": "#ffb547"},
    "alert": {"glyph": "⊖", "ascii": "!", "word": "Alert", "meaning": "ring with a bar", "color": "#ff6b8b"},
    "attention": {"glyph": "◡", "ascii": "n", "word": "Needs you", "meaning": "ring with a gap at the top", "color": "#ffb547"},
    "warming": {"glyph": "○", "ascii": "o", "word": "Warming up", "meaning": "dim ring", "color": "#ff9e2c"},
    "speaking": {"glyph": "◎", "ascii": "@", "word": "Speaking", "meaning": "three rings", "color": "#9beaf6"},
    "listening": {"glyph": "●", "ascii": "*", "word": "Listening", "meaning": "filled core", "color": "#3fd0e8"},
    "tool": {"glyph": "◔", "ascii": "%", "word": "Working", "meaning": "segmented ring", "color": "#3fd0e8"},
    "thinking": {"glyph": "◐", "ascii": "d", "word": "Thinking", "meaning": "half ring", "color": "#3fd0e8"},
    "idle": {"glyph": "○", "ascii": "o", "word": "Ready", "meaning": "steady ring", "color": "#2c8fa3"},
    "goal": {"glyph": "⊙", "ascii": "o.", "word": "On a goal", "meaning": "dot on ring", "color": "#2c8fa3"},
}

#: Highest first; the overlay is absent because it cannot win.
PRECEDENCE: tuple[str, ...] = ("kill", "offline", "alert", "attention", "warming", "speaking", "listening", "tool", "thinking", "idle")

__all__ = ["STATES", "PRECEDENCE"]
