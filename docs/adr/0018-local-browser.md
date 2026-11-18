# ADR-0018: Local browser via agent-browser and a baked Chromium, with a persistent profile and a CDP connect path

- **Status:** Accepted (Phase 3); the engine superseded by Hermes v0.23 (T-1519, 2026-11-18)

> Hermes v0.23 removed agent-browser and the per-action browser tools. The model now drives the
> Chromium the Hermes image bakes through one `browser_exec` tool; the persistent profile is
> `browser.user_data_dir` on the volume, and the browser runs beside the gateway
> (`bot_desktop.placement: gateway`, DEVIATIONS.md §31). The decision below stands for why the
> browser is local; its mechanism is history.
- **Reference:** the v2 plan §10 (D18)

## Context

The cloud browser backends (browser-use, browserbase, firecrawl) need keys and leave the tailnet. The owner needs logged-in sessions to survive restarts and, for some sites, their real desktop browser.

## Decision

Local browser via `agent-browser` + a baked Chromium (Lightpanda evaluated as the lighter alternative), with a persistent profile dir on the Hermes volume and a CDP `/browser connect` path to the owner's real desktop session. Purchases, sends and account changes sit behind a `/browser` approval.

## Consequences

Browsing stays on the tailnet; logins survive restarts via the volume-backed profile. Risky actions are gated. See [ADR-0019](0019-local-extraction-by-benchmark.md).
