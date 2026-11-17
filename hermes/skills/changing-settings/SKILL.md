---
name: changing-settings
description: "Change a setting yourself: voice pace, units, display. Use when asked to set or stop something about how JARVIS or the house behaves."
version: 0.1.0
author: JARVIS rebuild
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [settings, config]
    related_skills: [aura-voice, capability-gap]
---

# Changing a setting yourself

The owner says "speak slower", "use metric units", "reduce motion". You change it; they do not
open a settings page.

1. `settings_find` with their words. It returns the matching keys, each with its current
   value, `live` (takes effect now; false means it needs a restart, say so) and `security`.
2. Pick the one key that matches what they meant. Two close matches, or none: say so and
   ask, or file the gap (`capability-gap`). Never set a neighbouring key to look helpful.
3. `settings_set` with the key and a value of the key's type.
   - `security: false`: it just happens and is logged. Tell the owner what changed, from what,
     and that `settings_undo` puts it back.
   - `security: true` (auth, approval rules, device access): the owner gets a card with the old
     and new value. Wait for the answer; never retry a refused one in other words.

"Use the JARVIS voice" is `persona_switch` (AURA, JARVIS), not a setting: a
persona carries its own voice and name. A `voice.*` key is the engine's voice and pace.

Only keys the house actually has can be set. If `settings_find` finds nothing (the lock timer is
a per-browser console choice, nothing announces backups), say so and file `capability-gap`;
never set a neighbouring key. A key the house gains but nobody has reviewed as plain asks first.
