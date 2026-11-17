# ADR-0001: Pin the Hermes version everywhere

- **Status:** Accepted
- **Reference:** the migration plan §6 (D1)

## Context

JARVIS is built on the Hermes Agent. A moving Hermes version would change tool wiring, config schema and gateway behaviour under the apps without warning.

## Decision

Pin Hermes to `v2026.8.31` (v0.21.0) everywhere it is named — the installer branch and the image tag.

## Consequences

Every verified behaviour in these docs is true for v0.21.0. Moving the pin is a deliberate change that re-verifies the toolset display names, config version and gateway deviations recorded against it.
