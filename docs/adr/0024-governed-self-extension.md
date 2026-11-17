# ADR-0024: The self-extension loop is the 'anything' mechanism, and every step is governed

- **Status:** Accepted (Phase 5)
- **Reference:** the v2 plan §10 (D24); §0

## Context

The brief's self-evolution (DSPy/GEPA) is a separate repo not in this build. JARVIS still needs a way to grow new capabilities.

## Decision

The governed self-extension loop is the growth mechanism: spec → scaffold from repo templates → tests + benchmark tasks → sandbox run → GitLab MR (diff, test output, risk note) → CI + owner approval → deploy → reload → verify. No auto-merge, ever. Proven once on a real missing capability and kept as the reference MR.

## Consequences

New capabilities arrive through a reviewed, tested, approved MR — never auto-merged. This substitutes for the absent DSPy/GEPA repo. See [ADR-0025](0025-sandbox-disposable-ct.md) and [ADR-0027](0027-gepa-dspy-out-of-scope.md).
