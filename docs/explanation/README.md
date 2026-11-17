# Explanation

Understanding-oriented documentation: how the system is put together and why. For the
decisions behind these designs, see the [ADRs](../adr/README.md).

## Architecture

- **[Architecture](architecture.md)** — the whole system: the agent, the adapter, the
  front-ends, the house service.
- **[Standalone](standalone.md)** — running the house service on its own.

## Security

- **[Security](security.md)** — the security posture: tokens, network, the approval gate,
  the taint boundary.
- **[Threat model](THREAT_MODEL.md)** — what is defended against, and what is out of scope.
- **[Prompt injection](injection.md)** — the untrusted-content fence and how it holds.
- **[Voice identity](voice-identity.md)** — speaker verification and what it does not cover.
- **[Private chats](private-chats.md)** — a conversation that is not logged or stored, and
  what it does not cover.

## Design decisions

- **[Tooling decisions](TOOLING_DECISIONS.md)** — the tools chosen and measured, and why.
- **[Removed](removed.md)** — what was dropped in the rebuild, and where it went.
