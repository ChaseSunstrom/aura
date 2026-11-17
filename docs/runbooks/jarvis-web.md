# Runbook: `jarvis-web`

The SvelteKit console (`:8199`). It talks to the [adapter](jarvis-adapter.md) at
`http://jarvis:8643` for the HUD and to [`jarvis-core`](jarvis-core.md) for the house
and dashboard pages. Runs as node ([ADR-0009](../adr/0009-frontends-hud-over-adapter.md);
the runtime carries no npm). Its own routes are in the generated
[API reference](../reference/api-endpoints.md).

- **Image:** `${AURA_REGISTRY}/jarvis-web:${AURA_TAG}`
- **Port (bound to `TAILSCALE_IP`, the LAN address):** `8199`
- **Volume:** `web-storage` → `/app/.storage` (the console's own state, e.g. a
  password chosen in the browser)

## Start / stop

```bash
cd /opt/jarvis/deploy
docker compose up -d jarvis-web
docker compose restart jarvis-web
```

## Health

```bash
curl -fsS http://127.0.0.1:8199/healthz          # {"status":"ok"}
```

`start_period` is 30 s. The console **will start without a backend** and show its own
"cannot reach the server" state rather than crash.

## Logs

```bash
docker compose logs -f jarvis-web
```

## Config and env

The console reaches the adapter with `AURA_ADAPTER_URL` + `AURA_ADAPTER_KEY`
(the shared `AURA_API_KEY`), the house with `AURA_URL` + `AURA_TOKEN`. The
console password is set from `AURA_CONSOLE_PASSWORD[_HASH|_FILE]` or chosen in the
browser and kept in the `web-storage` volume; pairing uses `AURA_PAIRING_SECRET`.

### Signing in: the password, passkeys and the lock

- **Password.** Always works. It is the way back in when a passkey's device is lost.
- **Passkeys** (fingerprint, face or the device's PIN). They need the console's https
  address in `.env`: `AURA_CONSOLE_ORIGIN=https://jarvis.home.example`, then recreate
  `jarvis-web`. A passkey is bound to that name, so it is not offered over plain http or
  a bare address; the sign-in screen says why and shows the password.
  - Add one: sign in, Settings → Account → **Add a passkey**, on the device that will use it.
  - Lost a device: remove its passkey from another signed-in browser. That also signs out
    every session the passkey opened.
  - Remove one: Settings → Account → **Remove** beside its name. To drop them all, delete
    `console-passkeys.json` from the `web-storage` volume.
  - Changing `AURA_CONSOLE_ORIGIN` to another name orphans every passkey: sign in with the
    password and add them again.
- **Lock.** Lock now, or the page's Lock after, ends the browser's session on the server:
  the API, the `/hermes` relay and the sockets refuse its cookie until it signs in again.
  With **Voice unlock** on, the lock is only the screen (a voice opens no session).
- **Idle.** A browser that has not talked to the console for `AURA_CONSOLE_IDLE_MINUTES`
  (default 10080, a week; floor 5) must sign in again. An open tab keeps talking, so an
  unattended open tab is what Lock after is for. The phone's and the car's web views sign
  in with their key and are not idled out.

## Common failures

- **Console loads but the HUD is dead.** The adapter or `jarvis` is down; check
  [`jarvis-adapter`](jarvis-adapter.md) `/health`. The console reaching the adapter is
  independent of it reaching the house.
- **House/dashboard pages empty but the HUD works.** `jarvis-core` (the house
  service) is down or `AURA_URL`/`AURA_TOKEN` are wrong; see
  [`jarvis-core`](jarvis-core.md).
- **"Passkeys need the console's https address".** `AURA_CONSOLE_ORIGIN` is unset, or is
  not https. Set it and recreate `jarvis-web`.
- **A passkey stopped opening the console.** Its challenge lasts two minutes and five bad
  answers from one address wait out five minutes; the password is not locked by them.
- **Password lost after a redeploy.** A browser-chosen password lives in the
  `web-storage` volume; removing that volume resets it. Prefer
  `AURA_CONSOLE_PASSWORD_HASH` in `.env` for a durable one.

## Rollback

`make rollback` — see the [runbooks index](README.md#deploy-roll-out-roll-back).
