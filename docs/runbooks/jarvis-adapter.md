# Runbook: `jarvis-adapter`

The bearer-auth front door for the apps (`:8643`). One origin, one key: it proxies
the Hermes API server (`/v1/*`, `/api/sessions*`, `/api/jobs*`) and Hermes serve
(`/api/audio/*`), and relays the HUD and speak WebSockets
([ADR-0004](../adr/0004-thin-adapter.md)). Endpoints are listed in the generated
[API reference](../reference/api-endpoints.md); the socket contract is the
[HUD protocol](../hermes-hud-protocol.md).

- **Image:** `${AURA_REGISTRY}/jarvis-adapter:${AURA_TAG}`
- **Network:** `network_mode: service:jarvis` — it shares the [`jarvis`](jarvis.md)
  container's network namespace, so `127.0.0.1:8642` / `:9119` are Hermes and its own
  `:8643` rides `jarvis`'s port map. It has **no ports block of its own.**
- **Auth:** the master key (`AURA_API_KEY`), or a device's own key (T-0569) minted at pairing and stored only as a SHA-256 in `/opt/adapter-state/device-keys.json` on the adapter-only volume `jarvis-adapter-state` (not backed up: a lost volume means those devices re-pair; the master key still works). A damaged file admits no device key and refuses pairing and revoking with 503 rather than be overwritten: `docker exec jarvis-adapter mv /opt/adapter-state/device-keys.json /opt/adapter-state/device-keys.json.bad`, then re-pair. Only the master key mints, lists or revokes (`POST /api/pair`, `GET`/`DELETE /api/devices/keys`); the console's Devices screen is the place to do it. Hermes is reached with `HERMES_API_KEY`.

## Start / stop

```bash
cd /opt/jarvis/deploy
docker compose up -d jarvis-adapter
docker compose restart jarvis-adapter
```

Because it shares `jarvis`'s namespace, recreating [`jarvis`](jarvis.md) orphans the
adapter: it keeps running in the old namespace, where its own `/health` still answers
(so `docker ps` said *healthy* until the healthcheck also began probing `:9119`) but the
apps' `:8643` is gone. A `restart` cannot re-join a namespace that no longer exists —
**recreate it**: `docker compose up -d --force-recreate jarvis-adapter`. Note that
`docker compose up -d jarvis-web` can recreate `jarvis` on its own (a changed `.env`
changes jarvis's config, and jarvis is jarvis-web's dependency) — prefer `docker compose
up -d` for the whole stack, which recreates the adapter with it.

## Health

```bash
curl -fsS http://127.0.0.1:8643/health         # {"status":"ok","service":"jarvis-adapter",...}
```

The container healthcheck uses `python3 urllib` (the image has no `curl`).
`start_period` is 20 s.

## Logs

```bash
docker compose logs -f jarvis-adapter
```

## Common failures

- **`502 upstream unreachable`.** The adapter reached but Hermes did not answer.
  Check [`jarvis`](jarvis.md) health first; the adapter cannot be healthy on its own
  if the namespace owner is down.
- **`4401` close on the HUD socket.** The client sent no or a wrong bearer. The
  adapter accepts the socket first, then closes `4401` so browsers see a clean close
  frame instead of an opaque HTTP 403 — this is expected on a bad key, not a bug.
- **Adapter "running" but apps cannot reach `:8643`.** The port is published by the
  `jarvis` service (shared namespace). If `jarvis` is recreated without the port map,
  `:8643` disappears with it.

## Rollback

`make rollback` — see the [runbooks index](README.md#deploy-roll-out-roll-back).

## The console's size budget

`scripts/deploy-here.sh` rebuilds `jarvis-web`, reads its `build/client` out of the new image and
runs `scripts/verify/hud-size.mjs` on it before anything is synced or recreated. Over budget, it
stops with the offending chunk named (the output above the refusal); a console it cannot
measure is refused too. `scripts/deploy-here.sh --allow-over-budget` ships it anyway and logs that
it did. A deploy that does not rebuild `jarvis-web` does not run the check.

## What the last deploy proved

After every deploy `scripts/smoke.sh` sends its own run to the adapter (`scripts/smoke-report.py`,
copied beside it by both deploy scripts): which checks passed, the deployed tag, the first
failure and the check it failed at. Its last step (7, `scripts/erase_doors_check.py`) asks the house's
and the adapter's `forget_window` doors for a window of 0 minutes, which a deployed, guarded door
refuses (401/403 with no key, 400 with it) and which erases nothing; it never sends a window from
1 to 1440, so it proves the doors are there without using them. The house is asked only where the
smoke has `HASS_URL` (the deploys run the step in the Hermes container, which has it) and, with
`HASS_TOKEN`, for the 400 as well. Then `scripts/deploy-here.sh` runs the golden turn
(`scripts/verify/golden-turn.mjs`; by hand, `make golden-turn`) and sends each surface's result:

| Surface | What is driven | When it is skipped |
|---|---|---|
| console | a typed line through the deployed console, the adapter, Hermes and the model; the reply must carry a word that is in no prompt | never |
| car screen | `/car` opened with a stand-in phone bridge; the screen must draw and say it is linked. No turn: a head unit has no composer | never |
| phone app | the app must be in front on a phone adb sees. No turn is typed | no adb, or no phone attached |
| desktop app | nothing | always: nothing here can drive a desktop session |

The console's Board shows both as panel 07 VERIFIED: a cell lit when it passed on the deploy
that is running, dim when it last passed on an earlier one, red where the last run failed,
outlined when it has no pass (never run, or skipped); a skipped surface's reason is in the panel's
foot. The golden turn never decides a deploy, and a deploy from CI (`scripts/deploy.sh`) does
not run it: run `make golden-turn` after one.

`GET /api/verified` (bearer key) answers the same board; it is also the `verified` field of
`GET /api/status` and `GET /api/board`. `PUT /api/verified` takes a smoke run (`tag`, `at`,
`passed`, `failed`, `failed_check`), surface results (`at`, `surfaces`), or both. The state is
one file, `verified.state`, on the artifacts volume. A report that cannot be sent is one
`smoke-report: not sent (...)` line in the smoke's output and never changes a deploy's result;
the board then still shows the run before it. A new `PASS` line in the smoke needs a row in
`tests/contracts/verified.json` (`deploy/tests/test_smoke_report.py` fails without one).

## Artifact store

Blobs live on the `jarvis-artifacts` volume at `/opt/artifacts` (read-write), beside the
read-only `hermes-home` mount — never under it ([ADR-0041 addendum](../adr/0041-artifact-store-in-the-adapter.md)).
The volume is **not** in `jarvis-backup`'s tarball. Expired artifacts are swept at startup
and every 24 h (`artifacts: swept N expired` in the log). A publish that answers
**`507 artifact store is not writable`** means the volume is missing, read-only or owned by
root; the log line names the path and the errno.

The newest phone build has one stable address on the console: `/api/apk`. Signed in, it
redirects to that build's signed download (good for an hour); it answers 404 while the store
holds no build and 401 signed out. It knows only the store: the Apps panel may offer an
equal or newer GitLab release instead.

```bash
docker compose exec jarvis-adapter python3 -c "import os; print(os.stat('/opt/artifacts'))"   # uid should be 10001
```

Media elements fetch with the `signed_url` from the JSON (`?exp=&sig=`, one hour); a `401` on
it is an expired or forged signature, the same answer as a missing bearer.
