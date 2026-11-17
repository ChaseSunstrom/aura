# `$lib/ds` — the JARVIS v2 design system

The one place a console screen takes its UI from. A screen imports from `$lib/ds` (or a file
under it) and styles with `--jv-*` tokens; `scripts/verify/ds_boundary.py` (`make ds-boundary`,
CI's `tokens-sync` job) fails a screen outside this folder that imports another `.svelte`
component or types a font family or a colour. Its ratchet,
`scripts/verify/ds_boundary.baseline.json`, has been empty since T-0611 deleted the legacy UI
(`--require-empty` holds it there).

The console is built from this folder: `shell/` (the shell round the core, its stage, the
approval card, ⌘K, the Devices sheet, the desktop popup), `screens/` (every full screen the
launcher opens, the car's display, the lock, first run and enrolment, and `Markdown`, prose
JARVIS wrote) and `ui/` (the button, field, segmented choice, switch, sheet, gate and the four states a
screen has besides its content). `shell/routes.ts` maps a path to the core or a screen;
`drag.ts` is the stage's `use:draggable` and `shell/tiling.ts` its tiles (a panel dropped locks
into the nearest of the scene's slots, swapping with the one there, kept per scene on that device,
`e2e/stage-tiling.spec.ts`); `$lib/hud` is the logic underneath (`src/lib/hud/README.md`).

A screen never replaces the core (T-0725): the shell keeps the stage mounted and the screen
slides in beside it as a panel, from the inline end (`--jv-measure-panel` wide) or up from the
bottom on a phone (`--jv-measure-panel-rise` tall), while the core glides into the space left
by translate alone; Escape, Back and "Back to the core" slide it out. On a phone every menu that rises (a screen's panel and every sheet) has one frame: the grab handle (`ui/Grab.svelte`), a cap with its name and a cross, the sheet's ground and the projections' corner; dragging the handle down past a quarter of the menu's height, or flicking it, closes it (`ui/drag.ts`, `dismisses`), and the core shrinks as it travels to the top (`--aside-scale`) where it takes most of its box (`--core-share`) and is drawn at up to 3x. Every sheet (`ui/Sheet.svelte`:
the launcher, ⌘K, Devices, enrolment) is a drawer on the same measure and motion, and the core
moves aside for it too. Beside a panel the stage sizes the core and its strip to its own box
(`--u-w`/`--u-h`, container units) instead of the window; on a phone it keeps the core alone.
Reduced motion makes every move instant. `e2e/side-panel.spec.ts` holds it at 390, 1024 and
1440.

```svelte
<script lang="ts">
	import { Core, Projection, Label, Figure, coreStateFor } from '$lib/ds';
</script>
```

The look, in one paragraph: content is **projected light**, not a card. A projection is a dark
radial backing with one cyan bracket at its top-left, glowing text and, where it is pinned to an
edge, one edge line. Labels are JetBrains Mono, uppercase, tracking .22em; figures are Space
Grotesk Light. Cyan (`accent`) is JARVIS; violet is memory, embeddings and models; amber (`warn`)
is waiting on a person. No frosted glass, no big radii, no boxes. The direction is the v2 brief
(`docs/design/v2/PROMPT.md`, with the core's reference `docs/design/v2/reference/orb_engine.js`);
the skill `.claude/skills/jarvis-design-system/SKILL.md` has the rules.

Proof: `/gallery/core` shows every state and component; `e2e/ds-core.spec.ts` drives it
(`E2E_PORT=8323 npx playwright test e2e/ds-core.spec.ts`; `CORE_SCREENS=1` rewrites the
screenshots in `docs/screens/v2/core/`). Unit tests: `stage.test.ts`, `lines.test.ts`,
`core/engine.test.ts`, `ssr.test.ts` (every export renders on the server).

## Components

Every component takes `testid?` (a `data-testid`). The ones that animate in and out take
`leaving?` (play the exit: collapse, retract, fold) and `from?` (the presence the animation
starts at, from the stage's `view().from`, so an interrupted exit turns round instead of
restarting). Points are `[x, y]` in **percent of the stage**.

| Component | Props | Events | What it is |
|---|---|---|---|
| `Projection` | `core?: {x, y}` (viewport px the projection grows out of; omitted: grows in place), `edge?: 'top'\|'right'\|'bottom'\|'left'` (draws the edge line on that side), `label?` (its accessible name), `leaving?`, `from?`, `children` | — | The container for anything JARVIS shows: dark radial backing, cyan bracket top-left, glow. It grows out of the core and collapses back into it. |
| `Label` | `tone?: Tone` (default `faint`), `children` | — | The small caption: JetBrains Mono, uppercase, .22em. `violet` for memory/embeddings/models, `warn` while something waits. |
| `Figure` | `value: string \| number`, `unit?`, `size?: 'md' \| 'lg' \| 'display'`, `tone?: Tone` | — | A number or short value in Space Grotesk Light; the unit rides small beside it. |
| `Connector` | `start: Point`, `end: Point`, `beat?: number`, `leaving?`, `from?` | — | A curved line between two points that draws itself in (~0.5 s). Each change of `beat` sends one pulse along it; `0` sends none. Wire it to `Core`'s `onbeat`. |
| `LeaderLine` | `points: readonly Point[]`, `waiting?: boolean`, `leaving?`, `from?` | — | A thin line from the core (first point) to the projection it is about (last point). Fewer than two clean points draws nothing. `waiting` turns it amber: an approval waits at its end. |
| `Subtitles` | `text: string`, `speaker?: 'jarvis' \| 'user'` | — | What is being said. JARVIS bright, the user dim. A new line fades in; a line that grows (streaming) does not fade again. |
| `Conversation` | `turns: readonly ChatTurn[]`, `running?`, `heard?` (what the mic hears, not yet sent), `persona?` (default `JARVIS`), `testid?` | — | The stage's conversation: the session's last 12 turns as a projection, YOU dim and JARVIS bright in Markdown, following the newest unless the reader scrolls back. A streaming reply is drawn to its last whole word with its open `**`/backtick closed, and only the words that just arrived fade and rise in; a running turn with no words types. Reduced motion: no movement. |
| `PresenceStrip` | `task: string`, `device?: string`, `steps: readonly {label, confirm?}[]`, `current: number` (`steps.length` = all done), `leaving?`, `from?` | `ontakeover?()` — Esc or the button; absent: no take-over offered | The working strip: what JARVIS is doing on which device, step by step. Unrolls from the core; a `confirm` step is marked as needing a person. |
| `HoldButton` | `label: string`, `tone?: 'warn' \| 'danger' \| 'accent'`, `disabled?` | `onconfirm()` — after a full hold (~2.4 s, `MS.hold`) | Press and hold to confirm something irreversible. Letting go, sliding off or losing focus cancels; keyboard holds Space/Enter. |
| `RuleChip` | `text: string`, `kind?: string`, `tone?: Tone`, `removeLabel?` (default "Remove <text>") | `onremove?()` — a 44 px remove target; absent: no remove button | One rule JARVIS keeps (a standing approval, an automation, a preference). |
| `Core` | see [The core](#the-core) | `onbeat?(beats)` | JARVIS itself. |
| `Starfield` | `twinkle?` (default true) | — | The ground of a pack whose `background.type` is `starfield` (AURA): its nebulae as `--jv-theme-backdrop` and a seeded field of stars in the text colour, redrawn ~10×/s; hidden tab or reduced motion: one still frame. The shell draws it behind everything. |
| `ThemeTile` | `pack: Pack`, `worn?`, `note?`, `testid?`, `children?` (its actions) | — | One theme as a tile, in its own colours: the core's hue, the name, the swatches. Settings' gallery and the shell's drafted-theme card. |

Themes (`theme.svelte.ts`, `pack.ts`): the console wears the theme the house syncs (the primary persona's, or a drafted pack from `theme.apply`, re-fetched from `/api/themes/<id>` after a reload) unless this device keeps its own (`jv.theme`, Settings). A change dissolves (a view transition) and tweens every colour token for `--jv-dur-pulse`; the favicon follows the worn theme. `theme.preview` shows a drafted pack as a card, never worn until it is applied. Settings' Voice is a three-voice A/B per theme (`voices.json`), stored on this device as `jv.voice`.
| `Projected` | `type: string`, `data: unknown` (undefined: loading), `placement?: {core?, edge?, leaving?, from?}` | — | One `hud.projection`, drawn as its typed visual on a `Projection`, with loading, empty and error states. The 19 types and the sandboxed `custom_html` are in [`gen/README.md`](gen/README.md). |

`activity/Steps.svelte` is the stage's steps projection (imported by `shell/Stage.svelte`, not
exported from the package): `steps: readonly LiveStep[]`, `onstop?()` (absent: no Stop). Its
data is `liveSteps(hud)` in `activity/live.ts`, one pure function over the HUD state: each tool
call a step (thinking, acting, waiting on you, done, failed) with its kind's detail (the
browser's latest frame on the newest browse step, a search's sources, a command's output tail,
the state a device was left in, else a capped argument line for a tool with no words of its
own). The current step opens to its detail, older ones keep one line, six are drawn. Fixtures
are on `/gallery/steps`; `e2e/activity.spec.ts` drives mock-hermes's browse turn (T-0732).

`board/` is the Board screen's (`/board`, T-0733) drawing: `Board.svelte` (`panels: Panel[]`)
lays out six `Panel`s (one column under 700 px, two under 1200, three above, within
`--jv-measure-wide`), each a mechanism driven by its own number: `Flows` (kinds of task to
models, packets as fast as the route is busy), `Hours` (an hour a bar by model), `Cells` (a
cell a turn by how it ended; a cell a bench task by when it last passed), `Dial` (a minute a
bar, a hand turning at the call rate), `Tanks` (a card's memory by who holds it, a surface that
moves only while a model loads). `model.ts` is the pure mapping from GET /api/board and the
capability rows (`boardPanels`, `cellsOf`), with each panel's state: stale with its age,
unavailable with the reason. `clock.ts` is the one frame loop every moving part shares; it runs
only while a mechanism is on screen, the page is visible and motion is allowed. Fixtures are on
`/gallery/board`; `e2e/board.spec.ts` drives mock-hermes's `/api/board` in each state.

`screens/Models.svelte` is the Models screen (`/models`, T-1254): the house's models, their
downloads and each card's budget, with load, unload and delete (both behind a `HoldButton`),
settings, a Hugging Face search and the add form (vLLM, or llama.cpp with its one .gguf). It
holds no logic of its own: `hud/data/models.ts` maps the manager's answers and builds every
body (`modelsLook` picks its state), `hud/models.svelte.ts` is the calls through
`/hermes/api/models`. `e2e/models.spec.ts` drives mock-hermes's `/api/models` in each state.

`screens/Approvals.svelte` also lists the settings plugin's waiting proposals (T-1316): one card
per proposal with the value now and the value it becomes, Apply (`settingsApply`) and Discard
(`settingsDiscard`). `screens/settings-proposals.ts` is the pure part: `proposalCards` (a secret,
or a key the schema does not name, carries neither value) and `proposalsQueue` (one answer on
the wire at a time; the card leaves on the plugin's answer). The real plugin proposes only for a
security setting written from an app's `jsettings set`; a change asked in words takes the
approval card instead. `e2e/v2-settings-proposals.spec.ts` drives it against mock-hermes.

Above them it lists what waits to be remembered (T-1503, the `memory_propose` queue): one card
per proposal with the fact whole, where it would be stored, who proposed it and what that turn
had read, Drop (`memoryProposalDrop`) and Keep. Keep stores nothing: it hands the shell's `ask`
one sentence naming the id (`keepSentence`), so the keep is the `memory_proposals` call the
gate asks about, and the yes is said on the approval card under Waiting. `screens/memory-proposals.ts`
is the pure part: `memoryCards` (a row whose id is not the plugin's shape is dropped, since the
id goes into that sentence) and `memoryQueue`. `e2e/v2-memory-proposals.spec.ts` drives it
against mock-hermes.

That approval card shows the same two values (T-1317): the guard writes `Setting:` / `Now:` /
`Becomes:` directly before its card lines, `hud/approvalCard.ts` `parseApprovalChange` reads
them for a settings tool only, `shell/approval.ts` puts them on the ask as `change` (neither
value for a secret: `secretChange`), and `screens/ChangeRows.svelte` draws them on both cards.
`e2e/v2-settings-approval.spec.ts` is the browser check.

A change that was made has a way back (T-0700). On `settings.changed` the shell offers
"CHANGED <key> · UNDO" under the top bar for `MS.undo` (the `motion.dur.undo` token, five
seconds; held while the pointer or the focus is on it), and `screens/RecentChanges.svelte` is the
last section of Settings: the plugin's log (`settingsHistory`), newest first, each row saying
when, who, the key and both values through `ChangeRows` with `past` ("From" / "To"), three rows
before "Show all", and Put back on the newest change of a key only, never on a secret. Both
controls call the shell's `undoSetting` (`settingsUndo`), which says the outcome in the status
line: `undoSaid` says "waiting for your OK under Approvals" when the plugin answers with a
proposal, as it does for a security setting. The write an undo makes is not offered an Undo, and
neither is a secret. `changeRows` and `undoSaid` are in `settings-proposals.ts`;
`e2e/v2-settings-undo.spec.ts` is the browser check at 1440 and 390. Does not cover: a change to
the house's own configuration (jarvis-core), which is not in this log; more than one level back.

## The stage (`stage.ts`)

A pure state machine — no timers, no DOM. The host passes the time in (`performance.now()` in a
frame loop, a number in a test) and renders what `view()` returns.

**Scenes** (`SCENES`) and the core's pose in each (`POSES`; `y` is the core's centre as a fraction
of the stage height):

| Scene | Core `y` | Core `scale` | Core state |
|---|---|---|---|
| `idle` | .44 | 1 | `idle` |
| `listening` | .44 | 1.06 | `listening` |
| `answer` | .44 | .8 | `speaking` |
| `thinking` | .44 | .9 | `thinking` |
| `music` | .44 | .92 | `music` |
| `alert` | .44 | .82 | `alert` |
| `working` | .17 | .34 | `working` (lifted into the presence strip) |

The shell (`shell/scene.ts`) also holds `answer` at rest, with an `idle` core, while something a
turn drew is still out: for `VISUAL_HOLD_MS` after the newest `hud.projection` frame, until
Escape, or until the next turn. Without it a list left the stage with the last spoken word,
and with speech off there was no answer scene at all (T-1472).

**API.**

- `createStage(scene, now?, timings?)` — a stage showing `scene`, still.
- `go(stage, target, now)` — head for `target`. Returns the same object when nothing changes
  (so `$state` does not re-render).
- `view(stage, now)` — `{scene, target, phase, leaving, presence, from, settled, core}`. `scene`
  is the one scene to render (the incoming waits for the outgoing); `phase` is `still`,
  `leaving` or `entering`; `presence` is 0..1, linear in time; `settled` means nothing moves;
  `core` is the pose to draw the core at, mid-tween.
- `animationOffset(leaving, from, timings?)` — the negative `animation-delay` (ms) that starts a
  component's CSS animation at presence `from`.
- `STAGE_TIMINGS` — `{exit: MS.exit, appear: MS.appear}`, from the tokens.

**Transitions.** Any scene can go to any scene:

1. The outgoing scene leaves first, fully (`MS.exit`): projections collapse back into the core,
   lines retract, the strip folds, the subtitles fade. Render it with `leaving` set.
2. Then the incoming scene comes out (`MS.appear`).
3. The core tweens its position and size the whole way — through the exit and the entrance — and
   switches its motion state at once, so the engine's own tween runs alongside.

An interruption reverses cleanly: whatever is on stage turns round from the presence it had
reached (half gone → half an appearance back), and the core starts its new tween from where it
is. Pass `view().from` to each component's `from` and nothing jumps.

Not covered: what each scene contains. That is the shell's composition.

## The core

```svelte
<Core state={coreStateFor(reactor)} level={mic.level} envelope={voice.envelope} onbeat={(n) => (beat = n)} />
```

**Inputs.**

| Prop | Type | Meaning |
|---|---|---|
| `state` | `CoreState` (default `idle`) | What JARVIS is doing. A change tweens there and fires a shock ring. |
| `level` | 0..1 | The mic level while listening. |
| `envelope` | 0..1 | The speech envelope while speaking. |
| `music` | 0..1 | The music's beat strength while playing. |
| `tint` | token name | The primary persona's hue in place of the state's colour; `tintFor(persona, reactor)` (`$lib/orb/personas`) gives it in the contract's calm states, undefined for JARVIS and every other state. |
| `theme` | `ThemeId` | The theme whose core this is (`galaxy` for AURA, `reactor` for JARVIS); default the one the console wears (`theme.svelte.ts`, set by the shell from the primary persona). |
| `docked` | boolean | A small core (the popup's): the mini star budget. |
| `renderer` | `'auto' \| 'canvas2d'` | `canvas2d` skips the GPU (the gallery's side-by-side). |
| `freeze` | `{at, from?}` | Draw one exact frame, `at` ms (capped at 60 s) after a change from `from` (default `idle`) to `state`, and no loop. Screenshots and pixel tests use it. |
| `onbeat` | `(beats) => void` | Each working beat, with the count so far (feed `Connector`'s `beat`). |
| `label` | string | What a screen reader hears; default "<theme's name>, <state>" ("AURA, idle"). |
| `testid` | string | Default `core`. |

A missing `level`/`envelope`/`music` is synthesized, so the core moves believably with no input;
a value that is not a finite number counts as silence. The core never opens the mic: the inputs
are levels the voice pipeline already has.

**The nine states** (`CORE_STATES`; the targets are `LOOKS` in `core/engine.ts`):

| State | Looks like |
|---|---|
| `idle` | Cyan, rings turned apart, slow spin. Ready. |
| `listening` | Rings line up facing you; the shell breathes with `level`. |
| `speaking` | Pale cyan, brighter; the shell pulses with `envelope`. |
| `thinking` | Violet, fast spin, the shell shrinks and swirls. |
| `embedding` | Violet; points fade in and out of the shell. Memory, embeddings, a model loading. |
| `acting` | Cyan; rings tilt and quicken. A tool call inside a turn. |
| `music` | Cyan; the shell swells with `music`. |
| `alert` | Pink, nearly still, rings aligned. Something needs you, or JARVIS cannot work. |
| `working` | Cyan; rings wobble and the beat runs (`onbeat`). A long multi-step job. |

**From the reactor** (`REACTOR_TO_CORE`, `coreStateFor(name)`; unknown names and inherited keys
like `__proto__` give `idle`):

| Reactor | Core | Why |
|---|---|---|
| `idle`, `listening`, `thinking`, `speaking`, `alert` | same | |
| `tool` | `acting` | A tool call inside a turn. |
| `goal` | `working` | A /goal is the long job the presence strip exists for. |
| `attention` | `alert` | Something waits on the user; the amber leader line says which projection. |
| `warming` | `embedding` | The model is loading; violet is models, and `thinking` would claim a turn. |
| `kill` | `alert` | The kill switch stopped everything; the subtitle says why. |
| `offline` | `alert` | An idle core would claim it is ready, the one thing it must not say offline. |

**Rendering.** The theme pack names its core (`THEMES[id].core.renderer`); `core/renderer.ts`
(`createCoreRenderer(kind, opts)`, `rendererFor(theme)`) gives every core one interface —
`setState(state, inputs)`, `render(dt)`, `setQuality(level)`, `dock(position, size)`,
`freeze(...)`, `dispose()` — so a theme switch changes no screen code. AURA's **galaxy**
(`core/galaxy.ts` the model, `core/galaxy-draw.ts` raw WebGL2 or Canvas2D) is a spiral of stars
that tweens 0.035 a frame between eight looks (`GALAXY_STATE` folds `acting` into `work`, without its beat), with
2100 stars on a desktop, 1200 on a phone (coarse pointer) and 360 docked, on battery saver or
under reduced motion. JARVIS's **reactor** (`core/engine.ts`) is drawn on the GPU (three.js,
loaded on demand) with 1500 points, 600 in those cases. Both draw in Canvas2D where there is no
WebGL2 and after the GPU context is lost (until mounted again). The device pixel ratio is capped
at 2. It draws only while it is on screen and the page is visible; under reduced motion it stands
still — each change is one frame, no ring. Unmounting stops the frame loop, removes every
listener and frees the GPU context. Not covered: the reactor ignores `dock`'s position (it is
always centred) and the galaxy ignores the persona `tint` (its colours are the state's).

**Attributes** for tests: `data-state` (the current state), `data-core` (`galaxy` or `reactor`),
`data-renderer` (`webgl` or `canvas2d`, once chosen), `data-shocks` (state changes so far).

## Helpers

- `motion.ts` — `MS` (every duration, from the tokens: `appear`, `exit`, `bar`, `row`,
  `rowStagger`, `connector`, `beat`, `shock`, `kick`, `fade`, `unroll`, `hold`), `ms(name)`,
  `easing`, `cubicOf`, `prefersReducedMotion()`, `watchReducedMotion(fn)`.
- `tone.ts` — `TONES` (`accent`, `violet`, `warn`, `ok`, `danger`, `text`, `dim`, `faint`) and
  `toneColor(tone)`, the token behind each.
- `lines.ts` — `Point`, `cleanPoints(points)` (drops non-finite points), `connectorCurve(a, b)`.
