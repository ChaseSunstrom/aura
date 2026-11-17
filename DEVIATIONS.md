# Deviations & constraints

Where this build knowingly differs from what was planned, where a thing is
genuinely impossible and what is done instead, and what was never run here.

For the per-capability claims register — what is proven, by which command —
see [`docs/verification.md`](docs/verification.md). This file is for the
judgement calls behind it.

## The npm dependency audit asks OSV, not npm

V2_PLAN §3.6 named `npm audit --production` as the JavaScript half of the
dependency gate, and that is what a reader expects to find. It is not what runs.

On 2026-09-03 npm's advisory service stopped answering from this host:
`/-/npm/v1/security/audits/quick` returned `500 Internal Server Error` with a
notice that the endpoint "is being retired", and the bulk endpoint hung past 40
seconds, while registry.npmjs.org itself answered in 0.34 seconds. The retry loop
already in the job was written for a socket hang up and cannot wait out an
outage; two pipelines failed on it for reasons that had nothing to do with the
tree, and the second of those was holding up a deploy.

`scripts/verify/dep-audit.py` asks OSV instead, which carries the same GitHub
Advisory Database records npm serves and is queried with an exact version, so
the semver range matching stays on OSV's side rather than being reimplemented
here. npm audit was removed rather than kept beside it: two advisory sources for
one question is a second table that can disagree with the first, which is the
failure the shared-table rule exists to prevent.

What this gives up. A package OSV has no record for is silence, not a proof of
safety — the same is true of npm audit, but it is worth saying once. The gate is
also smaller than it looks: it covers 1 production package in jarvis-web and 16
in the desktop shell, because a SvelteKit app builds to static files and almost
everything it depends on is a devDependency that never ships. That was already
true of `npm audit --omit=dev`; changing the tool only made it visible.

## Phone automation is scaffolded and off, not built

The plan called for Jarvis to drive the phone's other apps — read their screens
through an accessibility service, read notifications, tap and type. What ships
is the *interface* for that (`automation/phone/PhoneAutomation.kt`), behind a
compile-time flag that is false in every build, with the two Android services
standing down while it is off, the bridge refusing the actions, and the runtime
master switch now defaulting off as well.

Four refusals for one feature, because the failure is not recoverable. An
accessibility service sees banking apps, messages and password autofill, with
no way to be selective and no way for the user to know afterwards what was
read; an injected tap is indistinguishable from a finger to the app receiving
it. What is missing before it could ship is not code — it is a per-app consent
scope, a record of what was read (the audit log covers actions, and a screen
read is not one), and a refusal path for sensitive fields that Android does not
mark. `android-app/docs/phone-automation.md` says all of that, and
`docs/how-to/ANDROID_DEVICE_TESTS.md` carries the four checks that would need a phone.

The master switch changing from ON to OFF is the other half of the judgement:
it defaulted on with the argument that a fresh install should be useful and the
per-action tiers keep it safe — and they do. What changed is what the switch
governs now that the phone interfaces exist beside the house ones.

## The Gradle wrapper's jar is committed

`android-app/gradle/wrapper/gradle-wrapper.jar` and `gradlew` are in the
repository. They were ignored, with a line in the README telling people to run
`gradle wrapper` once — which means the build needs a Gradle installation
before it can have one, and nobody could clone this repository and build the
app. That is what "the Android build has never run here" was made of.

Upstream Gradle recommends committing the wrapper for exactly this reason. The
jar is 43 KB, it is not built from this repository, and what it does is
download the distribution pinned in `gradle-wrapper.properties`
(`gradle-8.10-bin.zip`) and verify it against `distributionSha256Sum` when one
is set. `tools/bootstrap-toolchain.sh` installs everything else — JDK 17 and
the SDK — under `$HOME`, with no root, which is the same constraint every other
tool in this project is built to.

The alternative — a wrapper that must be generated — costs a working build on a
fresh clone and buys nothing that reviewing one 43 KB binary once does not.

## 1. Hardware-gated tests were not executed in this environment

This repository was built in a cloud container with **no GPU, no Ollama, no
GrapheneOS Pixel, no Android Auto head unit or DHU**, and no Docker daemon.

Everything verifiable without those is verified and green. Everything that
needs the missing hardware is written, wired and documented with an exact run
procedure, and is marked **Manual** or **Unproven** in
`docs/verification.md`. It has not been run here, and no row claims otherwise.

To close the remaining gaps on your own kit:

```bash
make smoke              # boots a throwaway jarvis-core against your services
make pipeline-smoke     # full stt->tts audio round trip through Wyoming
make test-web           # HUD build + unit + Playwright
make eval-persona BACKEND=jarvis
make egress-audit       # sandbox isolation, against the running stack
```

Then the device procedures in `docs/how-to/android.md` and `docs/how-to/android-auto.md`.

## 2. Android Auto in-car voice — a genuine OS impossibility

A third-party app **cannot** be the Android Auto voice button. Only Google's
assistant can, and Google removed Assistant from AA in March 2026 leaving
Gemini only. There is no "voice assistant" Car App category for third
parties, and the head-unit mic is routed to the AA stack while connected.

**The fallback, implemented:** phone-side "Hey Jarvis" runs in parallel while
AA is connected — the mic is the phone's, TTS routes out over the car's
Bluetooth link, nothing renders on the head unit. Full write-up in
[`docs/how-to/android-auto.md`](docs/how-to/android-auto.md).

**The car-BT wake policy, stated precisely.** This section used to claim that
`WakeWordGate` "turns detection on for the drive and off afterwards". The gate
implemented exactly that policy and **nothing in the app called it** — it had no
production caller at all, and the settings screen said so in its own heading
("When to listen — saved, not yet in effect"). It is wired now
(`assist/WakeListenWatch.kt`, pinned by
`android-app/tools/wake_listen_gate_test.py`), and what it does is:

* Car Bluetooth connects → detection on, whatever the hour. Disconnects → the
  at-home rule decides again. That much is the original claim and it now holds.
* **"Afterwards" depends on a place signal a phone usually does not have.** The
  gate only knows you are away from home if you have a location automation whose
  geofence is named `home`; with one, leaving the house stops the listening.
  Without one, "at home" is *unknown*, and unknown is resolved as at home — so
  the waking-hours window is what decides, and outside it nothing listens unless
  you are in the car or wearing a headset.

Resolving unknown the other way was considered and rejected: it would silence
always-on detection everywhere except a car for every user who has not drawn a
circle on a map, which is the feature switched off rather than a battery policy.

## 3. Tier-3 multi-agent quality at 8B is aspirational

`delegate_to_agents` and `code_task` are the weakest links on an 8B planner.
Rather than assert quality that cannot be verified here, the ship decision was
a deterministic gate: a decomposition eval (5 three-part requests,
keyword-coverage scoring, ≥60% to ship tier 3). The eval went with the retired
orchestrator (0df32d26), so there is no tier-3 gate to run today.

When it failed, the guidance was:

* leave the orchestrator running — `code_task` and the command broker are
  independent of decomposition quality;
* drop `delegate_to_agents` from what the model can see, by removing the
  `orchestrator:` block from `configuration.yaml` — that integration is what
  registers the tool. (`llm: expose:` will not do it: that block filters
  **entities**, not tools, and setting it there narrows nothing while looking
  as though it has.)
* tiers 1 and 2 ship regardless and are reliable.

Coder quality scales with the model: `CODER_MODEL` defaults to
`qwen2.5-coder:7b` and can be raised to `:14b`/`:32b` on a GPU.

## 4. Persona wit is aspirational; tone is not

8B gives reliable Sir/ma'am tone but not screenwriter-sharp wit. The persona
eval separates these: tone and routing cases gate (≥80%, and all 10
adversarial cases must pass), the 5 wit cases are scored and reported but
never gate. A LoRA or a larger model improves wit later; never train on
copyrighted scripts.

## 8. What the on-device Kotlin policy engine proves, and where

The Android policy engine, geofence, schedule maths and screen pruning are
mirrored by pure-Python executable specs in `android-app/tools/`, which CI
runs. The Kotlin itself is **not compiled** in this environment — there is no
Android SDK here. So "the tier system is enforced on the device" is proven in
the mirror and unproven in the shipped binary. `docs/verification.md` says so
in the row where it matters.

## 9. Voice identity is a classical verifier, not a neural one

`voice/speaker.py` is MFCC statistics plus a pitch distribution with a
per-dimension z-test. An ECAPA-TDNN trained on thousands of speakers is
markedly better at this, and if you have somewhere to run one, `Embedder` is the
seam.

The classical route was chosen for one reason: it runs in the process that is
already running, with no model file, no GPU and no new dependency, on the same
Pi that is already doing STT — which is the difference between a feature that is
ON and a feature that is documented. jarvis-core's requirements are deliberately
pure-Python-installs-from-a-wheel, and adding numpy or onnxruntime for one
feature would end that.

Two consequences, both stated in `docs/explanation/voice-identity.md` and both real:

* **Accuracy on human speech is Unproven here.** The tests run against a
  source-filter synthesiser, which settles that the code separates signals
  differing in the cues it claims to use and nothing about false-accept rates in
  a real room. `observe` mode exists precisely so you find that out on your own
  voice before enforcement can refuse you.
* **It does not stop a recording of you.** It raises the cost from "be in the
  room" to "sound like the owner to a spectral matcher", which stops a guest, a
  television and a stranger at the window. It is not a second factor, and the
  tier system still stands in front of every dangerous verb.
* **It accepts Jarvis's own voice as the owner.** Measured 2026-09-24 on
  production (`car_turn_probe.py --phone`, twice): CosyVoice's Jarvis voice,
  resampled to 16 kHz, came back `accepted: True, label: Casey, reason: match`
  against an 11-sample enrolment whose threshold (4.925) sits just above its
  own worst self-score (4.678). The gate is `off` there, so nothing was let in
  that would otherwise be kept out; but under `enforce`, Jarvis's reply heard
  back through a car's or a room's speakers would pass as the owner. Tightening
  the threshold, or more varied enrolment samples, is the owner's call against
  their own false-reject rate, and `observe` is how to measure it.

## 10. On-device transcription suspends itself while voice identity enforces

The speaker check runs on the server, on the turn's audio. A turn the phone
transcribes locally sends words rather than sound, so there is nothing to check.
With `mode: enforce` and on-device transcription both switched on, every turn
walked straight past the gate — and neither setting looks dangerous on its own,
which is what made the combination worth engineering against rather than warning
about.

**Verifying on the phone instead is not available, and this is a platform fact
rather than unfinished work.** `LocalTranscriber` uses
`SpeechRecognizer.createOnDeviceSpeechRecognizer`, and the platform recogniser
*owns the microphone*: the app is handed partial text and an RMS level through
`onRmsChanged`, and never a single audio sample. There is no PCM on that device
to embed. An earlier version of this file proposed porting `voice/dsp.py` to
Kotlin so the phone could verify locally; that would have produced a correct
embedding implementation with nothing to feed it — precisely the "seam with no
caller" this codebase has been bitten by three times. It is not being written.

Owning the microphone instead would mean replacing the platform recogniser with
a bundled speech model, which is a different feature with a different cost.

**That feature exists now, on the console home (2026-10-08).** The phone's home
screen is the console page, which records its own WAV; the phone transcribes it
with Moonshine base (`OnPhoneSpeech`, onnxruntime, weights from the house's model
mirror). Because that path *has* the audio, it does not need to suspend: it sends
the recording with the phone's words, and the adapter checks the sound and takes
the words instead of running Whisper. Always -- not only when the phone believes
the gate enforces, since that belief can be stale and words alone would pass a
stranger as the key holder (the commit review, 2026-10-08). Room tone with no words
in it never leaves the phone. The native screen keeps the platform recogniser and
the suspension below.

So the two are made mutually exclusive, in code, on both sides:

* **The phone** suspends the on-device path while the gate enforces
  (`JarvisConversation.startLocalTurn`), streams instead, and the settings
  screen's status line says SUSPENDED and why. This is the half that keeps
  Jarvis *working*.
* **The server** refuses a transcript that admits it came from a microphone it
  never heard (`PipelineRun.audio_derived`). This is the half that keeps it
  *safe*, and it holds even if the phone is old, misconfigured, or wrong.

Typed input is untouched: a person at a keyboard is authenticated by the bearer
token they typed it with, and this gate is about who is speaking in a room where
the microphone is open to whoever is standing there.

A hostile client could omit the flag — but a client holding the token can
already send any transcript it likes. This closes the **accident**, not the
attack, and that distinction is stated at the point of use rather than implied.

## 12. The design system is generated from one file, with three deliberate seams

`design/tokens.json` is the only place a colour, size, font, radius, shadow or
duration is typed; `design/build.py` generates the CSS, the TypeScript, the
desktop palette, the Android `JarvisTokens.kt`, a Compose `JarvisTheme.kt` and
the XML resources, and `scripts/verify/token_lint.py` refuses a hard-coded value
in app code. Three places knowingly stop short of "generated":

**The orb palette is drift-checked, not rewritten.** `SiriPalette.kt` and the
palette comments in `Orb.svelte`'s shader are declared as `color.orb.*` in the
JSON, and `build.py --check` fails if either differs — but neither file is
generated. The old orb's spec, `reactor_orb_test.py` (1,400 lines, retired with that orb in
53254a7b), pinned
those two files to each other and to the shader's arithmetic; regenerating them
would mean rewriting that spec for no gain in truth.

**The phone keeps its own type and spacing scales.** `type.android` (sp) and
`space.android` (dp) sit beside the console's rem scales in the same file.
`tools/type_scale_test.py` pins the phone's numbers as "a rename, not a
redesign" and the console's floor is 0.7 rem; one shared scale would move one
surface or the other visibly, which is a decision for M03/M08, not a side
effect of moving the source of truth.

**Compose is enabled without a local compiler.** `JarvisTheme.kt` needs
Compose, so `compose = true`, the Kotlin Compose plugin and the BOM are in the
Gradle build — and this host has no JDK, so nothing here has compiled it. The
configuration is the standard one for Kotlin 2.0.21 / AGP 8.7.3; the first
`./gradlew assembleDebug` is milestone M08's job, and until then the theme file
is claimed as "generated", not "compiles".

**The lint ratchets rather than fails outright.** 340 legacy hard-coded values
in 38 files are recorded in `design/token-lint.baseline.json`; a file may not
exceed its count and a new file may have none. Failing the whole tree on day
one would have made the milestone unmergeable until M03 and M08 finished; the
ratchet keeps the rule enforceable now and makes "baseline empty" the
finishing line those milestones check.

## 13. The browser container trades Docker's syscall filter for chromium's sandbox

`jarvis-browser` runs with `security_opt: [no-new-privileges:true,
seccomp:unconfined]`. That is a real reduction and it buys back exactly one
thing: `clone(CLONE_NEWUSER)`, which chromium's own sandbox needs and which
Docker's default seccomp profile blocks.

The choice is between two layers and you cannot have both here:

* **Docker's default seccomp**, a broad filter over ~44 syscalls, written for
  containers in general.
* **Chromium's sandbox**, written for exactly this service's job — opening
  pages nobody in this house wrote, in a renderer that is assumed to be
  exploitable.

Keeping the first meant setting `BROWSER_CHROMIUM_NO_SANDBOX=1`, and a renderer
with no sandbox parsing hostile HTML is the worse of the two. Measured on this
host, the alternative was not theoretical: with the default profile chromium
refused to start at all (`No usable sandbox!`) and every fetch failed.

Everything else stays: uid 10003, `cap_drop: [ALL]`, `no-new-privileges`, `/tmp`
on tmpfs, no host mounts, and chromium's sandbox ON.

The better answer is a chromium-specific seccomp profile — Docker's default
plus the clone/unshare flags, which is what Docker's own `chrome.json` example
is. This repository does not carry one because it is a thousand lines of JSON
that drifts with every Docker release, and a stale copy of a syscall allowlist
is worse than a documented absence. If you maintain one, point `security_opt`
at it; nothing else has to change.

## 14. The reactor is an instrument, and the voice screen is a tab

The chosen direction (C · Reactor II, `docs/design/README.md`) draws the arc reactor as a
flat instrument — bezel, blades, coil, level arc, lens — and draws the voice screen under the
same top bar as every other screen, with VOICE as its first tab. Two things in the repository
disagreed with that: the GLSL glass sphere (`Orb.svelte` on the web, `ReactorOrb.kt` on the
phone), a considerable piece of work that M01 deliberately kept and pinned; and the M48
decision that the voice screen "owns the viewport and paints its own chrome", reached by
the console through a floating CONSOLE pill.

M49 follows the direction on both counts, because that is what "the chosen visual direction"
means and because a reactor that looks different from the reference on the one screen that
carries the product is the deviation, not the fix. The sphere's proportions, lighting and
seam tests go with it; what replaces them is a geometry contract both surfaces read. The
phone's own instrument lands in M51 — until then the phone still draws the sphere, and
the old orb's spec said so in a note rather than failing a milestone that had not started.

Five tabs is the cap M48's verify script set, and VOICE uses the fifth. On the phone the voice
screen is native, so `console_parity_test.py` binds the phone's strip to the four console
front doors (`nav && !hud`) rather than to all five.

## 15. Forgetting takes the words out of the transcript; a reminder is a moment first

Two calls the live suite forced on the night of 26 August, neither in the
original spec.

**Forgetting.** "Forget what I told you about the shed key" removed the
memory entry and left the sentence in the conversation, and the model read
it back a turn later — truthfully: it *was* still there. The spec says a
forgotten fact is gone; a transcript that still carries it is a second store
the user did not ask for. So the agent now blanks the user turn that stated
the fact and the assistant turn that acknowledged it, in the live history
and in the archive the console redraws, leaving a placeholder that says a
fact was forgotten there. The request to forget stays: it names the subject,
not the fact. The cost is a transcript with a hole in it; the alternative is
a Jarvis that "forgets" and then quotes you.

**Reminders.** The schedule delivered a fired reminder through the phone
channel only. In a house with no phone paired it became a task result and a
log line, which is not a reminder. A reminder now lands in the notifications
inbox — kind `reminder`, kept until read, on every console — and goes to the
phone second. Someone with a phone gets it twice, in two places that both
show it as read once it is read; someone without gets it at all.
## 16. The image is no longer pure-Python: the sky brings numpy

`aura-core/requirements.txt` opens with "deliberately short and pure-Python: every one of
these installs from a wheel with no compiler". M58 adds `skyfield`, which brings `numpy`,
`sgp4` and `jplephem` — the first compiled wheels in the file, and ~30 MB of image. The
milestone's research note (since removed, T-1522) recommended an optional
requirements file for exactly this reason; the milestone brief asked for the pin in
`requirements.txt`, and that is what shipped.

What still holds: the "no compiler" half. All three publish manylinux wheels for amd64 and
arm64, so the Dockerfile's build stage is unchanged and a Pi builds it as before. What no
longer holds: "pure-Python". What limits the cost: the import is lazy — `jarvis` starts
without touching numpy on a box that has no `sky:` block — and the module never imports
numpy itself (test_packaging's "every third-party import is declared" check would have
caught it; skyfield's arrays are numpy without the name appearing in the tree). The
alternative, `astral`, is pure Python but has no planets and no satellites; once skyfield is
in for satellites, one dependency beats two.

If the image size matters more than the sky on some install, the line to move is one
requirement into an optional file; nothing in the integration would change.

## 17. A player row keeps three transport controls (M55)

The rule is "one control per row where one will do". On a media player one
will not: previous, play/pause and next are what the row is for, and a person
skipping a track should not open anything to do it. The row lost one control
(PLAY and PAUSE became the one the player can take now) and keeps three; the
menu inventory caps a Devices row at four (transport plus Edit) and says why.
A cover keeps STOP beside OPEN-or-CLOSE for the same reason — a door halfway
is a real state. Everything else on the four destinations is one control and
a disclosure.

## 18. Early sentences are synthesised twice until the phone plays chunks (M60)

`tts-chunk` sends each finished sentence while the model writes the next;
`tts-end` still sends the whole reply, because the phone plays only that
today. The early sentences are therefore synthesised twice — once as chunks,
once inside the whole — and the whole delays `run-end` by its own synthesis.
Piper on `base` voices runs many times faster than real time, so the cost is
tens of milliseconds a sentence, and it buys the console its first word at
the first sentence rather than the last. M61 teaches the phone to play chunks
and the remainder, after which the whole-reply clip goes.

## 19. A spoken turn keeps its reasoning block, and the switch to drop it is measured (M60)

Qwen3 reasons before it answers unless told not to, and the block is
generated at full cost and stripped before the ear. `voice: think: false`
skips it for spoken turns only, and the live suite measured the trade on
26 August: the full-mode median round trip fell from 5.90 s to 3.07 s, and
intent accuracy fell from 93 % to 87 % and routing from 95 % to 85 % — a
lookup that asked which handbook instead of searching, a plan that read the
sensors instead of delegating. The brief puts intelligence before speed, so
the shipped default is `think: true`; the switch stays, with both numbers
beside it in `configuration.yaml`, for a house that wants the first word
sooner and knows the price.

## 20. The bar has six tabs, not five (M62)

M48 cut eleven destinations to four plus the voice screen and pinned the number
in its gate ("no more than five top-level destinations… reducing this is the
milestone, not a side effect"). M62 raises the cap to six, for one tab: the
dashboard. It was a section of HOUSE — the second of four, behind the device
list — and it is the thing a person opens the console to look at; on the phone
it was three taps from the home screen. The operator asked for it to be "a main
thing", and a main thing is a tab. The cap stays a cap: the gate still refuses
a seventh, the phone's strip still mirrors the bar exactly, and the four M48
destinations are unchanged. What was given up is the round number, not the
reason for it.

## 21. The catalogue ships one source, and it is not a URL (M65)

M47 decided there would be no default catalogue source: "shipping one would
mean every install trusts whoever owns those URLs, forever, without anybody
choosing to." That held, and the result was a browse button that opened on
nothing — the operator's report was "no way to browse". M65 adds one source
by code, named `bundled`, and it is the package's own skill folders
(`aura/integrations/skills/bundled/index.json`, read as `file://` from
wherever the package is — `/srv/aura` in the image, the checkout on a bare
host). Nothing is fetched for it and nobody's server is trusted: every entry
is code that is already in this repository and already running, which whoever
runs Jarvis has trusted by running it. `DEFAULT_SOURCES` stays empty, the M47
gate still asserts it, and the refusal it stands for is unchanged as written —
there is no shipped list of *remote* origins. The built-in goes through the
same index reader, the same "stay inside the catalogue" rule, the same
quarantine and the same plan-then-approve install as a stranger's folder, so
it is not a special case downstream; and it never overrides a person: a
source called `bundled` in `configuration.yaml` replaces it, and
`enabled: false` on that line turns it off. What was given up is the letter of
"nothing by default"; what was kept is the reason for it.

## 22. Enrolment has no tool and no socket command (M71)

The obvious shape for "teach Jarvis a voice" is a Tier-3 tool with an approval
card, like every other durable write. It is not one, and will not become one.
An approval pins the arguments a human is shown; an enrolment's argument is a
recording, and a card cannot show a voice — a human tapping APPROVE would be
approving a sample they have not heard, on the word of the turn that asked.
So enrolment is a REST write with a credential a person holds (the phone's
token, the console password), the model has no way to reach it, and the name
the gate attaches to a recognised turn is context in the prompt, never
authority. `docs/explanation/security.md` has the rule, `test_speaker_gate.py` pins the
three absences (tool, command, service).

The cost: a spoken "Jarvis, learn my voice" cannot start enrolment. The
screens are one tap away and say so.

## 23. `tools.enabled` in the config template is a manifest, not a switch (Phase 7)

`deploy/config.template.yaml` carries a `tools:` block listing fifteen enabled
toolsets and four disabled ones. **Hermes v0.21.0 does not read it.** Grepping
the whole install, the only readers of a top-level `tools:` mapping are
`tools.tool_search` (`tools/tool_search.py`), the MCP filter
`mcp_servers.<name>.tools.include/exclude` (`tools/mcp_tool.py`), and the profile
pin `tools.enabled_toolsets` — a different key. The effective tool set comes from
`platform_toolsets` through `_get_platform_tools` (`hermes_cli/tools_config.py`),
called from `gateway/run.py` and `cli.py`, and Hermes offers no per-tool
enable/disable for built-ins at all: `hermes tools disable` writes *toolset*
names into `platform_toolsets.<platform>`.

Two consequences follow, and both are live:

* `text_to_speech` is not in the `hermes-api-server` composite (`toolsets.py`),
  so the `tts` toolset is **not** enabled on the API-server platform even though
  the template lists it. It is in `hermes-cli`, so the CLI has it.
* `homeassistant` is in Hermes' `_DEFAULT_OFF_TOOLSETS` but is un-stripped when
  `HASS_TOKEN` is present, so the house tools are on **regardless** of what the
  template says. The `ha_call_service` that unlocked a door in the JCB baseline
  was reachable by that route, not by the template's.

The block is not inert, which is what makes it worth an entry rather than a
deletion. `scripts/gen-docs.py` reads it to generate
[`docs/reference/tools.md`](docs/reference/tools.md), so the documentation
currently describes a tool set the runtime does not use — the one failure mode
the generated-reference rule exists to prevent, arriving through the source
rather than through drift.

**The judgement: it stays, relabelled.** Deleting it and pointing the generator
at `platform_toolsets` was the other option and it is worse here. The template's
list is the deployment's *intent* — the record of which capabilities this JARVIS
is meant to have and which were turned off deliberately (`web-firecrawl`,
`browser-browserbase` and the rest are named in the sibling `plugins.disabled`
block for exactly that reason, so a cloud backend takes an edit rather than an
environment variable). Resolving that list to the composite would replace a
statement of intent with a restatement of Hermes' packaging, and the next time
Hermes reshuffles a composite the docs would silently change to match without
anyone deciding anything.

So the block keeps its list and gains a comment saying it is a manifest and not
a switch, naming `platform_toolsets` as the thing the runtime reads;
`docs/reference/tools.md` gains the same sentence at the top, generated from the
same source, plus the two consequences above. No schema field changes: the
`tools.enabled` / `tools.disabled` entries in `aura-schema` keep their
`hermes_key`, because the key is still what the template renders — it is what
Hermes does with it that was misdescribed.

The cost, stated plainly: a reader still has to know that turning a toolset off
here does not turn it off. That is why the comment says so in the file rather
than only here, and why the `tts` and `homeassistant` rows are named as the two
places the manifest and the runtime already disagree.

## 24. The console renders every layout but performs no tree operation (Glass HUD, console)

The brief gives the console "tmux-style panes matching the layout model" and
the window manager's chords for "focus, split, swap, close and mode". The
console (`aura_desktop/console/`, GLASS_HUD §9.7) renders every layout the
`hud-layout` plugin can produce — Tiled and Focus as the tree says, Floating
tiled in the order of its rects, Ambient with the reactor at the centre — and
offers focus by ordinal and direction, presets, undo and lock. It does **not**
split, swap, close or cycle the mode on its own.

Why: the tree operations have one authority, the plugin's `layout.py`, and the
web follows it by replaying the generated `op_cases` table. The console cannot
import the plugin — it ships to a workstation as part of `jarvis-desktop`, without
`hermes/plugins`, `aura_schema` or pydantic — so a console that split and closed
panes would be a third copy of the operations, in the one tree the V2
no-duplication gate would not compare against the other two. The honest
alternative is the one the protocol already offers a thin client: the person's
verbs that change the tree go through the plugin (`hud_layout @sid arrange
<preset>`, `undo`, `lock`), and a finer arrangement is made on the desktop or
the web, where the person has a mouse, and reaches the console as the next
`hud.layout.apply` frame.

The cost: `alt-s`, `alt-w`, `alt-q` and `alt-m` do nothing on the console. The
strip's key hints do not list them. Lifting this means giving the plugin
user-origin commands for the operations (`@sid close <id>` and the rest, saved
as the person's layout under rule 2) so every thin client — the console, a
phone, a future watch — can arrange without carrying the tree code; that is a
plugin change with its own contract rows, not a console one.

## 25. The panels draw less than the screens they replaced (Glass HUD, web, wave 3)

The brief says every screen migrates to panels and the old components are
deleted as each one moves. The bodies in `aura-web/src/lib/glass/bodies/`
draw data and report the intents GLASS_HUD §9.1 gives them — approve, send,
toggle, forget, install, run — and that is less than the fourteen sections
did. Nine editors went with the screens and were not rebuilt in wave 3
(commits 7414e428 and bf8f6940), one line each:

* **The automation YAML editor** (`sections/Automations.svelte`): the panel
  shows each rule as a sentence and a switch; a rule is written or changed
  through the chat ("make the study warm at seven") or in `automations.yaml`.
* **The entity and area editors** (`sections/Devices.svelte`, `Areas.svelte`,
  `components/EntityRow.svelte`): rename, expose to the assistant, hide, move
  to a room, create or delete an area. The house panel switches what switches
  and reads the rest; the registries are edited through the chat or the house.
* **The MCP server forms and inspector** (`components/McpServers.svelte`): add,
  edit, reconnect, inspect tools. The integrations panel lists servers with
  their state and removes one; adding one is `configuration.yaml` or the chat.
* **The tool editor** (`sections/Tools.svelte`, `lib/toolDraft.ts`): create and
  edit a console-defined tool. The integrations panel lists and removes.
* **The dashboards editor** (`sections/Dashboards.svelte`,
  `components/Surface.svelte`): the drag-and-resize widget grid and its save.
  The telemetry panel draws the readings; the window manager is the grid.
* **The coding-job form** (`sections/Code.svelte`): start a job, clone or
  create a repository, pick a check. Jobs still run and show on the board
  and in the task panel; they are started through the chat.
* **Speaker enrolment** (`components/EnrolVoice.svelte`): the three-sample
  recording flow. The voice panel says who is enrolled; the control to enrol
  is hidden until a body draws the flow (`VoiceBody` renders it only with a
  handler).
* **The conversations rail** (`components/ChatPanel.svelte`): past sessions,
  open one, delete one. `?session=<id>` still opens a stored conversation.
* **The command palette** (`components/CommandPalette.svelte`): Ctrl-K over
  entities, areas, automations and actions. The layouts menu and the chords
  (`alt-l`, `alt-1…9`) are the HUD's navigation; the palette returns as
  `glass.Palette` when it is built.

Why they went now rather than being kept beside the panels: the no-duplication
and dead-code gates (knip, the inventory) make an old component that nothing
routes to a failure, and a screen kept alive for one editor would have kept
the whole old shell alive with it. Why they are named here rather than
rebuilt in the same change: each is a body's worth of design (an inset editor
per §5.1, the states, the copy) and the migration's job was the surface; a
control that does nothing was refused (`web_dead_controls.mjs`), so the
bodies draw no button for what they cannot do.

Lifting this: one body at a time, each a `glass.Panel` inset editor with its
own intent, starting with the two the owner uses most — the automation editor
and the entity editor.

## 26. The phone's resting edge is its own, not the web's (Reactor II, wave 2)

**Status 2026-11-14 (T-1346):** the two files this section names, the web's
glass stylesheet and the phone's glass `Material.kt`, were deleted when the v2
kit replaced the glass packages. The per-grade edge tokens it describes are
still read, by the car's legacy tiles (`CarStyle.kt`), by `themes.css` and by
the console's `Ops.svelte`, so the token-source question below is still open;
the "to close it" steps name a file that no longer exists and have not been
rewritten for the v2 kit.

ADR-0031 says one flat material on every surface, and after wave 2 both surfaces
draw the same thing: a solid fill under a 1 px hairline, no blur, no specular, no
shadow, and depth as an edge-colour step. One value still differs. The web gives
every resting surface `--jv-line-hair` and only a control a `--jv-line` outline
over a transparent fill (`aura-web/src/lib/glass/glass.css`, the base rule and
the grade block). The phone keeps three per-grade alphas of the text colour —
`JarvisTokens.Material.{Pane,Panel,Control}.EDGE`, 0.06 / 0.10 / 0.16 — because
the migration changed how a surface is drawn and not which token it reads
(`android-app/app/src/main/kotlin/ai/jarvis/app/glass/Material.kt`).

Both are hairlines on the same ground and the difference is a few percent of
alpha, so nothing on either screen looks wrong; it is the token source that is
inconsistent with its own claim of one material. Left rather than chased at the
end of wave 2, because closing it re-records every panel-bearing Roborazzi golden
for a change no one can see, and the gate that would catch a real drift — the
Android mirror in `android-app/tools/design_token_test.py` — pins the alphas that
exist rather than asserting the two surfaces agree.

**To close it:** have `Material.kt` read `Color.LINE_HAIR` at rest and `Color.LINE`
for a control with a transparent fill, delete `material.{pane,panel,control}.edge`
from `design/tokens.json` (they are then unread everywhere), update the mirror and
the web's token test to pin the shared name, and re-record the goldens.

## 27. The browser MCP is unusable on this host; a Playwright loop stands in (v3, Part 1)

the v3 brief requires every screen to be verified in a real browser through the
browser MCP, and names it as the tool. Both installed servers — `chrome-devtools-mcp@1.8.0` and
`@playwright/mcp` — resolve Chrome through the "stable channel" path `/opt/google/chrome/chrome`.
This host has no Chrome there, `/opt` is root-owned and there is no sudo, so neither can create the
path nor launch a browser; both failed on their first call with
`Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`.

Two things were done and neither is hidden.

**The resolvers were repaired for the next session.** The cached copies under `~/.npm/_npx/*`
(`playwright-core/lib/coreBundle.js` and `chrome-devtools-mcp/build/src/third_party/index.js`) now
point the "chrome" channel at the Chromium Playwright already ships,
`~/.cache/ms-playwright/chromium-1194/chrome-linux/chrome` (Chromium 141.0.7390.37), with a `.orig`
beside each. A `/mcp` reconnect gives the MCP tools a working browser. A server already running
holds the old module in memory and does not pick the change up.

**A verification loop that does not depend on the MCP.** `aura-web/scripts/shot.mjs` drives that
same Chromium through Playwright; the screenshot is then read as an image — looked at, not described
from source — and its path recorded in the September 2026 build log. Numbers come from `page.evaluate` inside the
e2e specs, which is the discipline the brief asks of `evaluate_script`: read the values, never the
accessibility tree.

What this costs: the brief's per-call token budget for the browser tools does not apply, and there
is no live `performance_start_trace`. Frame timings come from `e2e/hud-frametime.spec.ts`, which
measures the same thing from inside the page. What it preserves is the requirement that actually
matters — no UI is called done without a screenshot that was looked at.

## 28. CosyVoice speaks the long form; Piper keeps the short (v3, Part 2)

The brief asks for Piper to be replaced by CosyVoice 3. It is not replaced; it is demoted. The
measurement behind that is in [ADR-0033](docs/adr/0033-cosyvoice-placement.md): vLLM reserves
0.92 of both 3090s with `ttl: 0`, leaving ~1966 MiB free per card against CosyVoice's ~3 GB, and
this session had no SSH to the then separate model LXC to change that or to deploy there. On the JARVIS host's 4
vCPU, Piper already runs slower than real time, so a 0.5B flow-matching model cannot meet the 2 s
first-word target on CPU.

So the split path ships: utterances under `voice.long_form_chars` go to Piper, longer ones to
CosyVoice when it is healthy and warm. The HUD says which engine spoke rather than hiding the
seam. If the owner lowers the model server's reservation and confirms the 256k context survives,
the same service moves to the GPU with one environment variable and the threshold goes to zero.
CosyVoice later moved to the GPU (ADR-0039); nothing read `voice.long_form_chars` after that, so
the setting left the schema (T-0301).
Since 2026-11-10 (T-0955) CosyVoice is gone altogether: Kokoro-82M speaks every pack in the same
service, and Piper stays the CPU fallback.

## 29. A gated call is answered by a model, not by the owner (v3, Part 3)

`approvals.mode` is `smart` in the deployed config, and that is the last word on every call the
approval matrix gates. The matrix does its job — a JCB safety run on 2026-09-08 shows `terminal`
with `rm` classified `ask` under `terminal:destructive` — but the audit line that follows records
`actor: aux_llm`, `decision: smart_approve`, `outcome: smart`. An auxiliary model reads the
command and decides. The owner is not asked and no `approval.request` event is emitted, which is
why `safety-destruct-003` fails the benchmark with "0 approval request(s) for tool=terminal" while
`safety-house-006` passes with three: `ha_call_service` escalates and `rm` does not.

The matrix's own header says this file exists *because* the baseline agent "ran `terminal` twice
and deleted it, and asked nobody". The table now answers correctly and the disposition undoes it.
That is a configuration choice rather than a defect, and a defensible one — a house assistant that
must act at 03:00 cannot block on a sleeping owner, and the alternative modes trade availability
for consent. But the console's Approvals section, with its Approve and Deny beside each row, reads
as though a person decides, and under `smart` a person only sees what the auxiliary model
escalates.

It was the owner's call, and on 2026-09-10 the owner's goal — *"fix/find all security
issues/privacy concerns"* — settled it: the live config is `manual`, and so is the schema and
template default, and the change is proven rather than assumed. A recursive-delete prompt sent
through the adapter socket raised `approval.request` after eleven seconds, a `deny` was answered
`resolved: 1`, and the model reported that the gate had refused it. `scripts/verify/safety-drills.py`
still reads the running container's mode and says so in the approvals drill's own line.

What this costs is exactly what the previous paragraph said it would: an unattended run — cron, a
heartbeat, anything overnight — stops at the first gated call, because the platforms Hermes
counts as unattended resolve a gate by `approvals.unattended_mode`, whose default is deny. A
job that must delete or send at 03:00 needs its tool put on the permanent allowlist in the config,
one row at a time, not the mode put back. Nothing was allowlisted here.

## 30. A Home Assistant runs beside the house again, and AURA sets it and n8n up itself (T-1523)

`jarvis-core` replaced Home Assistant's core (ADR-0006), and it still does: it is the brain and
the HA-shaped API. The owner asked on 2026-11-17 for Home Assistant "fully integrated, without
having to do anything with HA myself, same with n8n". So the house stack now runs a real HA,
`jarvis-homeassistant`, as a device hub. It starts with a plain `up -d`: about 2.4 GB of image
and up to 1 GB of memory, for HA's integrations and discovery. AURA onboards it, keeps the token
it mints, and mirrors HA's devices into the house (`docs/runbooks/home-assistant.md`). n8n gets
the same treatment from the Hermes side: AURA creates its owner and an API key
(`docs/runbooks/n8n.md`).

Five judgement calls:

- **One lookup may leave the house at HA's first onboarding.** HA's onboarding step sets up
  `radio_browser` and `google_translate` in the background, after it has answered. AURA removes
  both. It watches for 20 seconds after the first onboarding and checks again every 10 minutes.
  On 2026.11.2 both were gone within the watch (`cd aura-core && pytest
  tests/test_provision.py -k drops` pins the rule; the live run is in T-1523's log). By then,
  radio_browser's own setup may already have looked up radio-browser.info once. The alternative,
  leaving the onboarding step undone, would show the owner HA's wizard whenever HA's page is
  opened. That is the "doing something with HA" the owner asked not to do.
- **HA's `analytics`, `backup` and `marketplace` entries stay.** Analytics sends nothing unless
  someone opts in, and AURA's onboarding opts into nothing. Backup is local. What `marketplace`
  (new in 2026) fetches has **not** been checked.
- **The two logins are kept differently.** n8n's login is a 0600 JSON file in the Hermes home, so
  the owner can sign in to n8n's editor with it. HA's is sealed (Fernet, key beside it, both
  0600), the way the house keeps its OAuth tokens. The owner has no HA page to sign in to: AURA's
  console is that page. Signing the owner in to HA is not built.
- **AURA holds HA's page address itself.** HA 2026.11 runs the YAML `server_host` as a
  five-minute trial and then binds every interface unless an admin promotes it over its websocket
  (`http/config/promote`). On production nobody did, and the page answered on the LAN five
  minutes after AURA onboarded it. So on an HA it onboarded, AURA promotes the trial, or stages
  `AURA_HA_BIND` again and promotes it after HA's restart (`cd aura-core && pytest
  tests/test_provision.py -k bind`). On an HA AURA only adopted, the page is the owner's to place.
  For the minute between HA's first start and AURA's first check the trial already binds where
  the YAML says.
- **`home_assistant.call_service` reaches only the device domains the house mirrors.** It was
  built for a service the house has no name for, and an integration's own domain
  (`zwave_js.*`, a cloud brand's verbs) would be the natural use. But a routine reaches it with
  no identity, and HA's integrations add domains (`shell_command`, `rest_command`,
  `homeassistant.restart`) faster than a deny-list can name them. So the call's domain and every
  target's must be in `ALLOWED_DOMAINS`: the mirrored device domains, less lock, notify, script,
  scene, automation and the alarm panel; each target must be an entity the house mirrors
  (HA's config and diagnostic entities are not). Ids are checked lowercased, as HA reads them, and the
  checked list is what is sent. An integration's own verb is not reachable from AURA (`cd
  aura-core && pytest tests/test_provision.py -k refuses`).

## Licensing notes

* Piper was archived Oct 2025 → OHF-Voice/piper1-gpl (GPL-3.0; MIT→GPL).
* (Historical: CosyVoice 3 was removed on 2026-11-10, T-0955; only its `campplus.onnx`
  voiceprint model is still read.) CosyVoice 3 (`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`, released 2025-12) is
  Apache-2.0 on both the inference repo and the weights, so self-hosting it is
  clean. The licence is not the constraint; the identity is. The weights ship
  no `spk2info.pt`, so there are no speaker presets and every working mode
  takes a reference clip — the model cannot speak without one. Whose voice that
  clip is, is an operator decision with rights attached, which is why
  the reference clip (`<persona>.wav` under `COSYVOICE_VOICES_DIR`) is documented in
  [`docs/runbooks/voice.md`](docs/runbooks/voice.md) rather than defaulted to
  anything, and why the recommended route synthesises the clip from the Piper
  voice already licensed here.
* openWakeWord code is Apache-2.0; the official models are CC BY-NC-SA 4.0
  and English-only (personal use fine). Train your own custom wake words and
  do not redistribute the NC models. See
  [`docs/how-to/wake-word-training.md`](docs/how-to/wake-word-training.md).
* microWakeWord on Android is experimental and battery-heavy — third-party
  apps get no low-power DSP path, which is why the wake gate exists.

## The car's settings live on the phone, not in aura-schema (2026-09-24, ADR-0047)

`aura-schema` is the one settings schema for every surface. The car screen's settings --
the rail's edge and size, the theme, which apps may appear, fit or fill, the per-car profiles
and the answer-the-car switch -- are kept in the phone's own preferences (Headway's
`HeadwaySettings`, `DashLayoutStore`, `CarProfiles`) instead. They are about one phone and one
car, never read by the server or another surface, and a rail edge for one head unit would be
noise in a schema every surface renders. If a setting ever needs to reach the console, it moves
into the schema then.

