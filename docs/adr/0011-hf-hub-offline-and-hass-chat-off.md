# ADR-0011: HF_HUB_OFFLINE in the image; the Home Assistant chat platform off

- **Status:** Accepted
- **Reference:** the migration plan §6 (D11)

## Context

faster-whisper's Hugging Face hub check hangs inside the container behind the exit node. Separately, `HASS_TOKEN` auto-enables Hermes's Home Assistant *chat platform*, a websocket subscriber we do not want.

## Decision

Set `HF_HUB_OFFLINE=1` in the image (the Whisper weights are baked via `--build-arg WHISPER_MODEL`, so nothing is lost) and switch the Home Assistant chat platform off in the template — only the `homeassistant` *toolset* is used.

## Consequences

No hub hang on start; no chat-platform reconnect loop. A rebuild that drops `HF_HUB_OFFLINE` will hang on the hub check. See [ADR-0012](0012-jarvis-core-agent-off.md).
