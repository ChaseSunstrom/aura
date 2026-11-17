# ADR-0015: Build and baseline the JCB before any Phase 2-8 change

- **Status:** Accepted
- **Reference:** the v2 plan §10 (D15)

## Context

The brief's ground rule: a benchmark must exist and a baseline must be recorded before any v2 code change, so every later change can be measured against it.

## Decision

The JARVIS Capability Benchmark (`bench/`: YAML tasks, a runner that captures tool calls via the runs/stream events, `bench/results/*`) lands first; the baseline run is recorded in the September 2026 build log and `bench/results/baseline.json`.

## Consequences

The baseline number is the reference for every later change and the > 2-point regression gate. No Phase 2-8 item starts until it is recorded. Capabilities are mapped to tasks in `CAPABILITIES.md`.
