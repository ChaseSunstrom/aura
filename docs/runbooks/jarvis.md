# Runbook: `jarvis` (Hermes gateway)

The agent itself: the Hermes API server on `:8642` plus the Hermes dashboard/serve
process on container loopback `:9119`. Runs `jarvis-entrypoint gateway run` from the
`${AURA_REGISTRY}/jarvis:${AURA_TAG}` image. Every other service in the stack
depends on it.

- **Image:** `${AURA_REGISTRY}/jarvis:${AURA_TAG}`
- **Ports (bound to `TAILSCALE_IP`, the LAN address):** `8642` (API server), `8643` (published here, served by the [adapter](jarvis-adapter.md) which shares this container's network namespace)
- **Volume:** `hermes-home` → `/opt/data` (the Hermes home: config, sessions, memory, notes)
- **Memory limit:** 6 GB

## Start / stop

```bash
cd /opt/jarvis/deploy
docker compose up -d               # the whole stack: recreating `jarvis` alone orphans jarvis-adapter (see its runbook)
docker compose restart jarvis
docker compose stop jarvis
```

Restarting `jarvis` also drops the [adapter](jarvis-adapter.md), which rides this
container's network namespace — bring both back together.

## Health

```bash
curl -fsS http://127.0.0.1:8642/health          # from the host
docker inspect --format '{{.State.Health.Status}}' jarvis
```

The healthcheck allows a **90 s `start_period`** for a model cold start; a restart
that answers slowly is not unhealthy yet. `docker compose up --wait` blocks on this.

## Logs

```bash
docker compose logs -f jarvis      # json-file, 20 MB × 5
```

## Config and env

Config is rendered from `deploy/config.template.yaml` into `/opt/data/config.yaml`
on the **first** start only (marker `/opt/data/.jarvis-rendered`); later starts never
overwrite it — edit the live file or wipe the marker to re-render. Model, provider,
toolsets, plugins and MCP servers all come from that template (see the generated
[tool](../reference/tools.md), [plugin](../reference/plugins.md) and
[MCP](../reference/mcp-servers.md) references). Secrets come from the host `.env`,
by name only.

## The self-extension checkout

`/opt/data/work/jarvis` (`SELFEXT_REPO_PATH`) is the tree the `selfext_*` tools scaffold
into, verify and push from; every one of them is hidden until it is a git work tree. It is
a snapshot, not a clone: the image carries a `git archive` of the commit it was built from
at `/opt/jarvis/seed/repo` (`build:jarvis` and `make build` write `deploy/seed-repo.tar`
right before the build and the Dockerfile unpacks it), and `jarvis-entrypoint` copies that
tree into the volume and commits it on the first start that finds no checkout there. A
later deploy does not refresh an existing checkout, nothing in it tracks the remote, and an
image built without the tar (every one before 2026-09-18) logs `selfext: no checkout` on
each start — `docker compose logs jarvis | grep selfext:` says which case a container is
in. `selfext_verify` runs ruff, pytest, mypy and vulture from the image's own venv at the
versions the pipeline pins. `selfext_open_mr` pushes the branch to the checkout's `origin`
with GitLab push options and whatever git credential that checkout has; the seeded checkout
has neither a remote nor a credential, so a merge request needs the owner to add both
(`git -C /opt/data/work/jarvis remote add origin …` and a token in a credential helper or
the remote URL). `GITLAB_TOKEN` in the host `.env` is not read by that push.

## Common failures

- **HTTPS and every SearXNG engine time out after a deploy (2026-09-03).** The
  stack's user-defined network took the 1500 MTU while the tailnet needs 1200; the
  compose network now pins `com.docker.network.driver.mtu: 1200`. If outbound TLS
  from the container hangs, check the network MTU first.
- **`faster-whisper` hangs on start behind the exit node.** `HF_HUB_OFFLINE=1` is
  baked into the image and the Whisper weights are built in
  ([ADR-0011](../adr/0011-hf-hub-offline-and-hass-chat-off.md)); if a rebuild drops
  that env the container will hang on a hub check.
- **Plugins missing after a config change.** The image's dashboard/serve service
  discovers plugins once, before the CMD runs, so the volume is seeded by an s6
  `cont-init` step ([ADR-0010](../adr/0010-volume-seed-cont-init.md)). A plugin added
  to the template but not seeded will not appear until a clean start.
- **`container name "/jarvis" is already in use`** (CI smoke, pipeline 17). The
  compose project name carries into container and volume names, so a second copy sets
  `COMPOSE_PROJECT_NAME=jarvis-ci-N`; never run the smoke stack with the default name
  beside the live one.
- **Every publishing container `Exited (255)` after a reboot, and `docker compose
  up -d` brings them back with no ports (2026-09-05, production down for a day).**
  All ports are bound to `${TAILSCALE_IP}` and dockerd started four seconds before
  tailscaled had assigned it: `failed to bind host port 100.64.0.51:8642/tcp: cannot
  assign requested address`. A restore that fails at daemon start is not retried by
  `restart: unless-stopped`, and a later plain `up -d` reuses the container whose network
  was never programmed (`docker port jarvis` prints nothing). The fix is
  `docker compose up -d --force-recreate jarvis jarvis-adapter jarvis-web`; the
  automatic version is `deploy/boot-recover.sh`, which the deploy installs as the deploy
  user's `@reboot` cron line and which waits for the address in `.env` before recreating
  only what is down. Its log is `logs/boot-recover.log` beside the compose file. A deploy
  checks the same address first (`scripts/deploy.sh` step 1c) and stops, with nothing
  changed, if it is still missing after a minute.
- **The root disk fills (2026-10-06: 100 %, a build failed with ENOSPC).** This host is
  also the CI runner, and killed CI builders, unmounted runner caches and a 5 GB image tag
  per deploy accumulate. `deploy/disk_guard.py` runs hourly from the deploy user's cron
  (`# jarvis-disk-guard`, log `bench/results/disk-guard.log`) and, below 20 GB free,
  removes those classes only: never an image a container uses, the running `AURA_TAG`,
  `.previous_tag`, the newest two sha tags per image or a named volume.
  `python3 deploy/disk_guard.py --dry-run` lists what it would remove.
- **A container crash-loops and nobody hears (2026-10-09: the reranker, about ten hours, on a
  port clash).** `deploy/health_guard.py` runs every five minutes from the same cron
  (`# jarvis-health-guard`, log `bench/results/health-guard.log`). A container of the
  `jarvis` or `jarvis-core` compose project that keeps restarting, is unhealthy, died with an
  error under a restart policy, or restarted 3+ times between checks puts one `notice` in
  the house's notifications (console and phone), and another when it recovers. It restarts
  nothing. Its memory is `~/.local/state/jarvis-health-guard.json`; while the house itself is
  down a notice cannot be recorded and is retried on the next run. The same run asks this host's CI runner
  `gitlab-runner verify` (T-0424): two failed checks in a row are one notice, "CI's runner on
  this host cannot reach GitLab" or "… is not running", and another when it takes jobs again
  ([CI](../ci.md) has the causes seen so far). `HEALTH_GUARD_RUNNER=` turns it off.
- **The model never starts and Jarvis goes quiet (2026-10-27: 26 hours, a vLLM start out of
  GPU memory behind a healthy container, T-0411).** A start that dies leaves
  `/tmp/jarvis-models-start-failed` in `jarvis-models`, which keeps it unhealthy until a
  start answers. The manager loads `LLM_MODEL` again after 5, 15 and 45 minutes (T-0412,
  `docker logs jarvis-models | grep "loading .* again"`), so health_guard's notice for
  it means three retries have failed: read the vLLM error in the same log before restarting.
- **A new model or voice image does not ship and nobody hears (2026-10-29: build:models failed
  grype in pipelines 642, 644 and 646 behind a green pipeline, T-0419).** `build:models` and
  `build:cosyvoice` are `allow_failure`, so a failed build or security scan leaves `:latest`
  on the last image that passed and the pipeline green. Each failed `.buildx` job leaves
  `image-failed-<svc>.txt` (its job URL) in its artifacts; `deploy:production` turns each into
  one house notice, "The new <svc> image did not ship", whose body links the job. Production
  is unchanged; the job's trace (grype's table, or the build error) says what to fix.
- **A model engine starts needing the internet (2026-10-09: vLLM reported usage to
  stats.vllm.ai, TEI asked the Hugging Face hub on every start, T-0343).** `make
  offline-selftest` starts a sealed copy of each CPU model service (embedder, reranker, the
  three Wyoming services) from its running container's image, command, env and read-only
  mounts under `--network none`, and asks it what its clients ask. A FAIL names what it
  tried to reach. The GPU services are listed as not covered. Run it after bumping an image.
- **The car's voice breaks and the owner finds out on a drive (voi-01, T-0196).**
  `scripts/ride-along.sh` asks production a read-only question as car-microphone audio:
  Jarvis's voice → the card's Whisper → a turn on the adapter's socket → the reply as car
  PCM, with the house's speaker gate asked on its own (it must refuse Jarvis's own voice).
  It approves nothing: an approval request fails the run. A pass appends one JSON line of
  stage timings to `bench/results/ride-along.log`; a failure appends why and records a
  `notice` in the house. Meant for the deploy user's cron as
  `15 4 * * * /opt/jarvis/scripts/ride-along.sh >/dev/null 2>&1 # jarvis-ride-along` (after the 03:30 self-review)
  (not installed until the owner approves the crontab line). `RIDE_TEXT`/`RIDE_EXPECT`
  change the question and the pattern its reply must contain.
- **AURA failed at something, or kept repeating itself, and nobody looked (T-0910).**
  `docker exec -i jarvis python3 - /opt/data/state.db --hours 24 < scripts/verify/aura_recent.py`
  reads the session store read-only and prints one line per turn that looped (the same tool
  call or reply three times in a row), had a tool answer with an error, or ended on a tool
  result with no reply: the time, where the session came from, the tool and a count, then a
  total. It exits 1 when it found anything. A turn's words are printed only with `--text`, 80
  characters at most. A denied approval is not an error (the deploy smoke denies one on
  purpose, T-1228). Not covered: a reply that is wrong but different each time, and a turn
  the gateway never stored. Run it after a deploy and whenever the owner says AURA misbehaved;
  each pattern it shows becomes a task.
- **The model keeps acting on a lesson a fix made false (2026-10-09: "phone_action fails from
  cron", true until the house route went first).** On each start the entrypoint runs
  `memory_health.py` before it replaces the plugins: a `tool` fact in `memory_store.db` that
  names a tool of a plugin this image changed gets trust 0.25 (under the 0.3 recall line) and
  a `stale:<plugin>@<date>` tag; the container log says which. Nothing is deleted; restore one
  with `UPDATE facts SET trust_score = 0.5 WHERE fact_id = N`.

## Rollback

`make rollback` — see the [runbooks index](README.md#deploy-roll-out-roll-back).
