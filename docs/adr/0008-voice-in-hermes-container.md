# ADR-0008: Voice runs inside the Hermes container; the Wyoming containers are retired

- **Status:** Accepted
- **Reference:** the migration plan §6 (D8)

## Context

The pre-Hermes voice path used separate Wyoming containers (whisper, piper, openwakeword). Hermes can host STT/TTS itself.

## Decision

Voice = faster-whisper + Piper inside the Hermes container, relay endpoints through the adapter; the Wyoming containers are retired; the wake word stays on-device (Android).

## Consequences

One fewer set of containers, and the audio relay rides the adapter. The Whisper weights are baked into the image (see [ADR-0011](0011-hf-hub-offline-and-hass-chat-off.md)). The legacy Wyoming services still present in the house stack are the old path (see the [side-services runbook](../runbooks/side-services.md)).
