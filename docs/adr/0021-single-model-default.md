# ADR-0021: Default to a single model; add a route only on a measured margin

- **Status:** Accepted (Phase 5)
- **Reference:** the v2 plan §10 (D21); §0

## Context

llama-swap serves one model at a time, so any big↔big route forces a swap into the hot path. The plugin lists three models; the brief named two.

## Decision

`qwen3.8-27b` is the default with zero swaps. The 35b is trialled on reasoning-heavy categories; a route is added only where its win outweighs the swap latency it forces. Auxiliary tasks stay on the main model unless a small model measurably co-resides at 256k. The `qwen3.8-flash-next-79b` is verified for fit before it is considered.

## Consequences

The hot path stays swap-free by default; any route is justified by a measured margin. See [ADR-0005](0005-auxiliary-tasks-on-main-model.md).
