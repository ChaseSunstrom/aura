# Android Auto: what is actually possible

Short version: **a third-party app cannot be the voice assistant on Android
Auto.** This is an OS/platform constraint, not a bug or a missing feature in
the Jarvis app. Jarvis in the car is therefore a *phone-side* experience that
happens to play through the car's speakers.

## The hard constraints (honest list)

1. **Only Google's assistant can own AA voice.** The Android Auto voice
   button (steering wheel / "Hey Google") is hardwired to Google's
   assistant. As of **March 2026, Google removed Assistant from Android Auto
   entirely — Gemini is the only voice layer**. There is no API, setting, or
   role that hands the AA voice pipeline to a third-party app.
2. **No "voice assistant" Car App category.** Android for Cars App Library
   templates cover navigation, POI, media/audio, messaging (via
   `MessagingService` patterns), and IoT (`androidx.car.app.category.IOT`).
   There is no assistant category; an app cannot render a conversational
   surface or grab the mic on the head unit.
3. **The most any app gets is tap-to-control.** The Car App Library's IoT
   template renders a described template the host draws — a list, a pane, an
   image — while parked or within the driving-allowed subset. That is the
   ceiling for a head-unit UI and it has no voice component. Jarvis **does**
   ship a car-app module now (`android-app/app/src/main/kotlin/ai/aura/app/car/`): a `CarAppService`
   (`templated/JarvisCarAppService`) declaring
   `androidx.car.app.category.NAVIGATION`, and `holo/CarNavScreen`, a
   `NavigationTemplate` whose map surface Jarvis draws itself (T-0613): the
   hologram drive of `docs/design/v2/Car.dc.html` with the turn, the trip,
   the subtitles, the same reactor core as the phone and one card at a time.
   The card's actions are also on the template's action strip, so a car that
   sends no taps on the map still has every one. Goldens:
   `docs/screens/v2/car/`.
   The car's position is the phone's GPS (the app's location permission;
   without it the drive says so), map-matched every few fixes by the
   house's `/api/maps/match` and dead-reckoned between fixes, so the car
   moves every frame. A destination comes from Jarvis (`hud.car.navigate`);
   the route is the house's Valhalla (`/api/maps/route`), asked again when
   the car leaves the ribbon or the driver says "reroute". "Next turn",
   "reroute" and a shown card's actions ("answer", "allow", "I'm driving")
   are done on the phone, never sent to Jarvis; a tool approval is a card
   too, and one nobody answers in 30 seconds is a no. Maps need a direct
   pairing with the house: a Hermes-only pairing has the drive without a
   route.
4. **A sideloaded car app does not appear in Android Auto at all.** This is
   the one that surprises people, and it is why a freshly-installed Jarvis
   APK shows nothing in the car. Android Auto's developer option for running
   apps from untrusted sources — the "Unknown sources" toggle everyone finds
   first — explicitly does not cover this kind of app. Google's own words, in
   [Test Android apps for cars](https://developer.android.com/training/cars/testing):

   > Android Auto has a developer option that lets you run apps that aren't
   > installed from a trusted source. This setting applies to media, messaging
   > notifications, and parked apps but **doesn't apply to apps built using
   > the Android for Cars App Library**.

   So there is no toggle, no adb command and no manifest change that makes a
   sideloaded build appear. There are exactly two routes, and both are about
   *distribution* rather than code:

   * **Google Play, any track** — an internal-testing track with one tester
     (you) is enough, and installing from Play makes the app a trusted source.
     It means the APK gets an account, a listing and Play signing, which for a
     private house assistant is a real cost and is why this is not the default
     recommendation.
   * **The Desktop Head Unit** — `scripts/`-free, no Play account, and the
     supported way to see the car surface during development. It runs on the
     machine, the phone connects to it over USB, and sideloaded Car App
     Library apps DO appear. The procedure is in *Acceptance gate* below.

   Nothing in this repository can change that, and the app says so on its
   settings screen rather than leaving you to conclude the car module is
   broken.
5. **Head-unit mic is Google's.** While AA is connected, the car mic is
   routed to the AA stack for Gemini. Third-party phone apps do not receive
   car-mic audio.

## The sanctioned fallback (the only viable hands-free path)

Run **"Hey Jarvis" on the phone itself, in parallel with Android Auto**:

- The **phone's mic** does the wake word + speech capture (the phone is
  mounted/charging in the car anyway; microWakeWord keeps working while AA
  is connected).
- Assist TTS output is ordinary media/assistant audio, so it **routes out
  the active Bluetooth/AA audio link to the car speakers**.
- Nothing renders on the head unit — the orb appears on the phone screen
  only. Keep eyes-off usage voice-only.

Limitations to accept: phone-mic quality vs. cabin noise (mount the phone
close), and if Gemini is mid-response the audio focus arbitration is
whoever grabbed focus last. In practice: don't use both assistants at once.

## Car-BT automation trigger (wake gate in the car)

Design for enabling always-on listening exactly while driving, without
burning battery the rest of the day:

- **Signal:** the phone's own `BluetoothProfile` connection callbacks. The
  car's BT MAC/name is the thing being watched.
- **Where the decision is made: on the phone.** `WakeWordGate` returns
  `shouldListen = true` whenever car BT is connected, at any hour, and its
  `carBtConnected` input comes straight from those callbacks. No server round
  trip, so the gate keeps working when the house is unreachable — which,
  driving, it often is.
- The server is told about the state change so automations can react to
  "driving" (that is what `get_user_context` reads to decide it should speak
  rather than notify), but it is not the source of truth for the gate.
- **Disconnect:** BT drop → gate re-evaluates → detection service stops
  within seconds.

## Acceptance gate (needs a real car or the Desktop Head Unit)

1. **Hands-free round trip over BT:** with AA connected and the phone
   mounted, say "Hey Jarvis, what's the temperature in the living room" →
   wake word fires on the phone, pipeline runs, **TTS answer plays through
   the car speakers**. Nothing appears on the head unit (expected).
2. **Gate behavior:** unplug/disconnect BT → wake word service stops
   (verify: mic indicator gone) — reconnect → resumes.
3. **Routing:** while driving, `get_user_context` reports `driving: true` and
   Jarvis speaks its answer instead of sending a notification.

Frame in all user-facing docs: the head unit belongs to Google; Jarvis rides
along on the phone. If Google ever opens an assistant role for AA we revisit,
but nothing in the current Car App Library roadmap suggests it.
