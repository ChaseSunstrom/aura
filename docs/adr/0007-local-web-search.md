# ADR-0007: Web search stays local via SearXNG

- **Status:** Accepted
- **Reference:** the migration plan §6 (D7); the v2 plan §0

## Context

The brief allows cloud search backends (Exa, Firecrawl) but they need keys and leave the tailnet. A local SearXNG already runs.

## Decision

Web search stays local — Hermes's `searxng` backend (`SEARXNG_URL`) against a stack-local SearXNG; no Exa/Firecrawl keys anywhere.

## Consequences

No search traffic leaves the tailnet and no keys are stored. The stack bundles its own SearXNG because the separate SearXNG CT has no egress. Phase 3 sets `web.search_backend: searxng` explicitly so it cannot silently fall back. See the [searxng runbook](../runbooks/searxng.md).
