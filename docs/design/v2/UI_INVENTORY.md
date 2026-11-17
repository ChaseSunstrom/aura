# v2 UI inventory

Brief v2 §1 "Old UI removed completely": every UI component, screen, style, tab, panel, toast,
dialog and layout on every surface, and what v2 does with it. Taken on 2026-11-08 from a read-only
sweep of the tree (the brief's audit, v2.3). **Fate**: `delete` (the v2 language has no such thing: rails,
tab strips, toasts, glass/card panels, the undecided-choice toggles), `rebuild` (the function stays,
drawn again on the v2 design system; a `delete` on a file that also hosts a live function, such as the root layout's toast host, means its legacy part goes and the rest is rebuilt), `keep` (logic or generated data that draws nothing itself).
**Done** is ticked only when the file is gone or rebuilt, by the lane that did it, with its commit.
Paths in the phone and car tables are relative to `android-app/app/src/main/` and
`android-app/app/src/main/kotlin/ai/jarvis/app/car/` as the section says.

## 1. Web console (`jarvis-web/src`)

| Path | What it is | Kind | Flag | Fate | Done |
|---|---|---|---|---|---|
| jarvis-web/src/app.html | Document shell, embed flag, web-origins meta | layout | | rebuild | ☑ kept as is (T-0611): it draws nothing; the embed flag and the origins meta are still read (`hermes/webPanel.ts`) |
| jarvis-web/src/routes/+layout.svelte | App shell: global styles, mounts the toast host | layout | LEGACY? (toast host) | delete | ☑ 6dc2ef51: the toast host went; the styles and the two links stay |
| jarvis-web/src/routes/+error.svelte | Page shown when a route throws | screen | | rebuild | ☑ 6dc2ef51: rebuilt on `$lib/ds` |
| jarvis-web/src/routes/[...path]/+page.svelte | The single catch-all route that mounts the HUD | screen | | rebuild | ☑ 204d0d64: mounts the v2 shell |
| jarvis-web/src/routes/[...path]/+page.server.ts | Catch-all route loader | other | | keep | ☑ kept (204d0d64: a path with no screen is a 404) |
| jarvis-web/src/routes/offline/+page.svelte | Console-unreachable page for phone and car | screen | | rebuild | ☑ 6dc2ef51: rebuilt, the core drawn in CSS (c756823a: `data-state` back) |
| jarvis-web/src/routes/offline/+page.ts | Offline route options | other | | keep | ☑ kept |
| jarvis-web/src/routes/gallery/+page.svelte | Living component gallery, includes a toast specimen | screen | LEGACY? (toast specimen) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/routes/gallery/Tokens.svelte | Every token rendered, with contrast checks | component | | rebuild | ☑ 6dc2ef51: rebuilt as `gallery/tokens/+page.svelte` |
| jarvis-web/src/routes/gallery/fixtures.ts | Gallery specimen data | other | | keep | ☑ 6dc2ef51: deleted with the gallery it fed |
| jarvis-web/src/routes/gallery/map/+page.svelte | Map scene alone, timeline titled "This turn" | screen | LEGACY? ("This turn") | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/routes/gallery/orb/+page.svelte | Orb scratch and verification page | screen | | rebuild | ☑ 6dc2ef51: deleted; the core's page is `/gallery/core` (T-0603) |
| jarvis-web/src/routes/gallery/reactor/+page.svelte | Reactor's eleven states as specimens | screen | | rebuild | ☑ 6dc2ef51: deleted; the core's states are `/gallery/core` (T-0603) |
| jarvis-web/src/routes/gallery/tokens/+page.svelte | Token reference route | screen | | rebuild | ☑ 6dc2ef51: rebuilt on `$lib/ds` |
| jarvis-web/src/lib/hud/Hud.svelte | HUD host: every screen; mounts Rail, Dock, Stage, popup | layout | LEGACY? (mounts left rail; "This turn" title; frosted floating popup card) | delete | ☑ 6dc2ef51: deleted; the host is `ds/shell/Shell.svelte` (204d0d64) |
| jarvis-web/src/lib/hud/Rail.svelte | Left rail of eight sections; a sheet under 721px | rail | LEGACY? (left rail) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/PanelDrawer.svelte | Panels drawer from the left: open tabs, add panel | panel | LEGACY? (tab list) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/Dock.svelte | Bottom dock: microphone and composer | component | | rebuild | ☑ 204d0d64: the shell's composer and mic; Dock deleted in 6dc2ef51 |
| jarvis-web/src/lib/hud/DockStrip.svelte | Dock's live activity ticker with toggles | component | | rebuild | ☑ 6dc2ef51: deleted; what runs is the working scene's presence strip (`ds/shell/Stage.svelte`) |
| jarvis-web/src/lib/hud/Stage.svelte | Reactor centre stage, caption and exchange | component | | rebuild | ☑ 204d0d64: `ds/shell/Stage.svelte` |
| jarvis-web/src/lib/hud/Readouts.svelte | Up to six readouts orbiting the reactor | component | | rebuild | ☑ 6dc2ef51: deleted; the stage's projections (`ds/shell/projections.ts`, 6f22d5a2) surround the core |
| jarvis-web/src/lib/hud/ToolOrbit.svelte | Tool-call chips orbiting the core | component | | rebuild | ☑ 6dc2ef51: deleted; a tool call is the working scene's connector |
| jarvis-web/src/lib/hud/ChatScreen.svelte | Full-screen chat with a conversation list | screen | | rebuild | ☑ 83658885: `ds/screens/Chat.svelte` |
| jarvis-web/src/lib/hud/CommandPalette.svelte | ⌘K command palette | dialog | | rebuild | ☑ 204d0d64: `ds/shell/Palette.svelte` |
| jarvis-web/src/lib/hud/SettingsMenu.svelte | Settings surface: Reactor, Panels, Selection rows | panel | LEGACY? (Reactor 3D/2D, Panels Tabs/Tiles, Selection toggles) | delete | ☑ 6dc2ef51: deleted; Settings is `ds/screens/Settings.svelte` (6d6af418) |
| jarvis-web/src/lib/hud/SettingsRow.svelte | One settings row: name column plus controls | component | | rebuild | ☑ 6d6af418: `ds/screens/Section.svelte` and `ds/ui/Field.svelte` |
| jarvis-web/src/lib/hud/ConsoleSignIn.svelte | Console password entry at top of Settings | component | | rebuild | ☑ 6d6af418: `ds/screens/SignIn.svelte` |
| jarvis-web/src/lib/hud/PeopleSection.svelte | Settings → People | panel | | rebuild | ☑ ff40359b: `ds/screens/People.svelte` |
| jarvis-web/src/lib/hud/RoutinesSection.svelte | Settings → Routines and the Automations panel | panel | | rebuild | ☑ 6dc2ef51: deleted; the routines editor is T-0665 |
| jarvis-web/src/lib/hud/AppsSection.svelte | Phone and desktop app downloads, with QR | panel | | rebuild | ☑ c756823a: Devices → Get the apps |
| jarvis-web/src/lib/hud/EnrolScreen.svelte | Voice enrolment sheet over the room | screen | | rebuild | ☑ ff40359b: `ds/screens/Enrol.svelte` |
| jarvis-web/src/lib/hud/LockScreen.svelte | Lock screen: password or voice, blurred backdrop | screen | LEGACY? (frosted blur) | delete | ☑ ff40359b: `ds/screens/Lock.svelte`, no blur |
| jarvis-web/src/lib/hud/SetupScreen.svelte | First-run setup sheet | screen | | rebuild | ☑ ff40359b: `ds/screens/Setup.svelte` |
| jarvis-web/src/lib/hud/OpsBoard.svelte | Ops board: eight panes at /ops | screen | | rebuild | ☑ 5e6d20ab: `ds/screens/Ops.svelte` |
| jarvis-web/src/lib/hud/PopupHud.svelte | Desktop popup's compact and listening HUD | screen | | rebuild | ☑ bea67c41: `ds/shell/Popup.svelte`, flat (DECISIONS 60) |
| jarvis-web/src/lib/hud/Glyph.svelte | Chrome line icons | component | | rebuild | ☑ 6dc2ef51: deleted; no `$lib/ds` control draws its icons |
| jarvis-web/src/lib/hud/arrangement.ts | Model for Panels (tabs/tiles) and Selection | other | LEGACY? (Panels Tabs/Tiles, Selection) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/reactorStyle.ts | Reactor 3D/2D style choice | other | LEGACY? (Reactor 2D/3D) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/sections.ts | The left rail's eight sections | other | LEGACY? (left rail) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/palette.ts | Command palette search | other | | keep | ☑ kept: `ds/shell/Palette.svelte` ranks with it |
| jarvis-web/src/lib/hud/favicon.ts | Favicon and title follow the reactor state | other | | keep | ☑ kept |
| jarvis-web/src/lib/hud/popupMood.ts | Desktop popup mood words | other | | keep | ☑ kept: the popup's title (bea67c41) |
| jarvis-web/src/lib/hud/chatScreen.ts | Chat screen day-grouping arithmetic | other | | keep | ☑ kept: the v2 chat's day lines (83658885) |
| jarvis-web/src/lib/hud/readouts.ts | Readout figures for Readouts.svelte | other | | keep | ☑ 6dc2ef51: deleted with Readouts |
| jarvis-web/src/lib/hud/*.ts (20 other files) | HUD state, feeds, voice, lock, pairing; no drawing | other | | keep | ☑ kept; the ones only the legacy UI read went in 6dc2ef51 |
| jarvis-web/src/lib/hud/data/*.ts (32 files + 2 JSON) | Panel data adapters | other | | keep | ☑ kept for T-0665's screens; the routines form and armed rules went in 6dc2ef51 |
| jarvis-web/src/lib/hud/layout/HudSurface.svelte | Room surface: map wired to the layout manager | layout | | rebuild | ☑ 6dc2ef51: deleted; the core's stage is `ds/shell/Stage.svelte` |
| jarvis-web/src/lib/hud/layout/TabStrip.svelte | Dock column's tab strip (tabs arrangement) | tab bar | LEGACY? (tab strip) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/layout/PanelMenu.svelte | Title-bar verb menu on right-click or long-press | component | | rebuild | ☑ 6dc2ef51: deleted; there are no panels to name a verb for |
| jarvis-web/src/lib/hud/layout/HudDemo.svelte | Window-manager demo at /gallery?hud=1 | other | | keep | ☑ 6dc2ef51: deleted with the window manager |
| jarvis-web/src/lib/hud/layout/*.ts (13 files) | Layout manager, tree, verbs, persistence, shipped layouts | other | LEGACY? (tabstack.ts is the tab strip model) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/map/MapSurface.svelte | Map: reactor, panel cubes on bands, docks | layout | LEGACY? (hosts TabStrip and Scroll selection; "This turn" sliver) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/map/PanelScroll.svelte | Scroll selection: character-select row | component | LEGACY? (Selection mode) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/map/PanelToken.svelte | Resting panel drawn as a glyph or a card | component | LEGACY? (card state) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/hud/map/*.ts (11 files) | Map solver, camera, bands, docks, groups, scrollLook | other | LEGACY? (scrollLook.ts is the Selection look) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/glass/glass.css | Glass material: grades, depth, focus ring | style | LEGACY? (glass panel style) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/glass/Pane.svelte | Pane-grade background glass | component | LEGACY? (glass panel style) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/glass/Panel.svelte | Panel-grade glass, used by every content panel | component | LEGACY? (glass panel style) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/glass/PanelFrame.svelte | Panel frame: title bar, id, size envelope | panel | LEGACY? (glass panel frame) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/glass/Control.svelte | Control-grade glass: buttons, chips | component | | rebuild | ☑ 6dc2ef51: deleted; the controls are `ds/ui/Button.svelte` and `Segmented.svelte` (6f22d5a2) |
| jarvis-web/src/lib/glass/Room.svelte | Ambient room layer (depth 0) | layout | | rebuild | ☑ 6dc2ef51: deleted; the ground is `base.css`'s glow and the core's stage |
| jarvis-web/src/lib/glass/Boot.svelte | Boot ignition clock | component | | rebuild | ☑ 6dc2ef51: deleted; a v2 boot is T-0665 |
| jarvis-web/src/lib/glass/Toast.svelte | Toast notices | toast | LEGACY? (toast) | delete | ☑ 6dc2ef51: deleted; a moment comes out of the core |
| jarvis-web/src/lib/glass/*.ts (8 files) | announce, boot, chords, depth, motion, panelTypes, spring, index | other | | keep | ☑ 6dc2ef51 took six; `panelTypes.ts` and `index.ts` stay as the types the data mappers name |
| jarvis-web/src/lib/glass/bodies/ActivityBody.svelte | Activity view (desktop/browser run) | panel | | rebuild | ☑ 6dc2ef51: deleted; the browser-run tile is T-0665 |
| jarvis-web/src/lib/glass/bodies/ApprovalsBody.svelte | Approval and clarify requests | panel | | rebuild | ☑ 204d0d64: `ds/shell/ApprovalCard.svelte`; the list is `ds/screens/Approvals.svelte` (6d6af418) |
| jarvis-web/src/lib/glass/bodies/AudioBody.svelte | Published recording player | panel | | rebuild | ☑ 6dc2ef51: deleted; media is T-0665 |
| jarvis-web/src/lib/glass/bodies/AuditBody.svelte | Audit log and crash reports | panel | | rebuild | ☑ 6dc2ef51: deleted; no v2 screen yet (T-0665) |
| jarvis-web/src/lib/glass/bodies/AutomationsBody.svelte | House and phone automations | panel | | rebuild | ☑ 6dc2ef51: deleted; the routines editor is T-0665 |
| jarvis-web/src/lib/glass/bodies/BrowserBody.svelte | Sandboxed web view or link card | panel | | rebuild | ☑ 6dc2ef51: deleted; the browser-run tile is T-0665 |
| jarvis-web/src/lib/glass/bodies/C2Body.svelte | Command and control: everything running now | panel | | rebuild | ☑ 5e6d20ab: the Ops board |
| jarvis-web/src/lib/glass/bodies/CapabilityBody.svelte | Capability map | panel | | rebuild | ☑ 6d6af418: Integrations' "what JARVIS may do" |
| jarvis-web/src/lib/glass/bodies/CarBody.svelte | Car panels for the /car HUD | panel | | rebuild | ☑ e53a5545: `ds/screens/Car.svelte` |
| jarvis-web/src/lib/glass/bodies/ChatBody.svelte | Chat stream panel | panel | | rebuild | ☑ 83658885: the chat screen |
| jarvis-web/src/lib/glass/bodies/DocumentBody.svelte | Document reader | panel | | rebuild | ☑ 6dc2ef51: deleted; media is T-0665 |
| jarvis-web/src/lib/glass/bodies/GoalsBody.svelte | Standing goal and its subgoals | panel | | rebuild | ☑ 6dc2ef51: deleted; no v2 screen yet (T-0665) |
| jarvis-web/src/lib/glass/bodies/HouseBody.svelte | House devices and controls | panel | | rebuild | ☑ 6dc2ef51: deleted; no v2 screen yet (T-0665) |
| jarvis-web/src/lib/glass/bodies/ImageBody.svelte | Image or camera still | panel | | rebuild | ☑ 6dc2ef51: deleted; media is T-0665 |
| jarvis-web/src/lib/glass/bodies/IntegrationsBody.svelte | Plugins, skills, MCP servers | panel | | rebuild | ☑ 6d6af418: `ds/screens/Integrations.svelte` |
| jarvis-web/src/lib/glass/bodies/JobsBody.svelte | Scheduled jobs | panel | | rebuild | ☑ 6dc2ef51: deleted; no v2 screen yet (T-0665) |
| jarvis-web/src/lib/glass/bodies/KanbanBody.svelte | Tasks by state | panel | | rebuild | ☑ 6dc2ef51: deleted; the kanban is T-0665 |
| jarvis-web/src/lib/glass/bodies/MemoryBody.svelte | Memory review | panel | | rebuild | ☑ 6dc2ef51: deleted; the Ops board counts it, the review is T-0665 |
| jarvis-web/src/lib/glass/bodies/ModelBody.svelte | Active model and its status | panel | | rebuild | ☑ 6dc2ef51: deleted; the chat's model select (83658885), the models UI is T-0665 |
| jarvis-web/src/lib/glass/bodies/ModelsBody.svelte | Model management | panel | | rebuild | ☑ 6dc2ef51: deleted; the models UI is T-0665 |
| jarvis-web/src/lib/glass/bodies/NotificationsBody.svelte | Moments and notices | panel | | rebuild | ☑ 6dc2ef51: deleted; a moment comes out of the core |
| jarvis-web/src/lib/glass/bodies/PairingBody.svelte | Phone pairing QR and code | panel | | rebuild | ☑ 6d6af418: Devices → pair another |
| jarvis-web/src/lib/glass/bodies/SettingsBody.svelte | Schema-generated settings panel | panel | | rebuild | ☑ 6d6af418: `ds/screens/Settings.svelte` |
| jarvis-web/src/lib/glass/bodies/TaskBody.svelte | One task: plan, output, diff | panel | | rebuild | ☑ 6dc2ef51: deleted; the kanban is T-0665 |
| jarvis-web/src/lib/glass/bodies/TasksBody.svelte | Task store, three views | panel | | rebuild | ☑ 6dc2ef51: deleted; the kanban is T-0665 |
| jarvis-web/src/lib/glass/bodies/TelemetryBody.svelte | Gauges and charts | panel | | rebuild | ☑ 5e6d20ab: the Ops board's panes; trends are T-0665 |
| jarvis-web/src/lib/glass/bodies/TimelineBody.svelte | Tool-call timeline, titled "This turn" | panel | LEGACY? ("This turn") | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/glass/bodies/VideoBody.svelte | Published video player | panel | | rebuild | ☑ 6dc2ef51: deleted; media is T-0665 |
| jarvis-web/src/lib/glass/bodies/VoiceBody.svelte | Push-to-talk, level, transcript | panel | | rebuild | ☑ 204d0d64: the shell's mic and the core's listening scene |
| jarvis-web/src/lib/glass/bodies/*.ts (4 files) | index, notices, settingsWidgets, types | other | | keep | ☑ 6dc2ef51 took notices and settingsWidgets; `index.ts` and `types.ts` stay as types |
| jarvis-web/src/lib/ui/CallLine.svelte | One tool call as one line | component | | rebuild | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/ui/Clamp.svelte | Clamped text with Show more | component | | rebuild | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/ui/EmptyState.svelte | Empty state | component | | rebuild | ☑ 6f22d5a2: `ds/ui/State.svelte` (all four states); deleted in 6dc2ef51 |
| jarvis-web/src/lib/ui/ErrorState.svelte | Error state | component | | rebuild | ☑ 6f22d5a2: `ds/ui/State.svelte`; deleted in 6dc2ef51 |
| jarvis-web/src/lib/ui/Field.svelte | Labelled settings control | component | | rebuild | ☑ 6d6af418: `ds/ui/Field.svelte` |
| jarvis-web/src/lib/ui/Graph.svelte | Knowledge graph view | renderer | | rebuild | ☑ 6dc2ef51: deleted; the memory review is T-0665 |
| jarvis-web/src/lib/ui/Input.svelte | Input or textarea | component | | rebuild | ☑ 6d6af418: `ds/ui/Field.svelte` |
| jarvis-web/src/lib/ui/OfflineState.svelte | Backend-down state | component | | rebuild | ☑ 6f22d5a2: `ds/ui/State.svelte`; deleted in 6dc2ef51 |
| jarvis-web/src/lib/ui/Row.svelte | Row of a Rows table | component | | rebuild | ☑ 6d6af418: `ds/screens/Section.svelte`'s rows; deleted in 6dc2ef51 |
| jarvis-web/src/lib/ui/Rows.svelte | Row table list idiom | component | | rebuild | ☑ 6d6af418: `ds/screens/Section.svelte`; deleted in 6dc2ef51 |
| jarvis-web/src/lib/ui/ScreenState.svelte | Four screen states in one place | component | | rebuild | ☑ 6f22d5a2: `ds/ui/State.svelte` |
| jarvis-web/src/lib/ui/Select.svelte | Fixed-list choice | component | | rebuild | ☑ 6f22d5a2: `ds/ui/Segmented.svelte`; deleted in 6dc2ef51 |
| jarvis-web/src/lib/ui/SkeletonRows.svelte | Loading placeholder rows | component | | rebuild | ☑ 6f22d5a2: `ds/ui/State.svelte`'s loading; deleted in 6dc2ef51 |
| jarvis-web/src/lib/ui/StagesBar.svelte | Segmented stages bar | component | | rebuild | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/ui/Toggle.svelte | On/off switch | component | | rebuild | ☑ 6f22d5a2: `ds/ui/Segmented.svelte`; deleted in 6dc2ef51 |
| jarvis-web/src/lib/ui/status.ts | Screen-state type | other | | keep | ☑ kept: `ds/ui/State.svelte` and the screens read it |
| jarvis-web/src/lib/components/CodeDiff.svelte | Code diff view | component | | rebuild | ☑ 6dc2ef51: deleted; the kanban's task view is T-0665 |
| jarvis-web/src/lib/components/Markdown.svelte | Markdown renderer | component | | rebuild | ☑ 6dc2ef51: `ds/screens/Markdown.svelte` |
| jarvis-web/src/lib/orb/Orb.svelte | 3D three.js orb reactor | renderer | | rebuild | ☑ 6dc2ef51: deleted; the core is `$lib/ds/core` (T-0603) |
| jarvis-web/src/lib/orb/ParticleSphere.svelte | Particle sphere round the reactor | renderer | | rebuild | ☑ 6dc2ef51: deleted; the core (T-0603) |
| jarvis-web/src/lib/orb/ConnectionCard.svelte | Card tethered to a sphere | component | LEGACY? (card) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/orb/*.ts (32 files) | Orb scene, materials, shaders, layout worker | renderer | | rebuild | ☑ 6dc2ef51: 27 deleted; personas, companions, connectionCard, geometry and sphere stay for the data mappers |
| jarvis-web/src/lib/reactor/Reactor.svelte | Flat 2D arc reactor instrument | renderer | | rebuild | ☑ 6dc2ef51: deleted; the core (T-0603) |
| jarvis-web/src/lib/reactor/*.ts (4 files) | Reactor spec, state, ignition, index | other | | keep | ☑ kept: spec, state and index (the phone mirrors the spec); ignition went in 6dc2ef51 |
| jarvis-web/src/lib/dashboards/*.ts (4 files) | Chart types, layout, series, house widgets | other | | keep | ☑ kept |
| jarvis-web/src/lib/knowledge/graph.ts | Knowledge graph layout maths | other | | keep | ☑ kept |
| jarvis-web/src/lib/styles/base.css | Page ground, focus ring, motion kill switch | style | | rebuild | ☑ 0c043664: already the v2 ground (the core's glow); its comment names the shell (c756823a) |
| jarvis-web/src/lib/styles/chrome.css | Shared motion (old chrome removed from it) | style | | rebuild | ☑ c756823a: deleted; nothing named its keyframes |
| jarvis-web/src/lib/styles/fonts.css | Self-hosted font faces | style | | rebuild | ☑ 0c043664: the design system's two faces |
| jarvis-web/src/lib/styles/tokens.css | Design tokens (generated) | style | | rebuild | ☑ generated from `design/tokens.json` (`make tokens`) |
| jarvis-web/src/lib/tokens.ts | Tokens as data (generated) | other | | keep | ☑ kept (generated) |
| jarvis-web/src/lib/toast.ts | Toast store | other | LEGACY? (toast) | delete | ☑ 6dc2ef51: deleted |
| jarvis-web/src/lib/motion.ts, textSize.ts | Motion policy and text-size choice | other | | keep | ☑ kept |
| jarvis-web/static/ (5 icons, fonts/ 7 files) | Favicons, app icons, fonts | other | | keep | ☑ kept |

I left out the API `+server.ts` routes, `lib/hermes`, `lib/server`, `lib/audio`, the README.md files, `e2e/` and `*.test.ts`, because none of them draw anything.

## 2. Desktop

### 2a. Tauri app (`jarvis-desktop-app`)

| Path | What it is | Kind | Flag | Fate | Done |
|---|---|---|---|---|---|
| jarvis-desktop-app/ui/index.html | "This computer" window with a six-tab nav | screen | LEGACY? (tab bar) | delete | ☑ T-1331: the tab bar was deleted and the window kept: pairing, permissions, checks and updates have no other home when no console is reachable. Six sections on one scrolling page, each a projection with its label |
| jarvis-desktop-app/ui/desktop.ts | Draws the "This computer" sections and tab switching | screen | LEGACY? (tab bar) | delete | ☑ T-1331: tab switching deleted, window kept for the same reason; `#section` scrolls the section into view and focuses its heading, every Tauri call unchanged |
| jarvis-desktop-app/ui/app.css | Styles for all three pages, including `.tabs` and `.tab` | style | LEGACY? (tab styles) | delete | ☑ T-1331: `.tabs`/`.tab` deleted, file kept (all three pages use it); projections, labels, stage ground, tokens only |
| jarvis-desktop-app/ui/consent.html | Tier-3 approval prompt window | dialog | | rebuild | ☑ T-1331: one projection on the ground, amber "waiting on you" label and bracket, Approve the one filled control |
| jarvis-desktop-app/ui/consent.ts | Approval prompt script | dialog | | rebuild | ☑ T-1331: kept as is, closing still denies; the new look is all in the page and `app.css` |
| jarvis-desktop-app/ui/unreachable.html | "No console there yet" page | screen | | rebuild | ☑ T-1331: one projection with an "Offline" label; the way to another console is its one filled control |
| jarvis-desktop-app/ui/unreachable.ts | Unreachable page script | other | | keep | ☐ |
| jarvis-desktop-app/ui/logic.ts | Pure decisions the pages draw | other | | keep | ☐ |
| jarvis-desktop-app/ui/tokens.css | Design tokens (generated) | style | | rebuild | ☐ |
| jarvis-desktop-app/ui/tokens.ts | Tokens as data (generated) | other | | keep | ☐ |
| jarvis-desktop-app/ui/tauri.d.ts | Tauri global typings | other | | keep | ☐ |
| jarvis-desktop-app/src-tauri/src/shell.rs | Tray menu, four windows, native notifications | other | LEGACY? (native notification toasts) | delete | ☐ |
| jarvis-desktop-app/src-tauri/logic/src/labels.rs | Tray status label text | other | | keep | ☐ |
| jarvis-desktop-app/src-tauri/tauri.conf.json | Window and bundle config | other | | keep | ☐ |
| jarvis-desktop-app/icons/ (9), src-tauri/icons/ (7) | App and tray icons | other | | keep | ☐ |

The popup window has no HTML of its own. It loads the web console at `/?popup=1`, so its UI is web `PopupHud.svelte` plus the frosted `.hud.floating` card in `Hud.svelte`. The console window embeds the web console. `dist/` is an untracked copy of `ui/` made at build time, so I skipped it.

### 2b. Python agent (`aura-desktop/aura_desktop`)

| Path | What it is | Kind | Flag | Fate | Done |
|---|---|---|---|---|---|
| aura-desktop/aura_desktop/consent.py | Tier-3 consent: Tk dialog, terminal and shell prompts | dialog | | rebuild | ☐ |
| aura-desktop/aura_desktop/companion.py | Tk ask dialog, terminal asker, notify-send notifier | dialog | LEGACY? (desktop notification toast) | delete | ☐ |
| aura-desktop/aura_desktop/codepopup.py | Tk popup that slides in top-right with a one-time code | dialog | LEGACY? (toast-style corner popup) | delete | ☐ |
| aura-desktop/aura_desktop/theme.py | Tk styling for the dialogs | style | | rebuild | ☐ |
| aura-desktop/aura_desktop/tokens.py | Palette (generated) | style | | rebuild | ☐ |
| aura-desktop/aura_desktop/reactor.py | Arc reactor drawn with ANSI terminal glyphs | renderer | | rebuild | ☐ |
| aura-desktop/aura_desktop/status.py | Text output of the `status` command | other | | keep | ☐ |
| aura-desktop/aura_desktop/actions/system.py | `notify` action via notify-send or win10toast | toast | LEGACY? (toast) | delete | ☐ |
| aura-desktop/aura_desktop/__main__.py | CLI output; wires up the code popups | other | | keep | ☐ |
| aura-desktop/aura_desktop/console/__init__.py | Terminal glass console package | other | | keep | ☐ |
| aura-desktop/aura_desktop/console/app.py | Console loop: socket, keys, frames, `--demo` | screen | | rebuild | ☐ |
| aura-desktop/aura_desktop/console/panes.py | Layout to pane cells; Focus rail (right side) and strip | layout | LEGACY? (rail) | delete | ☐ |
| aura-desktop/aura_desktop/console/render.py | Frame painter: panels, content cards, rail, strip | renderer | LEGACY? (draws rail and content cards) | delete | ☐ |
| aura-desktop/aura_desktop/console/state.py | Console state reducer (content cards) | other | | keep | ☐ |
| aura-desktop/aura_desktop/console/theme.py | Terminal palette and glyph painter | style | | rebuild | ☐ |
| aura-desktop/aura_desktop/console/console_theme.json | Console theme (generated) | style | | rebuild | ☐ |

## 3. Phone (`android-app/app/src/main`)

There are no `res/layout/*.xml` files and no app widgets. No notification uses a custom RemoteViews layout; they only use built-in styles.

| Path | What it is | Kind | Flag | Fate | Done |
|---|---|---|---|---|---|
| kotlin/ai/jarvis/app/MainActivity.kt | Home: orb, HUD Compose screen, settings body | screen | LEGACY? (Reactor 3D/2D, Panels, Selection toggles; toasts) | delete | ☑ console only (T-0667): the home is ConsolePage, no native HUD |
| kotlin/ai/jarvis/app/SettingsActivity.kt | Connection settings (Views), Layout section | screen | LEGACY? (Panels and Selection choices; ConsoleFrame tab bar; toasts) | delete | ☑ Layout section deleted (T-0667); one Compose screen on ui/v2 with ScreenState and ScreenNote, no toasts (T-0689) |
| kotlin/ai/jarvis/app/JarvisSettingsActivity.kt | Native Hermes settings (Compose) | screen | | rebuild | ☑ folded into SettingsActivity's Jarvis section, the name kept as a manifest alias (T-0689) |
| kotlin/ai/jarvis/app/JarvisAssistActivity.kt | Assist activation surface | screen | | rebuild | ☑ kept on Views drawn by `V2Words`, the kit's View face it shares with the overlay (DECISIONS #77) |
| kotlin/ai/jarvis/app/VoiceIdentityActivity.kt | Voice enrol and verify | screen | LEGACY? (toast) | delete | ☑ rebuilt on the v2 kit, forgets held not tapped (T-0639, `phone-voice.png`) |
| kotlin/ai/jarvis/app/ApprovalActivity.kt | Tier-3 consent screen | dialog | LEGACY? (toast) | delete | ☑ rebuilt in v2 Compose (T-0639): the T-0606 card, hold to allow, rule chips; no toast |
| kotlin/ai/jarvis/app/NfcTagActivity.kt | One-frame NFC read/write, toast prompt | other | LEGACY? (toast) | delete | ☑ no screen of its own; its one prompt ("hold a tag") is a ScreenNote in a window it now has (`Theme.JarvisOneFrame`, T-0639) |
| kotlin/ai/jarvis/app/ScanCodeActivity.kt | One-frame barcode scan | other | | keep | ☑ kept, no screen of its own; on `Theme.JarvisOneFrame`, since a no-display window that outlives `onResume` crashes (T-0639) |
| kotlin/ai/jarvis/app/PermissionRequestActivity.kt | One-frame permission ask | other | | keep | ☑ kept, no screen of its own; moved off the no-display theme, which crashed the process on every request (`OneFrameActivityTest`, T-0639) |
| kotlin/ai/jarvis/app/ListenTrampolineActivity.kt | One-frame listen trampoline | other | | keep | ☑ kept, no screen of its own: it finishes in `onCreate`, the one activity the no-display theme is for |
| kotlin/ai/jarvis/app/ui/ConsoleFrame.kt | Console nav strip that every screen wears | tab bar | LEGACY? (tab bar) | delete | ☑ deleted (T-0639) |
| kotlin/ai/jarvis/app/ui/ConsoleTab.kt | The two settings tabs the bar names | tab bar | LEGACY? (tab bar) | delete | ☑ deleted (T-0639) |
| kotlin/ai/jarvis/app/ui/SectionStrip.kt | Segmented in-page section strip | tab bar | LEGACY? (tab strip) | delete | ☑ deleted (T-0639) |
| kotlin/ai/jarvis/app/ui/JarvisUi.kt | Shared View kit: panels, buttons, tab underline, dialogs | style | LEGACY? (panel style; tab underline) | delete | ☑ the legacy parts deleted (panel, banner, chooser, check rows, status tags, screen titles: 447 of its 1,192 lines); what stays is the View face the overlays, the assist and companion cards and the car's setup still draw with (DECISIONS #78) |
| kotlin/ai/jarvis/app/ui/PanelCard.kt | HUD panel drawn as a card, by kind | panel | LEGACY? (card) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/ui/GoalCard.kt | Goal-loop card on the orb screen | component | LEGACY? (card) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/ui/ListActivityScaffold.kt | Skeleton for list screens, with toast | layout | LEGACY? (toast) | delete | ☑ rebuilt on the v2 kit: one projection of hairline rows (T-0639) |
| kotlin/ai/jarvis/app/ui/JarvisScreens.kt | Launch-by-name; "not available" toast | other | LEGACY? (toast) | delete | ☑ kept; its "not available" line is a ScreenNote |
| kotlin/ai/jarvis/app/ui/ConsolePage.kt | Console kept loaded in a WebView | screen | | rebuild | ☑ kept: the phone's home is the v2 console itself (T-0639 step 3), so it draws the console's own screens |
| kotlin/ai/jarvis/app/ui/CrashLogActivity.kt | Crash log screen | screen | | rebuild | ☑ rebuilt on the v2 kit, Clear held (T-0639) |
| kotlin/ai/jarvis/app/ui/SystemCheckActivity.kt | What's-missing screen | screen | | rebuild | ☑ rebuilt on the v2 kit (T-0639, `phone-system-check.png`) |
| kotlin/ai/jarvis/app/ui/DoorActivity.kt | Frame for screens that wake a locked phone | layout | | rebuild | ☑ kept: the lock-screen frame under the approval card (on the kit) and the companion card |
| kotlin/ai/jarvis/app/ui/ActivityStrip.kt | Living activity rows under the reactor | component | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/ui/KnowledgeGraphView.kt | Knowledge graph View | renderer | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/ui/PanelChartView.kt | Small chart View | renderer | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/ui/PanelMarkdown.kt | Markdown subset for text panels | component | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/ui/ReadabilityScrim.kt | Scrim behind orb text | component | | rebuild | ☑ moved to assist/ (T-0667): the overlay is its one user |
| kotlin/ai/jarvis/app/ui/ScreenStates.kt | Loading, empty, error and offline states | component | | rebuild | ☑ deleted with its `screen-states` golden; the kit's `ScreenState` is the four states (T-0639) |
| kotlin/ai/jarvis/app/ui/StateDot.kt | Status dot | component | | rebuild | ☑ kept on Views inside the tool-activity row of the overlay (DECISIONS #79): an accent dot on `motion.dur.pulse`, no panel |
| kotlin/ai/jarvis/app/ui/Notify.kt | Notification look that echoes the reactor | other | | keep | ☑ kept: notification styles have no screen (T-0639 step 6) |
| kotlin/ai/jarvis/app/ui/ApprovalBridge.kt, PermissionBridge.kt | Consent and permission notifications | other | | keep | ☑ kept: notifications, no screen |
| kotlin/ai/jarvis/app/ui/{ConsentGate,ConsentText,PromptPresence,ForegroundResultBridge}.kt | Logic and data for the UI (4 files; PanelData.kt deleted, T-0680) | other | | keep | ☑ kept: logic, no screen |
| kotlin/ai/jarvis/app/ui/theme/JarvisTheme.kt, JarvisTokens.kt | Compose theme and tokens (generated) | style | | rebuild | ☑ generated from `design/tokens.json` (`make tokens`); the kit draws on them |
| kotlin/ai/jarvis/app/ui/theme/PersonaAccent.kt | Accent colour per persona | style | | rebuild | ☑ kept: the persona's accent the kit's ACCENT and the core read |
| kotlin/ai/jarvis/app/ui/theme/PersonaIcon.kt | Launcher icon per persona | other | | keep | ☑ kept |
| kotlin/ai/jarvis/app/glass/Hud.kt | Glass HUD host (Compose) | layout | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Rail.kt | Focus-mode rail: strip, or side column on tablets | rail | LEGACY? (rail) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Selection.kt | Panels Tabs/Tiles and Selection arrangement | other | LEGACY? (Panels Tabs/Tiles, Selection) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Pane.kt | Pane-grade glass | component | LEGACY? (glass panel style) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Panel.kt | Panel-grade glass | component | LEGACY? (glass panel style) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/PanelFrame.kt | Panel title-bar frame | panel | LEGACY? (glass panel frame) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Surfaces.kt | Draws the glass fill and edge | style | LEGACY? (glass panel style) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Material.kt | Glass material table | style | LEGACY? (glass panel style) | delete | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Controls.kt | Glass buttons and choices (GlassChoice) | component | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Control.kt | Control-grade glass | component | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Boot.kt | Boot moment | component | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Stack.kt | Vertical carousel stack | layout | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Tree.kt | Tiled tree layout | layout | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/Strip.kt | Ambient-mode transcript strip | component | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/States.kt | Panel content states | component | | rebuild | ☑ deleted (T-0667) |
| kotlin/ai/jarvis/app/glass/ReactorView.kt | Glass reactor as a View | renderer | | rebuild | ☑ draws the v2 core (`reactor/core/V2Core`) on every View host |
| kotlin/ai/jarvis/app/glass/HudReactor.kt | Reactor inputs as Compose state | other | | keep | ☑ T-0779: `HudReactor` deleted; `ReactorModel` moved to `glass/ReactorModel.kt` |
| kotlin/ai/jarvis/app/glass/{Depth,Haptics,HudModel,Layout,LayoutOps,PanelTypes,Prefs,ReactorFeed,StackModel,SurfaceKind}.kt | HUD models (10 files) | other | | keep | ☑ Haptics, HudReactor, ReactorFeed and ReactorView stay (reactor hosts, T-0667); the other models are deleted |
| kotlin/ai/jarvis/app/reactor/Reactor.kt | Compose reactor; ReactorLook picks 3D or 2D | renderer | LEGACY? (2D/3D look switch model) | delete | ☑ ReactorLook deleted (T-0667); the composable deleted (T-0779) |
| kotlin/ai/jarvis/app/reactor/Instrument.kt | Flat 2D C2 instrument | renderer | | rebuild | ☑ deleted (T-0779): reachable only from the pre-v2 composable |
| kotlin/ai/jarvis/app/reactor/ReactorShader.kt | AGSL glass reactor shader | renderer | | rebuild | ☑ deleted (T-0779) |
| kotlin/ai/jarvis/app/reactor/ReactorCanvas.kt | Canvas fallback reactor | renderer | | rebuild | ☑ deleted (T-1299), with `ReactorScreenshotTest` and its fourteen goldens: the car's drive draws `CoreCanvas` (T-0722) and the phone V2Core (T-0779) |
| kotlin/ai/jarvis/app/reactor/{ReactorAnimator,ReactorFrame,ReactorHaptics,ReactorSpec,ReactorState}.kt | Reactor clock, spec, state (5 files) | other | | keep | ☑ T-0779: ReactorAnimator deleted with its composable; T-1299: ReactorFrame and ReactorSpec deleted too (their last readers were the Canvas and its screenshot test); haptics and state kept (the car and V2Core use them) |
| kotlin/ai/jarvis/app/reactor/orb/OrbView.kt, OrbRenderer.kt | GL orb view and renderer | renderer | | rebuild | ☑ deleted with `assets/orb` (T-0779) |
| kotlin/ai/jarvis/app/reactor/orb/{OrbGeometry,OrbMath,OrbMesh,OrbMotion,OrbScene}.kt | Orb geometry and motion (5 files) | renderer | | rebuild | ☑ deleted (T-0779) |
| kotlin/ai/jarvis/app/surface/SurfaceView.kt | House surface lines on the voice screen | component | | rebuild | ☑ kept on Views in the overlay (DECISIONS #79): hairline rows on tokens, the caption the v2 label (T-0639, `voice-surface`) |
| kotlin/ai/jarvis/app/surface/TaskDockView.kt | Task dock under the instrument | component | | rebuild | ☑ kept on Views in the overlay (DECISIONS #79): flat bars, the caption the v2 label (T-0639, `voice-task-dock`) |
| kotlin/ai/jarvis/app/surface/SurfaceWatch.kt | Surface data | other | | keep | ☑ kept: data |
| kotlin/ai/jarvis/app/assist/AssistOverlay.kt | Overlay window over other apps | screen | | rebuild | ☑ kept on Views as a window over other apps, drawn by `V2Words` (DECISIONS #77, `assist-popup`) |
| kotlin/ai/jarvis/app/assist/ToolActivityView.kt | Live tool-activity view | component | | rebuild | ☑ kept on Views in the overlay (DECISIONS #79): sentence rows, the caption the v2 label |
| kotlin/ai/jarvis/app/assist/hermes/ClarifyPrompt.kt | Clarify question dialog | dialog | | rebuild | ☑ kept as a platform dialog: from the wake service it must draw over other apps with no activity (DECISIONS #79); the question itself is also Jarvis's line on the card |
| kotlin/ai/jarvis/app/assist/hermes/GoalSheet.kt | "Set goal" sheet | dialog | | rebuild | ☑ kept as a platform dialog for the same reason (DECISIONS #79) |
| kotlin/ai/jarvis/app/assist/WakeTileService.kt | "Hey Jarvis" quick-settings tile | other | | keep | ☑ kept: a tile, no screen |
| kotlin/ai/jarvis/app/assist/{WakeWordService,VoiceClaim,PhoneAction}.kt | Foreground and action notifications (3 files) | other | | keep | ☑ kept: notifications have no screen (T-0639 step 6) |
| kotlin/ai/jarvis/app/assist/JarvisVoiceInteractionSession.kt | Opens the assist UI | other | | keep | ☑ kept: opens the assist card, no screen |
| kotlin/ai/jarvis/app/assist/ActivityRows.kt, KnowledgeGraph.kt | Activity-row and graph maths | other | | keep | ☑ kept: maths |
| kotlin/ai/jarvis/app/automation/ui/ActionPolicyActivity.kt | Action policy screen | screen | LEGACY? (toast) | delete | ☑ rebuilt on the v2 kit: rule chips per action, NEVER in the danger colour, the reset held (T-0639, `phone-action-policy.png`) |
| kotlin/ai/jarvis/app/automation/ui/AuditLogActivity.kt | Audit log screen | screen | | rebuild | ☑ rebuilt on the v2 kit, Clear held (T-0639) |
| kotlin/ai/jarvis/app/automation/ui/AutomationsActivity.kt | Automations list screen | screen | | rebuild | ☑ rebuilt on the v2 kit (T-0639, `phone-tasks.png`) |
| kotlin/ai/jarvis/app/automation/actions/builtin/ParityActions.kt | `ShowToast` action | toast | LEGACY? (toast) | delete | ☑ kept: a task's own `show_toast` runs from a service with no screen, where a ScreenNote cannot reach (DECISIONS #78) |
| kotlin/ai/jarvis/app/automation/{JarvisAutomationService,triggers/ReminderReceiver,actions/builtin/CommsActions}.kt | Service, reminder and comms notifications (3 files) | other | | keep | ☑ kept: notifications have no screen |
| kotlin/ai/jarvis/app/companion/CompanionAskActivity.kt | Question screen with orb and answers | screen | | rebuild | ☑ kept on Views, like the assist card (DECISIONS #78) |
| kotlin/ai/jarvis/app/companion/CompanionNotifications.kt | Proactive message notifications | other | | keep | ☑ kept: notifications have no screen |
| kotlin/ai/jarvis/app/tasks/TaskOverlay.kt | Floating work chip overlay | component | | rebuild | ☑ kept on Views as a chip over other apps (DECISIONS #79, `task-overlay`) |
| kotlin/ai/jarvis/app/tasks/TaskProgressView.kt | Task progress bars | component | | rebuild | ☑ kept on Views in the chip (DECISIONS #79): flat bars, the caption the v2 label (`task-overlay`) |
| kotlin/ai/jarvis/app/tasks/TaskNotifier.kt | Task progress notification | other | | keep | ☑ kept: a notification, no screen |
| kotlin/ai/jarvis/app/update/UpdateNotification.kt, InstallResultReceiver.kt | Update notifications | other | | keep | ☑ kept: notifications, no screen |
| kotlin/ai/jarvis/app/routines/RoutineWatcher.kt | Routine notifications | other | | keep | ☑ kept: notifications, no screen |
| res/values/{colors,strings,themes,tokens}.xml | Theme, colours, tokens, strings | style | | rebuild | ☑ `tokens.xml` and `colors.xml` generated from `design/tokens.json`; `themes.xml` gains `Theme.JarvisOneFrame` (T-0639) |
| res/values-v31/themes.xml | Splash-screen theme | style | | rebuild | ☑ kept: the splash adds to the base theme on the ground colour |
| res/drawable/ (8 files) | Launcher, persona and status vector icons | other | | keep | ☑ kept |
| res/font/ (5 files), res/mipmap*/ (5 files) | Fonts and launcher icons | other | | keep | ☑ kept |
| assets/console-offline.html | Offline page for the console WebView | screen | | rebuild | ☑ generated from the console's `/offline` route (`offline-asset.mjs`), so it is the console's own v2 page |
| assets/orb/ (11 files) | GLSL shaders for the GL orb | renderer | | delete | ☑ deleted with the GL orb they shaded (T-0779) |
| assets/fonts/ (2 files) | Font licences | other | | keep | ☑ kept |

All paths in this table are relative to `android-app/app/src/main/`. I skipped `res/xml` (service configs) and `src/debug/` (a test host activity).

## 4. Car

Paths are relative to `android-app/app/src/main/kotlin/ai/jarvis/app/car/` unless they start with `android-app/car/`.

| Path | What it is | Kind | Flag | Fate | Done |
|---|---|---|---|---|---|
| holo/CarNavScreen.kt | Car App Library NavigationTemplate: the drawn drive, one maneuver, the one card | screen | LIVE (replaced the pane screen, T-0613) | keep | ☑ kept: the drawn drive on the host's surface, no rail, tab bar or typed value; its two short messages (not set up, needs the microphone) are drawn in the subtitle zone, no `CarToast` (T-1335) |
| templated/CarOrbRenderer.kt | Reactor still for the template; reads ReactorLook | renderer | | rebuild | ☑ deleted (T-0613, `dcca5e8eb`): the map surface draws the hologram drive and the reactor core itself, so no still is drawn for the template |
| templated/JarvisCarAppService.kt | Car App service entry | other | | keep | ☑ kept, draws nothing: builds the host validator and hands the host `CarNavScreen` |
| dash/CarShell.kt | Car screen: rail, stage, panes, rail editor, tab icons | layout | LEGACY? (rail; layouts pinned as tabs) | delete | ☐ alive: still the screen drawn on the head unit's own display (built by `dash/CarSurface`, driven by `CarLinkService`, `CarVoiceStream` and `WakeWordService`); it still lays out the rail, tabs, floating cards and the rail editor, though the rail is hidden while the console page is up |
| dash/CarHud.kt | Console `/car` HUD in a WebView | screen | LEGACY? (toast) | delete | ☐ alive: the car's main page is the console's own `/car` in this WebView (used by `CarShell`, `HostedTiles`, `ContentTiles` and the voice turn); one `Toast` remains, in `say`, when no shell is up |
| dash/CarStartup.kt | First frame on the head unit | screen | | rebuild | ☑ the reactor igniting (`ReactorView`, the v2 core) with the wordmark under it, timed by `motion.dur.enter` and `slow`; no rail, tab, card or toast, no typed value; its ground and text read the car palette, which is the token palette unless the driver picks another (cc28d93ed) |
| dash/CarSheet.kt | Full-height choice sheet | dialog | | rebuild | ☐ a column of full-width rows over the dash, used by `CarShell` and `CarAppTile` for settings, theme, rail and pane menus; drawn with `Headway` colours and a token scrim; not read against the kit's sheet, so unknown whether it already matches |
| dash/PaneTreeView.kt | Pane tree as views, with dividers | layout | | rebuild | ☐ alive: `CarShell`'s split panes with draggable dividers and a per-pane menu, the tiled layout the v2 language drops; token sizes, no typed value |
| dash/DashTile.kt | One pane's content contract | component | | rebuild | ☐ the interface every pane tile implements (draws nothing itself); stays or goes with the pane system, so open with `PaneTreeView` |
| dash/CarGlyphs.kt | Car screen symbols | renderer | | rebuild | ☐ draws the control glyphs and 22 tab icons by hand on the `Headway` palette; the tab icons exist for the rail and go with it |
| dash/CarSurface.kt, CarDisplay.kt | Car display host | other | | keep | ☑ kept, draw nothing: the virtual display and the window `CarShell` lives in, and the encoder hand-off |
| dash/DashLayoutStore.kt, CarMusicLevel.kt | Saved dashboards; music level | other | | keep | ☑ kept, draw nothing: stored layouts and the music level reading; the store still keeps the tab and rail items the legacy shell reads, which go when it does |
| dash/tiles/JarvisTile.kt | Jarvis tab: reactor centre stage | panel | LEGACY? (tab) | delete | ☐ alive: `CarShell` builds it for the Jarvis pane (reactor, last line said, a touch to talk); a tab of the legacy shell |
| dash/tiles/MapsTile.kt | Next turn and map link | panel | | rebuild | ☐ draws the next instruction and a map link in `CarStyle`'s tile look on the `Headway` palette, inside the pane shell; not rebuilt |
| dash/tiles/MediaBrowseTile.kt | Media library browser | panel | | rebuild | ☐ draws a media app's library as rows in `CarStyle`'s tile look (13 uses) inside the pane shell; not rebuilt |
| dash/tiles/PhoneTile.kt | Calls pane | panel | | rebuild | ☐ draws the live call and recent calls in `CarStyle`'s tile look (16 uses) inside the pane shell; not rebuilt |
| dash/tiles/SensorsTile.kt | Car readings pane | panel | | rebuild | ☐ draws the car's readings in `CarStyle`'s tile look on the `Headway` palette inside the pane shell; not rebuilt |
| dash/tiles/ContentTiles.kt | Now-playing, notifications, messages and clock tiles | panel | | rebuild | ☐ four panes (2,883 lines) in `CarStyle`'s tile look (76 uses), with message cards, inside the pane shell; also holds the notification listener, which stays; not rebuilt |
| dash/tiles/HostedTiles.kt | Widget and launcher tiles | panel | | rebuild | ☐ hosts another app's own widget pixels and draws the launcher grid inside the pane shell; the widget's pixels are not ours, the launcher grid is unread against the kit, so unknown |
| dash/tiles/AppPaneTile.kt | Running real app in a pane | panel | | rebuild | ☐ the frame around a mirrored app picture, inside the pane shell, on the `Headway` palette; not rebuilt |
| dash/tiles/CarAppTile.kt | Third-party car-app interface pane | panel | | rebuild | ☐ the app chooser and the host of a third-party car app's templates, in `CarStyle`'s tile look, inside the pane shell; not rebuilt |
| dash/tiles/CarStyle.kt | Shared tile look, card corner radius | style | LEGACY? (card style) | delete | ☐ alive: every tile and `TemplateRenderer` build their panels from it; its radius is `JarvisTokens.Radius.LG` times the driver's corner choice |
| dash/tiles/WidgetSetupActivity.kt | Widget setup steps | screen | | rebuild | ☑ kept, no screen of its own: a transparent window (`Theme.JarvisCarInvisible`) that sets no content, so the driver sees only Android's bind dialog or the widget's own configure screen; nothing to rebuild (T-0710) |
| setup/CarSetupActivity.kt | One-time car setup | screen | v2 kit; its messages are a notice line on the screen (T-1324) | rebuilt | ☑ |
| setup/CarSetupScreen.kt | Compose setup steps | screen | | rebuild | ☑ rebuilt on the v2 kit: the stage, the gates and the choices as projections, one filled primary; nothing on it is destructive, so nothing is held (T-0710, `phone-car-setup.png`, `car-setup-*.png`) |
| setup/CarProfile.kt, GateReport.kt | Setup data | other | | keep | ☑ kept, draw nothing: the stored profile and the gate report state |
| theme/Headway.kt | Car colours, spacing, motion | style | | rebuild | ☐ motion and spacing read `JarvisTokens`, and the default palette is the token palette (`JarvisCarPalette`); but it still carries the driver's corner multiplier and, for any other picked theme, Headway's typed palettes from `core-dash` `CarTheme.kt` |
| theme/HeadwayTheme.kt, JarvisCarPalette.kt | Car palette selection | style | | rebuild | ☐ `JarvisCarPalette` is on tokens (all twelve slots); `HeadwayTheme` still stores and applies the driver's choice among Headway's themes, a choice the v2 language does not have |
| theme/CarControls.kt | Drawn transport controls | component | | rebuild | ☐ previous, play and next drawn as glyphs on the `Headway` palette, used by the now-playing pane; no rail, tab, card or toast; not read against the kit's one filled primary, so unknown |
| carapp/TemplateRenderer.kt | Draws car-app templates with own widgets | renderer | | rebuild | ☐ draws a third-party car app's lists, grids and tab templates (31 tab mentions) with `CarStyle` and the `Headway` palette; its words and colours come from resources and tokens (c23e2a7d1), but it still draws tabs and cards |
| carapp/CarAppSurfaceView.kt | Surface where car apps draw maps | component | | keep (was rebuild) | ☑ kept, nothing to rebuild: a `TextureView` that only carries the third-party app's own map pixels; Jarvis draws nothing on it |
| carapp/CarAppSession.kt | Template host; relays car-app toasts | other | LEGACY? (toast relay) | delete | ☐ alive: the template host `CarAppTile` runs a third-party car app through; the flag is stale, since `showToast` now only writes a line to the session log and shows nothing |
| video/ProjectionRequestActivity.kt | Asks for screen sharing | screen | | keep (was rebuild) | ☑ kept, no screen of its own: it sets no content and runs on `Theme.JarvisCarInvisible`, so the driver sees only Android's own sharing prompt; nothing to rebuild |
| video/{AppPaneHost,CarAppDisplay,OverlayDisplay}.kt | Live app picture and displays (3 files) | other | | keep | ☑ kept, draw nothing: the projection's picture and the virtual displays |
| input/HeadwayAccessibilityService.kt | Invisible touch overlay | other | | keep | ☑ kept: the touch relay draws nothing; its one drawn thing is a plain black cover over the phone screen while projecting (a framework colour, not a palette one) |
| {service/CarLinkService,link/CarPresence,voice/DriveEnd}.kt | Car link notifications (3 files) | other | | keep | ☑ kept: notifications on `Notify`'s template colours, no screen, no toast; its car-side ask goes through `CarShell` |
| HeadwaySettings.kt | Stored car UI choices (rail, theme) | other | | keep | ☑ kept, draws nothing: preferences only; it still stores the rail, theme and corner choices the legacy shell reads, which go when it does |
| android-app/car/core-dash/…/Rail.kt | Rail items model | rail | LEGACY? (rail) | delete | ☐ alive: read by `CarShell`, `DashLayoutStore` and `HeadwaySettings` |
| android-app/car/core-dash/…/RailStyle.kt | Rail edge (top by default; can be left) | rail | LEGACY? (rail) | delete | ☐ alive: read by `CarShell` and `HeadwaySettings` |
| android-app/car/core-dash/…/TabIcon.kt | Icons for tabs on the rail | tab bar | LEGACY? (tab bar) | delete | ☐ alive: read by `CarShell`, `DashLayoutStore` and `CarGlyphs` |
| android-app/car/core-dash/…/OverlaySpot.kt | Floating card position and size | layout | LEGACY? (card) | delete | ☐ alive: read by `CarShell` and `HeadwaySettings` for the floating call card |
| android-app/car/core-dash/…/CarTheme.kt | Car and phone colour model | style | | rebuild | ☐ still types about 40 colours by hand (three bases, six accents); the app's token lint does not walk `android-app/car/`, so it is not counted; only `JarvisCarPalette` reads it, for any theme but the default |
| android-app/car/core-dash/…/CornerStyle.kt | Corner rounding | style | | rebuild | ☐ the driver's corner-rounding choice, read by `Headway`, `HeadwaySettings` and `CarShell`; the v2 language has one radius, so it goes with the choice |
| android-app/car/core-dash/…/{DashLayout,PaneFit}.kt | Pane tree and fit | layout | | rebuild | ☐ the pane tree (`DashLayout`, read by `PaneTreeView`, `DashLayoutStore`, `CarShell`) and the app pane's fit (`PaneFit`, read by `AppPaneTile`, `AppPaneHost`); model only, they go or stay with the pane system |
| android-app/car/core-dash/…/{PaneKind,CarUiScale,CarUnits,AllowedApps}.kt | Pane models (4 files) | other | | keep | ☑ kept: pure models and scale maths, no view, colour or toast |
| android-app/car/core-video/…/ScreenEncoder.kt | Encodes the car screen surface | other | | keep | ☑ kept, draws nothing: the encoder |

The other `car-*` modules (protocol, transport, voice, audio, input, emulator) draw nothing.

Checked against the tree at T-1334: one file is gone (`templated/CarOrbRenderer.kt`), eleven keep rows are ticked after a read, and two rebuild rows (`CarAppSurfaceView`, `ProjectionRequestActivity`) became keep because they draw nothing of ours. Every delete row that is still open is alive: the car has two screens, the Car App Library drive (`CarNavScreen`, done) and the older `CarShell` dash on the head unit's own display, and nothing has removed the second yet.

### Still open

The legacy dash, to delete together once `CarShell` is replaced (each is alive and read by it):
- `dash/CarShell.kt`: the rail, tabs, floating cards and rail editor.
- `dash/CarHud.kt`: the `/car` console page in a WebView, still the dash's main page; one `Toast` left in `say`.
- `dash/tiles/JarvisTile.kt`: the Jarvis tab.
- `dash/tiles/CarStyle.kt`: the shared card look every tile builds from.
- `carapp/CarAppSession.kt`: the third-party car-app host; its toast relay is already silent, so the flag is stale and only the host remains.
- `core-dash` `Rail.kt`, `RailStyle.kt`, `TabIcon.kt`, `OverlaySpot.kt`: the models the shell, the layout store and `HeadwaySettings` read.

The pane system and its tiles, rebuilt or dropped with the dash (drawn in `CarStyle`'s tile look on the `Headway` palette):
- `dash/PaneTreeView.kt`, `dash/DashTile.kt`, `dash/CarSheet.kt`, `dash/CarGlyphs.kt`, `core-dash` `{DashLayout,PaneFit}.kt`.
- `dash/tiles/` `MapsTile`, `MediaBrowseTile`, `PhoneTile`, `SensorsTile`, `ContentTiles`, `HostedTiles`, `AppPaneTile`, `CarAppTile`, and `carapp/TemplateRenderer.kt`.

The palette, still typed for any theme but the default:
- `theme/Headway.kt`, `theme/HeadwayTheme.kt` with `JarvisCarPalette.kt`, `theme/CarControls.kt`, `core-dash` `CarTheme.kt` (about 40 typed colours, outside the token lint's walk) and `CornerStyle.kt`.

Smaller:
- `holo/CarNavScreen.kt`: its two `CarToast` lines moved onto the screen (T-1335), so nothing is open here; `dash/CarHud.kt`'s `say` Toast stays.
- `dash/CarSheet.kt`, `theme/CarControls.kt` and `dash/tiles/HostedTiles.kt` were not read against the kit, so whether they already match is unknown.

## File counts per surface

| Surface | Files |
|---|---|---|---|
| Web console | 102 Svelte/CSS/HTML files (96 `.svelte`, 5 `.css`, 1 `.html`), 3 route TS files, and 141 supporting TS files in grouped rows (plus 2 JSON data files and 12 static files) |
| Desktop: Tauri app | 14 source files (11 in `ui/` plus `shell.rs`, `labels.rs`, `tauri.conf.json`) and 16 icons |
| Desktop: Python agent | 16 files (9 modules plus 7 in `console/`) |
| Phone | 150 files: 113 Kotlin, 23 resources, 14 assets |
| Car | 58 files: 45 in app `car/`, 12 in `car-dash`, 1 in `car-video` |
