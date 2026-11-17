# ADR-0025: The sandbox is a disposable CT over the ssh terminal backend

- **Status:** Accepted (Phase 7); the CT is owner-blocked, and until it exists the model's shell runs under bubblewrap inside the container — [ADR-0045](0045-bubblewrap-sandbox.md), the interim
- **Reference:** the v2 plan §10 (D25)

## Context

Untrusted code and self-extension work must run somewhere isolated. Hermes's `terminal.backend` accepts `local|docker|ssh`. The old `jarvis-sandbox` container carried a `network_mode: none` invariant.

## Decision

The sandbox is a disposable CT reached over the ssh terminal backend (docker backend evaluated as the fallback), with snapshot-and-reset between tasks. The `network_mode: none` invariant and the egress audit stay in force for anything that must not reach the LAN.

## Consequences

Self-extension and untrusted code run in a resettable CT, not on the host. The egress guarantees are unchanged. See [ADR-0024](0024-governed-self-extension.md).
