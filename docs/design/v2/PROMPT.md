# JARVIS v2 — rebuild every surface, connect everything, fix everything (autonomous run)

You are working on **JARVIS**, my personal assistant. It runs on my home server (2× RTX 3090, local models), and has these surfaces:

- the desktop app
- the web console
- the desktop overlay
- the phone app
- the car (head unit)

This brief replaces the earlier one. Where they disagree, this one wins.

**Run fully autonomously. Don't ask me questions.** When something is unclear, make the most reasonable choice. Log each choice as one line in `DECISIONS.md`: what you decided, why, and how to undo it. Then keep going. Split the work into parallel lanes wherever they don't touch the same files. Stop only when everything in "Definition of done" is true.

The reference designs are on the same page as this prompt. The **v2 boards** are the target:

- `Core2.dc.html`: the main screen
- `Car.dc.html`: the car
- `Phone.dc.html`: the phone
- `Control.dc.html`: JARVIS working on a screen, plus devices and approvals
- `Integrations.dc.html`: connections

The design files use a canvas runtime you don't have: `support.js`, `<x-dc>` and `{{holes}}`. Read them as a visual and motion spec. **The `orbEngine` method in their scripts is the reference implementation of the core.** Port it, don't reinvent it.

---

## 0. Audit first

Map every surface:

- its stack
- how it talks to the server
- how voice works
- how permissions are requested
- how integrations and plugins are loaded

Reproduce every bug in section 6. Write all of this to `AUDIT.md` and work from that list.

---

## 1. Design language: projected light, not panels

The current UI reads as flat boxes, walls of text and duplicated tabs. Replace it everywhere with one language. Build it once as a **shared design-system package** (tokens, components, motion) that every surface uses.

### Look

- **Background.** Near-black `#010409` with a deep cyan radial glow `#062236` behind the core, plus a few faint, huge orbit ellipses.
- **Text and data.** These are **projected light, not boxes.** There are no card backgrounds, no frosted glass, and no big radii. Each piece of content has:
  - a soft dark radial backing behind it, just enough to stay readable over moving visuals
  - one cyan corner bracket at the top left
  - a faint glow on the text (`text-shadow: 0 0 12px rgba(63,208,232,.45)`)
  - where needed, a thin edge line in place of a border
- **Colors.**
  - accent cyan `#3FD0E8`
  - violet `#9B8CFF` for memory, embeddings and models
  - amber `#FFB547` for "needs you" and route
  - green `#5FE3A1` for OK
  - pink `#FF6B8B` for security and errors
  - main text `#F2FBFF`, secondary `#A9C8D8`, labels `#8DB4CA`
- **Fonts.**
  - Space Grotesk Light for anything you read at a glance: big numbers, headlines, what JARVIS says.
  - JetBrains Mono for labels, which are UPPERCASE with .22em letter-spacing, and for small data.
- **Accessibility.** Body text meets WCAG AA over its backing. Touch targets are at least 44px, and at least 52px in the car.
- **Show less.** One idea per projection, and short labels. Never put long text on screen. Progressive disclosure everywhere, including Settings, which today is a wall of text.

### The core (the orb)

It is a single 3D object, rendered on the GPU. Port `orbEngine` to WebGL or Three.js, with a Canvas2D fallback.

Its parts:

- **Particle sphere.** About 1,500 Fibonacci points, depth-shaded, with additive blending.
- **Reactor cage.** Inside the sphere, three segmented reactor rings on different axes form a spherical cage. Each ring segment is a **solid block with real depth**: dark faces hide what's behind them, and the edges are lit. Each block also has:
  - wire windings
  - a small LED
  - end caps
- **Detail around the rings.** Four struts run to the core, and points of light flow along each ring.
- **Glowing core.** It has a small wire sphere inside.
- **A soft floor glow** under the orb, for depth.

**The states are the core itself moving, never a visualizer around it:**

| State | The core does this |
|---|---|
| idle | slow gyroscope drift, gentle breathing, rings open into a sphere |
| listening | the rings swing to face me and become a flat reactor; the shell ripples with my live mic level |
| speaking | the whole core pulses with the speech audio envelope |
| thinking | the rings spin like a gyroscope; the shell contracts and swirls; violet tint |
| embedding / retrieval | particles are pulled into the core and respawn at the surface |
| tool use / acting | fast steady spin; points of light race along the rings |
| music | ring segments light up on the beat; the shell bounces |
| alert | the rings lock facing me; pink tint; the core flickers |

Change state by tweening its parameters, never by cutting. Every state change fires a short shock-ring out of the core.

Budget: 60fps on the desktop and phone. Lower the particle count on battery saver. Stop rendering completely when the core isn't visible.

### Contextual UI, Iron Man style

There are no permanent panels or tabs on the main screen. What's relevant **comes out of the core when it's needed, and goes back in when it isn't:**

- **Coming in.** A projection starts as a bright point at the core. It travels to its spot, opens first as a thin line and then to full height, with a slight tilt toward the core. A leader line draws from the core to it.
- **Going out.** The reverse: it collapses to a line, flies back into the core and fades.
- **Moving the core.** The core travels on screen to carry UI. When JARVIS starts working, the core rises and shrinks into a **presence strip** at the top. The strip unrolls from the core and shows the task, its step progress and "Esc to take over". Results flow out of it. When the work is done, the core comes back.
- **Any state to any state.** Every pair of states must transition cleanly. The outgoing state fully leaves first: projections collapse back into the core, lines retract, the strip folds up and the subtitles fade, all in about 0.45 s. Then the incoming state comes out, while the core tweens its position, size and motion the whole way. Nothing pops in or out, overlaps, or gets left behind. Test every state pair, including interrupting a transition halfway.
- **The presence strip** exists only while JARVIS is acting on a device. It isn't a permanent "island".
- **While JARVIS works.** The core hovers above the work and moves in its own **working** state: the rings orbit like a gimbal, and the core beats every 1.2 s.
  - On each beat, a pulse of light travels down smooth connector lines from the core to each visual it's using: the plan, a chart, a map.
  - Visuals **appear whole, fast and smooth**, never traced stroke by stroke. Each comes out of the core in about 0.5 s, its inner parts settle in quick staggers (bars rise in about 0.4 s, rows slide in about 35 ms apart), and its connector line draws in about 0.5 s.
  - Lines between related visuals use the same pulsing light.
  - See the Working scene on `Core2.dc.html`.
- **Visualize anything.** The model can also output a small vector scene (paths, shapes, text, fills, simple 3D with a camera) when no component fits. It renders through the same fast appear motion; it is not drawn live.
- **Leaving everything visible.** There's a launcher (all apps), ⌘K (search, commands, or "ask"), and a devices button. The old left rail and its tab soup go away.

### Settings: as few as possible, and JARVIS changes them

- **Cut settings to the minimum.** Remove every setting that exists only because a design decision wasn't made, such as the Reactor 2D/3D, Panels Tabs/Tiles and Selection mode toggles. Pick the best behaviour and delete the switch.
- **What stays** fits on one short screen: account and sign-in, people and voices, the persona and voice, privacy and approvals, and devices. Everything else is automatic or learned.
- **JARVIS can change any setting itself** ("lock after 5 minutes", "use the Ultron voice", "stop announcing backups"). It can also **set up integrations itself**: it starts the OAuth flow on whichever device I'm using, walks me through the consent screen, tests the connection and reports back.
- Setting changes that affect security, such as auth, approval rules or device access, go through an approval. Everything else just happens and is logged.

### Old UI removed completely

**No old UI may survive.** Every legacy component, style, screen, tab, panel, toast, dialog and layout is deleted from the codebase, not hidden behind a flag. Everything is rebuilt on the new design system. To guarantee it:

- Inventory every UI component in `AUDIT.md`. Mark each one as replaced or deleted, and delete it.
- Add a lint or CI check that fails if any screen imports anything outside the new design-system package, or uses a hard-coded color or font.
- Do a screenshot sweep of every screen and state on every surface, and save the results in `docs/screens/`. Any screen that doesn't match the v2 language fails the run.

### Generative UI: "visualize anything"

JARVIS composes projections on the fly from a typed component library. Each component is a schema the model fills in, and the client renders it:

- timeline, stat, line or area chart, bar chart, map with a route, list, table
- comparison, image, 3D model or point cloud, code, diff
- approval, progress, media player, device control, person card

For each projection the model only chooses the component type, the data and the placement. Responses stream: the first projection must render **within 300 ms** of the model emitting it. Add a "make me a visual of …" path that can also generate a one-off custom visual (sandboxed HTML/WebGL) when no component fits.

---

## 2. Devices: control everything from anywhere

- **A device mesh.** Every device runs a JARVIS agent:
  - desktop and laptop: a native helper
  - phone: an app with an accessibility service or automation APIs
  - car
  - TV (cast or ADB)
  - servers (SSH or Proxmox API)
  - smart home (Home Assistant)

  The devices reach each other from anywhere through a private overlay network (Tailscale or WireGuard) with mutual authentication. They are never exposed publicly.
- **Pull up on any device.** "Show it on my desktop" or "put it on the TV" moves the live view, with the handoff animation from the Control board.
- **JARVIS works visibly on screens.** On the desktop and phone, JARVIS can:
  - see the screen
  - move a **visible JARVIS cursor**: cyan, labelled, with a smooth path and a click ripple
  - highlight the element it's about to act on
  - type and scroll
  
  I can take over at any moment with Esc or a touch, and it pauses immediately. Prefer accessibility trees and APIs, and fall back to screenshots and vision.
- **Device status** appears in the devices view: online, battery, what's on screen, and what JARVIS is doing there.

---

## 3. Integrations: real accounts and anything else

The current "Integrations" screen is a catalogue of internal skills with raw `<untrusted_content>` wrapper text leaking into the descriptions. Replace it with the design on `Integrations.dc.html`.

### Native connectors, with real OAuth

Build these end to end, with sync, webhooks or push where available, and tests:

- **Google:** Gmail, Calendar, Drive, Docs and Sheets, Contacts, Maps, Places and Routes, Photos where the API allows, YouTube.
- **Microsoft 365:** Outlook, Calendar, OneDrive.
- **Messaging:** Slack, Discord, Telegram, and SMS/RCS via the phone.
- **Home:** Home Assistant, covering everything it exposes.
- **Code and servers:** GitHub, GitLab (self-hosted), Proxmox.
- **Media:** Spotify, Plex.
- **Notes and tasks:** Notion, Todoist, iCloud (CalDAV/CardDAV).
- **The car's own API**, where it has one.
- **Weather.**

### Anything else

- **Any API.** Paste a docs URL or an OpenAPI file. JARVIS generates a connector, runs a test call and shows the result.
- **Any MCP server**, local or remote.
- **Any website**, through a browser agent using my own signed-in profile.
- **Any app on my devices**, through computer use as in section 2.

### Permissions per capability

Every capability has a level: **Off / Read / Ask first / Allowed**. Tokens are encrypted at rest. Every action goes in an audit log. "What did you do today?" answers from that log.

### Google OAuth note

A personal Google Cloud app left in "Testing" status gets refresh tokens that expire after 7 days. Move it to "In production". As an unverified app it shows a warning screen but works for up to 100 users. Handle re-consent gracefully when it's needed. Record this in `GAPS.md`.

---

## 4. Approvals: redesign

Use the design on `Control.dc.html` and `Phone.dc.html`. Every approval is **one clear ask**:

- **What, in one plain line:** "Delete 3 old snapshots?" or "Text Sam 'No rush — see you at 7:45'?"
- **Why:** what I said or which routine triggered it.
- **Impact:** what changes, and whether it can be undone.
- **Big primary action.** **Hold-to-confirm** for anything irreversible, deny, and "Show me first".
- **A rule chip for next time:** Ask every time / Allow for this task / Allow when I'm home.

The approval appears on the device I'm currently using: phone, watch, car (by voice: "send" or "cancel") or desktop. Once I answer, it clears everywhere. While waiting, the core shows its alert state and the leader line is amber.

---

## 5. Car

### What's wrong now

- The car often falls back to the default template UI.
- When it's disconnected it speaks in a different voice.
- The ride-along fails with `urllib.error.URLError … [Errno 111] Connection refused`.

Fix all of these (see section 6).

### Map: register as a navigation app

Register the car app in the **navigation app category**. With Android Auto's Car App Library, that is `NavigationTemplate` with surface rendering. Navigation apps are allowed to draw their own map surface, which is how the hologram gets on screen.

Render the **live hologram drive** from `Car.dc.html` on that surface:

- a chase-camera 3D wireframe city
- buildings that fade into fog with distance
- glowing road edges and lanes
- a route ribbon with a floating turn chevron
- my car as a hologram
- a scan plane passing through the buildings

Clip geometry properly against the camera's near plane. Nothing may pop out of frame early.

### Real map data, where I actually am, in real time (not satellite)

- **Streets and roads:** OpenStreetMap vector tiles. Self-host them (Protomaps PMTiles or OpenMapTiles) so they're fast and free.
- **Building footprints and heights:** OSM `height` and `building:levels`, with **Overture Maps buildings** filling coverage gaps. If I add a key, offer Google Photorealistic 3D Tiles, stylised into the hologram.
- **Geocoding:** Photon (it's built on OSM data). **Routing:** Valhalla or OSRM, self-hosted, with live traffic where a source exists.
- **Position:** live GPS from the phone or car, map-matched to the road, smoothed and dead-reckoned between fixes.
- **Street names:** drawn upright, readable, with a dark backing and fog fade. Show only the current street and the next turn, never a pile of labels.
- **Other vehicles:** draw them only if the car actually provides that data. Never fake them.

### Car layout, with fixed zones so nothing overlaps

| Zone | Contents |
|---|---|
| top left | next turn |
| top right | arrival time, time left, distance, speed |
| bottom left | what JARVIS is saying |
| bottom right | the core |
| above the core | one contextual card at a time |

The core cycles cards through the same come-out/go-back motion:

- **Calls:** incoming calls show caller, relationship and why they might be calling (for example "Sam · dinner tonight"), with big Answer and Decline targets. Everything also works by voice: "answer", "decline", "tell them I'm driving", "call back when I'm parked". JARVIS can screen unknown callers. After every call it offers a short summary and any follow-ups. Outgoing calls are by voice: "call Sam". Music ducks or pauses, and the core shows the call state.
- **Messages:** read aloud; replies are confirmed by voice.
- **Approvals.**
- **Home on arrival:** garage, lights, heat.
- **Calendar:** next event and leave-by.
- **Music.**

Follow the driver-distraction rules. Everything must also be doable by voice.

---

## 6. Fix list (reproduce, fix, regression test)

1. **Integrations catalogue** shows raw `<untrusted_content>` wrapper text in descriptions. The wrapper is for model context only. Strip it in every UI.
2. **Duplicated "This turn" tabs** on several panels, and tab overflow menus. Remove them; the contextual UI replaces them.
3. **Walls of text:** Settings, routines and telemetry. Restructure with progressive disclosure. Settings also has duplicated toggles: Reactor 2D/3D, Panels Tabs/Tiles, Selection modes. Keep only real choices.
4. **Network list** labels the gateway `10.20.30.1` as "unknown" and lists every Proxmox VM as an unnamed "pc". Name the router, and pull VM names from Proxmox.
5. **`jarvis-models` health flaps** between unhealthy and normal. Find the cause, add backoff, and stop the alert spam.
6. **CI runner can't reach GitLab**, so deploys are blocked. Diagnose it and fix it if it's a config problem; otherwise list exact steps in `GAPS.md`.
7. **Voice latency** shows 3.4 s. Target: speech starts **≤ 1.2 s** after I stop talking, on LAN. To get there:
   - stream tokens and TTS
   - pre-warm the model on wake word
   - keep the hot model resident
   - cache embeddings
   
   Report p50/p95.
8. **Car ride-along** fails with `[Errno 111] Connection refused`. Fix the connection path and lifecycle: reconnect after the phone sleeps, Bluetooth handoff, process death.
9. **Phone and car connection** is flaky. Use one persistent connection (WebSocket or QUIC) with backoff and jitter, resume on network change, queue requests offline, show a clear connected/offline state, and remove all polling.
10. **Wrong voice when disconnected.** Always use JARVIS's own voice: bundle an on-device TTS voice that matches the server voice. Never switch silently.
11. **Permissions and speech recognition** are broken or flaky. Fix:
    - proper runtime flows with explanations, and deep links to Settings when permission was permanently denied
    - the correct foreground service for the mic
    - audio focus and ducking
    - on-device wake word, streaming speech-to-text, and an on-device fallback
12. **Battery.** Profile it and fix the causes: wakelocks, polling, rendering in the background. Target **≤ 3% per hour** idle with wake word on. Report before/after.
13. **Text and layout bugs** everywhere: clipping, overlap and placeholder strings. Sweep every screen at every size, including car displays.

---

## 7. Assistant capabilities, end to end

Everything here needs voice, UI in the new language, and tests. Where something is truly blocked, stub it behind a flag and list it in `GAPS.md`.

- **Conversation:**
  - wake word
  - barge-in (interrupt JARVIS while it's speaking)
  - follow-ups without the wake word
  - context carried across devices
  - handoff mid-conversation
- **Memory:** long-term memory with embedding and reranking, shown on the core's embed/think states. "What do you remember about …", plus forget and correct.
- **Daily life:**
  - calendar
  - reminders, timers and alarms
  - morning brief and evening wrap-up
  - leave-by alerts
  - weather
  - packages
- **Communication:** triage, summaries and drafts across email and chat. **Sending anything always goes through an approval** unless I've set a rule.
- **Calls and texts on every device:**
  - **Phone texts on the desktop.** Mirror my phone's SMS and RCS conversations to the desktop and web console: read them, search them, and reply with a typed or voice reply sent from my phone. Use the phone agent: SMS permissions where allowed, plus the notification listener and its reply actions for RCS and chat apps that have no API.
  - **Other notifications** follow me the same way, so I never have to pick up the phone.
  - **Calls everywhere.** Answer, decline or place calls from the desktop, the car or my watch, routed through my phone with Bluetooth or the platform's call-relay APIs. Include the same screening, voice commands and call summaries as in the car section.
  - **Context-aware handling.**
    - While I'm driving: read out only what matters and auto-reply "driving, back soon" if I've allowed it.
    - In a meeting or on do-not-disturb: hold calls and messages, and let urgent people through.
    - When I'm asleep: nothing gets through except emergencies.
  - **Privacy.** Mirrored messages stay end-to-end between my own devices, over the device mesh. They're never stored on any third-party service.
- **Home:**
  - lights, locks, climate, cameras and scenes
  - arrival and departure routines
  - security alerts, such as a new device on Wi-Fi
- **Computer and phone work:** do multi-step tasks visibly on the desktop or phone, such as booking, form filling, settings and file work, with the presence strip, cursor and approvals.
- **Dev and home lab:**
  - run Sampo lanes
  - build and test status
  - model management with GPU fit checks
  - Proxmox snapshots and VMs, with approvals for anything destructive
- **Research:** web research with cited answers, long-running research jobs with progress, and visual summaries.
- **Proactivity:** speak up at the right moment, respecting do-not-disturb and driving.
- **Routines:** a visual builder with triggers (time, place, device, event) and actions.
- **Speed:** cache, stream, parallelise tool calls, and keep UI work off the main thread.

---

## 8. Performance (every surface)

- 60fps for all animation on the desktop, web and phone. Never drop a frame on a scene change.
- Animate only `transform`, `opacity` and `filter`, never layout properties. Render the core on the GPU.
- Keep heavy work off the main thread (workers or native threads).
- Virtualize long lists. Lazy-load anything off-screen. Preload the next likely scene.
- Pause every animation and the core when it isn't visible, or when the app is in the background or on battery saver.
- **Budgets:**
  - first meaningful paint < 1 s on desktop and < 1.5 s on phone
  - input-to-feedback < 50 ms
  - projection appears < 300 ms after the model emits it
- Add performance tests to CI (frame-time traces, bundle-size limits). A regression fails the build.

## 9. How to run this: subagents, in parallel

- **Use subagents to ship every feature at the same time.** Split the work into independent lanes, for example:
  - design system and core
  - each surface (desktop/web, phone, car, overlay)
  - integrations
  - the device mesh and computer use
  - approvals and settings
  - voice and latency
  - car map and data
  - battery and performance
  - the old-UI removal sweep
  
  Run the lanes concurrently, each in its own branch or worktree, and merge them through the merge train.
- **Use Opus for all UI and design work:** the design system, the core, the motion, every screen and the screenshot review. Use faster models only for mechanical work such as migrations, test scaffolding and renames.
- Each lane writes its own tests and screenshots. A separate review subagent checks every lane against the v2 boards and this brief before it merges.

## 10. Autonomy rules (full auto, with hard limits)

- Don't ask me questions. Decide, log it, continue.
- Make small commits with clear messages. Put risky changes behind feature flags.
- **Never, even in full auto:**
  - delete my data
  - wipe memory
  - send real messages or emails to real people
  - make purchases
  - expose or rotate secrets
  - disable authentication or security
  - force-push or rewrite main
  - change router or firewall settings
  - unlock doors or open the garage during tests
  - run destructive Proxmox actions on real VMs
  
  Use test accounts, mocks and dry-runs for these.
- When you're truly blocked (credentials, hardware, store approval), stub it, list exact steps in `GAPS.md` and move on.

---

## 11. Definition of done

- Every surface uses the shared design system and matches the v2 boards. Side-by-side screenshots are saved in `docs/screens/`.
- **No old UI code remains:** the inventory in `AUDIT.md` is all deleted or replaced, and the CI check for legacy imports and hard-coded styles passes.
- Settings are reduced to the short list above. JARVIS can change settings and set up a new integration by voice, end to end.
- The working state, the fast-appear motion and the pulsing connector lines work everywhere. Every surface meets the performance budgets in section 8.
- The core is ported to GPU rendering, every state in the table above is visibly distinct, and it holds 60fps on the target devices.
- Come-out/go-back transitions, core travel and the presence strip work everywhere, including exit animations.
- All native connectors pass a live test against a test account. "Any API" and "Any MCP" can each add a new service in under a minute in a demo.
- Device mesh: pull up on another device, visible computer use with take-over, and approvals routed to the device I'm using all work.
- Texts from my phone can be read and answered on the desktop. Calls can be answered, declined and placed from the car and the desktop, with call screening and summaries, and calls behave correctly while I'm driving, in a meeting or on do-not-disturb.
- Car:
  - runs as a navigation app with the hologram map built from real OSM/Overture data around my live position
  - reroutes
  - nothing overlaps, and the text is readable at a glance
- Every item in section 6 is fixed and has a regression test. Tests pass:
  - unit and integration
  - web and desktop end-to-end (Playwright)
  - phone UI tests on an emulator
  - car tests in the Desktop Head Unit
- `REPORT.md` contains:
  - before/after for latency p50/p95, battery idle drain, reconnect time and cold start
  - what shipped
  - the list of `GAPS.md` items with exact steps for me
  - how to roll back each lane
