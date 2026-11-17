# ADR-0006: jarvis-core stays a house service; the orchestrator, sandbox and browser are retired

- **Status:** Accepted
- **Reference:** the migration plan §6 (D6)

## Context

The pre-Hermes design had jarvis-core running its own agent loop plus separate orchestrator, sandbox and browser services. Hermes is now the agent, and two agent loops would compete.

## Decision

jarvis-core stays as a *house service* (its agent loop off); Hermes reaches it through the built-in `homeassistant` toolset (`/api/states`, `/api/services/{domain}/{service}`, verified against jarvis-core `:8080`) plus the `smart-home` plugin. jarvis-orchestrator, jarvis-sandbox and jarvis-browser are retired.

## Consequences

One agent loop (Hermes). The house is controlled through a toolset, not a second brain. The retired service directories are gone from the tree; see [ADR-0012](0012-jarvis-core-agent-off.md) for the agent-off switch and [ADR-0025](0025-sandbox-disposable-ct.md) for the replacement sandbox.
