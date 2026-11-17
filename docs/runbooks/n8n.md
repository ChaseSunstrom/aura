# n8n and its code sandbox (T-0630)

n8n runs inside the Hermes stack (`deploy/docker-compose.yml`) with the sandbox its "instance AI"
uses. Jarvis lists, creates, activates and runs workflows through n8n's public API.

| Service | Role | Published |
|---|---|---|
| `sandbox-certs` | one-shot: mints the mTLS certificates into the `sandbox-tls` volume | no |
| `sandbox-api` | the sandbox service n8n talks to (HTTP 8080, gRPC 9090) | no |
| `sandbox-runner-1` | **privileged** Docker-in-Docker: starts each sandbox | no |
| `n8n` | the workflow server | `${TAILSCALE_IP}:5678` and `127.0.0.1:5678` |

`N8N_PORT` changes the published port. Nothing binds `0.0.0.0` (this host's rule; the sandbox
compose this came from published 5678 openly).

## Secrets

`deploy/n8n-env.sh <path>` writes `n8n.env` beside the stack's `.env` (gitignored, 0600): six
random secrets plus three aliases that copy one of them where a service names it differently. It
adds only keys that are missing, never changes or prints a value, and a second run writes nothing.
`scripts/deploy.sh`, `scripts/deploy-here.sh` and the installer run it before `compose up`. The
services read the file through `env_file`; the host's `.env` is never touched. To rotate, delete
the line (or the file), run the script, then recreate the four services. **Back `n8n.env` up with
the volume**: `N8N_ENCRYPTION_KEY` is what decrypts n8n's stored credentials, and a lost key makes
the `n8n-data` volume's credentials unreadable.

## First run: AURA does it (T-1523)

Nothing to do by hand. The `n8n-trigger` plugin starts a keeper (`hermes/plugins/_shared/_n8n_setup.py`)
20 seconds after the gateway starts, then every 10 minutes:

| n8n is | AURA | `service_setup_status` says |
|---|---|---|
| answering a key AURA already has (`N8N_API_KEY`, set by you or by AURA) | uses it, writes nothing | `adopted` |
| fresh (no owner yet) | creates the owner `aura@aura.invalid`, signs in, mints one API key with every scope | `provisioned` |
| refusing the key, with an owner AURA made | mints a new key with its own login | `renewed` |
| owned by someone else, no key that works | leaves it alone | `owned_elsewhere`: set `n8n.api_key` |
| not answering, or a proxy in front of it answering 5xx | tries again in a minute | `unreachable`: the detail names the status; check `N8N_URL` |

The key goes into the operator override file (`$HERMES_HOME/.storage/environment.json`, ADR-0044,
exported on every start) and into the running gateway, so the tools work at once. The owner login
AURA made is `$HERMES_HOME/.storage/n8n-owner.json` (0600): sign in to n8n's editor with it if you
want to look. Ask AURA "is n8n set up?" (`service_setup_status`, free) or "set n8n up again"
(`service_setup`, asks).

To use a key of your own instead, set `N8N_API_KEY` in the host `.env`: a key n8n accepts is never
replaced. The house's own n8n service (`jarvis-core`, its `n8n:` block) still reads
`aura-core/.env`; the Hermes tools do not need it.

## What Jarvis may do

| | Hermes plugin (`n8n-trigger`) | jarvis-core |
|---|---|---|
| list, read, executions | free | free |
| create, update | asks (`n8n:write-workflow`) | asks |
| activate / deactivate | asks (`n8n:activate`) | asks |
| run | asks (`n8n_trigger`, through the workflow's webhook) | asks |

Workflow JSON the model writes is checked before it is sent: at most 200,000 characters, nodes are
objects, and every connection starts and ends at a node that exists. n8n still judges node types
and parameters, and its message comes back verbatim. A created workflow is inactive until
activated. Running always asks; there is no "owner-marked, never ask" list yet.

## What a workflow can reach

A workflow runs with the n8n container's network: the stack's default network (the model server,
the adapter, `jarvis-core` through the host gateway if a node dials it) and the internet. Code nodes
run in the sandbox the runner starts. Treat a workflow Jarvis writes as code you approved, and read
the approval card.

## Pins and the daemon file

Images are pinned by digest in the compose file (`n8nio/n8n` 2.46.6, the three
`n8n-sandbox-service-*` images by index digest). Re-pin with
`docker buildx imagetools inspect <image>:latest` after reading the release notes.
`deploy/dind-daemon.json` is the runner's `/etc/docker/daemon.json`: only log rotation. JSON has no
comments; anything more (a registry mirror, an address pool) belongs there when a host needs it.

## When it breaks

- `service_setup_status` says `unreachable` with "settings answered 502": `N8N_URL` goes through a
  proxy that cannot reach n8n. The gateway shares the stack's network with n8n, so its own address
  is `http://n8n:5678`; set `n8n.url` to that (the settings tool writes the override file), and
  keep the proxied address for the browser.
- `sandbox-certs` exited non-zero: `docker compose logs sandbox-certs`; a rerun is safe (the
  volume keeps the certificates it made).
- `sandbox-api` unhealthy: `docker compose logs sandbox-api`; certificates missing means the
  `chown` step failed.
- The runner will not start: it needs `privileged`; a host that forbids it cannot run the sandbox,
  and n8n still works without instance AI (stop the three sandbox services, unset
  `N8N_INSTANCE_AI_SANDBOX_ENABLED`).
