# Runbook: the voice JARVIS speaks in

> **Updated 2026-11-10 (T-0955).** CosyVoice 3 is gone: its engine, its delivery brief, the clip
> ingest and its image layers (the upstream clone and its requirements) were deleted, and the
> `jarvis-cosyvoice` service is Kokoro, Whisper on the card and the speaker gate's CAM++
> voiceprints, which still read `campplus.onnx` from the mounted model directory. Callers name the
> model `kokoro`; a request with no voice is said in `af_heart`. `/health`'s `warm` is now
> Kokoro's, so the deploy's warm-up wait (step 5c) ends instead of running out its 600 s on
> CosyVoice's flag. The service, container and env names keep "cosyvoice" until T-0953 renames them.
> The CosyVoice sections below are history.

> **Updated 2026-11-09 (T-0901).** Kokoro speaks every pack's voice (AURA `af_heart`, JARVIS
> `bm_george`), inside the same `jarvis-cosyvoice` service. **CosyVoice 3 is no longer loaded:**
> production mounts no reference clips, and with none it skips its warm-up and never takes the
> card. With both loaded, its 2 GB model and Kokoro shared a VRAM cap measured for CosyVoice alone
> and 14 of 20 sentences failed out of memory: replies shown and never said. The owner: "you don't
> need to use cosyvoice3 if kokoro is better, you can remove it". `bash scripts/verify/tts-burst.sh`
> checks it live. The sections below on CosyVoice and its clips are history until T-0907 deletes
> the code; the clips on the host are left where they are.

> **Updated 2026-09-09 (overhaul Phase 0b/6).** TTS is now **CosyVoice 3 in the `jarvis-cosyvoice`
> service, attached to the JARVIS host's GPUs** — the two RTX 3090s are passed into this host and
> ~3.9 GiB per card is free beside vLLM. **Piper is the CPU fallback**, used when the GPU is busy or
> the service is unhealthy. Everything below about Piper is still correct *for the fallback path*;
> read "the TTS engine" as "the fallback engine" unless the section names CosyVoice.

Which engine says the words, how to change it, and what you are agreeing to when
you point it at a recording. The speech-to-text half is in
[jarvis.md](jarvis.md); this page is only the way out.

## What speaks today

Piper, inside the Hermes container — the `piper-tts` package installed by
`deploy/Dockerfile`, not the `wyoming-piper` container, which is the legacy
on-box path ([ADR-0008](../adr/0008-voice-in-hermes-container.md)). Every
surface reaches it through Hermes `POST /api/audio/speak`: the adapter, the
phone, the desktop, `scripts/smoke.sh` step 6 and the `voice` benchmark
category.

Measured on the production host, 2026-09-03, load average 23 on 4 vCPUs:

| Reply | `/api/audio/speak` |
| --- | --- |
| `Yes.` | 2.09 s |
| `The kitchen lights are on, Sir.` | 3.71 s |
| A two-clause status sentence, 78 characters | 5.81 s |

The whole utterance is synthesised before any of it is sent, so that column is
also the time to the first word. The target is 2 s. Note before reaching for a
faster engine: the box was carrying twenty-odd other jobs, and the reply is
encoded to MP3 after synthesis. Streaming the first sentence would buy more
than swapping the engine does.

## The three engines

| `tts: engine:` | What it is | How it is reached |
| --- | --- | --- |
| unset, or anything unrecognised | Piper over Wyoming, `:10200` | the default; a typo lands here rather than muting the house |
| `openai` | Kokoro, `--profile kokoro` | `/v1/audio/speech` on `:8880` |
| `cosyvoice` | Kokoro, in the `jarvis-cosyvoice` service of `deploy/docker-compose.yml` (CosyVoice 3 until T-0955) | `/v1/audio/speech` on `:8890` |

The two HTTP engines share one client
(`aura-core/aura/voice/openai_tts.py`) and differ only in their defaults.
That shape was chosen over a Wyoming shim because Hermes's built-in `openai`
TTS provider already honours `tts.openai.base_url`, so `/api/audio/speak` and
everything behind it follows with configuration and no new protocol code. A
Wyoming shim would have moved jarvis-core alone.

## CosyVoice 3 (removed, T-0955)

Not loaded since 2026-11-09 and deleted on 2026-11-10 (the notes at the top). What follows is how
it ran, kept for the measurements.

`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`, Apache-2.0, released 2025-12. The image
pins the inference code to a commit and the runbook pins the weights to a
revision, because the two are versioned separately upstream and a `main` that
moved under a fixed set of weights is a voice that changed without anyone
deciding it should.

**It is on, by default, on the JARVIS host's own card** (ADR-0039; superseding
ADR-0033 and the paragraph this one replaces, which said the GPUs were on `ai`).
Production runs it as the `jarvis-cosyvoice` service in `deploy/docker-compose.yml`;
`deploy/docker-compose.gpu.yml` passes card 0 in, and `scripts/deploy.sh` adds
that overlay only when `/dev/nvidia0` exists, so a host without the card still
starts the stack and speaks slowly on the CPU. Hermes' own TTS points at it
(`tts.provider: openai`, `base_url http://jarvis-cosyvoice:8890/v1`), and the
adapter's `/api/audio/speak` calls it directly with a `voice`, one reference
clip per persona.

Measured 2026-09-09 on this host (RTX 3090, card 0, beside vLLM at 0.85):

| what | measured |
|---|---|
| resident on the card, warm | 2146 MiB (cap `COSYVOICE_VRAM_CAP_MIB=2560`) |
| host RAM, warm / peak while loading | 3.3 GB / 7.0 GiB (compose limit 10G) |
| model load + warm-up after a start | ~2 min load, 30 s warm (189 s the first time a new image's kernels compile) |
| time to first byte, streamed PCM, mean of 3 | jarvis 1.16 s (the three personas measured beside it went in T-0936) |
| whole line (~5 s of speech) | 2.1–2.9 s |

The image runs torch 2.6.0 (cu124) and lightning 2.3.3 rather than upstream's
2.3.1 and 2.2.4, because the CI CVE gate refused the older pair; cuFFT is pinned
back to 11.0.2.54 because every newer cuFFT fails every FFT on this driver
(`aura-voice/Dockerfile` says how that was found).

How it fits where upstream's loader did not: the checkpoints are read to host
RAM, the LLM and the flow converted to half there, and only then moved (the
vocoder stays fp32); the speech tokenizer's ONNX session runs on the CPU. Both
are patches on the pinned upstream ref, in `aura_voice/engine.py`.

Known gap: the text normaliser (`wetext`) needs a model from ModelScope that the
download refuses (403 on part of it), so the frontend is off and numbers are
spoken as the model reads them. Spell figures out in copy meant to be spoken.

**What the voice is never given (T-0729).** Every surface speaks through the
adapter's `spoken()` (`aura_adapter/spoken.py`): a fenced block is said as
"The code is on screen.", each list item ends a sentence, an id (a UUID, a
hash, a long key) is "the one on screen", a path is its file name, and a bare
URL is its host. The console cuts its stream into whole sentences first
(`speech.ts` `cutStream`), never inside a fenced block and never at a full stop
the stream may still continue ("wallet." of "wallet.nu.fi") unless a tool has
started, which is where the text pauses. A fence counts only at the start of a
line, so three backticks in prose do not swallow the rest. The owner's reply
that was heard spelled out is the shared table `tests/contracts/speech-nufi.json`,
read by both suites.

**If the voice is down.** There is no automatic Piper fallback while Hermes'
provider is the shim: the adapter falls back to hermes serve, and hermes serve's
provider is the same shim. The way back is one line in the host's `.env`:

```bash
# /opt/jarvis-host/.env
TTS_PROVIDER=piper
AURA_TTS_ENGINE=wyoming
# then, on the host:
cd /opt/jarvis-host && docker compose up -d jarvis jarvis-adapter
```

The entrypoint pins `tts.provider` from the env on every start, so the live
config follows the line; set it back to `openai`/`cosyvoice` the same way. The
CI smoke stack runs on these two lines permanently, having no voice service.

**In the car when the server is out.** The phone's own speech engine is a woman's
voice unless a male one is installed, so the car never switches to it silently
(Build Brief §2.3). At each drive start, while the server answers, the car renders its
fixed lines in the persona's voice and keeps them on the phone
(`JarvisVoiceLines.FIXED`: "Jarvis is offline", the media acknowledgements, "Sorry,
I did not catch that", "Yes or no?", the lines the connect check says, and the outage
notice), re-rendering any clip a day old. Offline, a fixed line plays its clip; anything
else is preceded once per drive (however many times the head unit re-forms the link) by
the notice, "I can't reach the server, so my voice will sound different until it's
back", in Jarvis's voice, and then spoken by the phone's engine. While the house
answered within the last minute a line that falls to the phone's engine was only late:
it is said with no notice, and the notice stays armed (T-1160). No reply,
approval or message text is cached. The clips live in the app's files under
`jarvis-voice/`; clearing the app's storage drops them until the next drive.

**Offline voice (T-0618).** A free-form line offline is spoken by a voice kept on the
phone, not the phone's engine. The worn theme pack's `voice.offline.fallback` picks the
order, pinned by `tests/contracts/offline-voice.json`. JARVIS (`bundled-voice`): cached
clip, then the offline voice, and only if that is absent or fails to load the announced
system engine. AURA (`prerendered-clips`, T-0861): her own clips and no other voice ever;
a line with no clip is said in her own voice on the phone, Kokoro-82M `af_heart` int8
(T-0872), but only once this phone has measured it keeping up in real time; until then
(not fetched, or too slow) it stays unsaid, and the first of an outage plays her "I'm
working offline" clip instead (`JarvisVoiceLines.WORKING_OFFLINE`). Her voice is
`kokoro-int8-multi-lang-v1_0.tar.bz2` (132 MB, speaker 3, the US lexicon; about 160 MB
unpacked), fetched like the Piper archive below but only for a clips-only pack, and at
drive start only on an unmetered network. Right after it unpacks, `BundledVoice.fetch`
renders one car-sized line and keeps the ratio of render time to speech in `pace`
beside the weights; below 1 is real time. System check says "Ready", "Too slow on this
phone" or "Not downloaded".

**Replies in the car (T-1161).** A reply voiced at the house reached the car choppy and
cut off over mobile data (the 2026-11-12 drive: lines 8 s late, a stream that ran dry,
the car's own link clean). So in the car AURA's replies are said from that same on-phone
voice, a sentence at a time (`CarTurn.phone`, `CarTurn.sentences`): the first sentence is
heard while the next is made, and the house is not asked. The house setting
`hud.car_voice` chooses: `auto` (the default; the phone's voice when it is there and its
`pace` is below 1), `house`, or `phone` (the phone's even when it measured slow). The car
page hands it to the phone (`voiceAct`, `CarHud.voiceOf`; `tests/contracts/car-state.json`
`voice` pins the modes and the decision, `CarTurn.usesPhone`), and "set the car voice to
the house" changes it. A sentence the phone cannot make, or makes late, and everything after it,
goes to the house as before: the first sentence has 4 s (`FIRST_SENTENCE_MILLIS`; the
model is loaded at drive start while the house answers, so it rarely needs them), a later
one the length of the sentence before it plus 1.5 s. A render already running cannot be
stopped, so the next phone sentence waits for it, one sentence at most. Each sentence logs `a reply's sentence from the phone's voice: N ms
for M ms of audio` in the drive's log. It does not cover a pack other than AURA (its
replies stay the house's), the fixed lines and turn words (their own path, above), or the
voice outside the car; the phone's render pace under a hot, projecting phone and the
battery cost are not measured here. JARVIS's offline voice is Piper `en_GB-alan-medium` fp32 (the same voice the
persona's reference clip is made with, above), run by sherpa-onnx 2.0.1. Candidates
compared against that reference: alan-medium (closest timbre and pace, chosen),
northern_english_male (flatter, further from reference), alan-low (smaller, audibly
coarser). The int8 build (21 MB) was not compared, so fp32 stays.

*Delivery.* The weights are not in the APK or in git. jarvis-core mirrors the archive
(`vits-piper-en_GB-alan-medium.tar.bz2`, 67 MB, URL and sha256 pinned in
`aura/api/models.py` beside the wake word and Moonshine) and serves it at
`/api/models/<name>`; the phone's `ModelStore` downloads it over the origin it trusts,
checks the digest again, and `BundledVoice.fetch` unpacks it under `models/offline-voice/`
(then deletes the archive; about 63 MB on disk). It is fetched once while online: by
Settings, Download models, or at the first drive start while the house answers. Never
fetched: the announced system engine, as before. Fetched but not whole (the unpack fails,
the size marker disagrees, the model will not load): discarded, never loaded, fetched
again. Settings, System check shows "Offline voice" ready or not.

*Runtime.* Only the code is in the APK: sherpa-onnx's static-onnxruntime AAR, fetched at
build by `android-app/tools/fetch_sherpa_aar.py` (pinned sha256, gitignored `app/libs/`).
The app ships arm64-v8a only (`abiFilters`; armeabi-v7a and x86 are gone for every native
lib, and the emulator's x86_64 copies, 48.6 MB of the APK, are built in only with
`gradle -Paura.emulator`). The AAR's arm64-v8a `.so` is 24 MB uncompressed in the APK, as
`.so` files are stored here (9 MB of the 38.6 MB AAR download). Its static onnxruntime duplicates the app's onnxruntime 1.19.2 by roughly
15 to 20 MB of that. The alternative is sherpa's shared-runtime AAR on onnxruntime-android's
own library: a few MB (not measured), but it needs sherpa built against onnxruntime 1.19.x's ABI and a
`pickFirst` that makes the wake word and Moonshine run on a runtime they were not tested on.

*Licence.* What is known: the voice is a Piper VITS model trained on a
single British male speaker's recordings (the "alan" voice of the Mimic3/Piper voice sets).
The Piper code is MIT. The dataset's licence is not stated clearly: mimic3-voices lists it as
"All Rights Reserved", and the model card says only "See URL". That is not a grant to
redistribute or to ship in a product. The deployment is the owner's own devices, with the
mirror fetching the file from k2-fsa's public release for the owner's own use; the risk is
real the moment the app or the mirrored file goes to anyone else. Settle with the rights
holder, or swap the voice, before any public release.
AURA's Kokoro archive is Apache-2.0 throughout except its `espeak-ng-data` (GPL-3.0-or-later,
as in the Piper archive): LICENSES.md.

Offline listening: with audio already captured, the bundled Moonshine model hears it;
with no model the phone says it cannot hear, and with the speaker gate enforcing it
says it cannot check the voice, rather than bypass the gate (`OnDeviceStt`). A turn
takes transient audio focus that ducks music and ends on a call (`TurnFocus`).

```bash
# 1. the weights (~5 GB), pinned to the revision the shim was written against
huggingface-cli download FunAudioLLM/Fun-CosyVoice3-0.5B-2512 \
  --revision 29e01c4e8d000f4bcd70751be16fa94bf3d85a18 \
  --local-dir /opt/jarvis-host/voice/models/Fun-CosyVoice3-0.5B-2512   # COSYVOICE_MODELS_DIR

# 2. a reference clip (see below) as /opt/jarvis-host/voice/voices/jarvis.wav   # COSYVOICE_VOICES_DIR

# 3. up: one definition, in deploy/docker-compose.yml, with the GPU overlays
cd /opt/jarvis-host
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.gpu2.yml up -d jarvis-cosyvoice
curl -s http://127.0.0.1:8890/health

# 4. listen
curl -s http://127.0.0.1:8890/v1/audio/speech \
  -H 'content-type: application/json' \
  -d '{"model":"kokoro","input":"The kitchen lights are on.","voice":"af_heart"}' \
  -o /tmp/jarvis.wav
```

Then, to make the house speak through it, in `configuration.yaml` under
`voice:`:

```yaml
    tts: {engine: cosyvoice, url: 'http://127.0.0.1:8890/v1', voice: af_heart}
```

and, for Hermes's own `/api/audio/speak`, `tts.provider: openai` with
`tts.openai.base_url` pointed at `http://127.0.0.1:8890/v1`, model
`kokoro`, voice `af_heart`. Hermes wants an API key present even for a local
endpoint; any non-empty string will do, because the shim accepts an
`Authorization` header and ignores it.

## The reference clip, and what it commits you to

CosyVoice 3 has no voice of its own. The released weights ship no `spk2info.pt`,
so the modes that take a speaker id have an empty table and every mode that
works takes a reference clip. The written brief in
[docs/design/VOICE_BRIEF.md](../design/VOICE_BRIEF.md) steers *delivery* — pace,
level, falling ends. It cannot originate an identity. Whatever WAV you register
as `jarvis` is the identity.

**The clip must be yours.** The `<persona>.wav` under `COSYVOICE_VOICES_DIR` must be your own
recording, or one you hold the rights to, or a speaker from a corpus whose
licence permits it and whose attribution you honour. Do not point it at a film,
a broadcast, a podcast, or anything else recorded from a person who has not
agreed to this. The shim cannot check that and does not try: a registry entry
is a file that parsed as a WAV, nothing more.

The recommended route needs nobody's recording at all: synthesise the reference
clip with the Piper voice this stack already ships and is already licensed for,
then let the brief shape its delivery. JARVIS keeps sounding like itself across
the change, and the clip is reproducible from a command rather than a file
somebody has to keep.

```bash
# six and a half seconds of the voice the house already has, as the reference.
# The interpreter matters: piper is in the Hermes venv, not the system python,
# and synthesize_wav wants an open wave writer rather than a plain file.
docker exec jarvis /opt/hermes/.venv/bin/python -c "
import wave
from piper import PiperVoice
v = PiperVoice.load('/opt/data/cache/piper-voices/en_GB-alan-medium.onnx')
with wave.open('/tmp/jarvis-ref.wav','wb') as f:
    v.synthesize_wav('Good evening. The house is quiet, the cards are cold, '
                     'and nothing needs your attention just yet.', f)
"
docker cp jarvis:/tmp/jarvis-ref.wav aura-core/cosyvoice/voices/jarvis.wav
```

Run 2026-09-03: 6.57 s, 22050 Hz, mono, 16-bit. The onnxruntime
`pthread_setaffinity_np` lines it prints are noise from the container's CPU
mask, not a failure. Six to ten seconds of clean, unhurried speech is enough. Whatever you settle on
is then frozen as the preset name `jarvis` in the settings schema, so the
console, the phone, the desktop and the benchmark all ask for one name and get
one voice.

## Two personas, two voices

There are two personas, AURA and JARVIS (T-0936 removed the other three). AURA's
voice is her pack's (`themes/aura/theme.json` `voice`: Kokoro's `af_heart`).
JARVIS's clone reads `/opt/jarvis-host/voice/voices/jarvis.wav` with `jarvis.txt`
beside it (the words in the clip; with it the clone is zero-shot and follows the
clip closely). It was made on 2026-09-09 the way the section above recommends, from
Piper's `en_GB-alan-medium`, ten to twelve seconds, because the owner's instruction
to find and clone a clip per persona stops at ripping audio of real performers. Drop
a clip of your own in under the same name and it is the voice on the next start;
nothing else changes. Its delivery brief is `aura_voice/brief.py:BRIEFS`. The
three clips made beside it for the removed personas are still in that folder; nothing
reads them, and they were left rather than deleted.

A voice with no clip on the host is said in JARVIS's voice, not refused (T-0807): the
adapter's `speak` and `speak-pcm` retry the shim's 404 with `jarvis`, and both name
the voice actually used in `x-jarvis-voice` (and `speak`'s body `voice`). The console
says once per voice that it fell back. The adapter pre-warms the fast path's lines in
the active persona's voice at start and again within 15 s of each switch (T-1320); a
voice the shim refused is not retried until the next switch. The car keeps its offline clips per voice, so a switch renders the new
voice's lines at the next drive start.

Who is primary is the `jarvis-personas` plugin's business: `persona_switch` (the
tool JARVIS calls on "switch to AURA"), `/persona <name>` (what the console
dispatches from the settings menu's picker), `state/persona.json` on the volume
(read by the adapter into `GET /api/status`), and `persona.switch` on every live
HUD socket. The transcript, the wordmark, the accent tokens, the core (its calm states,
`tintFor` in `jarvis-web/src/lib/orb/personas.ts`; JARVIS adds no tint) and the voice
follow it; a finished subagent reports in the voice of the persona its sphere
wears, which is never the primary's.

## The trimmed flow prompt (T-0624)

Upstream makes the first piece wait for `prompt_token_pad` extra speech tokens
(ceil(n/hop)*hop - n, 0-24 at 25 Hz). The engine cuts the end of the reference's flow
prompt (tokens and their mel frames; the LLM prompt stays whole) to a multiple of the
hop, so the pad is 0; the log line `voice X: ... flow prompt N -> M tokens` says what
went. `COSYVOICE_TRIM_PROMPT=0` turns it off. Unproven by ear: after a deploy, run
`turn_latency_probe.py` before and after and the smoke's voice pitch check, and listen
to each voice; set it to 0 if the voice drifts.

## Timings the adapter keeps

`GET /api/status` carries `voice`: `{"transcribe_ms": {"n", "p50", "p95"},
"first_pcm_ms": {…}}` over the last 200 of each, nearest rank, reset when the
adapter restarts (`n: 0` and nulls until the first turn). `transcribe_ms` is a
recording in to its words out, successful answers only. `first_pcm_ms` is a
line's text in to its first PCM piece out of `/api/audio/speak-pcm`, for lines
CosyVoice made then: cache hits and the house's fallback voice are not
replies, so they are not counted. Neither covers the model's time to its first
sentence or the phone's own capture and playback; the §2.7 end-to-end figure is
still the phone's (GAPS G4).

```bash
curl -s -H "Authorization: Bearer $AURA_API_KEY" http://127.0.0.1:8643/api/status | jq .voice
```

`voice` also splits `transcribe_ms` (T-0605) into `receive_ms` (body in), `decode_ms`
(words ready, after the body), `gate_ms` (the speaker gate's own call, which runs
beside the decode) and `gate_wait_ms` (what the gate still added after the words:
0 = the concurrency hid it). Each transcribe answer carries its own as a
`Server-Timing` header. `scripts/hermes-probes/voice_latency_probe.py --base … --wav clip.wav`
sends n private transcribes (read-only) and prints p50/p95 per stage as one JSON line;
results go in `bench/results/voice-latency.md`.

## Failures seen

- **`tts pre-warm stopped: the voice answered 404 at …` in the adapter log.**
  The speech URL refused the fixed lines with something other than the warm-up
  503, so the fast path's cache stayed cold and every fixed line is made live.
  Check `TTS_URL` and that the shim answers `/v1/audio/speech`.
- **`tts pre-warm gave up: the voice at … never answered in 600s`.** The speech
  host stayed unreachable or warming for the whole pre-warm window; the cache is
  cold until the adapter restarts. Check that the CosyVoice container is up.
- **`/api/audio/transcribe` answers 413.** The body passed 10 MB
  (`TRANSCRIBE_MAX_BYTES`): a minute of 16 kHz WAV base64'd is about 2.5 MB, so
  this is a recording far longer than a turn. It is refused before Whisper. The
  cap is bytes, not seconds: several minutes of compressed webm fit under it, and
  only the upstream timeout bounds those.
- **The apps' speech comes back in English after another Speech language was
  chosen on the console.** The adapter asks the house for the chosen language
  (`GET /api/voice/language`) at most every 30 s and sends it to the card's
  Whisper; nothing chosen, or a house it cannot reach, leaves the card on
  English. `the house's voice language: …` at info in the adapter log names a
  failed read. A turn that falls back to Hermes' Whisper uses Hermes' own
  `stt.language`, not the console's choice, and replies are spoken by the
  voice's one English pack whatever language was heard (T-1487).
- **`ws relay ended: the client closed` at info.** One per socket the app
  dropped; a run of them during a reply is a phone losing its link mid-stream.

- **`/health` answers `no-voices`.** The clips directory is empty or not
  mounted. The container stays up on purpose so you can read that rather than
  watch it restart-loop.
- **`/v1/audio/speech` answers 503.** The model directory is missing or torch
  could not load it. The message names the path it looked in.
- **A 404 naming the registered voices.** The `voice` field asked for a preset
  with no clip behind it.
- **The container is unhealthy for the first few minutes.** A cold model load
  is tens of seconds and the first request pays it; the healthcheck allows five
  minutes before it counts that against the service.
