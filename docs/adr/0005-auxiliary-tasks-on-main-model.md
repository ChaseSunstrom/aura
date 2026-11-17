# ADR-0005: The goal judge and auxiliary tasks stay on the main model

- **Status:** Accepted
- **Reference:** the migration plan §6 (D5)

## Context

llama-swap serves one model at a time. Routing an auxiliary task (title generation, compression, approval, goal judge, vision) to a second model forces a swap into the hot path.

## Decision

The goal judge and all auxiliary tasks stay on the main model.

## Consequences

No swap latency is forced by auxiliary work. [ADR-0021](0021-single-model-default.md) revisits big↔big routing for reasoning-heavy categories only on a measured margin.
