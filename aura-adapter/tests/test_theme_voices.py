"""The adapter speaks each theme persona in her pack's voice (T-0861, design/AURA/PROMPT.md §3).

The adapter ships without themes/, so its map is typed; this pins it to the packs, so a
pack's new voice that the adapter does not know fails here rather than on the speaker.
"""
from __future__ import annotations

import json
from pathlib import Path

from aura_adapter.app import DEFAULT_VOICE, THEME_VOICES

THEMES = Path(__file__).resolve().parents[2] / "themes"


def test_every_theme_persona_speaks_in_her_packs_voice():
    packs = {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in THEMES.glob("*/theme.json")}
    assert THEME_VOICES == {tid: pack["voice"]["voiceId"] for tid, pack in packs.items()}


def test_a_line_with_no_voice_is_said_in_auras():
    assert DEFAULT_VOICE == "af_heart"
