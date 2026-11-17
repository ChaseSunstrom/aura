# Getting started

JARVIS is a fully local, private, voice-first assistant with real agency over the
homelab, built on [Hermes Agent](https://github.com/NousResearch/hermes-agent). Hermes
is the one agent loop; the model is `qwen3.8-27b` on vLLM behind llama-swap; STT is
faster-whisper and TTS is Piper, both on CPU inside the JARVIS container; nothing
leaves the house's LAN.

New here? Read in this order:

1. **[Install](install.md)** — stand the stack up on the JARVIS host.
2. **[First run](first-run.md)** — first start, health checks, and talking to it.
3. **[Pairing](pairing.md)** — connect a phone and the console.

## Where to go next

- **[How-to guides](../how-to/README.md)** — task-focused: voice, devices, backups, cross-device.
- **[Reference](../reference/README.md)** — generated: tools, plugins, MCP servers, slash commands, API endpoints, settings.
- **[Runbooks](../runbooks/README.md)** — per-service start/stop/health/failures.
- **[Explanation](../explanation/README.md)** — architecture, security, threat model.
- **[Decisions (ADRs)](../adr/README.md)** — why the system is shaped the way it is.
- **[CAPABILITIES.md](../../CAPABILITIES.md)** — every capability mapped to the benchmark task that proves it.

What AURA is and how it is built is the repository [`README.md`](../../README.md); the
by-hand host bootstrap, key rotation and rollback are the
[host runbook](../runbooks/host.md). This section is the quick path in.
