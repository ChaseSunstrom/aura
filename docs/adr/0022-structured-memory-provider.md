# ADR-0022: One structured memory provider, chosen by benchmark, alongside the built-in memory

- **Status:** Accepted (Phase 5)
- **Reference:** the v2 plan §10 (D22); §0

## Context

Eight external memory providers ship; two are self-hostable (mem0-qdrant, honcho). The built-in MEMORY.md/USER.md is always on and is the fast layer.

## Decision

Pick one structured provider by the memory-recall benchmark, alongside the always-on built-in memory. A locally-served embedding model backs retrieval over notes and documents. Memory writes are HUD-reviewable; credentials are never stored.

## Consequences

One external provider active at a time, chosen by evidence. The built-in layer stays the fast path. Retrieval is local.
