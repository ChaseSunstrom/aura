# ADR-0023: The design system is the token source of truth

- **Status:** Accepted (Phase 2)
- **Reference:** the v2 plan §10 (D23); §3.5

## Context

The approval door, goal card, tool-call row and voice orb each exist three times (web Svelte, Android Compose, desktop-via-web) and drift.

## Decision

`design/tokens.json` (402 tokens) generates CSS vars and Compose tokens; the token-lint is a CI gate; components are deduped within each framework and bound across surfaces by `tests/contracts/*.json`, not by copy. The four screen states (loading/empty/error/ready) are enforced by `look.spec`.

## Consequences

Tokens are the single source; components stay per-framework but cannot drift because the shared contract tables bind them. See the `jarvis-design-system` skill.
