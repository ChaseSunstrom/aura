# ADR-0019: The local extraction backend is chosen by benchmark, not by preference

- **Status:** Accepted (Phase 3)
- **Reference:** the v2 plan §10 (D19)

## Context

Local content extraction is not turnkey. Two local options exist (self-hosted Firecrawl and a Crawl4AI/readability plugin) and preference is a poor way to choose.

## Decision

Both backends are wired and scored on the web-research tasks; the higher scorer is kept and set as `web.extract_backend`. No cloud extraction fallback.

## Consequences

The extract backend is an evidence-based choice recorded in the September 2026 build log. No extraction traffic leaves the tailnet.
