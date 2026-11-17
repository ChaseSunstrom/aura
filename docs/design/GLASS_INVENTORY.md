# The Glass HUD — component inventory

Every existing UI element in the repository, mapped to the panel or component it becomes in
`GLASS_HUD.md`, with a disposition, so that nothing is missed and nothing old survives the
migration (the brief's "build a component inventory"; V2's no-dead-code gate applied to the plan).
`scripts/verify/glass-inventory.py` walks the three UI trees and fails naming any UI file whose
repo-relative path is not in this document, so a file added during the migration has to be given
a panel before CI passes.

## What counts as a UI file (kept identical to the script's collection rules)

| Tree | Rule | Count |
|---|---|---|
| web | every `*.svelte` under `aura-web/src` (since T-0611: the routes and `lib/ds`). `.ts` co-modules are logic, described in `src/lib/ds/README.md` and §5, not gated. | 45 |
| desktop | the desktop app's `aura-desktop-app/src-tauri/src/*.rs` and `ui/*.html`/`*.ts` (Tauri, ADR-0051), plus the Python device agent's modules that draw a window (`aura-desktop/aura_desktop/*.py` importing tkinter, and `theme.py` that styles them). The shell otherwise loads the web console. | 21 |
| android | a `*.kt` under `android-app/app/src/main/kotlin` in the `ui`, `surface` or `car` package, named `*Activity.kt`, or that renders something: a Compose function, a classic View/Drawable, an overlay window, a dialog, a Car screen, a notification, or a quick-settings tile. | 179 |
| console | the device agent's pane host, `aura-desktop/aura_desktop/console/*.py` (see §4), plus the one in-repo TTY prompt. Not walked by the script: the tree is gated by `test:desktop`, mypy, vulture, the token lint and jscpd, and the frames in `docs/design/screens/console-*.txt` are held to the code by `aura-desktop/tests/test_console_app.py`. | 5 |

Dispositions:

- **becomes `panel.x`** — its content becomes that panel's content component, rebuilt in glass;
  the old file is deleted when the panel lands.
- **merges into `glass.X`** — folded with others into one glass component.
- **rebuilt** — same purpose, new implementation (the reactor, the boot).
- **deleted** — no successor; the need goes away with the route tree or the theme.
- **logic kept** — no surface of its own (a reducer, a bridge, a timing table); stays, with its
  tests, and is re-pointed at the new components.
- **regenerated** — a generated file, produced again from the new tokens.

Panel ids are the types in `GLASS_HUD.md` §9.1. Glass components are: `glass.Room` (ambient
layer), `glass.Reactor`, `glass.Pane`, `glass.Panel` (frame: title, verbs, handles, rim, shadow,
scrim, the four content states), `glass.Stack` (phone carousel), `glass.Tree` (tiled split tree),
`glass.Rail` (Focus), `glass.Strip` (Ambient), `glass.Dialog`, `glass.Palette` (command palette),
`glass.Control` (button, icon button, input, select, toggle, chip, field), `glass.States`
(loading, empty, error, offline inside a panel), `glass.Row`, `glass.Table`, `glass.Figure`,
`glass.Timeline` (the tool-call row), `glass.Markdown`, `glass.Diff`, `glass.Chart`, `glass.Graph`,
`glass.Progress` (bar and ring), `glass.StateDot`, `glass.Notice` (toast), `glass.Boot`,
`notify.*` (notification and chat-platform templates).

## 1. Web — `aura-web/src` (45)

The Glass HUD's web surfaces were deleted with T-0611, when the console became the v2 shell on
`$lib/ds` (`docs/design/v2/Core2.dc.html`): the host (`hud/Hud.svelte`), the window manager
(`hud/layout/`), the map, the glass library and its thirty-six bodies, the arc reactor, the 3D orb,
and the primitives under `lib/ui/` and `lib/components/`. Git history keeps their rows;
`docs/design/v2/UI_INVENTORY.md` §1 gives each one's fate (rebuilt on the ds, or retired) and the
commit. What is left in `lib/glass/` and `lib/reactor/` is logic the shell reads, not a surface:
the panel types a `hud.panel.show` is named by (`glass/panelTypes.ts`), the shapes `hud/data/`
maps the feeds into (`glass/bodies/types.ts`) and the reactor's truth table
(`reactor/reactorState.ts`, `reactor/spec.ts`) that the core's state is folded from. §1.2–§1.8
went with their files; the numbers are kept so references to §1.9 onwards stay right.

### 1.1 Routes (5; `/gallery/core` is in §1.9)

| File | What it is | Component | Disposition |
|---|---|---|---|
| `aura-web/src/routes/+layout.svelte` | what every route shares: the styles, the two links, the reader's text size; no toast rail (a moment comes out of the core) | — | rebuilt (T-0611) |
| `aura-web/src/routes/+error.svelte` | a route that throws, or a path with no screen: what happened and the way back to the core | a v2 screen | rebuilt (T-0611) |
| `aura-web/src/routes/[...path]/+page.svelte` | the one route: every path the console ever had, resolved by `hud/activities.ts` and `ds/shell/routes.ts` to the core or a screen | `ds.Shell` | rebuilt (T-0611) |
| `aura-web/src/routes/offline/+page.svelte` | the offline page, prerendered and inlined into the APK as console-offline.html for the phone and the car to show while they cannot reach Jarvis; the core drawn in CSS | — | rebuilt (T-0611) |
| `aura-web/src/routes/gallery/tokens/+page.svelte` | `/gallery/tokens`, every token rendered | — | gallery |

### 1.9 The v2 design system (T-0603, new) (22)

`aura-web/src/lib/ds/` is the v2 package the shell is rebuilt from: content as projected light
out of the core, not panels. `src/lib/ds/README.md` documents every prop, the stage's scenes and
the core's states; `scripts/verify/ds_boundary.py` keeps screens outside it from importing
anything else. Its logic modules (`stage.ts`, `lines.ts`, `motion.ts`, `tone.ts`, `core/engine.ts`,
`core/states.ts`, `core/webgl.ts`, `core/canvas2d.ts`) are described in the README, not here.

| File | What it is | Component | Disposition |
|---|---|---|---|
| `aura-web/src/lib/ds/Projection.svelte` | a dark radial backing, one cyan bracket, a glow: how every piece of content appears | `ds.Projection` | new |
| `aura-web/src/lib/ds/Label.svelte` | the mono uppercase label, cyan or violet | `ds.Label` | new |
| `aura-web/src/lib/ds/Figure.svelte` | a big Space Grotesk Light number with its unit | `ds.Figure` | new |
| `aura-web/src/lib/ds/Connector.svelte` | the curved line from the core to a projection, with light flowing on beats | `ds.Connector` | new |
| `aura-web/src/lib/ds/LeaderLine.svelte` | a thin line from the core to the projection it is about, amber while an approval waits there | `ds.LeaderLine` | new |
| `aura-web/src/lib/ds/Subtitles.svelte` | what is being said, under the core | `ds.Subtitles` | new |
| `aura-web/src/lib/ds/Conversation.svelte` | the session's last turns beside the core, labelled YOU/JARVIS, JARVIS's in Markdown, streamed words fading in, a typing state (T-0728); its logic is `conversation.ts` | `ds.Conversation` | new |
| `aura-web/src/lib/ds/PresenceStrip.svelte` | the task JARVIS is doing on a device, its steps and the take-over | `ds.PresenceStrip` | new |
| `aura-web/src/lib/ds/HoldButton.svelte` | press-and-hold to confirm an action that needs a person | `ds.HoldButton` | new |
| `aura-web/src/lib/ds/RuleChip.svelte` | a rule JARVIS keeps (a standing approval, an automation, a preference), removable | `ds.RuleChip` | new |
| `aura-web/src/lib/ds/core/Core.svelte` | the GPU core in nine states, its Canvas 2D fallback, its battery and visibility rules | `ds.Core` | new |
| `aura-web/src/routes/gallery/core/+page.svelte` | `/gallery/core`, the core in every state with every ds component, for `e2e/ds-core.spec.ts` and `docs/screens/v2/core/` | — | gallery |
| `aura-web/src/lib/ds/activity/Steps.svelte` | the steps JARVIS is taking while it takes them: each tool's words, the page it has open, a picture, and Stop (T-0732); its logic is `hermes/hud.ts` | `ds.activity.Steps` | new |
| `aura-web/src/routes/gallery/steps/+page.svelte` | `/gallery/steps`, the steps in every state for `e2e/activity.spec.ts` | — | gallery |
| `aura-web/src/lib/ds/board/Board.svelte` | the Board's grid of six numbered panels, one column to three (T-0733); its numbers are `board/model.ts`, its one frame loop `board/clock.ts` | `ds.board.Board` | new |
| `aura-web/src/lib/ds/board/Panel.svelte` | a Board panel's chrome after the Sampo live Board: number chip, name, what, the live figure, footer; stale and unavailable in words | `ds.board.Panel` | new |
| `aura-web/src/lib/ds/board/Flows.svelte` | 01 MODELS: kinds of task flowing to the model that answered, packets as fast as the route is busy | `ds.board.Flows` | new |
| `aura-web/src/lib/ds/board/Hours.svelte` | 02 TURNS: an hour a bar, stacked by model, the hour now breathing | `ds.board.Hours` | new |
| `aura-web/src/lib/ds/board/Cells.svelte` | 03 TASKS and 06 SUITES: a cell an item, coloured by how it ended or when it last passed | `ds.board.Cells` | new |
| `aura-web/src/lib/ds/board/Dial.svelte` | 04 TOOLS: a minute a bar round a clock face, a hand turning at the call rate, the most-used tools | `ds.board.Dial` | new |
| `aura-web/src/lib/ds/board/Tanks.svelte` | 05 LOAD: each card a tank of who holds its memory, the served models, a surface that moves only while one loads | `ds.board.Tanks` | new |
| `aura-web/src/routes/gallery/board/+page.svelte` | `/gallery/board`, the Board live and degraded on fixtures | — | gallery |

### 1.10 Generative UI (T-0612, new) (22)

`aura-web/src/lib/ds/gen/` draws what `hud_project` sends: one component per type of
`tests/contracts/projections.json`, each on a `Projection`. `Projected.svelte` is the one entry
the shell mounts; `src/lib/ds/gen/README.md` documents it and every type. Its logic modules
(`contract.ts`, `draw.ts`) are described there, not here. The custom visual's frame is the
`/projection-frame` route (a `+server.ts`, no UI of its own).

| File | What it is | Component | Disposition |
|---|---|---|---|
| `aura-web/src/lib/ds/gen/Projected.svelte` | one projection by type: validates, then loading, empty, error or the type's view | `ds.Projected` | new |
| `aura-web/src/lib/ds/gen/Approval.svelte` | what is waiting on the owner, amber | `gen.approval` | new |
| `aura-web/src/lib/ds/gen/Bar.svelte` | a bar chart, the bars rising in | `gen.bar` | new |
| `aura-web/src/lib/ds/gen/Code.svelte` | code in mono, its language and path | `gen.code` | new |
| `aura-web/src/lib/ds/gen/Comparison.svelte` | options side by side, the chosen one lit | `gen.comparison` | new |
| `aura-web/src/lib/ds/gen/CustomHtml.svelte` | the model's own HTML in a sandboxed frame, run only on the owner's Run, capped in size and time | `gen.custom_html` | new |
| `aura-web/src/lib/ds/gen/Device.svelte` | a house device's state and level | `gen.device` | new |
| `aura-web/src/lib/ds/gen/Diff.svelte` | a unified diff, added and removed lines coloured | `gen.diff` | new |
| `aura-web/src/lib/ds/gen/Line.svelte` | a line chart in SVG, series and axes | `gen.line` | new |
| `aura-web/src/lib/ds/gen/List.svelte` | a list, virtualised when long | `gen.list` | new |
| `aura-web/src/lib/ds/gen/MapView.svelte` | places and a route on a graticule, no map library | `gen.map` | new |
| `aura-web/src/lib/ds/gen/Media.svelte` | what is playing and where | `gen.media` | new |
| `aura-web/src/lib/ds/gen/Model.svelte` | a 3D wireframe in three.js | `gen.model` | new |
| `aura-web/src/lib/ds/gen/Person.svelte` | a person, violet: who they are and what is remembered | `gen.person` | new |
| `aura-web/src/lib/ds/gen/Picture.svelte` | an image with its caption | `gen.image` | new |
| `aura-web/src/lib/ds/gen/Progress.svelte` | a task's steps and how far it is | `gen.progress` | new |
| `aura-web/src/lib/ds/gen/Scene.svelte` | the default visual: the model's vector picture, flat or through a camera | `gen.scene` | new |
| `aura-web/src/lib/ds/gen/Stat.svelte` | one big figure, its unit and change | `gen.stat` | new |
| `aura-web/src/lib/ds/gen/Table.svelte` | a table, virtualised when long | `gen.table` | new |
| `aura-web/src/lib/ds/gen/Timeline.svelte` | the day's events in order | `gen.timeline` | new |
| `aura-web/src/lib/ds/gen/Virtual.svelte` | the windowed rows long lists and tables draw through | `gen.Virtual` | new |
| `aura-web/src/routes/gallery/projections/+page.svelte` | `/gallery/projections`, every type, light and the states; `?live=1` draws the socket's for `e2e/projections.spec.ts` and `docs/screens/v2/projections/` | — | gallery |

### 1.11 The v2 shell and its screens (T-0611, new) (29)

The console the person meets, on the components above: the shell around the core, the screens the
launcher opens, and the few controls they share. `src/lib/ds/README.md` is the detailed map;
`docs/design/v2/UI_INVENTORY.md` §1 says which legacy file each one replaced.

| File | What it is | Component | Disposition |
|---|---|---|---|
| `aura-web/src/lib/ds/shell/Shell.svelte` | the console: the core on its stage, what the moment needs coming out of it, the top bar (All apps, ⌘K, Devices), the composer with the mic that mutes the open mic, the lock and first run in front | `ds.Shell` | new |
| `aura-web/src/lib/ds/shell/Stage.svelte` | the core screen's stage: the core where the scene puts it, the projections on the board's slots, the presence strip while working | `ds.Stage` | new |
| `aura-web/src/lib/ds/shell/ApprovalCard.svelte` | one open request as the Control board draws it: what, why, impact, the held yes, the no, "Show me first", the rule; a question with its answers | `ds.ApprovalCard` | new |
| `aura-web/src/lib/ds/shell/Palette.svelte` | "Search or command" (⌘K): ranks the screens and the shell's verbs, hands the rest to JARVIS | `ds.Palette` | new |
| `aura-web/src/lib/ds/shell/DevicesSheet.svelte` | the top bar's Devices: the paired devices, each a link to its row, and the way to pair another | `ds.DevicesSheet` | new |
| `aura-web/src/lib/ds/shell/Popup.svelte` | the desktop app's assist popup (`/?popup=1`, ADR-0051): the core, one line, the input with the mic, the approval card; flat (DECISIONS 60) | `ds.Popup` | new |
| `aura-web/src/lib/ds/screens/Screen.svelte` | a full screen's frame: its title, the way back to the core, its body | `ds.Screen` | new |
| `aura-web/src/lib/ds/screens/Section.svelte` | one titled part of a screen: the cap, a count, a line, the body | `ds.Section` | new |
| `aura-web/src/lib/ds/screens/SettingRow.svelte` | one setting: its control, one line that says what it does, More and Ask | `ds.SettingRow` | new |
| `aura-web/src/lib/ds/screens/Chat.svelte` | Chat, full screen: the conversations down the side (a drawer on a phone) and the open one, full height | screen | new |
| `aura-web/src/lib/ds/screens/Markdown.svelte` | prose Jarvis wrote, drawn through `renderMarkdown` | `ds.Markdown` | new |
| `aura-web/src/lib/ds/screens/ChangeRows.svelte` | a settings change as the value now over the value it becomes, or one sentence and neither value for a secret; the proposal card and the approval card both draw it (T-1317) | part | new |
| `aura-web/src/lib/ds/screens/RecentChanges.svelte` | Settings' last section: the settings changed by asking, newest first, with when, who and both values, and Put back on the newest change of a key (never a secret); the shell's five-second "Changed · Undo" offer is the same undo (T-0700) | part | new |
| `aura-web/src/lib/ds/screens/Approvals.svelte` | everything waiting for the person's OK, each as the core's card, and "nothing waits"; a held settings change as a before/after card with Apply and Discard (T-1316) | screen | new |
| `aura-web/src/lib/ds/screens/Integrations.svelte` | connect anything: search or describe a service, what is connected, what JARVIS may do, the four ways to reach anything else | screen | new |
| `aura-web/src/lib/ds/screens/Levels.svelte` | "What JARVIS may do": each capability's level, asked for through `capability_set`, never set from here | `ds.Levels` | new |
| `aura-web/src/lib/ds/screens/Texts.svelte` | the phone's mirrored messages, drawn as other people's words, never as orders | screen | new |
| `aura-web/src/lib/ds/screens/Walkthrough.svelte` | the first-run walkthrough: six steps in order, what the house shows done, a request to JARVIS per step with what was done and what waits on a yes, the screen to do it by hand | screen | new |
| `aura-web/src/lib/ds/screens/Devices.svelte` | the paired devices, pairing another (a code and its QR behind the console password), the connected phone's daily power ledger, today beside yesterday (T-0582), and the newest phone and desktop builds to download (ADR-0050) | screen | new |
| `aura-web/src/lib/ds/screens/MapRegions.svelte` | Offline maps under Devices: each region's state and size, a catalogue search with Download, Remove on a hold | screen section (projection inside Devices) | new (2026-11-11, T-0979) |
| `aura-web/src/lib/ds/screens/Settings.svelte` | one short screen: account, people and voices, persona and voice, privacy and approvals, devices; inside the phone app, its native screens | screen | new |
| `aura-web/src/lib/ds/screens/SignIn.svelte` | the console password once, for every setting behind it | screen part | new |
| `aura-web/src/lib/ds/screens/People.svelte` | everyone the house knows by name, their level and voice; rename, enrol, forget, add | screen part | new |
| `aura-web/src/lib/ds/screens/Enrol.svelte` | enrolling a voice: the house's phrases one at a time, each sample's verdict | sheet | new |
| `aura-web/src/lib/ds/screens/Lock.svelte` | the lock: the console password or an enrolled voice opens it | gate | new |
| `aura-web/src/lib/ds/screens/Setup.svelte` | first run: the owner's name, the console password, enrol now or later | gate | new |
| `aura-web/src/lib/ds/screens/Ops.svelte` | the Ops board at `/ops`: the eight panes, derived only while open | screen | new |
| `aura-web/src/lib/ds/screens/Board.svelte` | the Board at `/board`: GET /api/board every 10 s while open and visible, the four states (T-0733) | screen | new |
| `aura-web/src/lib/ds/screens/Models.svelte` | the Models screen at `/models`: the house's models with state, GPU budget and downloads; load, unload and delete (both behind a hold), edit settings, search and download, and the add form with the engine choice and the one .gguf; the four states (T-1254) | screen | new |
| `aura-web/src/lib/ds/screens/Car.svelte` | the car's display at `/car`: the core, one card, the turn and the speed, from the phone's `JarvisCar` bridge | screen | new |
| `aura-web/src/lib/ds/ui/Button.svelte` | light on the dark: primary, ghost, quiet, danger; touch-target sized | `ds.Button` | new |
| `aura-web/src/lib/ds/ui/Field.svelte` | one line of text to type, named by its label | `ds.Field` | new |
| `aura-web/src/lib/ds/ui/Segmented.svelte` | one choice of a few, in a labelled group of pressed buttons | `ds.Segmented` | new |
| `aura-web/src/lib/ds/ui/Switch.svelte` | one on-or-off, a `role="switch"` button | `ds.Switch` | new |
| `aura-web/src/lib/ds/ui/Sheet.svelte` | a sheet over the stage: the native modal `<dialog>` | `ds.Sheet` | new |
| `aura-web/src/lib/ds/ui/Grab.svelte` | the handle at the top of a menu that has risen on a phone: what a finger drags down to close it | `ds.Grab` | new |
| `aura-web/src/lib/ds/ui/Gate.svelte` | a modal nothing gets past until answered: the lock and first run | `ds.Gate` | new |
| `aura-web/src/lib/ds/ui/State.svelte` | loading, empty, error and offline, said in words, with Retry where it helps | `ds.State` | new |
| `aura-web/src/lib/ds/Starfield.svelte` | the worn pack's star field behind the core (AURA's galaxy ground), seeded so it is the same sky on every load; twinkles only when the pack says so | `ds.Starfield` | new (T-0861) |
| `aura-web/src/lib/ds/ThemeTile.svelte` | one theme in Settings: its swatch, name and a note (whose, kept here, a draft), with its actions | `ds.ThemeTile` | new (T-0861) |

### 1.12 Android files listed beside the web

| File | What it is | Component | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/CarHud.kt` | the car display's host of the console's `/car` in a web view, and the bridge that feeds it (2026-09-25; the Headway rail is hidden under it) | `ds/screens/Car.svelte` | new |

## 2. Desktop (21)

### 2.1 The desktop app — `jarvis-desktop-app` (Tauri, ADR-0051) (17)

The app still loads the console for everything the console does, so it inherits the HUD by
construction; what it draws itself are its own pages, on the console's tokens and in the Settings
menu's register (`SettingsRow`, `Control compact`), and the tray's word and icon. The Electron shell
(`src/main/*.ts`, `src/renderer/*.html`) was retired with it; its logic is ported, with its tests.

| File | What it is | Component | Disposition |
|---|---|---|---|
| `aura-desktop-app/src-tauri/src/main.rs` | the app: plugins, the hotkey, the `jarvis://` link, start-up, the update loops | the shell | new |
| `aura-desktop-app/src-tauri/src/shell.rs` | the agent it starts and watches, the socket, the console window, the assist popup, the consent window, the overlay and its Esc, the tray, the updater | the shell; the popup is the console's `?popup=1` | new |
| `aura-desktop-app/src-tauri/src/commands.rs` | what the app's own pages may call (the console window is granted none) | IPC | new |
| `aura-desktop-app/src-tauri/logic/src/console.rs` | what the window may load; a pasted address or `jarvis://pair` link read (was `pairing.ts`/`config.ts`) | logic | ported |
| `aura-desktop-app/src-tauri/logic/src/protocol.rs` | the agent's frames and its announcement line; which CLI subcommands a page may run | logic | new |
| `aura-desktop-app/src-tauri/logic/src/labels.rs` | the tray's status line in the reactor's words (was `tray.ts`) | logic | ported |
| `aura-desktop-app/src-tauri/logic/src/states.rs` | generated: the reactor's eleven states' glyphs and words | tokens | generated |
| `aura-desktop-app/src-tauri/logic/src/palette.rs` | the typing key (T-0696): its default, the line the tray says for it, and the cue that sends the popup's page to its line | logic | new |
| `aura-desktop-app/src-tauri/logic/src/dictation.rs` | hold-to-dictate (T-1258): the chord rules, the tray's line, the cue that arms the popup's page, and the one-press state machine that takes its spoken words from the title | logic | new |
| `aura-desktop-app/src-tauri/logic/src/update.rs` | when a downloaded build applies itself | logic | ported |
| `aura-desktop-app/ui/index.html` | "This computer": status, pairing, permissions, activity, checks, updates — the agent's CLI as pages | `SettingsRow` rows, `Field` rows, `Control compact` | new |
| `aura-desktop-app/ui/desktop.ts` | that window's script | — | new |
| `aura-desktop-app/ui/consent.html` | the native approval prompt, answerable with the console closed | the approvals pane's one decision | rebuilt |
| `aura-desktop-app/ui/consent.ts` | its script: the three answers; closing denies | — | new |
| `aura-desktop-app/ui/overlay.html` | the visible path (T-0617): a clear, click-through window over every screen with the agent's pointer, its target, the ripple of a press, its caret and "Esc to take over" | the focus ring and the accent glow, drawn over the owner's apps | new |
| `aura-desktop-app/ui/overlay.ts` | its script: draws each step the shell emits (`overlayFrame` in `logic.ts` maps and checks it) | — | new |
| `aura-desktop-app/ui/unreachable.html` | "no console there yet", retried every five seconds | the offline state with the offline glyph | rebuilt |
| `aura-desktop-app/ui/unreachable.ts` | its script | — | new |
| `aura-desktop-app/ui/logic.ts` | the pages' decisions (which answers a tier allows, the update line), vitest-held | logic | new |
| `aura-desktop-app/ui/tauri.d.ts` | the typed slice of `window.__TAURI__` the pages use | types | new |
| `aura-desktop-app/ui/tokens.ts` | generated `--jv-*` data and the glyph table | tokens | generated |

### 2.2 Python device agent — `aura-desktop/aura_desktop` (4)

The agent's two tkinter dialogs are grey system chrome and cannot become glass. The disposition
is that the shell's native approvals pane answers both (the `ask` frames already reach it over
the loopback socket), and where no shell runs the TTY prompt remains, styled by the console theme
(§4). tkinter is removed as a backend.

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `aura-desktop/aura_desktop/consent.py` | the tier-3 consent prompt: tkinter, then TTY, then refuse | the tkinter backend is deleted; the shell's native `panel.approvals` and the console-themed TTY prompt remain; the fail-closed rules stay verbatim | rebuilt |
| `aura-desktop/aura_desktop/companion.py` | the "ask" and "notify" backends: tkinter, TTY, refuse | same: shell pane, console TTY prompt; notifications through the shell's `notify.*` templates | rebuilt |
| `aura-desktop/aura_desktop/codepopup.py` | the sign-in code popup (T-0135): code, sender, Copy, Autofill, slide in/out on the float tokens | interim tkinter popup on the agent; the target is the Tauri shell's frameless popup window drawing the same frame, the agent keeping the board, copy and autofill logic | interim |
| `aura-desktop/aura_desktop/theme.py` | the tk palette copied from `tokens.ts`, with its AA test | deleted with the tk dialogs; its AA test becomes the token package's contrast test | deleted |

## 3. Android — `android-app/app/src/main/kotlin/ai/jarvis/app` (179; `Selection.kt` and `CarHud.kt` are listed beside the web, in §1.12)

The app is classic Views built programmatically from `JarvisUi`; the only Compose file is the
generated theme with no caller. The migration is to Compose: `glass.Stack` on phones,
`glass.Tree` on tablets and foldables, the door as Focus mode. Panel ids are shared with the web.

### 3.1 Activities (10)

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/ApprovalActivity.kt` | tier-3 consent screen over the keyguard | `panel.approvals` in the door (Focus mode over the keyguard; params hidden until unlocked, as `ConsentGate` rules) | becomes `panel.approvals` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ApprovalProof.kt` | signs a sensitive approval behind the system's fingerprint prompt | none (the system's BiometricPrompt; no drawn UI of its own) | new (2026-11-11, T-0945) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/EmergencyCountdown.kt` | the ten-second count before an emergency alert is sent, and its cancel | none (logic only; drawn by EmergencyCountdownActivity) | new (2026-11-11, T-1015) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/EmergencyCountdownActivity.kt` | the emergency countdown over the keyguard: what is about to be sent, the seconds left, Cancel | a v2 projection on the stage (`ui/v2`), danger-coloured | new (2026-11-11, T-1015) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/JarvisVoiceInteractionSession.kt` | no UI of its own: the system's assist session, which starts the popup and reads the screen it was called over (T-0824) | none (it draws nothing) | kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/JarvisAssistActivity.kt` | assist-gesture popup with the orb and conversation | Ambient mode host (the assist gesture opens the HUD in Ambient) | rebuilt |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ListenTrampolineActivity.kt` | invisible one-frame Activity starting the mic service | unchanged; draws nothing | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/MainActivity.kt` | the HUD host: `glass.Room` + `glass.Stack` (phone) / `glass.Tree` (tablet), `glass.Reactor` in the chat panel's head, the boot, the four content states per panel; the transcript, tools, goal, model, graph and notices are still Views wrapped as panel bodies | `glass.Hud` (`glass/Hud.kt`) | rebuilt (host landed; bodies migrate panel by panel) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/JarvisSettingsActivity.kt` | Jarvis's settings, native (2026-09-25): the persona, then the console's settable leaves grouped and worded by the schema's contract, written through `jsettings` | folded into `SettingsActivity`'s Jarvis section; the name is a manifest alias (T-0689) | deleted |
| `android-app/app/src/main/kotlin/ai/jarvis/app/NfcTagActivity.kt` | invisible NFC reader-mode frame | unchanged; draws nothing | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/PermissionRequestActivity.kt` | invisible `requestPermissions` host | unchanged; draws nothing | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ScanCodeActivity.kt` | invisible hand-off to a scanner | unchanged; draws nothing | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/SettingsActivity.kt` | connection, voice, model, channels in one column | one Compose screen on `ui/v2` (T-0689): the connection, voice on the phone, the persona and `approvals.mode`; the rest links to the console's `/settings` | rebuilt |
| `android-app/app/src/main/kotlin/ai/jarvis/app/VoiceIdentityActivity.kt` | voice enrolment and verification | `panel.voice` enrolment view | becomes `panel.voice` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/WakeEnrolActivity.kt` | teach the wake word the owner's ways of saying "Hey Jarvis" (T-0714): eight takes with each one's peak, a read-aloud line, the fit and its leave-one-out count | `panel.voice` enrolment view | becomes `panel.voice` |

### 3.2 Assist (12)

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/AssistOverlay.kt` | the wake overlay over other apps, drawn as the island (T-0695): pill, route pill, card, approval | the island (`material.floating`, DECISIONS 80) | rebuilt |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/island/IslandView.kt` | the island's shape: opaque fill, accent edge, the pill↔card morph on the glass spring, its gestures | the island (`material.floating`) | new (T-0695) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/island/RestIsland.kt` | the resting island's window on the camera hole: an untouchable app overlay, hidden with the status bar, the conversation's island and the screen | the island (`material.floating`) | new (T-0890) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/island/RestView.kt` | the resting pill: the pack's fill or AURA's space, accent edge, the persona's core as one frozen frame, the presence word or the phone's status | the island (`material.floating`, `rest-*`, `core-rest`) | new (T-0890) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/island/RouteActivity.kt` | a held route as the island's activity: where to and how far | the island's activity pill | new (T-0695) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/island/RouteGlyph.kt` | the route pill's arrow, in the accent | the island's activity glyph | new (T-0695) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/ToolActivityView.kt` | tool-call rows and progress under the orb | `panel.timeline` compact view | merges into `panel.timeline` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/VoiceActivity.kt` | voice-activity thresholds (pure logic) | unchanged | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/VoiceClaim.kt` | "Was that you?" notification after a refused voice, with a That was me action | `notify.attention` template with the reactor icon | new (2026-10-07) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/DriveStart.kt` | Decides whether a car session is a new drive; no UI of its own | none (logic only) | new (2026-10-08) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/Parked.kt` | Keeps where the car was left; answers `where_parked`; no UI of its own | none (logic only) | new (2026-10-08) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/DriveEnd.kt` | Tells Jarvis once a real drive is over; its only UI is the reply as a notification | none (notification) | new (2026-10-09) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/JarvisVoiceLines.kt` | Keeps the car's fixed lines in Jarvis's own voice and announces a fallback in it; no UI of its own | none (logic only) | new (2026-11-08) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/PreparedLines.kt` | Holds the newest voiced lines (the coming turns, a repeated warning) in memory so saying one needs no link; no UI of its own | none (logic only) | new (2026-11-11, T-1070) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/ProjectionAppop.kt` | Sets PROJECT_MEDIA through Shizuku so screen sharing starts with the car without a tap; no UI of its own | none (logic only) | new (2026-10-08) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/WakeWordService.kt` | "Hey Jarvis" service and its two notifications | service unchanged; notifications use `notify.listening` / `notify.attention` templates with the reactor icon | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/WakeTileService.kt` | the quick-settings tile | the tile's icon and state word come from `glass.Reactor` (listening / idle / offline) | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/OrbWidget.kt` | the home-screen orb widget (T-0590) | a ring tinted with the reactor state's colour and its state word, drawn through `RemoteViews`; one tap opens the listen trampoline | new; not glass (a launcher draws it) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/hermes/ClarifyPrompt.kt` | clarify dialog with options or text | `panel.approvals` clarify view (a `glass.Dialog` when a service raises it) | merges into `panel.approvals` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/hermes/GoalSheet.kt` | "set goal" sheet with SET / DRAFT / SUBGOAL / GATE | `panel.goals` composer (sentence-case verbs) | merges into `panel.goals` |

### 3.3 Automation (8)

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/automation/JarvisAutomationService.kt` | trigger owner; "pause automations" notification | service unchanged; `notify.service` template | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin/CameraActions.kt` | headless capture, scanner hand-off | unchanged | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin/CommsActions.kt` | SMS, call, contacts, `send_notification` | `send_notification` uses `notify.notice` | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin/ParityActions.kt` | Tasker-parity actions incl. toasts | toasts become `glass.Notice` when the HUD is up, system toasts otherwise | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/automation/triggers/ReminderReceiver.kt` | reminders and their notification | `notify.reminder` template | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/automation/ui/ActionPolicyActivity.kt` | per-action policy editor | `panel.settings` approval-matrix group (Phase 7's matrix lives in the schema) | becomes `panel.settings` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/automation/ui/AuditLogActivity.kt` | audit log list and detail | `panel.audit` | becomes `panel.audit` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/automation/ui/AutomationsActivity.kt` | automation list with consent toggles | `panel.automations` (phone view) | becomes `panel.automations` |

### 3.4 Car (98: the three template-screen files for Google's Android Auto under `car/templated/`, the 63 moved from Headway, ADR-0047, and 31 added since)

Android Auto templates are host-styled; the only thing ours is the reactor still.

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/templated/CarOrbRenderer.kt` | renders the orb to a bitmap for the head unit | `glass.Reactor` Canvas renderer's still (one renderer, one geometry) | merges into `glass.Reactor` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/templated/JarvisCarAppService.kt` | the CarAppService | unchanged | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarNavScreen.kt` | NavigationTemplate: the drawn drive, one maneuver, the one card (call, approval, message, home, calendar, music) | replaced the PaneTemplate screen (orb image, what was said) | logic rebuilt (T-0613, T-0648) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/carapp/RendererOwner.kt` | names the package holding the car-app permission, for the first-run notice | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/CarPokeListener.kt` | answers a car that connects to the phone first | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/PokeSlot.kt` | sockets the car opened, waiting for a session | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/bluetooth/CarBluetoothStream.kt` | answers the car's Bluetooth service: the pairing request and the car's replies in the drive log (no UI) | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/SocketRoute.kt` | which socket may carry the session: only the car's own is bound to the car | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/setup/CarProfile.kt` | what worked for one car, kept by its Bluetooth address | the car setup (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/setup/CarSetupActivity.kt` | the car setup: grants, pick the car, connect, pair the Wi-Fi, the static-IP step | the car setup (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/setup/CarSetupScreen.kt` | the setup screen: the five gates live, one remedy per failure | the car setup (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/setup/GateReport.kt` | the five gates and the remedy for each failure | the car setup (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/JarvisTile.kt` | the Jarvis tab: the reactor, the last exchange, one touch to talk | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/theme/JarvisCarPalette.kt` | the car screen's default palette, from the design tokens | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/CarTurn.kt` | a question from the car to Jarvis, answered through the car's speakers | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/CarVoice.kt` | what the Jarvis tab shows of the car's voice turn: the reactor's phase, what was heard and answered | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/CarMessageAnnouncer.kt` | no surface: a message arriving during a drive becomes a car turn read aloud, once, with a reply offered (2026-09-25) | the car's voice turn | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/CarMusicLevel.kt` | no surface: the music's level from a visualizer, for the car reactor's pulse behind a setting (2026-09-25) | the car page's reactor | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/log/CarLogUpload.kt` | no surface: each drive's session log to the adapter's artifact store (2026-09-25) | Diagnostics | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/routines/RoutineWatcher.kt` | no surface of its own: a fired routine's answer as a notification, its consent on the approval screen (2026-09-27) | Notifications | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/PhoneAction.kt` | the allow-Jarvis notification; otherwise no surface: a `phone_action` run through the phone's dispatcher and answered (2026-09-25) | the notice template | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/HermesCarJarvis.kt` | Jarvis over the adapter's HUD socket, for the car | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/JarvisTranscriber.kt` | the car's microphone, heard by Jarvis's transcriber | the car shell (ADR-0047) | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/HeadwaySettings.kt` | Persisted user choices, in one place so the two activities cannot disagree. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/audio/CarAudioStream.kt` | Everything between "the car opened its audio sink channels" and "the car plays what Headway says". | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/audio/MediaQueue.kt` | Captured audio waiting for the car, so that reading never waits on sending. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/audio/PhoneAudioCapture.kt` | Taps whatever the phone is playing, so it can be sent to the car. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/carapp/CarAppSession.kt` | Where a [CarAppSession] has got to. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/carapp/CarAppSurfaceView.kt` | The patch of screen a car app draws its own map into. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/carapp/CarAppTrips.kt` | Turn-by-turn from a car app, which is the good version of the same thing. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/carapp/CarConnectionProvider.kt` | Tells car apps that the phone is projecting, because on this phone it is. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/carapp/CarHostCapability.kt` | Whether a third-party car app can accept Headway as its host on this install. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/carapp/TemplateApps.kt` | An app that offers a car interface, and what kind of one it says it is. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/carapp/TemplateRenderer.kt` | Draws a car-app template with Headway's own widgets. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/CarDisplay.kt` | Raised when the platform will not give Headway a display to put the car screen on. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/CarGlyphs.kt` | The symbols the car screen draws. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/CarSheet.kt` | A full-height panel of choices, over the dashboard. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/CarShell.kt` | The car screen: a rail, a stage, and whatever the driver put on it. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/CarStartup.kt` | What the car shows in the first second and a half of a session. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/CarSurface.kt` | The car screen as its own display, rendered at the head unit's own resolution. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/DashLayoutStore.kt` | The dashboards the driver has saved, and which one is on the car screen. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/DashTile.kt` | One pane's worth of content on the car dashboard. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/PaneTreeView.kt` | Turns a [DashLayout] into views, with dividers the driver can drag and — when the layout is unlocked — a way into every pane's menu. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/AppPaneTile.kt` | A pane containing a real app, running. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/CarAppTile.kt` | A third-party app's own interface, drawn by Headway. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/CarStyle.kt` | The look every tile shares, and the reason the numbers here are plain dp. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/ContentTiles.kt` | The setting that says which notification listeners the user has enabled. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/HostedTiles.kt` | A pane showing another app's own live `RemoteViews`, hosted with `AppWidgetHost`. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/MapsTile.kt` | The next turn, and one tap to the map itself. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/MediaBrowseTile.kt` | Browse any media app's library, and start something playing. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/PhoneTile.kt` | The phone pane: whoever is calling, and everyone who called before. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/SensorsTile.kt` | What the car says about itself: speed, revs, fuel, tyres, the outside temperature, the odometer. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/dash/tiles/WidgetSetupActivity.kt` | The three steps a widget needs before a pane can show it, none of which a `Presentation` is allowed to do. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/diag/SelfTest.kt` | Everything about Headway that can be answered without a car. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/input/CarInputStream.kt` | Everything between "the driver touched the car's screen" and "the phone behaved as though the driver had touched *its* screen". | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/input/HeadwayAccessibilityService.kt` | The unprivileged path from a car touch to a synthesised gesture on the phone. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/BluetoothCarLink.kt` | Raised when the Bluetooth half of the link cannot be established. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/CarCompanion.kt` | Pairs Headway with the car's access point, so joining it needs no approval sheet. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/CarPresence.kt` | Brings the link up when the car appears, without anybody touching the phone. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/CarWifiNetwork.kt` | Raised when the phone cannot join, or stay on, the car's access point. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/CarWifiProvisioning.kt` | Getting the car's access point saved on the phone, so its MAC stops changing. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/link/PhoneCertificateStore.kt` | The phone certificate Headway presents to the head unit, and the user's replacement for it. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/log/SessionLog.kt` | Local time, not UTC: the user correlates this with "it dropped out just after I pulled onto the motorway", and they remember that in their own clock. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/media/CarMediaBrowser.kt` | One app's library, browsed a level at a time. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/media/CarMediaStatusStream.kt` | Tells the car what is playing, so its own buttons have something to act on. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/media/MediaApps.kt` | The media apps on this phone that publish a browsable library. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/nav/NavigationFeed.kt` | One step of a route, as much of it as the navigating app will say. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/phone/CarPhone.kt` | One entry in the recent-calls list. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/phone/PhoneAssistant.kt` | Which app on this phone is the assistant. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/quirks/HeadUnitQuirks.kt` | Corrections applied to touch coordinates arriving from the head unit. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/sensor/CarSensorStream.kt` | Everything between "the car reported a reading" and "the dashboard shows it". | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/service/CarConnectCheck.kt` | The one spoken line on link-up naming what will not work this drive (home, the car page, music's grant); no visual of its own. | the car shell (ADR-0047) | new 2026-10-06 |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/service/CarLinkResume.kt` | Keeps the car's address for 12 hours so the link resumes after process death; no visual of its own. | the car shell (ADR-0047) | new 2026-11-08 (T-0604) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/service/CarLinkService.kt` | Marks a failure that happened while joining the car's access point. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/RoadSnap.kt` | Puts a GPS fix on the drawn road and reads the gyroscope's turn; no visual of its own. | the car's map (T-0613) | new 2026-11-12 (T-1101) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/service/SavedNetworkWait.kt` | Skips the wait to join the car's Wi-Fi when a reconnect still holds it; no visual of its own. | the car shell (ADR-0047) | new 2026-11-12 (T-1100) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/service/DialPatience.kt` | How long the link keeps dialling a head unit that refuses before it has named its endpoint; no visual of its own. | the car shell (ADR-0047) | new 2026-11-11 (T-0997) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/theme/CarControls.kt` | The transport controls, drawn rather than written. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/theme/Headway.kt` | The one place Headway's colours, spacing and motion are defined. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/theme/HeadwayTheme.kt` | Which palette [Headway] is drawing with, and who to tell when it changes. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/AppPaneHost.kt` | The one live app picture, and which pane it is currently in. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/CarAppDisplay.kt` | Where a third-party app goes when the driver taps it, and where its touches come back to. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/CarVideoStream.kt` | Everything between "the car opened a video channel" and "the car is showing the phone screen". | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/ForegroundApp.kt` | Which app the car pane should believe is in front. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/OverlayDisplay.kt` | A secondary display the *system* created, and what it measures. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/PhoneRotation.kt` | Turns the phone's own display sideways, so a mirrored app lays out in landscape. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/ProjectionRequestActivity.kt` | The one screen whose entire job is to ask for screen sharing and go away. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/VideoPump.kt` | Carries encoded frames from [ScreenEncoder] to [VideoChannel] across the gap between a synchronous callback and a suspending, flow-controlled send. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/video/ProjectionLossNotice.kt` | Says once, on the car, why the picture stopped when the screen-share grant is lost (T-1082). | the car shell (ADR-0047) | no drawing of its own: a rule |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/service/ResetReport.kt` | The log line a session that ends on a connection reset leaves: what the phone had in flight (T-1217). | the car shell (ADR-0047) | no drawing of its own: a log line |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/CarVoiceStream.kt` | Everything between "the driver pressed Voice" and "the phone did what they asked". | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/PhoneMediaControl.kt` | Carries out "pause", "next" and "volume up" on whatever app is playing. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/PhoneMicSource.kt` | The phone's own microphone, shaped like the car's. | the car shell (ADR-0047) | moved from Headway |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarNavScreen.kt` | Jarvis on the head unit as a navigation app: a NavigationTemplate over the host's map surface | the car hologram (Car.dc.html) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarSurface.kt` | draws the drive onto the host's map surface on its own thread | the car hologram (Car.dc.html) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarDrive.kt` | what the drive shows: the card the driver can act on, its actions and the route state | the car hologram (Car.dc.html) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/HoloGround.kt` | the hologram alone (the drive's scene over the sky), and `GroundView`: it as the wireless car's ground, under the see-through console page, redrawn every vsync | the car hologram (Car.dc.html) on the Headway path, where the page's OSM tiles were | new (2026-11-08, T-0741, T-0747) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/DriveFeed.kt` | feeds a drawn drive: GPS into the car's position, the house's map-match and buildings | none (data feed for CarNavScreen and GroundView) | new (2026-11-08, T-0741) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarZones.kt` | lays out the drive's boxes on the surface so cards never overlap the route | none (layout logic) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/SpacePainter.kt` | how the drive map is coloured by the persona's pack `map.style`: JARVIS's hologram in its tokens or AURA's space drive in her pack's colours | none (painter for CarSurface and GroundView) | new (T-0871) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/FlatMap.kt` | the drive as a flat, heading-up street map: roads by class with their names, water, green, footprints, the route and the car's arrow, in the `map` tokens; and what is ahead (T-1255): the driven route dimmed, the next turn's arrow, closures and roadworks in amber, one-way arrows, road numbers on plates, fuel, charging and parking named, a new route shown whole with north up | none (painter for CarSurface and GroundView, in place of the paused 3D drive) | new (2026-11-13, T-1252) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme/ActiveTheme.kt` | the theme pack the phone wears: the primary persona's own, or JARVIS's for a persona without one | none (theme state read by the phone's screens and the car) | new (T-0869) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarPosition.kt` | where the car is drawn between GPS fixes | none (logic only) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/DriveWords.kt` | what the driver says to the drive itself: the shown card's action, by its label | none (logic only) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/HoloPainter.kt` | replays a HoloFrame onto a canvas the way Car.dc.html's `startMap` paints | the car hologram (Car.dc.html) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/Hologram.kt` | the OSM hologram's scene, camera, route and buildings, and its ink | the car hologram (Car.dc.html) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/HoloWorld.kt` | the place drawn solid on the car's ground: buildings with lit walls, windows and roofs, street surfaces and paint, parking, water, green, trees, lamps, signals and roadworks, in the `color.world` tokens | the car hologram's ground, after the owner asked for the real street instead of wire blocks | new (2026-11-11, T-1060) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/HoloLight.kt` | where the sun is for a place and a moment, and how much daylight that is: what lights the car's ground by the hour (no drawing of its own) | the car hologram's ground, after the owner asked for lighting by the time of day | new (2026-11-12, T-1107) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/HouseMaps.kt` | asks the house's `/api/maps` for a route, the street the car is on and the buildings around it | none (logic only) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/RouteGuide.kt` | the house's route answer and the next manoeuvre along it | none (logic only) | new (2026-11-08, T-0613) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/MapPack.kt` | reads a region's map pack (footprints, roads, places) from the phone's own copy | none (logic only) | new (2026-11-11, T-0979) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/MapTiles.kt` | keeps the house's map answers by the tile in a size-capped cache, for the ground where the phone holds no pack | none (logic only) | new (2026-11-11, T-1074) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/MapPackSync.kt` | copies the house's map packs to the phone on an unmetered network | none (logic only) | new (2026-11-11, T-0979) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/PackRouter.kt` | finds a route over a pack's roads when the house does not answer | none (logic only) | new (2026-11-11, T-1026) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/RouteAsk.kt` | decides whether a route ask goes to the house or to the packs | none (logic only) | new (2026-11-11, T-1026) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarCards.kt` | holds the current card of each kind and hands the drive the one it should show | none (logic only) | new (2026-11-08, T-0648) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarSources.kt` | feeds the cards from the real call, message, calendar, music and arrival home | none (logic only) | new (2026-11-08, T-0648) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/car/holo/CarUnits.kt` | distances and speed in the locale's units (imperial in the US, Liberia and Myanmar) | none (logic only) | new (2026-11-08, T-0648) |

### 3.5 Companion (2)

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/companion/CompanionAskActivity.kt` | question card over the keyguard | `panel.approvals` clarify view in the door | becomes `panel.approvals` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/companion/CompanionNotifications.kt` | proactive question / notify notifications | `notify.ask` / `notify.notice` templates | logic kept |

### 3.6 Surface and tasks (6)

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/surface/SurfaceView.kt` | one line per house surface panel | `panel.house` | merges into `panel.house` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/surface/SurfaceWatch.kt` | subscribes to surface events, formats lines | `panel.house` store | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/surface/TaskDockView.kt` | task dock rows with a thin bar | `panel.kanban` compact view | merges into `panel.kanban` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/tasks/TaskNotifier.kt` | task progress notification | `notify.task` template | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/tasks/TaskOverlay.kt` | floating task chip over other apps | the overlay bubble's task count (with `AssistOverlay`) | merges into `glass.Reactor` overlay |
| `android-app/app/src/main/kotlin/ai/jarvis/app/tasks/TaskProgressView.kt` | three progress bar modes | `glass.Progress` | merges into `glass.Progress` |

### 3.7 UI package (33)

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ApprovalBridge.kt` | suspending approval API, notification fallback | unchanged; opens the door, falls back to `notify.approval` | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ConsentGate.kt` | when consent params and Approve are allowed (pure) | unchanged; the door obeys it | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ConsentText.kt` | the consent params as key: value lines, every value whole (pure) | unchanged; the door shows it, RAW the exact text | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/CrashLogActivity.kt` | crash log list and report | `panel.audit` crash view | becomes `panel.audit` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/DoorActivity.kt` | base for keyguard-waking question screens | the door host (Focus mode over the keyguard) | rebuilt |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ForegroundResultBridge.kt` | Activity-result actions from services | unchanged | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/JarvisScreens.kt` | launches automation screens by class name | opens the matching panel instead | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/JarvisUi.kt` | the programmatic widget kit | `glass.Control` Compose module (sentence case, control glass) | rebuilt |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ListActivityScaffold.kt` | base for list/detail screens | `glass.Panel` with a list content and a detail Focus | deleted |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/PermissionBridge.kt` | suspending permission requests | unchanged | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/PromptPresence.kt` | "a prompt is on screen" guard | unchanged; guards the door and the overlay | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/ReadabilityScrim.kt` | gradient scrim behind the overlay's text | `glass.Panel` scrim rule (GLASS_HUD §3.2) | merges into `glass.Panel` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ScreenStates.kt` | loading/empty/error/offline builders | `glass.States` | rebuilt |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ApprovalCard.kt` | the approval card's fields read back out of the reason (T-0606) | `panel.approvals` (the console's twin is `hud/approvalCard.ts`; cases in `tests/contracts/approval-event.json`) | new (v2) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ScreenNote.kt` | the screen's own note in place of the system toast | the v2 kit's live region | new (v2) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/v2/V2.kt` | the phone's half of the v2 language: Stage, Projection, Label/Headline/Body/Figure, HoldButton, ScreenState | `$lib/ds` (docs/design/v2/PROMPT.md §1) | new (v2) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/reactor/core/V2Core.kt` | the console's core on the phone: `CoreEngine` (engine.ts, pinned by `tests/contracts/core_frames.json`) stepped on the frame clock while resumed and drawn by `CoreCanvas` (canvas2d.ts); every `ReactorView` host draws it (T-0712) | `$lib/ds/core/Core.svelte` | new (v2) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/reactor/core/CoreCanvas.kt` | canvas2d.ts on an Android Canvas: the shell of light, the rings, the flow, the heart, additive in an offscreen layer | `$lib/ds/core/canvas2d.ts` | new (v2) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/V2Words.kt` | the assist overlay's and popup's words as the console's: the state as a Label, the user's line and the reply as Subtitles, the typed line as the dock's field (T-0712) | `$lib/ds` Label, Subtitles, the shell's dock | new (v2) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/MarkdownText.kt` | a reply's Markdown as spans on the popup's, overlay's and car tab's reply TextViews: bullets, numbers, bold, italics, code, http(s) links, tables (T-0727); V2Words.kt also holds the reply's CappedScroll | `$lib/markdown.ts` renderMarkdown in Subtitles, bound by `tests/contracts/markdown_text.json` | new (v2) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/assist/ReplyReveal.kt` | the popup's and overlay's reply as the console's conversation draws it: streamed to its last whole word with its open Markdown closed, new words fading and rising in, typing dots while a turn has no words; `V2Words.who` labels the turns YOU/JARVIS, start-aligned (T-0728) | `$lib/ds/Conversation.svelte` and `conversation.ts`, bound by `tests/contracts/streaming_markdown.json` | new (v2) |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/StateDot.kt` | the one status dot | `glass.StateDot` | rebuilt |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/Notify.kt` | the notification table: a level's reactor state, colour and word, the `notify.*` templates at their levels, the aliases — mirrored from `tests/contracts/notify.json` by `tools/notify_test.py` | `notify.*` | glass |
| `android-app/app/src/main/kotlin/ai/jarvis/app/channel/HouseDownNotice.kt` | Posts the one "house is down" notice on the alerts channel when the house stops answering, and rewrites it to "back" when it answers (T-0578). | `notify.*` | no drawing of its own: a notification |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/SystemCheckActivity.kt` | permissions and special-access checklist | `panel.settings` system-check group | becomes `panel.settings` |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme/JarvisTheme.kt` | generated Compose MaterialTheme (no caller today) | regenerated as the glass Compose theme; becomes the app's one entry point | regenerated |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme/PersonaAccent.kt` | the accent every Compose control and the reactor wear: the primary persona's hue (2026-09-25) | the glass theme's accent | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme/PersonaIcon.kt` | the launcher's icon and name: one persona alias enabled at a time (2026-09-25) | the launcher | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/ConsolePage.kt` | the console's own HUD page, kept loaded: the car screen's /car and the phone's home screen, with the phone bridge (music, actions, routines) (2026-09-27) | the console | new |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme/JarvisTokens.kt` | generated colour/type/space/radius constants | regenerated with the material, depth and spring groups | regenerated |
| `android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme/JarvisThemes.kt` | generated theme packs (AURA, JARVIS): colours, fonts, core, map, voice, wake per theme (T-0861) | what the phone and car read for the active theme | new, generated |

### 3.8 Update (2)

| File | Today | Becomes | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/update/InstallResultReceiver.kt` | install prompt and status notification | `notify.notice` template | logic kept |
| `android-app/app/src/main/kotlin/ai/jarvis/app/update/UpdateNotification.kt` | "new build available" notification | `notify.notice` template | logic kept |

Notification channels (eight today: `jarvis_approval`, `jarvis_service`, `jarvis_alerts`,
`jarvis-wake-heard`, `jarvis-wake-attention`, `jarvis_companion`, `jarvis_reminders`,
`jarvis_actions`) keep their ids (a renamed channel loses the person's settings) and get one
template each from `notify.*`: the reactor icon in the channel's state colour, the state glyph, a
sentence-case title, and the same verbs as the panel (Approve / Deny / Open).

### 3.9 Glass library (wave 3, new)

The files the migration adds on Android, the counterpart of the web's glass library (deleted
from the web with T-0611; the phone's v2 rebuild is T-0639). They are not old
surfaces and have no "becomes"; they are listed so the gate stays green while the library lands.
Disposition **glass**: it is the new system. The names are the web library's, file for file,
because a layout JARVIS arranges on the desktop has to mean the same thing when it reaches the
phone — `Room`, `Pane`, `Panel` and `Control` are the same four grades on both surfaces.

Behind them sit the Compose-free modules that the gate does not collect, because they render
nothing: `Depth.kt` (the five levels and the grade→level rule, mirroring `depth.ts`), `Budget.kt`
(the blur budget, mirroring `budget.ts`), `Material.kt` (the §5.1 table resolved to
numbers) and `Prefs.kt` (reduced motion, reduced transparency and high contrast). They are pure so
the decisions they make are pinned by a JVM test rather than by a screenshot.

| File | What it is | Component | Disposition |
|---|---|---|---|
| `android-app/app/src/main/kotlin/ai/jarvis/app/glass/ReactorModel.kt` | what a surface knows about its core as Compose state (inputs, level, warming, caption, persona hue), read by `ReactorView`; HudReactor, its pre-v2 host, was deleted in T-0779 | `glass.Reactor` (state) | glass |
| `android-app/app/src/main/kotlin/ai/jarvis/app/glass/ReactorView.kt` | the v2 core (`V2Core`) as a View for the surfaces still built from Views (popup, overlay, enrolment, companion, voice identity, the car's tile and startup), and the owners a Service window needs | `glass.Reactor` (View host) | glass |
| `android-app/app/src/main/kotlin/ai/jarvis/app/glass/Haptics.kt` | the four felt moments (snap, tile, approve, kill) and their three feels | `glass.Haptics` | glass |

## 4. Console

The console surface is the device agent's pane host (`GLASS_HUD.md` §9.7, "What the console is"):
`python -m aura_desktop console`, on the adapter's HUD socket, rendering the same layout document,
content panels, tool calls, approvals and goal the web console renders. The upstream Hermes TUI
(`hermes` CLI; not in the tree; `hermes/` holds plugins, skills, cron and the home volume) stays
untouched; the other in-repo terminal surface is the device agent's TTY prompt (§2.2).

| Surface | Today | Becomes | Disposition |
|---|---|---|---|
| Hermes TUI (upstream) | Hermes's own terminal client: an Ink/React app that takes a skin YAML | untouched; the glass console is a separate pane host on the HUD socket, so Hermes needs no change | out of scope |
| `aura-desktop/aura_desktop/console/theme.py` | — (new) | the console theme read from the generated `console_theme.json`: the palette with its truecolor → 256 → 16 → none ladder, the type roles as SGR, the double/single/ASCII box sets | glass |
| `aura-desktop/aura_desktop/console/panes.py` | — (new) | the layout document as cells: Tiled and Focus as the tree says, Floating tiled in rect order, Ambient hidden; replays `tests/contracts/hud-layout.json` | glass |
| `aura-desktop/aura_desktop/console/state.py` | — (new) | the reducer from gateway events: chat, tool calls, approvals, goal, `hud.panel.*`, `hud.layout.*`, the status report; the reactor's inputs per `tests/contracts/reactor.json` | glass |
| `aura-desktop/aura_desktop/console/render.py` | — (new) | every panel type's content and its four states inside a double-line border lit from the reactor's side; the strip with the ANSI reactor; the Ambient room | `panel.*` on the console |
| `aura-desktop/aura_desktop/console/app.py` | — (new) | the socket, the keys (§9.8 chords), the loop, and `--demo` that draws `docs/design/screens/console-*.txt` | glass |
| device agent TTY prompt (`consent.py`, `companion.py`) | plain `input()` prompts with the reactor's attention badge | the console theme's prompt: palette, double-line border, the ANSI reactor glyph in attention | rebuilt (§2.2) |
| `scripts/hermes-probes/*` | developer probes printing to stdout | not surfaces | out of scope |

Evidence, one frame per mode (`panes.MODES`) from the demo tape:
`docs/design/screens/console-overview.txt`, `docs/design/screens/console-task.txt`,
`docs/design/screens/console-focus.txt`, `docs/design/screens/console-ambient.txt`
(`scripts/console-frames.sh` rewrites them; the desktop suite fails when they are stale).

## 5. Logic modules that feed the panels (not gated, listed so the migration re-points them)

Wave 3 adds the data layer, `aura-web/src/lib/hud/data/` — one pure mapping per panel body from
the sources the console already holds (the Hermes snapshot, the house socket's registries and
commands, the console's own endpoints, `tests/contracts/hermes-settings.json` for the settings
catalogue) onto the shapes in `glass/bodies/types.ts`. `mappings.test.ts` pins each mapping on
fixtures and the contract examples; `mocks.test.ts` drives the two mock backends through the real
clients so a field the mock stops sending fails there rather than as a blank panel in a browser.

Web: `lib/hermes/hud.ts` (the HUD reducer), `lib/hermes/panels.ts` (the content contract), `lib/components/hud/panelData.ts`, `lib/components/hud/panelMotion.ts` (folds into the spring set), `lib/hermes/webPanel.ts`, `lib/hudStatus.svelte.ts` (becomes the reactor state store), `lib/activity.svelte.ts`, `lib/screens.ts` (deleted with the routes; its chord table moves to the layout verbs), `lib/sectionLink.svelte.ts` (becomes per-panel link state), `lib/motion.ts`, `lib/tokens.ts` (regenerated), `lib/toast.ts`, `lib/commandPalette.ts`, `lib/shortcuts.ts`, `lib/knowledge/graph.ts`, `lib/dashboards/{widgets,layout,series,tiles,chartTypes}.ts` (layout.ts is superseded by the layout tree; the rest feed `panel.telemetry`), `lib/sections/settingsPlan.ts` (superseded by the schema), `lib/ui/index.ts`.

Android: `HudState`, `HermesRpc`, `WakeStartPolicy` and the other protocol/service files are not
UI and are not listed; `JarvisApp.kt` creates the channels named in §3.8.

## 6. Counts

Recounted 2026-10-09 over the tables above, after the rows for files that no longer exist were
dropped (the retired web routes, sections, components, HUD cards, dashboard widgets and
primitives, the old Android orb, boot and panel surface, the console WebView; git history keeps
them); the web recounted 2026-11-08, when T-0611 deleted the glass console (the android row was
not recounted then). **Files** is what `scripts/verify/glass-inventory.py` counts (the gate
`make docs-check` runs); **Rows** is the rows above. `glass / new / kept` gathers the glass-era
and v2 rows (`glass`, `new`, `kept:`, the gallery frames, the desktop's `ported` and `interim`);
`regenerated` includes `generated`.

| Tree | Files | Rows | becomes | merges into | rebuilt | deleted | logic kept | regenerated | moved from Headway | glass / new / kept |
|---|---|---|---|---|---|---|---|---|---|---|
| web | 45 | 45 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 41 |
| desktop | 21 | 21 | 0 | 0 | 4 | 1 | 0 | 2 | 0 | 14 |
| android | 179 | 179 | 10 | 14 | 8 | 3 | 27 | 2 | 64 | 51 |
| **total** | **245** | **245** | 10 | 14 | 16 | 4 | 27 | 4 | 64 | 106 |

The few rows still marked **deleted** name files that exist and are meant to go with the old
chrome (the Android console nav strip and tabs, the list scaffold, the tk theme).
