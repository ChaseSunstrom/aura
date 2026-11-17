# ADR-0034: One task store, projected from Hermes rather than owned by JARVIS

- **Status:** Accepted (2026-09-08)
- **Comes from:** the v3 plan §5, the v3 brief Part 1 ("Task surfaces")

## Context

Work reaches JARVIS through eight different mechanisms, and today each has its own panel and its own
shape: goals and subgoals (the `jarvis-goals` plugin), kanban cards (`hermes kanban`, SQLite at
`~/.hermes/kanban.db`), cron jobs (`hermes cron`, fifteen verbs), heartbeats (`hermes kanban
heartbeat`), recurring loops, event hooks (`hermes hooks`), webhooks (`hermes webhook`) and one-off
runs (`/v1/runs`). The brief asks for one coherent model of work surfaced as a timeline, a board and
a queue, with every task linking to its transcript, tool calls and artifacts, creatable and
cancellable from every surface including voice.

The obvious implementation is a JARVIS-owned table that every surface reads and writes. It is the
wrong one. Hermes already owns each of these objects and each has a CLI that the owner uses
directly; a second writer for the same fact is exactly the duplication v2 spent a phase deleting. A
cron job created in the HUD has to be the same object `hermes cron list` prints, or the two drift
inside a day and the HUD becomes a lie about the system.

## Decision

**One read model, no new database.** `TaskRecord` is a projection over the sources Hermes already
owns. Writes go back to the source's own verb; nothing is stored twice.

```
TaskRecord = {
  id:        string            # "<source>:<native id>", stable across restarts
  source:    goal | kanban | cron | webhook | hook | heartbeat | run
  kind:      goal | subgoal | card | schedule | trigger | run
  title:     string
  state:     queued | running | blocked | done | failed | paused | cancelled
  parent:    string | null     # a subgoal's goal, a card's epic
  created:   ISO-8601 UTC
  due:       ISO-8601 UTC | null
  schedule:  cron expression | interval | null
  links:     { transcript, tool_calls, artifacts }
  actions:   ("pause"|"resume"|"cancel"|"run"|"approve"|"steer")[]
}
```

Three projections over the one list, chosen by the panel, not by the data:

- **timeline** — what happened, ordered by event time, one row per state change.
- **board** — what is in flight, columns by `state`, cards grouped by `parent`.
- **queue** — what is scheduled, ordered by next fire time.

`actions` is computed from `source` + `state`, so a surface never has to know which CLI verb backs a
button; the adapter maps the action to the verb once.

## Consequences

- **Good.** `hermes cron list` and the HUD queue can never disagree. Nothing to migrate, nothing to
  back up, no second schema to keep in step with the settings schema.
- **Good.** Voice gets the same actions for free: `command.dispatch` already reaches the same
  adapter path the HUD uses.
- **Bad.** A projection is only as fast as its slowest source. Seven `hermes` invocations per
  refresh would be unusable, so the adapter caches per source with a short TTL and refreshes a
  single source on its own events rather than polling all seven.
- **Bad.** There is no place to put a field Hermes has no room for (a JARVIS-only label, say). If
  one is ever needed it goes in the kanban card's own metadata, not in a new table.
- **Neutral.** `id` carries the source in its prefix, which makes it obvious in a URL and in a log
  which subsystem a task came from.
