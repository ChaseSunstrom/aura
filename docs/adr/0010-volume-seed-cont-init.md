# ADR-0010: Seed the Hermes volume from an s6 cont-init step

- **Status:** Accepted
- **Reference:** the migration plan §6 (D10)

## Context

The official Hermes image starts its dashboard/serve service *before* the CMD runs, and that service discovers plugins once. A volume seeded only by the CMD entrypoint would miss plugins on first boot.

## Decision

The volume is seeded by an s6 `cont-init` step (`/etc/cont-init.d/03-jarvis-seed`, the same script as the CMD entrypoint). Because cont-init scripts do not inherit the container environment, the script imports `/run/s6/container_environment` itself.

## Consequences

Plugins and config are present before the dashboard service discovers them. A plugin added to the template but not seeded will not appear until a clean start. See the [jarvis runbook](../runbooks/jarvis.md).
