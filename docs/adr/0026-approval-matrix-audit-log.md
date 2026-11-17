# ADR-0026: The approval matrix and the unified audit log live in the schema and one structured log

- **Status:** Accepted (Phase 7)
- **Reference:** the v2 plan §10 (D26)

## Context

Approvals were scattered and the audit trail lived in three separate jsonl logs, hard to query from the HUD.

## Decision

Per tool and per plugin `auto|ask|deny` with platform overrides live in the settings schema; destructive homelab actions always ask. Every tool call, approval, memory write, outbound message and deploy is logged in one rotated structured file, queryable from the HUD.

## Consequences

One audit log replaces three; approvals are typed config, not scattered hooks. See [ADR-0017](0017-one-settings-schema.md).
