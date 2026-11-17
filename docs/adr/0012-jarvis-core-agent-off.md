# ADR-0012: jarvis-core runs with the agent loop off

- **Status:** Accepted
- **Reference:** the migration plan §6 (D12)

## Context

With Hermes as the agent ([ADR-0006](0006-jarvis-core-house-service.md)), jarvis-core must not also answer conversation requests, or two agents would run.

## Decision

jarvis-core keeps running as the house service with `llm.enabled: false` (a new switch, `AURA_AGENT_ENABLED`), so `POST /api/conversation/process` answers `501` and Hermes is the only agent loop.

## Consequences

The house exposes state and services, not a chat agent. A `501` from `/api/conversation/process` is expected, not a fault. See the [jarvis-core runbook](../runbooks/jarvis-core.md).
