---
name: aura-voice
description: "Use for JARVIS voice work: TTS/STT choice, cloning, latency."
version: 0.1.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Voice, TTS, STT, Homelab, Hermes]
    related_skills: [homelab-runbook, hermes-agent]
---

# JARVIS's voice (TTS / STT)

## Current state (verify before asserting — read config, don't recite this)
- TTS: the *decided* engine is **CosyVoice 3 on the JARVIS host GPU** (ADR-0033 as
  superseded 2026-09-09), with four cloned profiles and Piper as the CPU fallback.
  What is still *configured* on this host is `tts.provider: piper`
  (`en_GB-alan-medium`) — read the config before answering, do not recite either.
  Piper measured on `/api/audio/speak` 2026-09-03 under load: 2.1 s for one word,
  3.7 s for a short sentence, 5.8 s for a long one. The utterance is synthesised
  whole, so those are also the times to the first word.
- STT: local faster-whisper; Groq/ElevenLabs keys may exist as fallbacks.
- Config lives in `~/.hermes/config.yaml` (on this host `/opt/data/config.yaml`):
  sections `tts:` and `stt:`. Check `grep -A20 '^tts:' $HERMES_HOME/config.yaml` before
  answering any "what voice do you use" question.
- Never hand-edit config.yaml — `hermes config set tts.provider <name>` etc.

## The latency truth
Dominant reply latency is the LLM writing the sentence, not the TTS. A local
clone on the JARVIS host's GPU cuts *speech* from ~2 s to well under a second for short
replies; say that trade-off when the user asks for "quicker" so expectations are set.

## Provider landscape (what Hermes accepts)
- **Built-in local**: `piper` (incl. custom `.onnx` via `tts.piper.voice` — no cloning),
  `neutts` (zero-shot clone from a `ref_audio` + `ref_text` clip; air-q4 is ~0.2 GB,
  CPU-capable), `kittentts` (tiny, fixed voices — no cloning).
- **OpenAI-compatible server**: any self-hosted TTS exposing `/v1/audio/speech` works
  via `tts.openai.base_url` — this repo ships one for CosyVoice 3 in
  `aura-voice/`, run as the `jarvis-cosyvoice` service of `deploy/docker-compose.yml`. **The two 3090s are in the JARVIS
  host**, shared by jarvis-models (vLLM), CosyVoice and Whisper.
  Measured 2026-09-09 with the 27b resident, ~20.1 GiB of
  24 GiB is held per card and about **3.9 GiB per card is free**. A 0.5B
  flow-matching model in fp16 (~3 GB) fits in that headroom without lowering
  llama-swap's reservation — but it is headroom, not spare capacity, so pin the
  device explicitly and degrade to the CPU fallback rather than OOM.
  See `docs/runbooks/voice.md`.
- **Command provider**: `tts.providers.<name>` with `type: command` for any CLI that
  takes a text file and writes audio; supports `voice_compatible`, `warm_command` /
  `release_command` (preload/unload on the speech toggles).
- Full provider list and per-provider knobs: the upstream hermes-agent skill's `configuration.md` reference
  and docs `https://hermes-agent.nousresearch.com/docs/user-guide/features/tts`.

## "Make the voice actually sound like JARVIS / be quicker"
Research bank with real model weights (HF tree API, verified 2026-09-03) and the
integration paths, easiest first: `references/voice-tts.md`. The short version:
1. **NeuTTS + a ~15 s clean reference clip** — zero new infrastructure, first test.
2. **CosyVoice 3 on the JARVIS host GPU** — the decided answer: sub-second
   streaming, OpenAI-compatible server, house network only, four named profiles.

## Procedure for a voice change
1. Read current `tts:` / `stt:` config; state what's running now.
2. For a clone: a small number of clean, prosodically representative references
   beats a long noisy corpus — fifteen good seconds outperforms an hour of dialogue
   with music under it. Output from a cloned profile stays on this system and is
   never published, which is the basis on which the owner authorised the four
   character profiles.
3. Test the candidate (bake a one-liner, let the user judge) **before** switching
   `tts.provider`.
4. Switch via `hermes config set`; verify with a fresh `text_to_speech` call and
   report first-word latency.
5. Update `references/voice-tts.md` with the chosen model and any measured latency.

## Pitfalls
- `web_search` for "how big is model X" returns junk (SearXNG); use the HF tree API
  (see web-search-fallback §4). The `safetensors` param-count field is often 0 — sum
  file sizes instead.
- XTTS-v2 is CPML (non-commercial); Chatterbox/F5/CosyVoice are MIT/Apache-2.0.
- KittenTTS/Kokoro cannot clone — they're the wrong tool for a JARVIS voice, however fast.
- Voice toggle warm-up: local engines load lazily; the speech toggles hold leases
  (`tts-lease`) so the first spoken reply after enabling speech isn't silent.
- ffmpeg is required for voice-bubble delivery of non-Opus providers (Piper, NeuTTS, KittenTTS).
