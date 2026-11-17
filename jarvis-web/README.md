# jarvis-web

The v2 console for Jarvis: the core on its stage, with full screens (chat, approvals,
texts, devices, integrations, ops, car, settings) that the launcher, ⌘K and the devices
sheet open (`src/lib/ds/README.md` and `src/lib/hud/README.md` are the maps). A **thin
client**: the browser captures mic audio, sends it to the Hermes adapter through this
server's `/hermes/*` relay for transcription, renders the streaming reply, plays the
returned speech, and animates the core (idle / listening / thinking / speaking,
reactive to audio levels). The house screens drive the backend socket at `/ws`.

All intelligence (STT, LLM, intents, TTS) lives in the backend.

## Backends

The default backend is **jarvis-core**, the standalone home-automation platform.
Home Assistant still works: the two speak the same websocket contract
(`/api/websocket`, the `auth_required` → `auth` → `auth_ok` handshake and
`/api/tts_proxy/…` media paths), so a single client covers both.

`AURA_BACKEND` picks which pair of variables wins; the other pair stays as a
fallback, so an HA-only deployment keeps working untouched.

| `AURA_BACKEND` | url comes from | token comes from |
| --- | --- | --- |
| `core` (default) | `AURA_URL`, else `HA_URL` | `AURA_TOKEN`, else `HA_TOKEN` |
| `ha` | `HA_URL`, else `AURA_URL` | `HA_TOKEN`, else `AURA_TOKEN` |

Both the `/ws` relay and the `/api/tts` proxy target whichever backend is
selected. `/api/config` reports the resolved backend (kind, url, which env vars
were used, whether a token is present — never the token itself).

Commands only jarvis-core implements degrade gracefully: the client turns an
`unknown_command` error into an `UnsupportedCommandError` (`src/lib/jarvisClient.ts`)
instead of an exception that takes the page down, and `listTools` / `callTool` fall back to
the service catalogue and `call_service`.

## Environment variables

| Var | Default | Purpose |
| --- | --- | --- |
| `AURA_BACKEND` | `core` | `core` (jarvis-core) or `ha` (Home Assistant) |
| `AURA_URL` | — | jarvis-core base URL, e.g. `http://jarvis-core:8123` |
| `AURA_TOKEN` | — | jarvis-core access token. **Server-side only.** |
| `HA_URL` | — | Home Assistant base URL. Used when `AURA_BACKEND=ha`, or as the fallback when `AURA_URL` is unset |
| `HA_TOKEN` | — | Long-lived HA access token. **Server-side only.** |
| `AURA_PIPELINE` | `Jarvis` | Pipeline name, reported to the client by `/api/config` |
| `AURA_TTS_VOICE` | `en_GB-alan-medium` | Voice name, reported to the client by `/api/config` |
| `AURA_VAD_HANGOVER_MS` | — | How long a pause must last before a spoken turn is over; `/api/config` clamps it to something usable |
| `PORT` | `8199` in the Docker image, `3000` for a bare `node build` | HTTP listen port |
| `HOST` | `0.0.0.0` | Listen address of the `node build` launcher |
| `ORIGIN` | (assumes https) | Set to the public origin (e.g. `http://localhost:8199`) so adapter-node builds correct absolute URLs / cookie flags |
| `AURA_ALLOWED_ORIGINS` | — | Extra origins allowed to open `/ws` and `/hermes/ws`, comma-separated (e.g. `https://hud.example`). Same-origin is always allowed; see below |
| `AURA_ADAPTER_URL` | — | The jarvis-adapter (Hermes) base URL, e.g. `http://127.0.0.1:8643`. The console relays `/hermes/ws`, `/hermes/api/*` and `/hermes/v1/*` to it |
| `AURA_ADAPTER_KEY` | — | The adapter's bearer key, injected by the relay. **Server-side only.** `/api/pair`, which answers with it, is never relayed |
| `AURA_CONSOLE_PASSWORD`, `AURA_CONSOLE_PASSWORD_HASH`, `AURA_CONSOLE_PASSWORD_FILE` | — | The console password: plaintext, a `scrypt$…` hash, or the file that holds the hash (default `.storage/console-password`). See `src/lib/server/consoleAuth.ts` |
| `AURA_CONSOLE_IDLE_MINUTES` | one week | Minutes a session may sit unused before the console asks again (never less than 5) |
| `AURA_PAIRING_SECRET` | — | The pairing secret this console mints with. **Server-side only.** |
| `AURA_CONSOLE_ORIGIN` | — | The site's name for passkeys, set where the console runs, never taken from the request |
| `AURA_HUD_WEB_ORIGINS` | `AURA_CORS_ORIGINS` | Origins a web-kind panel may frame; empty means link cards only |
| `AURA_LLM_URL` | — | Base URL the `/llm/*` relay forwards to; without it the relay answers 503 |

A url **and** a token must resolve or the `/ws` relay closes with
`server missing AURA_URL/AURA_TOKEN` (or the `HA_*` names when
`AURA_BACKEND=ha`).

### Who may open `/ws`

A socket on `/ws` is an **authenticated admin session** — the server attaches
the backend token to it, so whoever holds one can read every state and every
event and call any service, `lock.unlock` included.

WebSocket upgrades are not covered by the same-origin policy: there is no
preflight, and `Origin` is advisory unless the server checks it. So the relay
checks it (`isOriginAllowed()` in `src/lib/server/backend.ts`, hand-copied into
`server/ws-proxy.js`) and answers before the socket exists:

* a client that brings its own bearer token — the phone, a script — is passed
  straight through: the relay injects nothing and the backend judges the token;
* a browser (it sends an `Origin`) from the same origin, comparing host:port, so a
  TLS terminator in front does not break it, or from an origin in
  `AURA_ALLOWED_ORIGINS`, gets the server-held token, but only once it has signed
  in to the console (see below);
* any other browser origin, including `Origin: null` from a sandboxed frame — **403**;
* no `Origin` and no token — **401**.

Without the origin check, any page the user happens to open — an ad frame, a blog —
could open `ws://jarvis.local:8199/ws` and drive the house. Set `AURA_ALLOWED_ORIGINS`
to the exact origin you serve the console on if you want to pin it harder than
"whatever `Host` says" (that default still matches a DNS-rebinding attacker, who
controls both headers).

## Pages

v2 has one route, `src/routes/[...path]`; `src/lib/ds/shell/routes.ts` maps a path to the
core or to a screen, and `src/lib/hud/activities.ts` lists the paths the console has ever
had, which keep resolving (to the core, or to the screen that took over) because the
phone's strip, bookmarks and notes still carry them. A path with neither a screen nor an
activity is a real 404. The screens are in `src/lib/ds/screens/` and are listed, with the
shell round them, in `src/lib/ds/README.md`; what feeds them is in `src/lib/hud/README.md`.

Besides the console, the server has `/healthz`, `/offline` (prerendered, the phone's offline
page), `/gallery/*` (the design system's proof pages), the `/hermes/*` and `/llm/*` relays,
and the `/api/*` routes under `src/routes/api/`.

### What the console can and cannot change

`/tools` prefers a `jarvis/tools/list` websocket command. Backends that do not
implement it fall back to the service catalogue from `get_services`, projected into the
same shape, and test runs go out as `call_service` (`listTools` and `callTool` in
`src/lib/jarvisClient.ts`).

Clearing an area assignment sends `area_id: ""`, not `null`: jarvis-core's
registry skips null-valued fields on update.

## How the token stays server-side

The browser never sees the backend token:

- The page connects to `ws(s)://<origin>/ws`. The Node server (adapter-node
  build, see `server/ws-proxy.js`) opens the real connection to
  `${url}/api/websocket`, performs the auth handshake (`auth_required` →
  `auth` with the token → `auth_ok`) itself, swallows those auth frames, and
  then relays JSON and binary audio frames in both directions.
- TTS audio: the `tts-end` event carries a path on the backend. The browser
  requests `/api/tts?path=...`; `mediaProxyTarget()` in
  `src/lib/server/backend.ts` resolves it and only lets it through if the
  **normalised** URL is still same-origin with the backend and still under
  `/api/tts_proxy/` or `/api/tts/`. Normalisation matters: the URL parser
  collapses `%2e%2e` into a dot segment just like `..`, so a substring test for
  `..` alone leaves `/api/tts_proxy/%2e%2e/%2e%2e/api/states` reachable — with
  the admin token attached. Redirects are refused (`redirect: 'error'`) so a
  30x cannot pivot the proxy onto another host. Then the bytes are streamed with
  the `Authorization: Bearer` header attached server-side.
- The Hermes adapter's key is held the same way: `/hermes/api/*` and `/hermes/v1/*`
  are forwarded with it injected, and `/hermes/ws` is relayed by `server/hermes-proxy.js`.
- The browser session is identified by random `httpOnly` cookies set in
  `hooks.server.ts` (`jarvis_sid`, and `jarvis_console` once signed in) — nothing
  auth-related in `localStorage`.
- CSP (`svelte.config.js`): `default-src 'self'`, no CDN assets; everything is
  bundled. `script-src` adds only `wasm-unsafe-eval` (the wake word's runtime is
  WebAssembly) and `connect-src` adds the GitLab origin the Devices screen reads
  releases from.

Because the token lives in the server's environment, the console reports only *whether* a
token is configured (`tokenConfigured` in `/api/config`). Point the app at a different
backend by changing `AURA_BACKEND` / `AURA_URL` / `AURA_TOKEN` where the web server runs
and restarting it.

### Where the trust boundary actually is

Hiding the token is not access control. The `/ws` relay authenticates to the backend with
the admin token on behalf of whoever opened the socket, so what stands in front of it is the
gate in `src/lib/server/consoleAuth.ts`, which `hooks.server.ts` applies to every `/api/*`
and `/hermes/*` request (a short list of routes is exempt: sign-in, `/api/pair/claim`,
`/api/config`, model downloads, the speaker routes) and `server/ws-proxy.js` applies to a
browser's socket:

- **With a password set** (`AURA_CONSOLE_PASSWORD`, `…_HASH` or `…_FILE`), a request needs a
  live console session, the adapter key, or a paired device's own key. The password is never
  stored, only its scrypt hash; attempts are counted per caller. A misconfigured password
  leaves the console closed rather than open.
- **With no password set**, every browser that passes the origin check counts as signed in,
  so reachability is authority again: bind the port to a trusted network, or put an
  authenticating reverse proxy in front of both `/` and `/ws`. Do not expose the port to
  the internet either way.

## Design system

One source of truth, in files under `src/lib/styles/`, imported once by
`+layout.svelte`:

| File | What is in it |
| --- | --- |
| `tokens.css` | every `--jv-*` custom property on `:root` — palette, type scale, spacing, radii, glows, durations. Generated from `design/tokens.json` (`@generated`: edit the source, run `python3 design/build.py`) |
| `base.css` | document ground, focus ring, selection, and the reduced-motion kill switch |
| `fonts.css` | the self-hosted faces: JetBrains Mono and Space Grotesk |
| `themes.css` | the per-theme overrides of those tokens |

There is no shared component stylesheet: each `$lib/ds` component draws itself.

`src/lib/tokens.ts` mirrors `tokens.css` as data, for the values that have to
reach JavaScript (the HUD's per-state accent). `tokens.test.ts` diffs the two
and fails if they drift, so a colour cannot be changed in one place only.

**No file outside `tokens.css` and `tokens.ts` may contain a raw hex value**
(`tokens.test.ts` scans the stylesheets for it; `scripts/verify/token_lint.py` covers the
rest).

### Palette

The values are in `tokens.css`; the names to know are `--jv-bg` (the page ground),
`--jv-panel` / `--jv-panel-solid`, `--jv-accent` (the system colour) and
`--jv-accent-deep`, `--jv-warn` / `--jv-warming`, `--jv-danger` / `--jv-danger-text`
(the lighter one for text), `--jv-line` / `--jv-line-soft` / `--jv-line-hair` (borders and
row rules), and `--jv-text` / `--jv-text-bright` / `--jv-text-dim` / `--jv-text-faint`.

Every text token clears **WCAG AA (4.5:1)** on `--jv-bg`, and the dimmest still clears it
at the lowest opacity the CSS applies. `tokens.test.ts` computes the ratios rather than
trusting the eye.

### Type

Two families: `--jv-font-chrome` and `--jv-font-body` (both monospace, JetBrains Mono) for
the UI, and `--jv-font-display` (Space Grotesk) for figures. Labels are uppercase with
generous tracking (`--jv-track-label`, `--jv-track-control`, `--jv-track-logo` for the
wordmark).

The size scale runs from `--jv-fs-2xs` to `--jv-fs-display`; `tokens.test.ts` fails any
size token that reads smaller than its floor, because the old scale once bottomed out at
under nine pixels and that was the size of every pill, entity id and caption in the
console.

On top of that, a reader's own multiplier: `textSize.ts` writes one percentage onto the
root element (four named steps: compact, standard, large, largest), which moves every
`rem` together. It is a percentage rather than a pixel size on purpose — it multiplies
whatever the browser is already set to instead of overriding it — and it is stored per
browser (`localStorage`, key `jarvis.textSize`), because the right size for a phone in a
hallway and for a monitor on a desk are different answers to the same question.

Light is glow rather than a box: `--jv-glow-sm|md|lg` for lit things, `--jv-elev-panel` /
`--jv-elev-float` for things that sit above the page, and `--jv-bracket` for the corner
bracket.

### The tab icon

The v2 core, in the browser tab — the same core as the one on the stage, not a
second drawing of it. `scripts/gen-icons.py` (repo root) renders every icon
Jarvis ships from `tests/contracts/core_frames.json` (the numbers `engine.ts`
draws): this tree's favicon, apple-touch and PWA rasters, the phone's
adaptive-icon layers and persona foregrounds, the desktop set and the Tauri
bundle's `.ico`/`.icns`. Before it there were four hand-drawn reactors, one of
them this tree's own icons script (since deleted), and none of them agreed on
how many rings there were; until T-0808 it drew the Reactor II instrument the
stage no longer shows.

```
npm run icons                        # rewrite every icon from the geometry
python3 ../scripts/gen-icons.py --check   # fail, by name, on any stale file
python3 ../scripts/gen-icons.py --sheet /tmp/icons.png   # a contact sheet to look at
```

The icon is the core at rest: the three rings in one moment of their motion,
the particle shell round them and the lit heart. At 64 px and under (and in
`favicon.svg`, which a browser only draws at tab size) it is a reduced tier:
the rings as solid faces on a dark rim, scaled up to fill the plate, and the
heart. `src/lib/icons.test.ts` checks the shipped files are icons a browser can
use — a real PNG, a lit core on a dark plate down to 16 px, linked from
`app.html`; `--check` is what proves they are the geometry's.

The generated files are committed rather than built, so `vite dev` has a favicon
too. Note that `mrmime` — which sirv, and therefore both `vite dev` and
adapter-node, type static files with — has no entry for `.ico`; the dev plugin in
`vite.config.ts` and the launcher in `scripts/postbuild.mjs` each set
`image/x-icon` before sirv runs, which sirv then keeps.

### QR codes

`src/lib/qr.ts` is a complete QR encoder — byte mode, versions 1-40, all four
error-correction levels — with no dependencies. It exists because the CSP
(`script-src: self`, `img-src: self data:`) blocks a CDN library and a remote QR
image alike, and rendering a pairing code server-side would send the secret
through an extra hop for nothing.

A QR encoder cannot be checked by eye: wrong output still looks like a QR code.
So it is verified against OpenCV, which shares no code with it:

```
pip install opencv-python-headless numpy
node --experimental-strip-types tools/qr_fixtures.mjs | python3 tools/qr_roundtrip.py
```

That established three things, recorded in `qr.test.ts`: 165 symbols round-trip
through OpenCV's *detector* (below the 73-module ceiling that detector was
measured to have — past it, it cannot read its own encoder's output); pinned to
the same mask, the two encoders agree byte for byte at versions 2, 7, 9, 12, 14,
33 and 40; and all 160 (version, level) byte capacities match. CI runs the cheap
consequences of that — the capacity table, the structural checks and golden
digests — and needs no OpenCV.

## Motion

Fast, consistent, and always optional. Durations and easings are tokens
(`--jv-dur-*`, `--jv-ease-*` in `tokens.css`), and `src/lib/motion.ts` reads them for
JavaScript (`DURATION`, `EASE`) so a token change is the only change. It also holds the
list stagger: rows start `--jv-stagger-step` apart, **capped at `--jv-stagger-cap`**
(320 ms), so 200 rows cost 320 ms, not five seconds.

**`prefers-reduced-motion: reduce` turns all of it off.** `base.css` sets every animation
and transition to none, and `motion.ts:prefersReducedMotion()` is what JavaScript asks.
**The core obeys it too**, which took JavaScript: a `requestAnimationFrame` loop is not an
animation as far as the cascade is concerned, so the largest and brightest moving object in
the app was for a while the one thing that ignored the setting. Under reduced motion it
stands still and draws one frame per change (`src/lib/ds/core/`), and
`motion.ts:watchReducedMotion()` picks up a change to the preference without a reload.
`prefersReducedMotion` defaults to **true** when there is no `matchMedia` to ask (SSR),
because guessing "animate" and being wrong is the failure that actually costs someone.

## Console: keyboard and status

The v2 shell's keys are ⌘K (the palette, `ds/shell/Palette.svelte`) and Tab, which reaches
every control with a visible focus ring.

`src/lib/consoleLink.ts` is the console's own socket, and the only one in the app that
reconnects (pages deliberately do not: a page that lost its socket also lost its
subscriptions, and silently reattaching would leave stale rows looking live). It backs off
exponentially with jitter and reports `offline` after three consecutive failures.

## Accessibility

- A visible focus ring on everything focusable — an `outline`, not a
  `box-shadow`, because half these controls already carry a glow and a
  shadow-based ring loses to it.
- `aria-live="polite"` on the subtitles, the conversation and the steps; `role="alert"`
  on failures.
- The command palette is a combobox/listbox with `aria-activedescendant`.
- Colour contrast passes AA for text, asserted numerically in `tokens.test.ts` and against
  the rendered backdrops in `e2e/a11y-contrast.spec.ts`.
- `e2e/a11y-axe.spec.ts`, `a11y-keyboard.spec.ts` and `a11y-preferences.spec.ts` cover axe
  on every screen, keyboard reach and focus return, and the reduced-motion and
  reduced-transparency preferences.

## Secure context / mkcert

`getUserMedia` requires a secure context:

- `http://localhost:8199` works out of the box (localhost is always a secure
  context).
- Accessing via a LAN IP (`http://<lan-address>:8199`) will **not** get mic
  access. Either generate a locally-trusted cert with
  [mkcert](https://github.com/FiloSottile/mkcert) (`mkcert -install && mkcert
  jarvis.lan 192.168.x.y`) and terminate TLS in front of the app (Caddy,
  Traefik, nginx), or reach the box over WireGuard using a hostname signed by
  your local CA.

## Barge-in

The mic stays open while Jarvis speaks. The reply's first moments teach what Jarvis
sounds like through the speakers; after that a level well above that echo, held briefly,
is a person cutting in, and the reply stops (`src/lib/hud/bargeIn.ts`, the same rule as
the phone's `BargeIn.kt`, both bound by `tests/contracts/barge-in.json`). Someone who
talks no louder than the speaker is heard as echo: they wait, or press.

## Run

```sh
npm install
npm run dev          # dev server (includes the /ws proxy)
npm run build        # vite build + install ws-proxy launcher into build/
AURA_URL=http://jarvis-core:8123 AURA_TOKEN=... PORT=8199 node build

# against Home Assistant instead
AURA_BACKEND=ha HA_URL=... HA_TOKEN=... PORT=8199 node build
```

Health check: `wget -qO- http://localhost:8199/healthz` → `{"status":"ok"}`.

Docker: `docker build -t jarvis-web .` — multi-stage, listens on 8199,
`CMD ["node","build"]`.

## Tests

```sh
npm run check                        # svelte-check: types across .ts AND .svelte
npm test                             # the same check, then the vitest unit tests
npm run build && npx playwright test # e2e: the built app + mock backend + fake mic in chromium
```

`jarvis-web/CLAUDE.md` has the one-file forms (`npx vitest run <file>`,
`E2E_PORT=8320 npx playwright test e2e/v2-shell.spec.ts`) and the budgets.

- Playwright serves `build/`, so build first. `playwright.config.ts` starts
  `../tests/web/serve-e2e.mjs`, which runs the built server against the mock backend,
  and every spec's browser starts signed in and muted.
- `../tests/web/mock-ha.mjs` implements the shared contract (auth handshake, the
  management commands `get_states`, `get_services`, `get_config`, `call_service` and
  `subscribe_events` / `unsubscribe_events`, the registries) and `mock-hermes.mjs` stands
  in for the adapter. A new server payload key must be added there too.
- `serve-e2e.mjs` points `AURA_URL` at the mock and `HA_URL` at a dead port, so the whole
  e2e suite doubles as a precedence check on `resolveBackend`.
- Playwright finds its own chromium (set `PLAYWRIGHT_BROWSERS_PATH` for a preinstalled
  one) and runs it with fake media-device flags and `e2e/fixtures/open-mic.wav` as the
  microphone.
- `src/lib/server/backend.test.ts` covers the `/api/tts` allow-list, plain and
  percent-encoded traversal included.
- `npm test` runs `svelte-check` first: markup and component props are the one part of
  this app that vitest does not type-check.
- `src/lib/ds/ssr.test.ts` server-renders every `$lib/ds` export; a component that
  touches `window` or `document` outside an effect takes down the first paint only in
  production.

## Audio path

- Capture: `getUserMedia` with the echo canceller, noise suppressor and gain control
  stated (`VOICE_CAPTURE`), as 16 kHz mono 16-bit PCM and WAV (`src/lib/audio/pcm.ts`) --
  PCM because the adapter's speaker gate must hear the same audio the transcriber does.
- Playback: Jarvis's voice streamed as 16-bit PCM (`src/lib/audio/playback.ts`, a
  `PcmStream`), each piece scheduled where the last ends so the line starts before its
  end exists, through an `AnalyserNode` that drives the core; a barge-in stops it at once.
  `/api/tts` remains the whole-line fallback.
