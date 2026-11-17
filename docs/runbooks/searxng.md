# Runbook: `searxng`

Web search that actually answers. The SearXNG on the separate `searxng` CT has no
egress (every engine times out), so the stack carries its **own** SearXNG, reachable
only on the stack network. Hermes's web tools read `SEARXNG_URL`, which the host
`.env` sets to `http://searxng:8080` ([ADR-0007](../adr/0007-local-web-search.md)).

- **Image:** `searxng/searxng:2026.8.22-9fea41204` (pinned)
- **Port:** none published — internal to the stack network only
- **Config:** `deploy/searxng/settings.yml` mounted **read-only, single file**
- **Hardening:** `no-new-privileges`, `cap_drop: ALL`, then only `CHOWN/SETGID/SETUID`; 1 GB memory limit

## Start / stop

```bash
cd /opt/jarvis/deploy
docker compose up -d searxng
docker compose restart searxng
```

## Health

```bash
docker compose exec searxng wget -q --spider http://127.0.0.1:8080/healthz && echo ok
```

`start_period` is 20 s, 3 retries.

## Logs

```bash
docker compose logs -f searxng
```

## Common failures

- **Every engine times out after a deploy (2026-09-03).** The stack network MTU: the
  daemon's `mtu: 1200` applied to the default bridge only, so the user-defined network
  came up at 1500 and outbound TLS hung. The compose network now pins MTU 1200 — fixed
  at the [`jarvis`](jarvis.md) network level, shared by SearXNG.
- **`settings.yml` not writable / next deploy's tar cannot overwrite it.** The file is
  mounted read-only as a single file on purpose: mounting the whole directory let the
  container chown it to its own uid and the next deploy could not replace the host
  copy. Keep it a single-file read-only mount.
- **SearXNG bound too widely.** It is bound to loopback under granian and reachable
  only on the stack network (commit c580d57); it must never be published to the LAN.

## Rollback

`make rollback` — see the [runbooks index](README.md#deploy-roll-out-roll-back).
