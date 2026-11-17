# ADR-0035: Settings gets three tiers over the one schema, and the tier is a schema field

- **Status:** Accepted (2026-09-08)
- **Comes from:** the v3 plan §6, the v3 brief Part 1 ("Settings, radically simpler")
- **Builds on:** [ADR-0017](0017-one-settings-schema.md) (one settings schema), [ADR-0032](0032-the-room-has-no-tabs.md) (settings is a menu, not a tab)

## Context

`aura-schema` is the single source of truth for 167 settings; `make schema` generates the config
template, the settings reference and `tests/contracts/hermes-settings.json`, which the console and
the phone both render. v2 measured that 166 of the 167 rows write somewhere, so the problem is not
honesty — it is that one flat list of 167 rows behind an "advanced" switch is not usable by a person
who wants to turn the wake word off.

The brief asks for three tiers (Basic, Advanced, Expert), plain-language descriptions, a "what does
this affect" line, reset-to-default, search across all tiers, live-apply where possible, and a
natural-language settings tool writing through the same validated schema.

The trap is obvious: a hand-maintained list of "which settings are basic" is a second source of
truth, and it will be wrong the first time somebody adds a setting.

## Decision

**The tier is a field on the schema, not a list beside it.**

- Each field gains `tier: basic | advanced | expert`, defaulting to `advanced`. A new setting
  therefore appears somewhere sensible without anyone editing a second file, and the seven Basic
  rows are declared where the setting is declared.
- `make schema` emits `tier` into `tests/contracts/hermes-settings.json` alongside the group, widget
  and description each row already carries. Every surface reads that one contract.
- **Basic** is one screen, seven rows: voice on/off, wake word, model preference, notifications and
  quiet hours, theme, approval strictness, density.
- **Advanced** is every row grouped by area, searchable, with description and default visible.
- **Expert** is the raw schema editor: validation on every keystroke and a diff before save.
- Search spans all three tiers and matches name, description and current value; a hit in Expert
  shows its tier so the reader knows where they landed.
- `reset-to-default` is generated from the schema's own default, so it cannot go stale.
- Live-apply where the runtime allows it; the row says plainly where a restart is needed, using the
  same word in the row, the toast and the history.
- The natural-language path is the existing `aura-settings` plugin extended with a `set` tool that
  validates against the same Pydantic model before writing. "Turn off the wake word at night" is the
  same write path as the switch.

## Consequences

- **Good.** One list, three views. No second file to drift. A setting added to the schema is
  reachable, described and resettable the moment it exists.
- **Good.** The settings tool and the settings UI cannot disagree: both go through the schema's
  validation, and an invalid value is refused identically in both.
- **Bad.** Every existing field needs a `tier` decision at once, and "advanced" as the default means
  a careless addition lands in the middle tier rather than being noticed. The schema test asserts
  that exactly seven fields are `basic`, so a drive-by promotion fails CI.
- **Neutral.** Expert is a real editor over the raw document, which means it can express states the
  two friendlier tiers cannot. That is the point of having it, and the diff-before-save is what
  makes it safe.
