# ADR-0009: The front-ends keep their house screens and get a new Hermes HUD over the adapter

- **Status:** Accepted
- **Reference:** the migration plan §6 (D9)

## Context

The console and apps already had house/dashboard screens on the jarvis-core relay. The Hermes agent needs a new surface (voice, chat, approvals, goals, runs, settings).

## Decision

The front-ends keep their house/dashboard screens on the jarvis-core relay and get a new Hermes HUD (voice `/`, chat, approvals, goals, runs, settings·assistant/tools) over the adapter WebSocket.

## Consequences

Two data paths per app: the house (jarvis-core) and the HUD (adapter). The console runtime carries no npm. See the [jarvis-web runbook](../runbooks/jarvis-web.md) and `docs/hermes-hud-protocol.md`.
