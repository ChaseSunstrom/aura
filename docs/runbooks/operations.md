# Runbook: operations (bring-up, health, backup)

The cross-cutting operations for the whole system. Per-service detail is in the
[runbooks index](README.md); this page is the "start of shift" view.

## Bring everything up

Two stacks, started two ways. The house service (`aura-core/docker-compose.yml`,
on host networking) is started from the checkout; the Hermes stack
(`deploy/docker-compose.yml` — gateway, adapter, console, extractor, voice) runs
from the host's rsync'd copy in `/opt/jarvis-host` and is put there by
`make deploy` or the pipeline, never by `make up` from a checkout, because a
second compose project with the same container names would fight the deployed one.

```bash
make up          # jarvis-core (house) with --build
```

`make up` refuses to run from a git worktree (it would re-create the house
containers from a checkout). For a **deploy of a built tag** rather than a local
build, use the health-gated rollout:

```bash
make deploy AURA_TAG=<tag>     # rsync deploy/ → host, pull, up, poll /health, auto-rollback on failure
make rollback                    # restore the previous tag
```

## Check it

```bash
make verify                      # the full gate: offline suite, then the hardware-backed checks
# or per-service health, from the deploy dir on the host:
cd /opt/jarvis/deploy && docker compose ps
curl -fsS http://127.0.0.1:8642/health   # jarvis (Hermes)
curl -fsS http://127.0.0.1:8643/health   # adapter
curl -fsS http://127.0.0.1:8199/healthz  # console
```

**Cold-start drill.** `make drill DRILL=--yes` stops the house stack, starts it again and prints each
service's seconds to healthy (`deploy/cold_start_drill.py`). It exits non-zero when a
service is past its budget (120 s; 300 s for the three that load a model first, 240 s
for the gateway;
`--budget SERVICE=SECONDS` changes one), never gets healthy, or starts before a service it
depends on was healthy. The house is down for as long as the start takes, so run it when
nobody is using it. A bare `make drill` only reads the compose file: it lists the
services and budgets and fails on a `depends_on` that does not wait for its dependency's
healthcheck. It refuses the Hermes stack. It does not show that a healthy service answers
correctly, or how long a model takes to warm after "healthy".

## Take it down

```bash
make down        # stops the house stack; the Hermes stack: cd /opt/jarvis-host && docker compose down
```

## Back it up and restore it

The [`jarvis-backup`](jarvis-backup.md) service tars the Hermes volume nightly at
03:15 (container TZ), encrypts it with age (`BACKUP_AGE_RECIPIENT`) into `${BACKUP_DIR}`,
copies it to `${BACKUP_MIRROR_DIR}` and keeps the newest `BACKUP_KEEP` (14) in each. On
Sundays it restores the newest into a scratch home and reads it back (`jarvis-backup drill`).
The restore steps are in that runbook — run them before you need them.

## The two stacks and where state lives

| Stack | File | State that must survive a redeploy |
| --- | --- | --- |
| Hermes | `deploy/docker-compose.yml` | `hermes-home` volume (`/opt/data`), `web-storage` volume, the host `.env` |
| House | `aura-core/docker-compose.yml` | jarvis-core config + its named volumes |

The only precious host state is `.env` and `aura-core/.env` (real tokens,
gitignored) and the two named volumes. `scripts/deploy.sh` never touches the host
`.env`. Never `git clean -xfd` on the host — it also deletes the untracked model
caches.

### A host that ran the tree before the folders were renamed

The house folder was `jarvis-core/` until 2026-11-15. A pull moves the tracked files to
`aura-core/`; the env file, the voice models and the map data are untracked and stay under
`jarvis-core/`. `make up` checks for that first (`scripts/check-house-folder.py`) and stops,
printing one `mv` line per thing to move, so the house is never started from a folder
without them. Stop the house with `make down`; it leaves the services of a profile (maps,
fixtures and the rest) running, so if any of those run, also
`cd aura-core && docker compose --profile '*' down`. Both work after the pull because the
compose project keeps the name `jarvis-core`, which is also why the named volumes are found
again; a `docker compose down` typed in the old folder does not, since the compose file is
no longer there. Then make the moves it prints, remove what is left of `jarvis-core/`, and
`make up`. A directory the new folder already has empty (git tracks `maps-data/.gitkeep`)
is printed as one `mv …/* …/` line and is not listed file by file; a name starting with a
dot is not matched by it. A file it lists as present in both folders is not moved for you:
compare and keep one. A `jarvis-core` that is a symlink to `aura-core` passes the check.

## Where to look when something is wrong

| Symptom | Start at |
| --- | --- |
| Nothing answers on the LAN | [`jarvis`](jarvis.md) health + logs |
| HUD dead, console loads | [`jarvis-adapter`](jarvis-adapter.md), then [`jarvis`](jarvis.md) |
| House/dashboard pages empty | [`jarvis-core`](jarvis-core.md) |
| Web search returns nothing | [`searxng`](searxng.md) (MTU, egress) |
| No backups in `${BACKUP_DIR}` | [`jarvis-backup`](jarvis-backup.md) |

## Server addresses the service plugins accept

Plex, Jellyfin, n8n, Proxmox, Grafana, InfluxDB, ComfyUI and llama-swap (`PLEX_URL`, `JELLYFIN_URL`, `N8N_URL`, `PROXMOX_URL`, `GRAFANA_URL`, `INFLUX_URL`, `COMFYUI_URL`, `LLAMA_SWAP_URL`) send a token to the address in their setting, so all eight apply one rule before building a request (`service_url` in `hermes/plugins/_shared/_egress.py`, T-1342). **https** to any host is fine. **Plain http** is accepted only to a private, loopback or unique-local IP (`http://192.168.1.20:8080`, `http://[::1]:8000`), `localhost`, `host.docker.internal`, or a single-label name that starts with a letter (a compose service, which is how `.env.example` reaches `http://n8n:5678` and `http://jarvis-models:8000`). Refused on either scheme: a login (`user:pw@`), a query or fragment, a space or backslash, a bad port, and the metadata or link-local addresses (`169.254.169.254`, `metadata.google.internal`, `fd00:ec2::254`). A refusal is a tool answer naming the setting (`ok: false`, "PLEX_URL may use plain http only to …") and sends nothing; fix the setting, or put the server behind https.

A failure from those servers reaches the model as one fixed shape (`hermes/plugins/_shared/_failure.py`, T-1348): `{"ok": false, "http": 401, "error": "HTTP 401: <fixed hint>"}`, or `"<ErrorClass>: <hint>"` with no `http` key when nothing answered. The hint is one of a handful of sentences (rejected credentials, not found, busy, server error, refused, unreachable, timed out); the server's own body, the request address and any header text are never repeated, because a server can write anything into them. A 429 or 503 repeats `Retry-After` only as a plain number of seconds from 1 to 3600, and the plugin does not retry. Jellyfin, Plex, Proxmox, n8n, the calendar, the shared DAV helper (contacts, files), Grafana (dashboards and the GPU telemetry tools), ComfyUI, llama-swap and the smart-home house tools all answer this way (T-1363), and the `HTTP 502`/`ConnectError` wording the circuit breaker reads is unchanged. Two exceptions, both our own text and not a remote server's: n8n's REST 400, whose message is passed through on purpose so the model can fix its own filter (an n8n workflow's own answer is returned only for a 2xx), and the `error` sentence the model manager (`model_download`, `model_add`, `model_settings`, `model_delete`) writes in its JSON refusal through the adapter, which says why a limit was hit; a non-JSON or empty adapter body gets the status and hint.

The same two rules now cover the local extractor (`EXTRACT_URL`, so a page's text can no longer reach the model as an error), the shared DAV address (`DAV_URL`, contacts and files; an absolute link the server names on another host is refused so the DAV password is never sent there), the house settings writer (`aura-settings`: a refused write answers the status and a fixed hint, and a 400 says only that the value was refused and to check the setting's allowed values, so the house's own validation text, which can quote the value, is no longer relayed) and the GitLab token-expiry check (`GITLAB_API_URL`, T-1374).

Not covered: a dotted name over plain http (`http://nas.lan:8080`) is refused even if it resolves to the LAN, since a name could resolve anywhere: give the IP or use https. A single-label name is trusted to be the stack's own, so a resolver search domain that sends it outside is not caught. DNS answers are not checked. TLS verification stays each plugin's own setting (`PROXMOX_VERIFY_SSL`, `COMFYUI_VERIFY_SSL`, `GRAFANA_VERIFY_SSL`); this rule does not change it. n8n's `webhook` argument, when the model passes a full URL, is not checked by it.

## Permission levels per capability

Each capability (mail, calendar, contacts, files, house, phone, media, shell, web) has a level in `<HERMES_HOME>/state/capability-levels.json`, read on every call like the kill switch. `off` refuses every tool in it; `read` runs reads and refuses writes; `ask` makes every write ask; `allowed` stops asking about a write the matrix asks about on every call. Unset is the matrix as written. A DENY row, the kill switch (whole or per family), a guest, a first message to a new recipient, a destructive shell command, a lock or garage call and `execute_code` stay as they are at any level; an unreadable file reads as `ask`. `/hud capabilities` prints every capability with its level and tools (shape: `tests/contracts/capability-levels.json`); `capability_set` changes one, always asks, and is audit-logged (`audit_query` answers "what did you do today"). Undo: set it back, or delete the file.
