---
name: sky-over-the-house
description: Answer "when is the next ISS pass", "what's the moon tonight", "which planets are up" from the house's sky entities, in spoken-friendly form.
version: 0.1.0
---

# Sky over the house

The house service publishes sky entities (the old sky integration lives on there). Read them with the
Home Assistant tools instead of computing anything:

- `ha_list_entities(domain="sky")` — everything the sky integration exposes (ISS next pass, overhead now, moon phase, planets tonight).
- `ha_get_state("sky.iss_next_pass")` — ISO time of the next visible pass; convert to the user's local time (the house's timezone) and say
  "in about N minutes/hours" as well as the clock time.
- `ha_get_state("sky.moon")` — phase text (waning gibbous, …); attributes carry illumination when present.
- `ha_get_state("sky.planets_tonight")` — a list; read the attributes for rise/set.

Spoken answers: one sentence, no timestamps in ISO form, no attribute names. If an entity is `unavailable`, say the sky data is not
updating rather than guessing.
