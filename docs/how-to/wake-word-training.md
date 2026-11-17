# Custom wake word: training, deployment, and where it runs

"Hey Jarvis" ships pretrained in both openWakeWord and microWakeWord, so P5/P6
work out of the box. This doc is the path for a **custom** phrase (or a
better-tuned "Hey Jarvis") and for understanding where detection runs on each
surface.

## Licensing, upfront

- **openWakeWord code** ([dscripka/openWakeWord](https://github.com/dscripka/openWakeWord)):
  Apache-2.0 — free to use anywhere.
- **Official pretrained models** (including `hey_jarvis_v0.1`):
  **CC BY-NC-SA 4.0**, English-only. Personal, non-commercial use — exactly
  our case — is fine. Don't redistribute them inside a commercial product.
- Models **you train yourself** with the notebook are yours; the synthetic
  speech generated with Piper is unencumbered for this use.

## Training a model (openWakeWord official path)

1. **Use the official training notebook/Colab** from the openWakeWord repo
   (`notebooks/training_models.ipynb` / the "train new models" Colab). It
   automates the whole recipe below; a full run is a few hours on a free
   Colab GPU.
2. **Synthetic positives:** thousands of clips of the target phrase are
   generated with **Piper** TTS (now maintained as
   [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl); GPL-3.0
   code, which is fine — we only consume its audio output) across many
   voices, speeds, and pitches. Rule of thumb: 3–6 syllable phrases detect
   best ("hey jarvis" is in the sweet spot); avoid phrases that collide with
   common speech.
3. **Negative data:** large "not the wake word" corpora (the notebook pulls
   precomputed openWakeWord features from public datasets — speech, music,
   noise) plus *adversarial* negatives: phonetically close phrases
   ("hey travis", "jar of fish") that Piper also synthesizes.
4. **Augmentation:** room impulse responses + background noise mixing
   (audiomentations/torch-audiomentations in the notebook) so the model
   survives kitchens and cars rather than just clean TTS audio.
5. **Iterate on the false-accept/false-reject curve.** Test against real
   recordings of *your* voice at distance. Expect 2–3 training rounds tuning
   `target_phrase` variants and negative weighting.
6. **Optional per-speaker verifier:** openWakeWord supports
   `custom_verifier_models` — a small logistic-regression layer trained on
   ~20 recordings of the actual user saying the phrase (plus negatives),
   attached per wake word model via `custom_verifier_threshold`. Cheap and
   effective for cutting TV/guest false accepts; only speakers you trained
   will trigger reliably.

## Deployment targets

### Server-side: wyoming-openwakeword (primary)

The `wyoming-openwakeword` container listens on **tcp/10400** and comes with
`ok_nabu`, `hey_jarvis`, `hey_mycroft`, etc. preinstalled. It is part of
`aura-core/docker-compose.yml`, and `voice: wake:` in
`aura-core/config/configuration.yaml` points at it.

- Custom models: drop the trained `.tflite` into the container's
  `--custom-model-dir`, restart it, and name the model in the `voice:` block
  (or on the pipeline that should use it).
- AURA's word is a second custom model folder: compose mounts
  `themes/aura/assets/wake/` as `/custom-aura`, and the service names a model by
  its file's stem, so `aura.tflite` there is "aura". Make it from `aura.onnx` with
  `onnx2tf` (not a dependency of this repo; run it where it is installed). While AURA
  is the primary persona jarvis-core asks the adapter (`/api/status`) and listens for
  "aura", but only if the service's `describe` lists it, else for `voice: wake: model:`
  (an unknown name makes openWakeWord listen for its default word without a word of
  complaint). `WAKE_THRESHOLD` / `WAKE_TRIGGER_LEVEL` are the service's, one pair for
  every word; the defaults are the service's own 0.5 and 1 (what `hey_jarvis` runs at).
  Once `aura.tflite` is in place, set AURA's measured pair from `aura.metrics.json`
  (`threshold` 0.95, `framesToFire` 2).
- This is where wake word runs for **satellite hardware** (Voice PE etc. can
  offload wake word to the server, though Voice PE normally runs
  microWakeWord on-device).

### On-device Android: openWakeWord on the phone (`ai.jarvis.app`)

The Jarvis app runs openWakeWord's own three ONNX models on the phone
(`OnDeviceWakeWord.kt`): the shared melspectrogram and embedding stages, then one
head per name (`hey_jarvis_v0.1.onnx`, and the personas' heads when they are on).
The weights come from the house (`ModelStore`); without them the house's
wyoming-openwakeword listens instead (`WakePath.SERVER`).

- **Steps.** Each 80 ms step feeds the melspectrogram the new 1280 samples and the
  480 before them, as openWakeWord's streaming does: 8 mel frames a step, unbroken,
  which is what the heads were trained on. Until T-0714 the phone fed 1280 alone (5
  frames a step), gapping the name in time; synthetic takes fired 127 of 252 times
  that way and 142 the reference way (`scripts/verify/wake_verifier_live_check.py`;
  Piper's synthesis is random, so a rerun moves each count by a few).
- **Levels.** Settings → Wake sensitivity picks a `WakeSensitivity`: strict
  0.65 for 3 steps, normal 0.5 for 2, eager 0.4 for 2, with a refractory period
  (`WakeScore.kt`). A lower bar is lower for every voice, the television's too.
- **Teach it the name** (T-0714). The stock head was trained on synthetic voices,
  so a real voice's other ways of saying it score under the bar. Settings → Teach
  it the name records eight takes said differently (quick, drawn out, quiet, from
  across the room, as a question…) and a read-aloud line, and fits the per-speaker
  verifier from step 6 above on the phone (`WakeVerifier.kt`, `WakeEnrol.kt`): a
  logistic regression over the head's 16×96 embedding window, saved as
  `files/wake/hey_jarvis.verifier`. A "hey jarvis" step the head scores at the
  level's gate or more (strict 0.3, normal 0.2, eager 0.1) is lifted to the
  verifier's probability when that is higher; the level's threshold, steps and
  refractory then decide as before, so a step the head is sure of still fires and a
  step it did not hear at all still misses. A take the head scores under 0.1 is
  refused at enrolment, since nothing could lift it. Each take shows
  the head's own best score, and the screen reports how many takes the rest still
  recognised with each one left out. "Forget my takes" deletes the file. On
  synthetic voices, held-out takes fired 121 of 224 times without a verifier and,
  with one, 136 at strict, 149 at normal and 160 at eager. Other phrases fired 4 of
  128 times with the head alone: still 4 at strict, 16 at normal and 24 at eager,
  nearly all rhymes of the name ("Hey darling", "Hey Travis"). So the level is the
  trade: strict adds hearing with no added false wake, eager hears most and wakes on
  rhymes, and the house's speaker gate still drops a turn in another voice. Real
  voices are not measured here.
- Battery/UX caveats of the Android implementation are covered in
  `docs/how-to/android.md` §4 (no DSP path for third-party apps, foreground
  service, experimental quirks).

### Browser (aura-web)

P4 status: the web HUD uses **push-to-talk plus VAD-based hands-free**
(browser VAD endpointing after PTT arm) — no wake word in the browser yet.
The plug-in point is documented in `aura-web`: an
**openWakeWord-to-WASM/ONNX** runtime (onnxruntime-web running the melspec +
embedding + wake models on an AudioWorklet feed) can be slotted in front of
the existing VAD without changing the pipeline contract. Until that lands,
hands-free in the browser means "arm once per session", not "always
listening".

## Quick reference

| Surface | Engine | Model file | Where it runs |
|---|---|---|---|
| jarvis-core / satellites | openWakeWord | `.tflite` (OWW) | wyoming-openwakeword, port 10400 |
| Android app (`ai.jarvis.app`) | openWakeWord (ONNX), plus the owner's verifier | `.onnx` (OWW), `hey_jarvis.verifier` | foreground service on the phone |
| Web HUD | none yet (PTT + VAD) | — | OWW-WASM plug-in point reserved |
