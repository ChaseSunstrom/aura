# First run

You have [installed](install.md) the stack. This is what the first start looks like and
how to know it worked.

## 1. Confirm health

From the host:

```bash
cd /opt/jarvis-host           # the rsync'd deploy/ on the host (a checkout uses deploy/)
docker compose ps             # jarvis, jarvis-adapter, jarvis-web, searxng, jarvis-backup
curl -fsS http://$TAILSCALE_IP:8642/health   # jarvis (Hermes API)
curl -fsS http://$TAILSCALE_IP:8643/health   # adapter
curl -fsS http://$TAILSCALE_IP:8199/healthz  # console
```

The [`jarvis`](../runbooks/jarvis.md) container allows a **90 s start period** for a
model cold start; a slow first `/health` is not a failure yet.

## 2. Open the console

Browse to the console's address (`https://jarvis.home.example` behind the house's Caddy, or `http://<host>:8199`) from the LAN or over WireGuard. The first open
shows the **setup sheet**: your name, the console password (twice) and whether
to enrol your voice now. It names you as the house's owner
(`POST /api/voice/speaker/people`, level `owner`), chooses the password, and —
if you said so — walks you through the enrolment phrases. "Later" closes it for
good; the password can still be chosen from the pairing panel, and people from
Settings → People. The console then shows the room: the reactor, the panels, the
docks.

Everyone else is added under **Settings → People** — a name and a level
(`member`, or `guest` for someone who may ask but not have anything gated done),
then "Enrol voice". What a level means is in
[voice identity](../explanation/voice-identity.md).

## 3. Talk to it

Say the wake word or use push-to-talk from the phone, or type in the console chat. The
[voice path](../how-to/README.md) is: record → transcribe (faster-whisper) → the turn
runs → reply → speak (Piper). Everything stays on the LAN.

Set a standing objective with `/goal <text>`: after each turn the goal judge returns
done / continue / wait, and Hermes keeps going until done, paused, cleared, or the turn
budget is hit. The apps show every verdict's reason.

## 4. If the model is cold

llama-swap serves one model at a time. A switch or first load takes about two minutes
and every client waits; the apps show **warming up model** while llama-swap reports
`starting`. Provider and auxiliary timeouts are 600–900 s, so a cold start is a delay,
not an error ([ADR-0021](../adr/0021-single-model-default.md)).

## Something wrong?

Jump to the [runbooks](../runbooks/README.md): the "where to look when something is
wrong" table points you at the right service.
