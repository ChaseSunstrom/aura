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

## First run (the owner, once)

1. Deploy (or `docker compose up -d` from the host dir). `sandbox-api` must go healthy first;
   `docker compose ps` shows it. The first start pulls three images, the runner pulls the sandbox
   image inside its own daemon.
2. Open `http://<TAILSCALE_IP>:5678`, create the owner account.
3. Settings, n8n API, create an API key.
4. Put it in the host `.env` as `N8N_API_KEY=` (Hermes plugin) and, for the house service, in
   `aura-core/.env` as `N8N_API_KEY=`. `N8N_URL` already defaults to `http://n8n:5678` for
   Hermes (a host that set it earlier keeps its own value) and to `http://127.0.0.1:5678` for
   jarvis-core. Recreate `jarvis` (and the adapter with it) so the key is read.
5. Ask Jarvis to "list my n8n workflows".

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

- `sandbox-certs` exited non-zero: `docker compose logs sandbox-certs`; a rerun is safe (the
  volume keeps the certificates it made).
- `sandbox-api` unhealthy: `docker compose logs sandbox-api`; certificates missing means the
  `chown` step failed.
- The runner will not start: it needs `privileged`; a host that forbids it cannot run the sandbox,
  and n8n still works without instance AI (stop the three sandbox services, unset
  `N8N_INSTANCE_AI_SANDBOX_ENABLED`).
