# CI/CD for JARVIS — runner, variables, stages

## Topology

```
dev LXC (this repo, gitlab-runner container) ──push──► gitlab.home.example (HTTPS)
        │  buildx over /var/run/docker.sock                  │ registry: registry.home.example (HTTPS)
        └──────────────── images ───────────────────────────►│
                                                             ▼
                                        JARVIS host ◄── deploy job: ssh deploy@<host> (LAN), docker compose
```

Base images (`nousresearch/hermes-agent`, `python`, `node`, `docker`, `anchore/*`) pull straight
from Docker Hub: the Tailscale exit node and the `repository` mirror went with Tailscale on
2026-10-27 (a leftover `registry-mirrors` entry in a daemon.json only costs a failed lookup). The registry is HTTPS behind Caddy with the house's `*.home.example`
certificate (since 2026-10-27), so neither Docker needs an `insecure-registries` entry for it.
A plain-HTTP registry would: `sudo scripts/setup-docker-registry.sh HOST:PORT` merges it with jq
and keeps mtu/mirrors/dns.

## Runner

1. GitLab → project → Settings → CI/CD → Runners. If a runner tagged `jarvis` with Docker
   access is already online, reuse it. Otherwise "New project runner": tag `jarvis`, untick
   "run untagged", copy the `glrt-…` token.
2. On the dev LXC (docker group is enough):
   ```bash
   RUNNER_TOKEN=glrt-… scripts/setup-runner.sh
   ```
   This runs `gitlab/gitlab-runner` as a container, registers a **docker executor** with
   `/var/run/docker.sock` mounted into jobs (buildx via the host daemon, no privileged DinD),
   host networking (so the integration stage can bind 127.0.0.1:8642/8643 and reach the tailnet).
3. Test: push a branch → the `test` stage must run on the runner.

## CI variables (Settings → CI/CD → Variables, all **protected + masked**)

| Variable | Type | Used by |
|---|---|---|
| `DEPLOY_SSH_KEY` | File | deploy — private key of the `deploy` user on the JARVIS host |
| `AURA_HOST` | Variable | deploy — LAN address or name of the JARVIS host |
| `AURA_SSH_PORT`, `AURA_SSH_USER` | Variable | deploy (defaults 22 / `deploy`) |
| `CI_JARVIS_API_KEY`, `CI_HERMES_API_KEY`, `CI_HERMES_SERVE_TOKEN` | Variable | integration — throwaway keys for the runner's stack |
| `CI_HASS_TOKEN` | Variable | integration — a jarvis-core token so the house tools answer |

`CI_REGISTRY_USER` / `CI_REGISTRY_PASSWORD` are GitLab's job token; nothing else is needed to push.

## Stages

| stage | jobs | gate |
|---|---|---|
| test | `test:lint`, `test:plugins`, `test:adapter`, `test:config-render`, `test:plugin-discovery` (real Hermes image, `HERMES_PLUGINS_DEBUG=1 hermes plugins list` must list every plugin with no error), `test:web`, `test:android` (python mirrors) | any failure stops the pipeline |
| build | `build:jarvis`, `build:adapter`, `build:web` — buildx, registry cache, tags `:$CI_COMMIT_SHORT_SHA` (+`:main`, `:$CI_COMMIT_TAG`), SBOM with syft, grype fails on **critical** fixed CVEs; `build:android` manual | CVE gate |
| integration | `integration:smoke` — the three images up with `deploy/docker-compose.yml` on the runner against the host's jarvis-models, then `scripts/smoke.sh`: health, streamed completion, executed tool call, goal loop (continue→done), failing gate blocks, Whisper→Piper round trip; tear down | all six checks |
| deploy | `deploy:production` — auto on the default branch, manual on tags/other branches; `scripts/deploy.sh` (rsync deploy/, record `.previous_tag`, write `AURA_TAG`, pull+up, poll `/health`, smoke on the host, rollback on failure, prune) | health + smoke |

## Rollback

Automatic: the deploy job restores `/opt/jarvis/.previous_tag` and re-runs `compose up`.
Manual from the dev shell: `AURA_TAG=<sha> scripts/deploy.sh` or `scripts/deploy.sh --rollback`.
On the host itself (emergency only): `cd /opt/jarvis && AURA_TAG=<sha> docker compose up -d`.
