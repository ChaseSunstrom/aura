# ADR-0014: The JARVIS host is this LXC; the pipeline builds on a runner there; the registry is HTTPS

- **Status:** Accepted
- **Reference:** the migration plan §6 (D14)

## Context

The owner declared the dev LXC *is* production. The owner's shared CI runners cannot build or reach the tailnet (their dind never becomes healthy; their job containers cannot reach the tailnet).

## Decision

The stack lives in `/opt/jarvis-host` (the checkout stays in `/opt/jarvis`); `deploy.sh` reaches it over its own tailnet name with a dedicated deploy key; a `jarvis`-tagged docker runner on the host builds the images and runs the smoke stack. The GitLab registry on `:5050` is HTTPS behind the tailnet proxy.

## Consequences

Deploy and rollback go through `scripts/deploy.sh` over the tailnet; rollback restores only a tag the registry still has. The CVE gate is real (images apply security updates; the web runtime carries no npm; the Hermes image drops the bundled docker CLI). See the [runbooks index](../runbooks/README.md#deploy-roll-out-roll-back).
