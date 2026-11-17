# ADR-0032: The room has no tabs; JARVIS pulls the panels up

- **Status:** Accepted (2026-09-07, on the owner's review of the deployed Reactor II console)
- **Amends:** [ADR-0031](0031-reactor-ii-returns.md) §3 (the frame keeps its top bar and readouts; the tabs go) and [ADR-0030](0030-glass-hud-design-system.md) §9.2's "a screen is a named layout"
- **Reference:** the C2 reactor reference — the chat view at rest and the **task view** while something runs

## Context

The console has six tabs (Working, Dashboards, House, Work, Knowledge, Settings). Each is a
path that loads a shipped layout of three or four panels, so the room is always full whether or
not anybody asked for those panels. The owner's direction: *"instead of having separate tabs,
like there are, have jarvis able to pull up all of the different panels (except for settings,
which should be its own menu, with all customizeable settings NO DUPLICATES OR FALSE
IMPLEMENTATIONS), and fix the jarvis loading animation, and when jarvis is doing a task or
whatever, have it animate like the c2 reactor task page."*

Three things were measured before deciding anything.

1. **The agent can already open any panel.** `hud_open` (the `hud-layout` plugin) takes any of
   the twenty-two types in `tests/contracts/hud-layout.json`, and `hud.panel.show` carries
   fourteen content kinds. Both are deployed and proven on every deploy by the smoke's steps 5b
   and 5c. Nothing new is needed for JARVIS's half.
2. **A person cannot.** `layout/coverage.ts` records it in as many words: the `open` op has no
   chord and is reached by "load a named layout from Layouts, or ask JARVIS". Delete the tabs
   and the only way to see the house is to talk to a language model.
3. **The settings panel is honest, and I first said it was not.** The initial reading of this
   was wrong and is corrected here rather than quietly dropped: I read the *contract's*
   `settable` flag and reported that 153 of 193 rows were disabled boxes. The console does not
   use that flag. `fromContract` treats a row as settable when the plugin allows it **or** it is
   env-backed, and `settingWrite` gives each of those a real target — the Hermes settings
   plugin, jarvis-core's store, or an environment override jarvis-core keeps and applies at the
   next restart, which the row already says. Measured properly: **166 of 167 rows write
   somewhere**, one (`memory.provider`) is drawn disabled and says it is managed by the stack,
   and no two rows share a key or a description. There are no duplicates and no dead controls.

   What is wrong is reach: **50 rows are `advanced` and drawn only on `/settings/system`**.
   With the tabs gone nobody will type that path, so a fifth of the settings would become
   unreachable — the requirement "all customizeable settings" is about that, not about
   pruning.

## Decision

1. **No tabs.** The top bar keeps the brand, the reactor, the state word and the readouts. The
   centre carries what the room is *doing*, not where to go.
2. **The room is empty at rest** — the instrument, its caption and the current exchange, which
   is the mockup's chat view. Panels arrive because they were asked for: by JARVIS
   (`hud_open`, `hud.panel.show`, `arrange`), or by the person through a **panel launcher** in
   the dock that lists every type and opens one. The launcher is not a tab bar: it adds a panel
   to the room rather than replacing the room, and it exists because a surface reachable only
   through an LLM has one point of failure.
3. **Every old path still resolves** — `/house/devices` opens the house panel and focuses it
   rather than loading a four-panel layout. Bookmarks, the phone's strip and deep links keep
   meaning what they meant.
4. **Settings is a menu, not a panel and not a tab**: a surface of its own over the room, with
   its own dismiss, opened from the dock and by every path *about the catalogue*. It shows
   **every** settable row including the 50 advanced ones, behind a switch inside the menu rather
   than behind a URL nobody will type. The one managed row stays disabled with its reason. A test
   pins what the audit found — every drawn row has a write target, and no two share a key or a
   description — so it stays true rather than being true today.

   Three things this cost, each found by running the suite against it and each fixed here.
   *Which paths*: the focus decides, not the prefix — `/settings/console` is about the pairing
   panel and `/settings/voice` about the voice one, and a menu keyed on `/settings*` put a
   dialog between the person and the panel they had asked for. *The feed*: the live feeds are
   chosen by the panel types on the surface, so with no settings panel the catalogue was never
   fetched and the menu opened onto skeletons that never resolved; the open menu now counts as
   a type. *The states*: a menu has no `PanelFrame` to draw loading, empty, error and offline
   for it, so it draws them itself with the same `ScreenState` every screen uses.

   And the rows are **named**. The catalogue ships a key and a sentence and no name, so the
   menu put the sentence where the name goes: 167 rows of prose, two lines each, nothing to
   scan. The name is derived from the key with the group's own prefix dropped (`tts.app_voice`
   in Voice is "TTS app voice"; `grafana.url` in Homelab stays "Grafana URL", or the thirteen
   `*.url` rows there would all read "URL"), and the sentence becomes the row's hint.
5. **A running task wears the mockup's task view**: the reactor at the centre with the plan's
   progress on its arc, `STEP n OF m`, the percentage, the step's own sentence and the elapsed
   line; the plan, the live tool calls and the output as the room's panels; the approval bar at
   the foot when something is held.
6. **The boot is the instrument arriving in an empty room.** With nothing tiled at rest there
   is nothing to condense, so the wave is the ignition, then the frame, the stage's words, the
   two panels, and the dock. Measured while implementing this: the boot drew its own reactor
   fixed at the viewport centre while the room's instrument was already lit in the stage, 216 px
   higher on a 900 px screen — the whole signature moment played as two overlapping instruments
   and resolved into one when the overlay faded. So the overlay goes: `bootState.progress` is
   handed to the room's own reactor, and `Boot.svelte` keeps only the clock and the gallery's
   still. The chrome joins the wave with the same `use:condense` every panel already uses, which
   is what stops the console from sitting there whole around an instrument still assembling.

## Consequences

`hud/activities.ts` stops mapping paths to layouts and maps them to a panel to open; the five
shipped layouts in `layout/shipped.ts` become presets the launcher and JARVIS can still ask for
by name, not something a URL loads. `e2e/hud.spec.ts`'s tab cases become launcher cases. The
settings contract keeps all 167 rows — the schema is right, the *rendering* was wrong — and the
console decides which are controls. Nothing about the panel types, the layout document, the
manager or the four modes changes.
