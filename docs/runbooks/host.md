# Runbook: the host, by hand

What the owner's own host was built with and is kept running by: starting and stopping the
Hermes stack, a model switch, the hand-made bootstrap, rollback and key rotation. A new
install does none of this by hand: [the installer](../getting-started/install.md) checks
the machine, writes fresh secrets and starts both stacks. The per-service pages are in the
[runbooks index](README.md); bring-up and health are in [operations](operations.md).

This page was the root README's second half until the README became the project's front
page (T-0865). Not covered: the house stack (`aura-core/docker-compose.yml`,
[jarvis-core](jarvis-core.md)).

## Start / stop / logs (JARVIS host)

```bash
cd /opt/jarvis
docker compose up -d                 # start everything (pulls the pinned tag from .env)
docker compose ps                    # health: jarvis, jarvis-adapter, jarvis-web, jarvis-backup
docker compose logs -f jarvis        # gateway + dashboard (s6-supervised); also volume logs/gateways/default/current
docker compose down                  # stop (volume and .env stay)
curl -fsS http://$TAILSCALE_IP:8642/health && curl -fsS http://$TAILSCALE_IP:8643/health
```

Hermes CLI inside the container: `docker exec -it jarvis hermes doctor`, `hermes cron list`,
`hermes plugins list`, `hermes sessions list`, `hermes tools list --platform api_server`.

## Model switch and cold start

llama-swap serves one model at a time. Switching (`/llama models`, the `llama_swap_load` tool, or
`hermes model`) evicts the resident model; a load takes ~2 minutes and every client waits. The apps
poll `/api/status` and show **warming up model** while llama-swap reports `starting`; timeouts are
900 s on the provider and 600–900 s on auxiliary tasks, so a cold start is a delay, not an error.
`curl -H "Authorization: Bearer $LLM_API_KEY" http://127.0.0.1:9000/running` shows the state (llama-swap
in `jarvis-models`, see `docs/how-to/models.md`); `GET /unload` frees the GPUs.

## Troubleshooting the Qwen tool-call parser

Symptom: HTTP 200 with a filled `reasoning` field, empty `content` and empty `tool_calls`, tools
"described" but never executed. On the validated build (`--reasoning-parser qwen3 --tool-call-parser
qwen3_xml`) this does **not** happen (Phase A evidence). If a vLLM upgrade brings it back:

1. Check the wire: `curl …:9000/v1/chat/completions` with one `tools` entry — `finish_reason` must be
   `tool_calls`.
2. Uncomment `extra_body.chat_template_kwargs.enable_thinking: false` under `providers.qwen-vllm`
   in the volume's `config.yaml` (template: `deploy/config.template.yaml`), restart `jarvis`.
3. Or serve with `--tool-call-parser hermes`, or drop `--reasoning-parser qwen3` (the model's
   file under `deploy/models/seed/models/`).

## Backup / restore of the Hermes volume

`jarvis-backup` tars `jarvis-hermes-home` nightly at 03:15, encrypts it with age (`BACKUP_AGE_RECIPIENT`)
into `${BACKUP_DIR}` (keeps 14), copies it to `BACKUP_MIRROR_DIR` and restores the newest into a scratch
home every Sunday (`BACKUP_AGE_IDENTITY` is the key for that); see
[docs/runbooks/jarvis-backup.md](jarvis-backup.md) for the three settings. Manual:
`docker compose run --rm jarvis-backup once`. Restore: `docker compose run --rm jarvis-backup restore
<file>` prints the exact commands (stop the stack, untar into the volume as uid 10000, start).

## Web search

Hermes' `web` toolset uses the local SearXNG (`SEARXNG_URL`). If every engine times out
(`unresponsive_engines` in `…/search?format=json`), the SearXNG CT has no egress — fix that CT, not
JARVIS.

## Host bootstrap (once, on the JARVIS host)

New to Jarvis? Don't follow the steps below: [`docs/getting-started/install.md`](../getting-started/install.md)
leads with `scripts/install.sh`, one script that checks the host, writes fresh secrets and
starts both stacks. The steps below are the by-hand version the owner's own host was built with.

1. Deploy user: `adduser --disabled-password deploy && usermod -aG docker deploy` (docker group, no sudo).
   Put the pipeline's public key in `/home/deploy/.ssh/authorized_keys`.
2. Registry: `registry.home.example` serves HTTPS with the house's `*.home.example`
   certificate, so Docker needs no `insecure-registries` entry. `docker login registry.home.example`
   with a deploy token confirms a pull works. (`scripts/setup-docker-registry.sh HOST:PORT` is only
   for a plain-HTTP registry: it merges `insecure-registries` with jq and keeps `mtu`, mirrors and `dns`.)
3. `mkdir -p /opt/jarvis/backups && chown deploy /opt/jarvis` and copy `deploy/.env.example` to
   `/opt/jarvis/.env` (the directory is `AURA_REMOTE_DIR`; on a host that also carries the source
   checkout, as the owner's single LXC does, use `/opt/jarvis-host`); fill the secrets (`openssl rand -hex 32` ×3), `TAILSCALE_IP`, `HASS_TOKEN`
   (a jarvis-core token), plugin URLs/tokens. This is the only hand-created file on the host.
4. Volumes are created by compose (`jarvis-hermes-home`, `jarvis-web-storage`). First start renders
   `config.yaml`/SOUL/USER/MEMORY from the image; later starts never overwrite them.
5. Deploy: from the dev LXC `AURA_HOST=<host> AURA_TAG=<sha> make deploy`, or push to `main`.
6. Firewall: `sudo scripts/setup-host-firewall.sh` — the host's ufw drops everything but the
   bind interface (plus `LAN_ALLOW` subnets), and the stack's containers must reach jarvis-core on the host's 8080
   (the console's house pages, Hermes's Home Assistant toolset). Opens that port to the Docker
   ranges only.
7. The console on HTTPS 443: the house's Caddy terminates TLS with the `*.home.example`
   certificate and proxies `jarvis.home.example` to the console's port (8199).
   `AURA_CORS_ORIGINS` in the host's `.env` lists that origin. Test from the LAN or over
   WireGuard: `curl https://jarvis.home.example/healthz`.

## Rollback

* Automatic: a failed rollout (health or smoke) restores `/opt/jarvis/.previous_tag` and re-runs
  `docker compose up -d`, then fails the job loudly.
* From the dev LXC: `make rollback` (or `AURA_TAG=<sha> make deploy`).
* On the host: `cd /opt/jarvis && AURA_TAG=<sha> docker compose up -d`.

## Key rotation

| key | where | rotate |
|---|---|---|
| `AURA_API_KEY` (apps → adapter) | host `.env`; phone/console pairing | change in `.env`, `docker compose up -d`, re-pair the phone (QR from Settings → Console), update `AURA_ADAPTER_KEY` for the console (same value) |
| `HERMES_API_KEY` (adapter → API server) | host `.env` | change both `HERMES_API_KEY` uses in `.env`, `docker compose up -d` |
| `HERMES_SERVE_TOKEN` (adapter → hermes serve) | host `.env` | same as above |
| `HASS_TOKEN` (Hermes → jarvis-core) | host `.env` | mint a new jarvis-core token, replace, `docker compose up -d` |
| Deploy SSH key | GitLab CI variable `DEPLOY_SSH_KEY` + `/home/deploy/.ssh/authorized_keys` | generate `ssh-keygen -t ed25519`, add the new public key on the host, replace the CI variable, remove the old public key after the next green deploy |
| Registry deploy token | GitLab → Settings → Repository → Deploy tokens | create a new token, `docker login` again on the host, revoke the old |

Secrets never enter an image layer or a committed file: the images carry templates; `.env` on the
host and CI variables carry the values. Nothing in any image carries one (checked inside
`jarvis:d0d29b0a`: no test file, no `_probe.py`, and the Dockerfile copies neither).

**The host's `.env` is where they have to be real, and on 2026-09-07 three of the four were not.**
`AURA_API_KEY` was `devadapterkey0123456789` — the dev placeholder the phone's test fixtures
use — and `HERMES_SERVE_TOKEN` and `AURA_CONSOLE_PASSWORD` were `dev…` values of the same
shape. Only `HERMES_API_KEY` was a real 64-character random. The stack binds the host's LAN
address only, so the exposure is bounded to the LAN and the owner's WireGuard, but the console's password and the
apps' bearer were guessable by anyone who had read this repository. Rotate all three by the
rows above; the entrypoint now says so loudly in the log on every start until you do.
