# ADR-0003: The HUD protocol is the TUI-gateway JSON-RPC over WebSocket

- **Status:** Accepted
- **Reference:** the migration plan §6 (D3)

## Context

The apps need a rich, bidirectional protocol (streaming turns, approvals, goals, panels). The API server's chat-completions endpoint is generic and does not dispatch slash commands.

## Decision

The HUD protocol is the TUI-gateway JSON-RPC over WebSocket; the chat-completions API is for generic clients only.

## Consequences

The apps talk to JARVIS over the adapter's `/ws`, using `command.dispatch`, `prompt.submit` and the session methods. The contract lives in `docs/hermes-hud-protocol.md`, pinned by `tests/contracts/`. See also [ADR-0016](0016-runner-api-and-gateway.md).
