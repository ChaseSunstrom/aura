---
name: jarvis-design-system
description: The JARVIS v2 design system (2026-11-08) — projected light around the core, the `$lib/ds` package every console screen draws from, the token source of truth, the generated files every surface consumes, the token lint and the ds boundary, and the four screen states every screen must implement. Use before writing or changing any UI on the web console, the desktop app or the Android app, and whenever a colour, size, font, radius, shadow or duration is about to be typed.
---

# Jarvis design system — v2

One source of truth, generated everywhere, linted in `make token-lint` and
`make ds-boundary`. The direction is the JARVIS v2 brief
(`docs/design/v2/PROMPT.md`; the core's reference is
`docs/design/v2/reference/orb_engine.js`). It replaced the Build Brief's
panels (the Build Brief and the boards beside it), which the
legacy screens still draw until the shell lane swaps them out. These are
rules, not preferences.

**The package.** `aura-web/src/lib/ds` is the one place a console screen
takes its UI from: `Projection`, `Label`, `Figure`, `Connector`, `LeaderLine`,
`Subtitles`, `PresenceStrip`, `HoldButton`, `RuleChip`, the `Core` (JARVIS
itself, nine states) and the stage machine that moves between scenes. Its
`README.md` lists every prop, event, scene and state; `/gallery/core` shows
them. Import from `$lib/ds` only: `scripts/verify/ds_boundary.py` fails a
screen outside the package that imports another `.svelte` component or types a
font family or a colour (its baseline is a ratchet, like token-lint's). The
console is also the desktop app, the desktop popup and the phone's home, so
this one package is what every surface looks like.

## 1. Where a value is typed, and where it is not

- **`design/tokens.json`** is the only file in the repository where a colour,
  size, font, radius, shadow or duration is typed by a human. DTCG format:
  groups of `{ "$type", "$value", "$description" }`; `"{color.focus}"` is an
  alias; `"{color.text@0.06}"` is that colour at an alpha (a glass edge).
- **`python3 design/build.py`** regenerates every consumer. Each output carries
  `@generated from design/tokens.json — DO NOT EDIT`; editing one is a bug the
  next `--check` reports:
  - `aura-web/src/lib/styles/tokens.css` — `--jv-*` custom properties
  - `aura-web/src/lib/tokens.ts` — `TOKENS`, `token()`, `cssVar()`, `tokenMs()`, `STATE_ACCENT` (the eleven reactor states)
  - `aura-desktop/aura_desktop/tokens.py` — `TOKENS` + the named palette `theme.py` imports
  - `android-app/…/ui/theme/JarvisTokens.kt` — `JarvisTokens.Color/Type/Weight/Space/Radius/Motion/Material/Depth/Light/Spring`; `JarvisUi` aliases them
  - `android-app/…/ui/theme/JarvisTheme.kt` — the Compose `MaterialTheme`, its typography on the bundled `res/font` faces
  - `android-app/app/src/main/res/values/tokens.xml` + `colors.xml` (aliases only)
  - `design/generated/console-theme.json` — the terminal's palette (with 256/16-colour fallbacks), type roles, border sets and reactor glyphs
- **`python3 design/build.py --check`** fails if any output is stale. (The reactor's
  contract is `tests/contracts/reactor.json`, the eleven states, bound by each surface's
  own tests; the old SVG geometry contract went with Reactor II.) CI runs it as
  `tokens-sync`; `make tokens` regenerates. Fonts are vendored by
  `design/fonts.py` (`make fonts`) from pinned upstream commits — no font CDN, ever.
- **`python3 scripts/verify/token_lint.py`** fails on any hard-coded colour,
  spacing, type or motion value in app code (web `.svelte/.css/.ts`, Android
  `.kt`, desktop `.py`). `design/token-lint.baseline.json` is a ratchet: legacy
  files may keep their counted hits until their milestone clears them; a new
  file or a grown count fails. Never edit the baseline to make a check pass —
  `--update-baseline` is for a milestone that legitimately removed hits.
  Documented exceptions (the GLSL orb, its renderer, `SiriPalette.kt`, the QR
  encoder) are listed in the baseline with the reason.

Adding a token: add the leaf to `tokens.json` with a `$description` that says
what it is *for*, run `design/build.py`, use the generated name. Never add a
token for one use — use the closest existing step; if none fits, the scale is
wrong and that is a conversation, not a new number.

## 2. The vocabulary

Names are the console's `--jv-*` set (`design/tokens.json` `$meta.naming`):

| Need | Token | Notes |
|---|---|---|
| Ground | `--jv-bg` (#010409) | the deep ground; `--jv-stage-ground` is the v2 stage's radial (`--jv-core-glow` #062236 at the core, `--jv-core-haze`, then the ground); `--jv-bg-raised` one step up |
| Projection (v2) | `--jv-projection-backing` (a dark radial), `--jv-projection-tint`, `--jv-projection-bleed`, `--jv-projection-bracket` (16px), `--jv-glow-bracket`, `--jv-projection-tilt`/`-perspective` | what `Projection` draws: content is light on a dark radial, one cyan bracket top-left, an edge line where it is pinned. Use the component, not the tokens by hand |
| Core (v2) | `--jv-core-{haze,speak,work,hot,white,spark,halo,face}`, `--jv-core-width`, `--jv-strip-width` | the core's own light, read by both its renderers; never for anything else |
| Panel (legacy) | `--jv-material-panel-solid` (rgba(4,11,19,.78)) | `border: 1px solid var(--jv-material-panel-edge)` (the accent at .14), `border-radius: var(--jv-material-panel-radius)` (3px), and **cyan corner brackets** top-left and bottom-right, `--jv-bracket` (12px) arms in `--jv-accent` — `.jv-glass[data-grade='panel']` draws them with `::before`/`::after`. No backdrop blur (DECISIONS #6). `--jv-panel` is the solid fallback |
| Raised control / row | `--jv-surface-2` | hovered row, active segment |
| Inset | `--jv-surface-sunken` | diffs, console output |
| Input ground | `--jv-field` | |
| The accent | `--jv-accent` | **spent, not spread**: the current step, the live value, the active tab underline, the one primary control per screen |
| Accent at rest | `--jv-accent-deep` | idle orb, iris arcs, a secondary rule |
| Ink on accent | `--jv-accent-ink` | text on a filled control, never on the ground |
| Tint / light | `--jv-wash`, `--jv-wash-strong` / `--jv-glow` | glow is budgeted: reactor core, current step, push-to-talk ring |
| Semantic | `--jv-ok` green #5FE3A1 · `--jv-warn` amber #FFB547 (warnings, needs-you) · `--jv-danger` pink #FF6B8B (errors, security) · `--jv-danger-text` · `--jv-violet` #9B8CFF (memory, embeddings, a second series) | semantic colour is not the accent |
| Reactor states | `--jv-reactor-{idle,listening,thinking,speaking,tool,goal,attention,warming,alert,kill,offline}` — the state colour C, each an alias of a base colour; offline is amber (the brief's offline ring); `--jv-warming` (a cold start) | `STATE_ACCENT` in tokens.ts; `amber`/`gold` and the `orb.*` blob palettes stay retired as names |
| Text | `--jv-text` = `--jv-text-bright` #F2FBFF · `--jv-text-dim` #A9C8D8 (secondary, the user's subtitle) · `--jv-text-faint` #8DB4CA (labels) · `--jv-glow-text`, `--jv-glow-subtitle` (text is light: it glows) | all clear AA on the ground |
| Marks | `--jv-tick` | ticks, dashed coils, decorative dots — **never text** (below AA on purpose) |
| Rules | `--jv-line` (edge) · `--jv-line-soft` (divider) · `--jv-line-hair` (between rows) | |
| Type | `--jv-font-body` = `--jv-font-chrome` (JetBrains Mono — the UI, labels, data) · `--jv-font-display` (Space Grotesk, at `--jv-weight-display` 300 — big numbers, the wordmark) | sizes `--jv-fs-2xs…display` (2xs = 11px, the label size; floor 11px), weights 300/400/500/600; **labels are UPPERCASE with `--jv-track-label` (.22em)**, buttons/chips/status words UPPERCASE with `--jv-track-control` (.14em); running text is never tracked; `--jv-track-logo` the wordmark |
| Space | `--jv-space-1…7` | `0` and a `1px` hairline are the only literals allowed |
| Radius | `--jv-radius-sm/md/lg/pill` | `md` (3px) panes, panels, dialogs; `sm` (2px) buttons and inputs; `lg` (4px) the dock and rail buttons; `pill` chips (= `--jv-material-*-radius`) |
| Chrome | `--jv-bracket`, `--jv-tilt`/`--jv-tilt-boot`/`--jv-perspective` (side panels turn 10° toward the core, boot from 55°), `--jv-rail-open`/`--jv-rail-closed` (216/68px), `--jv-touch` (44px) | |
| Elevation | `--jv-elev-panel/float`, `--jv-glow-sm/md/lg` | |
| Motion (v2) | `--jv-dur-{appear,exit,bar,row-in,connector,beat,shock,kick,fade,unroll,hold}`, `--jv-ease-{appear,exit,quick,unroll}`, `--jv-stagger-project` (35ms rows) | read through `MS` in `$lib/ds/motion.ts`, never as numbers |
| Motion (legacy) | `--jv-dur-fast` (hover) · `--jv-dur-base` · `--jv-dur-scan` (section scan-in) · `--jv-dur-unfold` (detail card) · `--jv-dur-boot` (panel swing) · `--jv-dur-toast` (timer line) · `--jv-ease-standard`/`-out` = cubic-bezier(.2,.8,.2,1), `--jv-ease-scan` · `--jv-stagger-row/row-cap` (70ms rows) · `--jv-stagger-step/cap` (boot, lists) · `--jv-rx-*` | everything collapses under `prefers-reduced-motion`; animations run only while visible |
| Material | `--jv-material-{pane,panel,control}-{solid,edge,radius}` (+ `edge-hover`), `--jv-emission-*` (what `.jv-glass` reads) | translucent fills at an alpha of a palette colour, accent edges; no blur; `--jv-scrim` is the text scrim |
| Depth | none | §6 is order, not optics: `data-depth` 3 and 4 take a stronger `--jv-line*` edge, and nothing scales, blurs or shifts. The `--jv-depth-*` table was deleted with the glass |
| Light | `--jv-light-halo-<state>`, `--jv-light-halo-{extent,speaking-peak,warming-peak}` | the reactor's own core brightness, read by its spec on the web and the phone. The rim, shadow and mix that lit panels from it are gone |
| Springs | `--jv-spring-{glass,snap,control}-{stiffness,damping,mass,ratio,settle}` | §8.1; `ratio` is derived by build.py for Compose's `spring(dampingRatio)` — never typed |

Android: `JarvisUi.Type.*` (sp) and `JarvisUi.Space.*` (dp) are the phone's
scales; `JarvisTokens.Color.*` the palette; Compose uses `JarvisTheme { }`.
Desktop: `from .tokens import BG, ACCENT, …`.

## 3. The look, in rules (v2)

- **The core is the screen.** JARVIS is the core in the middle of a deep
  ground; everything else is light it projects. Its nine states (idle,
  listening, speaking, thinking, embedding, acting, music, alert, working)
  say what it is doing; a reactor state maps to one through
  `coreStateFor`. Scenes move by the stage machine: the old scene leaves into
  the core, then the new one comes out of it.
- **Content is projected light, never a card.** A dark radial backing, one
  cyan bracket at the top-left, glowing text, and an edge line only where it
  is pinned to an edge (`Projection`). Never cards, frosted glass, backdrop
  blur, filled panels or big radii. A line from the core says what a
  projection is about (`LeaderLine`; amber while it waits on a person).
- **Colour is meaning.** Cyan (`--jv-accent`) is JARVIS. Violet is memory,
  embeddings and models. Amber is waiting on a person. Green is ok, pink is
  danger. Nothing else is coloured.
- **Type.** Labels are JetBrains Mono, UPPERCASE, tracked .22em
  (`--jv-track-label`), 11px, in `--jv-text-faint` (`Label`). Figures and
  display type are Space Grotesk Light (`--jv-font-display`, weight 300;
  `Figure`). Running text is never tracked.
- **Confirm by holding.** Something irreversible is a `HoldButton` (about
  2.4s), not a dialog.

The legacy screens (until the shell lane replaces them) follow the Build
Brief's rules:

- Near-black ground with a radial glow behind the core, cyan as the accent,
  amber / violet / green / pink as the four meanings (needs-you, memory, ok,
  error). Panels are translucent with a 1px cyan edge and cyan corner brackets;
  the only filled cyan control on a screen is its primary action (APPROVE).
- Everything is JetBrains Mono. Labels — a panel's name, a section caption, a
  readout key, a dock caption — are UPPERCASE, 11px, tracked `--jv-track-label`,
  in `--jv-text-faint`, often led by a two-digit accent number (`01 MODELS`).
  Big numbers are Space Grotesk Light (`--jv-font-display`, weight 300).
- Navigation is a left rail of the eight sections (Models, Notifications,
  Routines, Jobs, House, Telemetry, Integrations, Work), each with an icon, a
  label, `⌥1–8` and a badge; it collapses to icons. Nothing hides behind a
  "+N" overflow: a strip that does not fit scrolls.
- Motion (the boards' CSS): boot — the core scales in from blurred, side panels
  swing in rotateY 55°→10°; section switch — a bright sweep line runs top to
  bottom while `clip-path` reveals the content, then rows slide in 70ms apart;
  detail — a card unfolds from a line to full width to full height, bars grow
  from the baseline; toasts turn in with a timer line; the security alert's
  edge pulses pink. Easing is cubic-bezier(.2,.8,.2,1). Everything collapses
  under reduced motion and stops while hidden.
- Density comes from information: hairline rows (`--jv-line-hair`), tabular
  numerals wherever digits line up, a sparkline beside a figure.

## 4. The four screen states — every screen, no exceptions

Every routed screen implements **loading, empty, error, offline**, each as a
real moment on the design system — never a blank, never a toast with a log line:

- **loading** — `Skeleton` rows in the shape of the content, `role="status"`.
- **empty** — a title that says what would be here and one sentence on how it
  gets here (`No tasks have run today. Ask Jarvis for something, or schedule one.`).
- **error** — what went wrong and what to do (`Couldn't load tasks. The backend
  answered 500. Retry, or check docker compose logs jarvis-core.`), `role="alert"`, a Retry.
- **offline** — the link is down, reconnecting, and what you see is the last
  known state; a Reconnect now.

`$lib/ds/ui/State.svelte` says all four so a screen cannot forget one;
`scripts/verify/web_states_check.py` and the v2 specs (`e2e/v2-*.spec.ts`)
enforce it. Every visible control does something (`web_dead_controls.mjs`);
every screen holds at 360/768/1024/1440 with no horizontal overflow.

## 5. Before you finish any UI change

```bash
python3 design/build.py --check           # generated files current, orb palette not drifted
python3 scripts/verify/token_lint.py      # no new hard-coded value, no grown count
python3 scripts/verify/tokens_check.py    # every surface's token file matches design/tokens.json
python3 scripts/verify/ds_boundary.py     # console screens draw from $lib/ds, no typed font or colour
```

`make token-lint`, `make tokens-check` and `make ds-boundary` run them. The core and the
package's components are on `/gallery/core` (`e2e/ds-core.spec.ts`). The token reference is `/gallery/tokens` in
the console (`aura-web/src/routes/gallery/tokens/+page.svelte`) and every
generative-UI type in every state is on `/gallery/projections`; a new token or component appears
there in the same change.
