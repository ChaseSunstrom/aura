# ADR-0002: Keep model thinking on

- **Status:** Accepted
- **Reference:** the migration plan §6 (D2)

## Context

The qwen3 reasoning parser can be told to skip its thinking block (`enable_thinking=false`). Tool calls were verified to populate correctly with thinking on.

## Decision

Keep model thinking ON; do not set `enable_thinking=false`.

## Consequences

Tool calls remain reliable. The config template carries the `enable_thinking: false` escape hatch commented out, to be enabled only if a future vLLM build stops populating `tool_calls` with thinking on. A later measurement confirmed dropping the reasoning block on spoken turns costs intelligence.
