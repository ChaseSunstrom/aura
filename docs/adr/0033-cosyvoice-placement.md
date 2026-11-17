# ADR-0033: CosyVoice 3 runs beside Piper on the JARVIS host, not on the GPU

- **Status:** **Superseded by [ADR-0039](0039-cosyvoice-on-the-jarvis-host-gpu.md) (2026-09-09)** — the GPUs are now passed into the JARVIS host, so the premise of the three-way analysis below no longer holds. The analysis and the 2026-09-08 addendum are kept as the history of how ADR-0039 was reached; do not act on the decision.
- **Was:** Accepted (2026-09-08), superseding the "opt-in engine, owner decision required" note in the September 2026 build log (2026-09-05)
- **Comes from:** the v3 plan §9, the v3 brief Part 2

## Context

The brief names three candidate placements for Fun-CosyVoice3-0.5B and asks for a decision made on
measured latency rather than on preference.

**(a) Its own service in the AI LXC, beside vLLM.** Measured through llama-swap's API on
2026-09-08, `ai.example.ts.net:9000/running`:

```
vllm serve cyankiwi/Qwen3.8-27B-AWQ-INT4 --max-model-len 256000 --kv-cache-dtype fp8_e5m2
  --gpu-memory-utilization 0.92 --tensor-parallel-size 2 --enable-prefix-caching     ttl: 0
```

Two RTX 3090s at 24576 MiB. `0.92 x 24576 = 22610` MiB reserved per card, `ttl: 0` so the model is
never unloaded, leaving about 1966 MiB free per card. CosyVoice 3 at 0.5B in fp16 needs about 3 GB
and does not fit. Making it fit means lowering `--gpu-memory-utilization`, and on an AWQ-INT4 27B at
256k context the freed memory comes out of the KV cache — that is, out of the context window the
whole system is built around.

Two further facts settle (a) for this session rather than for the project:

- There is **no SSH from this environment to the AI LXC** (`ssh jarvisdev@ai.example.ts.net` →
  `Permission denied (publickey,password)`). A compose service cannot be deployed there and
  llama-swap cannot be restarted with a different reservation.
- Lowering the reservation is a production change to the model server the whole house depends on.
  It is the owner's call, not a benchmark's.

**(b) CPU on the JARVIS host.** 4 vCPU, 16 GB RAM, 21 GB free on `/`. Piper — a model two orders of
magnitude smaller — already measures RTF 1.63-2.52 on this host, i.e. slower than real time. A 0.5B
flow-matching model with a vocoder will be worse, and the target is 2 s to first audible word.

**(c) A split path.** Short interjections synthesised by the fast engine, long-form speech by
CosyVoice, with the engine chosen per utterance and shown in the HUD.

## Decision

**Take (c). Ship the split path now; keep (a) as the owner-gated upgrade.**

- `aura-voice/` stays where it is: an OpenAI-shaped `POST /v1/audio/speech` service, reached
  by configuration from all three consumers (jarvis-core's `OpenAiTtsClient`, Hermes's built-in
  `openai` TTS provider through `tts.openai.base_url`, and the console) rather than by a new
  protocol. It runs behind a compose profile so a host without the weights still starts.
- **Piper stays installed as the automatic fallback** and as the short-utterance engine. Which
  engine spoke is surfaced in the HUD's voice panel — never silently swapped.
- The routing rule is one threshold, not a model: an utterance under `voice.long_form_chars`
  (default 160 characters, in the settings schema) goes to Piper; anything longer goes to CosyVoice
  if it is healthy and warm, else to Piper.
- CosyVoice is **warmed on boot** and keeps speaker embeddings preloaded, because the first
  synthesis after start is the slow one.
- Long replies stream sentence by sentence rather than synthesising the whole reply first, and
  barge-in cuts audio within 200 ms.

If the owner later lowers `--gpu-memory-utilization` on the AI LXC and confirms the 256k context
survives, the same service moves there with `COSYVOICE_DEVICE=cuda` and the threshold goes to 0.
Nothing else changes: the endpoint, the voice registry and every consumer's configuration are
already device-agnostic.

## Addendum, 2026-09-08: the owner lowered the reservation and the context survived

The upgrade condition below — "if the owner later lowers `--gpu-memory-utilization` and confirms
the 256k context survives" — is now half met, and the measurement is recorded here rather than
left as a claim.

The owner set the reservation to **0.85**. `ai.example.ts.net:9000/running` reports:

```
vllm serve cyankiwi/Qwen3.8-27B-AWQ-INT4 --max-model-len 256000 --kv-cache-dtype fp8_e5m2
  --gpu-memory-utilization 0.85 --tensor-parallel-size 2 --enable-prefix-caching     ttl: 0
```

`0.85 x 24576 = 20890` MiB reserved per card against 22610 at 0.92, so roughly **3686 MiB free per
card** against CosyVoice 3's ~3 GB. It fits, with a few hundred MiB of headroom rather than a
comfortable margin.

**The 256k context survived.** vLLM refuses to start when the KV cache cannot hold `max-model-len`,
and this server started and answered a chat completion with `--max-model-len 256000` still on the
command line. That is the confirmation this ADR asked for, and it removes the reason the decision
went to (c) rather than (a).

What still stands between the decision and the deployment is **access, not judgement**. There is no
SSH from this environment to the AI LXC — `jarvisdev`, `root`, `ai`, `jarvis`, `ubuntu` and
`debian` all answer `Permission denied (publickey,password)` — and the box exposes only llama-swap
on :9000: ports 2375, 2376, 9001, 8000 and 9443 are closed, so there is no Docker API or
Portainer to deploy through either. The JARVIS host cannot host it instead: it has no
`/dev/nvidia*`, no `libcuda`, and Docker there runs `runc` with no NVIDIA runtime.

So the split path stays in force for now, and moving to (a) is one of two owner actions: add a key
this environment can use to the AI LXC, or run the compose service there directly — the image
builds from `aura-voice/Dockerfile` and needs `COSYVOICE_FP16=1` on a GPU, the weights
mounted at `/models/Fun-CosyVoice3-0.5B-2512`, and the threshold `voice.long_form_chars` set to 0.

## Consequences

- **Good.** No regression risk to the voice benchmark: the fast path is the engine that already
  meets it. No production change to the model server. One endpoint, one voice registry, one place
  to point at a GPU later.
- **Bad.** JARVIS has two voices until (a) happens, and they do not sound identical. This is
  visible in the HUD by design rather than hidden, and the threshold is a setting so the owner can
  push everything to one engine.
- **Bad.** The 2 s target is met by Piper, not by CosyVoice, on this hardware. The measured
  CosyVoice CPU number is reported as itself in the September 2026 build log, not blended into an average.
- **Neutral.** `~/jarvis/voices/` does not exist on this host, so no cloned profile has a reference
  clip yet. The registry reads whatever the owner drops at `~/jarvis/voices/<name>/`; with nothing
  there it falls back to the built-in voice and says what is missing.

**Rights.** Nothing in this repository downloads, extracts, scrapes or rips reference audio from
films, shows, videos or any other media, and no instruction for doing so is written anywhere in it.
A reference clip is supplied by the owner, who holds the rights to it. Output from a cloned profile
is for the owner's private use on this system and is not to be published or distributed
(`docs/runbooks/voice.md`).
