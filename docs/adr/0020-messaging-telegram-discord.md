# ADR-0020: Telegram and Discord are the messaging surfaces

- **Status:** Accepted (Phase 4)
- **Reference:** the v2 plan §10 (D20)

## Context

The brief wants JARVIS reachable from chat. Both Telegram and Discord ship as bundled gateway platforms; enabling them gives the same session, memory and goals from the phone.

## Decision

Enable Telegram and Discord with allowlists and DM pairing. Voice notes route through Whisper; approvals are answerable from chat. Signal is added only if `signal-cli` exists on the host.

## Consequences

The phone gets the full agent over chat, behind an allowlist and pairing. Signal is conditional on the host tool.
