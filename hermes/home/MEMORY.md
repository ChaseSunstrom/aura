# MEMORY.md — homelab and project context (seeded 2026-09-02)

## Hosts (LAN, `*.${HOUSE_DOMAIN}` behind the house's Caddy; WireGuard from outside)
- `jarvis` — the JARVIS host (Debian 12, Docker). Runs Hermes (this agent), the jarvis-adapter,
  the web console, and jarvis-core as the *house service* (entities, automations, MQTT, phone/desktop
  device channel) on :8080. **The two RTX 3090s (24 GB each) are in this host** — `nvidia-smi`
  works here — shared by the models, CosyVoice and Whisper.
- `jarvis-models` — the house's LLM server, a service on the JARVIS host: llama-swap on :9000
  (OpenAI-compatible, `LLM_API_KEY` required) serving vLLM models `qwen3.8-27b` (main) and
  `qwen3.6-35b` — those two and no others. One model resident at a time across both cards, so a
  swap takes minutes ("warming up model"). The console's Models panel is /settings/models.
- `gitlab` — self-hosted GitLab CE (HTTPS), container registry on `registry.${HOUSE_DOMAIN}` (HTTPS).
- `n8n` — self-hosted n8n (https://n8n.${HOUSE_DOMAIN}, `N8N_URL`), uses the same LLM endpoint.
- `grafana` — Grafana 13 (https://grafana.${HOUSE_DOMAIN}, `GRAFANA_URL`) with the homelab metrics (GPU, VRAM, temps).
- `searxng` — the stack's own SearXNG (`SEARXNG_URL`, http://searxng:8080) used for web search.
- Tailscale, its exit node and the old registry mirror were retired on 2026-10-27; a `*.ts.net` name no longer resolves.
- ComfyUI is not running, so image generation is off until it moves onto the JARVIS host.
- Proxmox API is reachable through PROXMOX_URL with an API token (see the proxmox-control plugin).

## Conventions
- Never hand-edit anything on the JARVIS host; everything ships as a pinned image via the GitLab pipeline.
- Secrets live in `.env` on the host, never in images or git.
- Prefer LAN addresses; nothing is exposed to the internet.
- The house speaks Home Assistant's REST dialect (jarvis-core): `ha_list_entities`, `ha_get_state`,
  `ha_call_service` work against it. Areas, automations and phone/desktop actions come from the
  `smart-home` plugin.

## Voice
- STT: faster-whisper (local). TTS: **CosyVoice 3 on the JARVIS host GPU** is the target engine, with
  a voice per persona (AURA, JARVIS) chosen from the speaking agent; Piper
  `en_GB-alan-medium` on CPU is the fallback for when the GPU is busy or unavailable. Target ≈2 s from
  end of speech to first spoken word when the model is warm.
