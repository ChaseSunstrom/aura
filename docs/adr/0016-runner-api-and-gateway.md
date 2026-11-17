# ADR-0016: The runner uses the API server for ordinary tasks and the adapter /ws for gateway-only tasks

- **Status:** Accepted
- **Reference:** the v2 plan §10 (D16); §0

## Context

The API server does not dispatch slash commands (verified in the migration): goals, voice and plugin commands go through the TUI gateway, not `/v1/chat/completions`.

## Decision

The runner drives ordinary prompts via `/v1/runs` + `/v1/runs/{id}/events` (tool calls captured), and drives goals, voice and plugin commands through the adapter `/ws` (`command.dispatch` / `prompt.submit`).

## Consequences

The goal/voice/safety-approval categories are handled correctly; a runner that used only `/v1/chat/completions` would mis-handle them. See [ADR-0003](0003-hud-protocol-tui-gateway-ws.md).
