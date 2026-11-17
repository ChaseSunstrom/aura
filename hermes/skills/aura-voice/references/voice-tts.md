# JARVIS voice — local TTS clone options (researched 2026-09-03)

Goal: make JARVIS's voice actually sound like JARVIS (a clone of a reference clip)
and be quicker than Piper on CPU (~2 s to first word).

Latency reality: the dominant cost is the LLM writing the sentence, not the TTS.
Local TTS on the house's GPU cuts the *speech* part from ~2 s to well under a second
for short replies; total reply latency still waits on the model.

## Cloning-capable models (real HF download weights, verified 2026-09-03 via the
HF tree API — see web-search-fallback §4 for the method)

| Model | Weights | Notes |
|---|---|---|
| NeuTTS (air, q4 GGUF) | ~0.2 GB | Zero-shot clone from a reference clip; runs on CPU; **built into Hermes** (`tts.provider: neutts` with `ref_audio`/`ref_text`) |
| XTTS-v2 | 2.1 GB | 6-sec clone; CPML licence — non-commercial only |
| Chatterbox Turbo | 4.0 GB | MIT, emotion control |
| CosyVoice2-0.5B | 4.9 GB | Apache-2.0, streaming, best latency/quality on the 3090; ships an OpenAI-compatible server → Hermes via `tts.openai.base_url` on the LAN |
| F5-TTS | 6.7 GB | Apache-2.0, very natural |
| Chatterbox (full repo) | 13.9 GB | all variants; overkill for one voice |
| KittenTTS / Kokoro-82M | 0.025 / 0.33 GB | fast and tiny, but **fixed voices, no cloning** — wrong tool for a JARVIS voice |

## Integration paths (easiest first)
1. **NeuTTS** — no new infrastructure: point `tts.provider` at it with
   `ref_audio`/`ref_text` (~15 s of clean reference clip on the Jarvis host).
2. **CosyVoice2 / F5-TTS / Chatterbox Turbo on the AI host** — 4–7 GB fits beside
   the resident LLM (or on the second 3090). Run its OpenAI-compatible server;
   Hermes `tts.openai.base_url` → LAN URL. Or wire it in as a command provider
   (`tts.providers.<name>`, `type: command`).
3. **Piper with a custom `.onnx`** (`tts.piper.voice` = absolute path) — no cloning,
   only pre-made voices.

## State at time of research
- No model chosen yet; the agreed next step is a NeuTTS test with a JARVIS
  reference clip, then judge the voice before committing to the AI-box option.
- Cloning a celebrity voice: legally grey (UK), fine in practice for private home use.
- Cloning the *user's* voice instead would also satisfy the JARVIS persona question —
  ask before assuming which timbre is wanted.

## Method note
Weights were measured by summing file sizes from
`https://huggingface.co/api/models/<repo>/tree/main?recursive=true`. The model API's
`safetensors` param-count field read 0 for every repo checked — don't trust it.
Gated repos (e.g. some F5/KittenTTS mirrors) return an error object on the tree
endpoint; use the README there.
