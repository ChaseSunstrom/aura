# ADR-0027: GEPA/DSPy self-evolution is out of scope for this build

- **Status:** Accepted
- **Reference:** the v2 plan §10 (D27)

## Context

The brief leans on DSPy/GEPA prompt tuning, but it is a separate repo not present in this Hermes install.

## Decision

GEPA/DSPy self-evolution is out of scope unless the harness is solid and the external repo is added. The capability-growth path is the governed self-extension loop ([ADR-0024](0024-governed-self-extension.md)). If added later, only changes that raise the JCB score are kept.

## Consequences

No dependency on an absent repo. Growth happens through reviewed MRs today; any future GEPA work is gated on a measured JCB improvement.
