# Runbook: optional profiled services (house stack)

These are the optional services in the **house stack** (`aura-core/docker-compose.yml`),
each behind a Compose `--profile` so it stays off unless asked for. The Makefile sets
`COMPOSE_PROFILES ?= mqtt,search` by default; enable others per invocation:

```bash
cd /opt/jarvis/aura-core
docker compose --profile <name> up -d <service>
# or for the session:
COMPOSE_PROFILES=mqtt,search,cameras docker compose up -d
```

| Profile | Service | Image | What it is |
| --- | --- | --- | --- |
| `mqtt` | `mosquitto` | `eclipse-mosquitto:2` | MQTT broker for house devices |
| `search` | `searxng` | `searxng/searxng:2026.8.22-…` | House-stack SearXNG (distinct from the [Hermes stack's own](searxng.md)) |
| `cameras` | `go2rtc` | `alexxit/go2rtc:1.9.14` | Camera stream gateway |
| `geocode` | `photon` | `rtuszik/photon-docker:2.4.0` | Offline geocoder |
| `maps` | `valhalla` | `ghcr.io/valhalla/valhalla-scripted:3.9.1` | Routing and map matching for the car and console ([maps.md](maps.md)) |
| `kokoro` | `jarvis-tts` | `kokoro-fastapi-cpu` | Alternative TTS backend (Kokoro), used in the TTS review |
| `cosyvoice` | `jarvis-cosyvoice` | built here | CosyVoice 3 behind an OpenAI-shaped endpoint — off, and unproven on this hardware ([voice.md](voice.md)) |
| `fixtures` | `jarvis-radicale` | `tomsquest/docker-radicale` | CalDAV/CardDAV fixture for tests |
| `fixtures` | `jarvis-mailsink` | `rnwood/smtp4dev:v3` | Throwaway SMTP sink for tests |

## Operations

```bash
cd /opt/jarvis/aura-core
docker compose --profile <name> ps
docker compose --profile <name> logs -f <service>
docker compose --profile <name> down     # stops that profile's services
```

## Notes and gotchas

- **The `fixtures` profile is for tests and benchmarks, not production.** `radicale`
  and `mailsink` back the personal-data JCB tasks; do not point real accounts at them.
- **Two SearXNGs exist.** The house stack's `search` profile and the Hermes stack's
  bundled [`searxng`](searxng.md) are separate; Hermes reads `SEARXNG_URL`
  (`http://searxng:8080`, the Hermes-stack one) for its web tools
  ([ADR-0007](../adr/0007-local-web-search.md)).
- **The Wyoming voice containers are the legacy on-box voice path.** For v2, voice
  (faster-whisper + Piper) runs inside the Hermes container and the Wyoming containers
  are retired ([ADR-0008](../adr/0008-voice-in-hermes-container.md)); the wake word
  stays on-device on Android.
