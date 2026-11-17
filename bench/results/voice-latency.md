# Voice latency (T-0605, v2 section 6.7 / AUDIT V7)

Target: speech starts at most 1.2 s after the owner stops talking, on the LAN.
Measured by `scripts/hermes-probes/voice_latency_probe.py --base <adapter> --core <house> --wav <clip> --n 20`
(private transcribes, read-only). All figures ms, p50 / p95.

| When | Where | n | client | receive | decode | gate | gate_wait | transcribe | core_verify |
|---|---|---|---|---|---|---|---|---|---|
| before (590933d1, from the deploy check) | production | 3 | - | - | - | - | - | 1391 / 3075 | - |
| before this change (4a4360cb tree, 590933d1 image: no Server-Timing yet), 2026-11-08 04:55 UTC, a 2.7 s clip of Jarvis's voice, private | production | 12 | 341 / 409 | - | - | - | - | - | - |
| profile, 2026-11-08 05:20 UTC (590933d1 image), same clip, with `--core` | production | 10 | 407 / 556 | - | - | - | - | - | 186 / 256 |
| `/api/status` at the same time: transcribe n = 29 **394 / 1391**; first PCM of a spoken reply (speak-pcm request to first byte) n = 4 **1596 / 4066** | production | | | | | | | | |
| after T-0605 (e7f864b2 deployed 2026-11-08 06:2x UTC), same clip, `--core`, n = 20 | production | 20 | 307 / 363 | 0.3 / 0.3 | 305 / 361 | 179 / 244 | **0 / 0** | 305 / 362 | 182 / 193 |

Local (fakes under `aura-adapter/tests`, no Whisper, no GPU): proves the stages are
reported and ordered (transcribe >= decode >= 0), not the production cost. Its figures are
a few ms of adapter overhead and must not be read against the rows above.

## What the code already does

- The speaker gate is already concurrent with decode (`asyncio.create_task` in
  `/api/audio/transcribe`); `gate_wait` is the proof: if it is ~0 the gate costs the turn
  nothing, and if not, `gate` shows by how much it outlasts decode.
- Decode is on the card's Whisper (~0.7 s) with Hermes' CPU Whisper as fallback (~3 s): a p95
  near 3 s means the fallback answered. Read `decode` first.
- Not done, pending the production rows: streaming partial audio, wake-word pre-warm.

## Where the time goes (2026-11-08)

Transcription is not the bottleneck: decode on the card answers a 2.7 s clip in ~0.4 s and the
speaker gate (~0.19 s) runs beside it. The 1,391 ms p50 reported right after the 590933d1 deploy
was three cold requests; with 29 it is the p95. The first audio of the reply is: CosyVoice streams
PCM, but its first piece waits for `FIRST_HOP_TOKENS` = 25 speech tokens (one second of audio at
25 Hz), generated at about 0.55× real time, so ~1.6 s from the request to the first byte. The
next cuts are there (a short first clause, a smaller first hop if it still sounds right, a cached
acknowledgement in Jarvis's own voice for turns that run tools) and in the model's time to the
first sentence, which this table does not measure yet.

## A whole turn, after the deploy (2026-11-08, `turn_latency_probe.py`, n = 10 each)

"In one short sentence, what is a kilowatt hour?", typed, so this starts at the prompt and adds the
transcribe row above (~0.3 s) for a spoken turn. All ms, p50 / p95.

| | first token | first sentence | first PCM of it | prompt to first audio |
|---|---|---|---|---|
| whole first sentence to TTS | 1841 / 2537 | 2231 / 3169 | 1553 / 1686 | 3845 / 4749 |
| first clause to TTS (`first_clause: true`) | 849 / 3973 | 1227 / 4320 | 1532 / 1651 | 2748 / 5971 |
| whole first sentence, prompt trimmed to the hop (T-0624, b99d5fee, n = 12) | 1244 / 2316 | 1774 / 2735 | **1265 / 1460** | 3128 / 4000 |

Trimming each voice's prompt to a multiple of the 25-token hop (`COSYVOICE_TRIM_PROMPT`, on by
default; the load logs `flow prompt 310 -> 300 tokens` for jarvis) takes 288 ms off the first PCM at
p50 (1553 → 1265) and 226 ms at p95 (1686 → 1460). The first-token and first-sentence columns are
the model, not the trim (see the variance note below).

**Not met:** speech ≤ 1.2 s after the owner stops talking. A spoken turn is ~0.3 s transcribe +
~0.9-1.8 s to the model's first token + ~0.4 s to its first clause + ~1.3 s to CosyVoice's first
PCM with the trimmed prompt, about 2.9-3.8 s at p50. The first-clause cut barely moves the TTS row (1553 → 1532): the
first piece waits for 25 speech tokens whatever the text length, as the profile said. The model's
first-token time varies a lot between runs (849 vs 1841 p50 with the same prompt), so the two
first-sentence rows are mostly model variance, not the cut.

What is left, largest first: the model's time to first token (reasoning; `voice.reasoning_effort`,
T-0641), a smaller first hop (`COSYVOICE_FIRST_HOP_TOKENS`, needs a listening test
for seams), and the model's time to first token (Hermes's prompt prefix and vLLM prefix caching;
T-0637).

## Spoken against typed, after T-0644 (T-0683, 2026-11-08 19:0x UTC, image 8b944dee)

`turn_latency_probe.py --n 1` run 24 times against the deployed adapter, alternating a spoken turn
(`voice: true`, so the adapter sends `config.set reasoning` with the session's id first:
`VOICE_REASONING_EFFORT` = **low**) and a typed one (`--typed`, the model's `LLM_REASONING_EFFORT` =
**medium**), 12 of each. Same question as above, first-clause cut on. The host was quiet: no pipeline,
no gradle and no deploy running (load 4.3 falling, the top process 40% of one core). Percentiles are
over the 12 turns of each kind, linear interpolation. All ms, p50 / p95 (min-max).

| | first token | first sentence | first PCM of it | prompt to first audio |
|---|---|---|---|---|
| spoken, effort low (n = 12) | 1781 / 3718 (568-3899) | 2191 / 4143 | 1224 / 1431 | **3524 / 5398** (2088-5594) |
| typed, effort medium (n = 12) | 1473 / 2267 (594-2341) | 1884 / 2617 | 1233 / 1410 | **3066 / 3976** (2037-4045) |

**Reading.** The lower effort buys a spoken turn nothing measurable on this question: its first token
is not earlier (1781 against 1473 at p50, and a worse p95), and the TTS row is the same in both, as
it should be. The two ranges overlap almost entirely (568-3899 against 594-2341), so at n = 12 the
model's own variance is larger than any effect of the level. Either a one-sentence factual question
reasons briefly at both levels, or the session-scoped level does not reach the request vLLM gets
(Hermes' `_set_reasoning` stores `create_reasoning_override` and sets the live agent's
`reasoning_config`; this probe does not see the reasoning tokens, so it cannot tell which). What the
owner waits for after they stop talking is still ~0.3 s transcribe + ~3.5 s at p50, ~5.4 s at p95,
against the 1.2 s target. The next measurement counts reasoning tokens per turn at each level (the
HUD's reasoning deltas), which separates "the level is ignored" from "this question reasons little".
**Answered (T-0723, `reasoning-effort.md`):** the level is ignored. The provider's static
`extra_body.reasoning_effort` overrode every session's level, so both kinds of turn ran at medium.
