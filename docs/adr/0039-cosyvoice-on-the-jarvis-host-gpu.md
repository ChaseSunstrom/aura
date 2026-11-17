# ADR-0039: CosyVoice 3 runs GPU-attached on the JARVIS host; a swap and a TTS load take turns

- **Status:** Accepted (2026-09-09)
- **Supersedes:** the decision of [ADR-0033](0033-cosyvoice-placement.md) ("take (c), the split
  path; keep (a) as the owner-gated upgrade"). ADR-0033's context, its three-way analysis and its
  2026-09-08 addendum are kept — they are the history of how this decision was reached.
- **Comes from:** the overhaul brief Phase 0b and Phase 6

## Context

ADR-0033 chose the split path because the JARVIS host had no GPU and the AI LXC had no room. Both
halves of that premise have changed, and the change was never written down.

**The GPUs are on the JARVIS host now.** Measured 2026-09-09 on the host itself:

- `nvidia-smi` runs and lists two RTX 3090s. `/dev/nvidia0`, `/dev/nvidia1`, `/dev/nvidiactl`,
  `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools` and `/dev/nvidia-modeset` all exist.
- They are the **same physical cards the AI LXC's vLLM uses**, not new hardware. `nvidia-smi`
  attributes 20600 MiB per card to PIDs that are not in this LXC's PID namespace.

**The reservation had already been lowered, which is what made this possible.** `/running` reports
`--gpu-memory-utilization 0.85` (not the 0.92 that ADR-0033's body, the v3 plan §9 and the
`aura-voice` skill all still recorded), `--tensor-parallel-size 2`, `--max-model-len 256000`,
`ttl: 0`. ADR-0033's addendum caught the change; nothing else did.

**What is actually free, measured rather than computed.** `scripts/verify/gpu-allocation.py` drives
the CUDA driver API directly:

```
CUDA driver version: 13.3
devices visible: 2
  device 0 (NVIDIA GeForce RTX 3090): OK  allocated 64 MiB, free 3262 / 24123 MiB
  device 1 (NVIDIA GeForce RTX 3090): OK  allocated 64 MiB, free 3262 / 24123 MiB
PASS: all 2 device(s) allocated and freed
```

Stepping allocations up in 256 MiB blocks until failure gives the number that matters:
**3072 MiB is the most a second process can actually take on a card**, against 3262 MiB reported
free. The 190 MiB difference is context and fragmentation overhead, and it is why "3686 MiB free"
computed from the utilisation fraction (ADR-0033's addendum) overstates the budget by ~600 MiB.

**There is no `nvidia` container runtime and no way to install one.** `docker info` lists
`io.containerd.runc.v2 runc` only; `nvidia-container-toolkit` is not installed and `sudo` requires a
password this session does not have. Direct device passthrough was proven instead — the same
allocation test, run inside `python:3.12-slim` with `--device` entries and the host's `libcuda.so.1`
and `libnvidia-ml.so.1` bind-mounted, produces byte-identical output.

**SSH to the AI LXC is still refused** (`Permission denied (publickey,password)`), so llama-swap
still cannot be reconfigured from here. That constraint from ADR-0033 survives.

## Decision

**CosyVoice 3 runs in its own GPU-attached container on the JARVIS host.** The three-way placement
benchmark is closed: candidate (a) is moot because the service no longer goes to the AI LXC,
candidate (b)'s CPU latency survives only as the reason the fallback is acceptable, and candidate
(c)'s length threshold is no longer the routing rule.

1. **Device access by direct passthrough, not by runtime.** The compose service declares the six
   `/dev/nvidia*` device nodes and bind-mounts the host's `libcuda.so.1` and `libnvidia-ml.so.1`
   read-only. This needs no toolkit and no root. The driver userspace is *mounted*, never baked into
   the image, so the image cannot drift from the host's kernel module — a mismatch that would
   otherwise surface as a working `nvidia-smi` and a failing first allocation.

2. **Pinned to one card, capped below the ceiling.** `CUDA_VISIBLE_DEVICES=0` and a hard cap
   comfortably under the measured 3072 MiB ceiling. Card 1's headroom is left to ComfyUI, so the two
   GPU consumers this house already has do not contend on the same card.

3. **A model swap and a TTS load take turns, and the swap wins.** This is the starvation rule the
   brief asks for. vLLM computes `0.85 x 24576 = 20890` MiB per card against the card's *total*, not
   against what is free, so a cold start while TTS holds ~3 GiB leaves a margin of a few hundred
   MiB — too thin to promise. Therefore every vLLM start releases the TTS service's GPU memory
   before it loads and re-warms it once the model answers `/health`. (Until T-0411 only the
   `llama_swap_load` tool did this, so a restart, a preload or an on-demand load after an unload
   skipped it; from 2026-10-27 to 2026-10-29 every start died at vLLM's free-memory check. The
   release now lives in `deploy/models/vllm_prestart.py`, the `vllm` on the image's PATH, which also
   kills engine processes orphaned by a dead API server and marks the container unhealthy when a
   start dies. A start that dies leaves the voice released until its next line reloads it.) A swap is already approval-gated and already evicts the
   resident model for minutes, so the few seconds of CPU-fallback speech it costs are the cheapest
   part of an operation the owner has already accepted. The model server is the house's critical
   path; the voice is not.

4. **Failure to allocate degrades, it does not crash-loop.** If the device is absent, the driver
   mismatches, or the cap cannot be met, the service starts on CPU, reports `device: cpu` on its
   health endpoint, and Piper remains available. A GPU that disappears is a quality regression, not
   an outage. The compose service therefore must not carry a device request that makes the
   container refuse to start when the GPU is missing.

5. **Which engine and which device is speaking is shown in the HUD**, never silently swapped —
   carried forward from ADR-0033 unchanged, and now including the device, so a session that has
   quietly fallen to CPU is visible rather than merely slower.

## Consequences

- The `voice.long_form_chars` threshold stops being a routing rule and becomes dead configuration;
  CosyVoice serves every utterance it is healthy enough to serve.
- Piper's role narrows from "the short-utterance engine" to "the fallback", which is what the
  corrected `aura-voice` skill and `docs/runbooks/voice.md` now say.
- The budget is genuinely tight. 3072 MiB is a ceiling measured on a machine whose other tenant can
  change: if the owner raises `--gpu-memory-utilization` again, the TTS service must degrade to CPU
  rather than fight for memory, and rule 4 is what makes that safe.
- `scripts/verify/gpu-allocation.py` is the gate. It runs on the host, in CI and in the deployed
  container, and it fails on a real allocation rather than on the presence of a device node.

## Addendum (2026-09-10): the first word, not the whole line

The owner heard CosyVoice as "slower than Piper". Measured on the deployed shim, the
whole-line route (`/v1/audio/speech` as MP3, what the adapter's `/api/audio/speak` relays)
answers when the last sample exists, about six tenths of the line's own length after the
request: 0.8 s for "Ready.", 4.2 s for a 97-character sentence, 9.6 s for 247. The model is
autoregressive; that cost does not shrink. What shrinks is the wait for the *first* word: the
shim already streamed PCM (`response_format: pcm`, `engine.stream`), and nothing downstream
took it. Now the adapter relays it (`POST /api/audio/speak-pcm`, `audio/pcm` + `x-sample-rate`,
chunked), the console's relay forwards those headers, and the console's player schedules each
piece where the last ends (`Player.playPcm`) while the speech queue fetches the next sentence
ahead. Same sentences, streamed: the first byte at 1.3 s whatever the length, and synthesis at
about 0.55x real time keeps ahead of playback. The whole-line route stays as the fallback for an
engine that does not stream (the adapter answers 404 there) and for a shim still warming (503).
Proof: `test_speak_pcm_streams_the_line_as_it_is_made` in `aura-adapter/tests/test_adapter.py`,
`aura-web/e2e/hud-speech-stream.spec.ts`; the numbers are `docs/verification.md`'s.

## Addendum (2026-10-06): "Piper remains available" made true

Decision 4 said a CPU-bound service leaves Piper available, but nothing routed to it: on
2026-10-06 a Proxmox host driver update (kernel module 615.71.09, this container's userspace
610.57) left CosyVoice on the CPU, `device: cpu` on its health, and every spoken line about
170 s. The adapter now reads that health field (at most every 30 s) and, while it says `cpu`,
answers `speak` and `speak-pcm` from the house's own TTS -- jarvis-core's `POST /api/voice/tts`,
which speaks one line through the Wyoming Piper the house already runs -- with
`x-jarvis-tts-engine: house`, so decision 5's HUD readout names it. Persona voices are lost
while it lasts (Piper has one voice); a house that cannot speak either falls through to the
CosyVoice path as before. Proof: `test_cpu_voice_is_replaced_by_the_house_voice` in
`aura-adapter/tests/test_adapter.py` and `test_voice_tts_speaks_one_line_through_the_house_voice`
in `aura-core/tests/test_api.py`; live on production the same night, a line in 1.6-3.7 s
(`x-jarvis-tts-engine: house`) where the deploy smoke had measured 169952 ms.
