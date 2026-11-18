# ADR-0047: Jarvis is the phone side of Android Auto, and Headway is folded into it

- **Status:** Accepted (2026-09-24, on the owner's request)
- **Comes from:** the owner, 2026-09-24: *"fully migrate headway into jarvis (make jarvis work the same way, but fix all issues in headway as it isn't easy to connect to the car, etc.) and just make jarvis the thing that displays with nice animated tabs for maps, apps, music, etc. … REPLACE HEADWAY WITH JARVIS"*
- **Plan:** the 2026-09-24 car plan
- **Inherits:** Headway's ADRs, now `docs/car/adr/H-0001-kotlin-protocol-core.md` … `H-0010`

## Context

Headway is an unprivileged Android app that speaks the reverse-engineered Android Auto
Protocol to a factory head unit over Wi-Fi and draws the car's screen itself. It reached a
real 2021 Chevrolet Infotainment 3 unit on 2026-08-13. Its protocol stack is pure JVM Kotlin
with an emulator that drives an acceptance suite in CI. Jarvis's phone app was a second app on
the same phone, whose only car surface is a template screen under Google's Android Auto app —
which a GrapheneOS phone without Google's app never runs.

Connecting Headway to the car was the owner's complaint. Headway's own README counts it: pair
Bluetooth, approve a Wi-Fi prompt and leave it on screen, pair the car's Wi-Fi a second time
through a companion scan, and for a head unit that hands out no addresses (the Malibu announces
`access_point_type=STATIC`) configure a static IP by hand. Each failure looks like the same
timeout.

## Decision

1. **One app.** Headway's modules are vendored into `android-app/` with their git history
   (`android-app/car/`, merged at `adabc1c`): the JVM stack as `:car-protocol`,
   `:car-transport`, `:car-dash`, `:car-voice`, `:car-emulator`; the Android libraries as
   `:car-video`, `:car-input`, `:car-audio`. The app code Headway ran on moves into
   `ai.aura.app.car.*`; the protocol packages keep their `dev.headway.*` names, because
   renaming twenty thousand lines of protocol buys nothing. Headway's JVM/Android split
   (H-0001) stays: every rule about the protocol and the car screen's layout runs in CI
   without a device.
2. **Only the car's own socket is bound to the car's network.** Headway bound its whole
   process to it (`bindProcessToNetwork`), which was right for an app that needs nothing else.
   Jarvis reaches its adapter over mobile data or Tailscale for the whole drive — the
   transcriber, the model and the voice — so it binds the one AAP socket and refuses an
   unbound fallback that would leave into the VPN or over cellular (`car/link/SocketRoute.kt`).
3. **The car screen is Jarvis, drawn in Compose from the design tokens.** Tabs for Jarvis,
   Maps, Music, Apps, Phone, Messages and Car on Headway's rail; a tab is a Headway layout, so
   a split such as "Maps + Now playing" is simply a tab. The reactor plays while the link comes
   up. Headway's tile models (media sessions, the car-app host, notifications, the sensor
   channel) are kept; its View shell is replaced.
4. **Connecting is one guided flow per car**, run by Jarvis: pick the car, two companion
   associations in one sitting (Bluetooth, then Wi-Fi filtered to the exact BSSID the first
   handshake reported, so the approval prompt never comes back), the static-IP step only for a
   car that announces STATIC, then a live list of the five gates with one remedy per failure.
   What worked is remembered per car.
5. **The steering-wheel voice button talks to Jarvis.** The car's microphone goes to Jarvis's
   own transcriber; Headway's deterministic command grammar runs on the transcript first, so
   media and launch commands act locally and at once. Vosk is not shipped (it needed a 50 MB
   model and runtime code generation that GrapheneOS blocks, H-B-028).
6. **Licence.** Headway and Jarvis have one owner, so the code combines without a licence
   change; Headway's GPLv3 headers stay on its files.
7. **minSdk 33** for the app: every car feature needs it and GrapheneOS supports nothing older.
   The template screen for Google's Android Auto stays (`car/templated/`).

## Consequences

- Headway's repository is retired once this lands, with a pointer here.
- `android.car.permission.TEMPLATE_RENDERER` and the `androidx.car.app.connection` authority
  are device-global names: Jarvis will not install beside Headway (H-0009). The first run says
  which package holds them.
- What cannot be proven without the car stays Manual or Unproven in `docs/verification.md`
  (the Car section), with the drive checklist in `docs/car/drive-checklist.md`.
