# Speech-to-text model: base vs small vs distil-small.en (2026-10-06)

Why: the owner reported garbled transcripts from the console. The root cause was a leaked
recorder (fbc89cc5), but `base` also mishears clean speech often enough to matter.

How: six fixed command sentences spoken by the production CosyVoice voice, transcribed in the
`jarvis` container with faster-whisper on CPU, int8, `language=en`, VAD on, the production
`initial_prompt`; word error rate by word edit distance. Script: the session's
`stt_bench.py` (synthesise via `/api/audio/speak`, then `WhisperModel.transcribe`).

| model | WER | p50 per utterance | max | load |
|---|---|---|---|---|
| base | 9.8 % | 0.49 s | 0.55 s | 0.6 s |
| small | **4.7 %** | 1.33 s | 1.38 s | 13.5 s |
| distil-small.en | 6.1 % | 1.11 s | 1.17 s | 10.3 s |

Decision: `small` is the default. It halves the errors for +0.85 s, inside the 2 s budget
for a short utterance. Clean TTS speech flatters every model; a real room mic widens the
gap, so the measured gain is a floor.

Not measured: real microphone audio, accents, long dictation.
