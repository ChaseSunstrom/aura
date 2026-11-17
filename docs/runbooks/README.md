# Runbooks

One runbook per service in the JARVIS stacks: how to start and stop it, where its
health and logs are, and the failures that have actually happened to it (with the
date and the decision that recorded them). Nothing here is invented — every incident
traces to the September 2026 build log, the migration plan, `DEVIATIONS.md`, or a
comment in the compose file it describes.

## The two stacks

JARVIS runs as two independent Docker Compose stacks on the JARVIS host (the dev
LXC, which the owner declared *is* production — [ADR-0014](../adr/0014-host-is-lxc-runner-registry.md)):

| Stack | File | Services |
| --- | --- | --- |
| **Hermes stack** (v2) | `deploy/docker-compose.yml` | [`jarvis`](jarvis.md), [`jarvis-adapter`](jarvis-adapter.md), [`jarvis-web`](jarvis-web.md), [`searxng`](searxng.md), [`jarvis-backup`](jarvis-backup.md), [`n8n` + sandbox](n8n.md) |
| **House service** | `aura-core/docker-compose.yml` | [`jarvis-core`](jarvis-core.md), [`jarvis-homeassistant`](home-assistant.md) + [optional profiled services](side-services.md) |

The Hermes stack is the agent; the house service is Home Assistant behind the
`homeassistant` toolset. The Hermes stack reaches the house over
`host.docker.internal` ([ADR-0006](../adr/0006-jarvis-core-house-service.md)).

## Deploy, roll out, roll back

The production rollout is health-gated with automatic rollback; the pipeline's deploy
job runs the same script (`scripts/deploy.sh`, [ADR-0014](../adr/0014-host-is-lxc-runner-registry.md)).

```bash
# deploy a built tag to the host over the LAN (rsync deploy/ → host, pull, up,
# then poll /health on 8642 and 8643; on failure it restores the previous tag)
make deploy AURA_TAG=<tag>          # = bash scripts/deploy.sh

# put the previous tag back (takes the tag from the host, no AURA_TAG needed)
make rollback                         # = bash scripts/deploy.sh --rollback
```

Rollback restores **only a tag the registry still has**; if the previous image is
gone the health-passing new stack is left up rather than replaced by a pull error
([ADR-0014](../adr/0014-host-is-lxc-runner-registry.md)). `make rollback` once could
not start because a `AURA_TAG` check ran before the `--rollback` branch — fixed
2026-09-02.

## Per-service operations

All five Hermes services live in one compose project (`name: jarvis`), so from the
deploy directory on the host:

```bash
cd /opt/jarvis/deploy            # the rsync'd copy on the host
docker compose ps                # status of every service
docker compose logs -f jarvis    # follow one service (json-file, 20m ×5 rotated)
docker compose restart jarvis    # restart one service
```

Each published port is bound to `${TAILSCALE_IP}` only (the host's LAN address) — no other interface answers.
Logs are `json-file` capped at 20 MB × 5 files per container.

## Health at a glance

| Service | Health endpoint | From |
| --- | --- | --- |
| `jarvis` | `http://127.0.0.1:8642/health` | Hermes API server |
| `jarvis-adapter` | `http://127.0.0.1:8643/health` | adapter |
| `jarvis-web` | `http://127.0.0.1:8199/healthz` | console |
| `searxng` | `http://127.0.0.1:8080/healthz` | SearXNG |
| `jarvis-backup` | none (a loop; check `docker compose logs jarvis-backup`) | — |

The general operations runbook (bring-up, backup, restore, the older two-compose
dev flow) is [operations.md](operations.md). The host by hand (the model switch, the
hand-made bootstrap, rollback, key rotation) is [host.md](host.md).

## Doors into the agent

Not services of their own — they run inside the `jarvis` container — but they have
their own on/off switches, their own allowlist and their own failure modes:
[messaging.md](messaging.md) covers Telegram, Discord and the inbound webhook
listener, including how to prove that an unlisted account is refused.

## The way out

[voice.md](voice.md) covers the speaking half: which of the three engines is
saying the words, what each one costs, how to switch, and the rights question
attached to the reference clip CosyVoice cannot speak without.

## Work nobody asked for in the moment

[proactive.md](proactive.md) covers the scheduled side: the morning brief and the
other jobs the image seeds, the GPU threshold watchdog and its debounce, the one
setting that decides where any of it is delivered, and what to look at when a brief
does not arrive.

## Subsystems with their own runbook

- **[The model's shell](sandbox.md)** — the bubblewrap sandbox a `terminal` or `execute_code` run goes to.
- **[Map data](maps.md)** — self-hosted tiles, buildings, geocoding and routing for the car and the console.
- **[Connecting accounts](connectors.md)** (Google, GitHub, Microsoft 365, Slack, Discord, GitLab, Spotify, Notion, Todoist) — the native OAuth consent, where the token lives, and unlinking.
