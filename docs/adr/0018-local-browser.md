# ADR-0018: Local browser via agent-browser and a baked Chromium, with a persistent profile and a CDP connect path

- **Status:** Accepted (Phase 3)
- **Reference:** the v2 plan §10 (D18)

## Context

The cloud browser backends (browser-use, browserbase, firecrawl) need keys and leave the tailnet. The owner needs logged-in sessions to survive restarts and, for some sites, their real desktop browser.

## Decision

Local browser via `agent-browser` + a baked Chromium (Lightpanda evaluated as the lighter alternative), with a persistent profile dir on the Hermes volume and a CDP `/browser connect` path to the owner's real desktop session. Purchases, sends and account changes sit behind a `/browser` approval.

## Consequences

Browsing stays on the tailnet; logins survive restarts via the volume-backed profile. Risky actions are gated. See [ADR-0019](0019-local-extraction-by-benchmark.md).
