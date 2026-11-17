# HUD.md — the v3 design plan

The accepted plan for JARVIS v3's interface, across the web console, the desktop shell, the Android
app and the terminal console. It replaces the Glass HUD plan, which described the v2
window manager. The decisions behind it are [ADR-0036](../adr/0036-the-map-replaces-the-tiler.md)
(the map), [ADR-0031](../adr/0031-reactor-ii-returns.md) (the reactor is the instrument) and
[ADR-0032](../adr/0032-the-room-has-no-tabs.md) (no tabs). The audit and the deletions list are
the v3 plan.

Nothing here is drawn from a film or a game interface. There is no third-party asset, no trademark,
no likeness, and no font CDN — JARVIS is local-only, so both typefaces are vendored.

---

## 1. The concept

**A volumetric display in a dark room.** Panels are light projected into space, not objects sitting
on a surface. They glow from within; their edges are luminous contour lines; their content sits on a
faint substrate. Depth comes from scale, parallax, atmospheric falloff and focus — never from a drop
shadow. A panel that is far away is smaller, dimmer and slightly desaturated, the way a light source
is across a room.

The room has one light in it: the reactor. Its state colour is the room's colour, so the whole HUD
changes mood when JARVIS's state changes, and a person can read the system from the corner of their
eye without reading a word.

---

## 2. Palette

Six bases and four states. No hue is new in v3; the identity is the cyan-on-void the console already
has, and the whole table lives in `design/tokens.json`.

| Role | Token | Hex | Used for |
|---|---|---|---|
| Void | `color.bg` | `#03070b` | The room's ground, never flat — the reactor's halo is painted onto it |
| Hologram tint | `color.surface-2` | `#0a141c` | The one tint every emissive surface uses, at its own alpha |
| Primary emissive | `color.accent` | `#4fe3ff` | What is live now: focused contour, active reactor, focus ring, the one filled control per panel |
| Secondary emissive | `color.accent-deep` | `#1fa9c9` | Everything at rest: idle reactor, band rules, links, selected rows |
| Neutral text | `color.text` | `#d3e6ec` | Body and headings |
| Muted text | `color.text-dim` | `#7c9ea9` | Secondary lines, units, timestamps |

Supporting neutrals — the same two families at other lightnesses, not new hues: `bg-raised #050b11`,
`panel #070f16`, `field #050b11`, `surface-sunken #030709`, `accent-lift #7ee6ff`,
`accent-ink #031016`, `text-bright #f1fafc`, `text-faint #6f8d99`, `tick #455f68`, `line #16323f`,
`line-soft #0f2430`, `line-hair #0b1b23`.

States: `ok #6ff2c0`, `attention #f2b84b`, `warming #ff9e2c`, `danger #ff6b5c`. The kill switch has
its own red, `#e0344b`, deliberately not `danger` — "I stopped it" must never read as "something
broke".

**The accent budget.** `accent` is the rarest colour on screen: at most one focused panel contour,
one filled control, and the reactor. Everything else at rest is `accent-deep` or a neutral. A screen
where three things are `#4fe3ff` has lost its focus indicator.

---

## 3. Typography

Two families, both OFL, both vendored under `aura-web/static/fonts/`.

| Family | Weights | Role |
|---|---|---|
| **Barlow** | 300, 400, 500, 600 | Everything a person reads as language: headings, body, labels, buttons, empty states |
| **JetBrains Mono** | 400, 500 | Everything a person reads as a value: telemetry, elapsed times, token counts, log lines, code, ids |

Why these two and not the usual pair. Barlow at 300 gives the thin technical voice a hologram needs
at 28–44 px without reaching for a condensed face, and it stays readable at 13 px where a
grotesk-with-personality would not. JetBrains Mono's figures are tabular by default, so a running
clock or a token counter does not jitter one pixel per tick — the reason a mono face is here at all
is the numerals, not the texture.

Scale — the token names, not a parallel set of words. The HUD needs three usable body sizes and two
display sizes, and `design/tokens.json` already carries exactly those:

| Token | Value | Role |
|---|---|---|
| `--jv-fs-display` | `clamp(1.35rem, 3.2vw, 2.5rem)` | The reactor's number, the boot word |
| `--jv-fs-2xl` | `clamp(1.3rem, 2.4vw, 1.65rem)` | An open panel's own name |
| `--jv-fs-lg` | `1.15rem` | A section inside a panel; a glyph's mark |
| `--jv-fs-md` | `1rem` | Prose, the transcript |
| `--jv-fs-sm` / `--jv-fs-xs` | `0.88` / `0.8rem` | Rows, list items, a card's summary |
| `--jv-fs-2xs` | `0.72rem` | Units and timestamps beside a value — mono only |

`--jv-font-body` is Barlow, `--jv-font-chrome` is JetBrains Mono. There is no third family and no
separate display face: `--jv-font-display` is Barlow at a display size, which is what a wide weight
range is for.

Rules, each of which the v2 console breaks somewhere and v3 does not:

- **Sentence case everywhere.** No all-caps labels. The v2 console has `TRANSCRIPT`, `THIS TURN`,
  `SEND`, `VOICE`, `CHAT`; v3 has "Transcript", "This turn", "Send", "Voice", "Chat".
- **No eyebrow labels.** A panel's name is its title, not a tracked-out word above its title.
- **No middle-dot meta strings.** `READY · CHAT` becomes "Ready" with the mode as a control, not as
  prose. `alt-1…9 focus · alt-z undo · alt-k lock` becomes a keyboard-shortcut list the command
  palette owns, not a permanent strip of dots.
- **No decorative numbering**, and no `→` appended to a link or a button.
- Line length under 80 characters. Prose panels cap at `68ch`.
- **Type is never rotated.** Panels are angled in the scene; their text is billboarded, rendered
  screen-facing at native resolution. A label the owner has to tilt their head to read is a bug.
- When a panel's backdrop brightens past the contrast floor, a scrim fades in behind the text rather
  than the text changing colour — colour changes make the type look like a different component.

---

## 4. Emission grades

Three grades, exact numbers, in `design/tokens.json` as `emission.*`.

| | **Field** | **Panel** | **Control** |
|---|---|---|---|
| Where | Bands, substrate, halo, backgrounds | Every panel body | Buttons, inputs, chips, the composer |
| Surface | `surface-2` @ **0.04** | `surface-2` @ **0.10** | `surface-2` @ **0.16** |
| Contour | none | 1 px `accent-deep` @ **0.22**; focused 1 px `accent` @ **0.50** | 1 px `text` @ **0.16**; hover 1 px `accent` @ **0.40** |
| Bloom | none | `0 0 18px accent` @ **0.10** | none at rest; `0 0 12px accent` @ **0.16** on hover |
| Falloff | per band: inner **1.00**, middle **0.75**, outer **0.55** | inherits its band | none |
| Response | none | 380 ms spring on focus | 180 ms spring on hover |

**The bloom budget.** Only two things bloom noticeably: the reactor, and the one focused panel. Every
other panel's bloom is capped at 0.10 alpha, because a bloom pass on everything is a blur pass, and a
blurred interface reads as a screenshot of an interface.

**Reduced transparency.** Each grade collapses to its solid at the same hierarchy — Field to
`bg-raised`, Panel to `panel-solid`, Control to `surface-2` — contours stay, bloom and falloff go.
Nothing moves position; the hierarchy is carried by the same three steps in solid form.

---

## 5. Motion

Springs, from `design/tokens.json`:

| Spring | Stiffness | Damping | Mass | Settles | Used by |
|---|---|---|---|---|---|
| `control` | 520 | 34 | 1 | 180 ms | Hover, press, chip in/out |
| `snap` | 380 | 30 | 1 | 260 ms | A panel moving along its band, neighbours parting |
| `glass` | 210 | 24 | 1 | 380 ms | A panel opening or closing |
| `camera` | 120 | 22 | 1 | 640 ms | Every camera move |

The camera is the slowest spring because its move crosses the whole screen; a camera on the panel
spring feels thrown rather than flown.

- **Every spring is interruptible.** A new target retargets the running spring from its current
  position and velocity. Nothing restarts, and nothing queues.
- **Stagger** ≤ 40 ms per element, ≤ 400 ms for the whole choreography.
- **Hover intent** debounced 120 ms in, 250 ms out, so sweeping the pointer across the map does not
  set off a wave of expansions. Keyboard focus does the same with no delay in (the person aimed) and
  the same delay out. Long-press (400 ms) is the Android equivalent.
- **One non-user-triggered moment: the boot sequence.** The reactor ignites and the map resolves out
  of the void. On launch and on request, never per screen, never on navigation.
- **Reduced motion** → crossfades only. No camera, no parallax, no orbit; the flat projection.
- Animation stops when `document.hidden`, and on Android when the screen is off.

Motion answers something. A panel materialises because it was opened; a tool call lands on the
timeline because it happened; approvals fly to focus because one is waiting. Nothing animates to
show that it can.

---

## 6. The map

### 6.1 Bands

```
                      outer  r = 2.6   reference, archives         falloff 0.55
                 middle  r = 1.75      context, tasks              falloff 0.75
            inner  r = 1.0             what is happening now       falloff 1.00
                        ( reactor )
```

Every panel has a band (a radius), an angle on that band, and an arc width from its size envelope.
Pitch is bounded to ±18°; there is no roll.

The bands are rings in space, not ellipses on a plane (`hud/map/room.ts`, 2026-09-09). Each is
tilted its own way — inner about x, middle and outer about x and z, by ~3–11° — so the three cross
rather than nest; the near side of a band draws larger than the far side (perspective on size,
0.7–1.35×; position stays the ellipse's so the room fits the viewport the way it did). The room
has one camera — a drag on the ground or on the reactor yaws it without bound and leans it within
the pitch swing — and what it turns is the things, not the places (the owner, 2026-09-18): a
glyph's slot and the ring under it are solved and drawn at the rest view and never move with the
room; the reactor turns, and every cube turns on its own centre. A glyph is a cube in that room
(`PanelToken`, 2026-09-10): a true cube, `CUBE_FIT` of its slot so it projects inside the rect the
overlap gate measures at any turn, standing upright on its ring and turned by the camera's yaw and
elevation (`cubeTransform`), so a quarter turn shows its flank and a half turn its back, and pitch
still tips its faces. Every side face carries the mark, faded by how far it is turned from the camera, so
the far side of the room is never a ring of blanks. A card is a reading surface: it faces the
camera, keeps the elevation so it still sits in the room, and is `measure-token-depth` thick. The
map's zoom is a dolly **about the reactor**, and a panel whose place has gone off the edge stays
off the edge and is counted ("N more — zoom out to see them"), never carried round to the arc that
is still visible. Glyphs and cards do not grow with the zoom; open panels take no perspective at
all. A hovered glyph's card is placed over the glyph — after the open panels, which never move for
a hover, and before the other glyphs, which part for it — so the pointer that raised it is still on
it; packed like the rest it could land beside a neighbour, which then expanded instead, and the two
flashed for ever. A glyph whose card cannot be placed clear (wedged between the reactor and an open
panel) stays a glyph under the pointer rather than covering anything; a click still opens it.

### 6.2 Panel inventory and band assignment

| Band | Panels |
|---|---|
| inner | chat, timeline, approvals, goals, voice, model |
| middle | task, kanban, jobs, automations, browser, image, document, telemetry, house, notifications |
| outer | memory, audit, settings, integrations, capability, skills, plugins, mcp, pairing |

`approvals` and `model` promote to the inner band when an approval is pending or a cold start is
running, and fall back when it clears. That promotion is the only automatic movement; everything
else moves because someone or something asked.

### 6.3 The three states

```
glyph                     card                         open
┌──────┐                  ┌──────────────────┐         ┌────────────────────────────┐
│  ◉   │                  │ Approvals      2 │         │ Approvals                2 │
│  2   │   ──hover 120ms→ │ Write to /etc    │  ─Enter→│ ─────────────────────────  │
└──────┘                  │ Restart jarvis   │         │ Write to /etc/hosts        │
 56 px                    └──────────────────┘         │   ↳ terminal · 12s ago     │
                           240 x 120                   │   [ Approve ]  [ Deny ]    │
                                                       │ Restart jarvis-core        │
                                                       │   [ Approve ]  [ Deny ]    │
                                                       └────────────────────────────┘
```

Each step shows real content, not a bigger version of the same thing: the glyph is a count, the card
is the two most recent items, the open panel is every item with its controls. Neighbours part along
the band to make room; nothing is ever covered.

### 6.4 The solver

Pure arithmetic over `(viewport, camera, panels)`. Four guarantees:

1. Arcs on a band never intersect; the minimum gutter is 2° at the inner band, scaled by radius so
   the pixel gap is constant.
2. A panel keeps its band unless the band cannot hold it, in which case it spills **outward**, never
   inward — the inner band's job is to stay legible.
3. A manually placed panel is a **pin**: immovable, and the rest arrange around it.
4. Re-solving an unchanged input returns identical angles. No drift, no shuffle on a repaint.

Zoom changes density, not just scale: below 0.6 every panel is a glyph; above 1.4 a card becomes
open. "Frame everything" and "return to reactor" are one keystroke each.

**Modal exceptions, exhaustively:** the command palette, a destructive confirmation, and the
kill-switch confirm. These three dim the map instead of taking a slot on it. Nothing else is modal.

### 6.5 Presets

| Preset | Camera | Arrangement |
|---|---|---|
| Overview | framed on everything | all glyphs |
| Task | inner band filling the view | the current work's panels inner, the rest outward |
| Focus | on one panel | that panel open, everything else a glyph |
| Ambient | close on the reactor | reactor plus a one-line strip |

---

## 7. Default view per surface

**Web and desktop, 1440 x 900** — first run is calm: the reactor, the conversation and one context
panel. Everything else is a glyph on its band or not yet opened.

```
┌───────────────────────────────────────────────────────────────────────────┐
│ ◉ Jarvis     Ready                          link  qwen3.8-27b  Voice  Stop│
├───────────────────────────────────────────────────────────────────────────┤
│    ·  ◌ memory        ◌ audit          ◌ settings      ◌ skills  ·        │
│                                                                           │
│         ┌──────────────────┐                    ┌──────────────────┐      │
│         │ Conversation     │        ╱▔▔╲        │ This turn        │      │
│         │                  │      ╱  ◉◉  ╲      │                  │      │
│         │ Nothing said yet │      ╲  ◉◉  ╱      │ No tool calls    │      │
│         │ Say something or │        ╲__╱        │ yet. Ask for     │      │
│         │ hold space.      │       Ready        │ something.       │      │
│         └──────────────────┘                    └──────────────────┘      │
│                                                                           │
│     ◌ tasks     ◌ house      ◌ telemetry     ◌ browser     ◌ jobs         │
├───────────────────────────────────────────────────────────────────────────┤
│ [◫] [⊞] [🎙]  Say or type — hold space to talk            Send    ⌘K      │
└───────────────────────────────────────────────────────────────────────────┘
```

**Android phone, 412 dp** — one band in view, swipe between bands, pinch to zoom out to the whole
map. Glyph rows, 48 dp targets, the reactor pinned at the top.

```
┌─────────────────────────┐
│ ◉ Jarvis        Ready   │
│         ╱▔▔╲            │
│       ╱  ◉◉  ╲          │
│       ╲  ◉◉  ╱          │
│         ╲__╱            │
│  ‹ inner ·  middle  ›   │
├─────────────────────────┤
│ Conversation            │
│ Nothing said yet.       │
│ ──────────────────────  │
│ This turn               │
│ No tool calls yet.      │
├─────────────────────────┤
│  ◌   ◌   ◌   ◌   ◌      │
│ [🎙]  Say something     │
└─────────────────────────┘
```

**Console (terminal)** — the same bands as panes, the reactor as an ANSI ring, a highlight ring
where the web has hover.

```
┌─ jarvis ──────────────── ready ─ qwen3.8-27b ─┐
│  memory   audit   settings   skills           │  ← outer, dim
│ ┌─ conversation ───┐  ,-~-.  ┌─ this turn ──┐ │
│ │ nothing said yet │ (  ()  ) │ no tool calls│ │  ← inner
│ │                  │  `-_-'   │              │ │
│ └──────────────────┘          └──────────────┘ │
│  tasks  house  telemetry  browser  jobs        │  ← middle
└─ say or type ─────────────────────────────────┘
```

### 7.1 The mic, and who is speaking (2026-09-09)

**Open mic.** The dock is two things (the owner, 2026-09-18: "just a simple text box and a
microphone (that is open) and can just be muted as well"): the mic, open from the first frame,
whose button is a mute (`aria-pressed` is the mute; muted is a dim glyph with a slash and no
glow, listening is the lit hairline, recording the glow), and the composer, folded to a button
that unfolds into the box and Send — on a click, on a wake word's prefill, on a steer from the
C2 panel — and folds again on an Escape with nothing typed. Open, the room listens on its own:
an utterance begins when the level rises clearly above the room's floor for three frames and
ends after `voice.vad_hangover_ms` of quiet (`src/lib/hud/openmic.ts`, tested on a cranked
clock); a blip under 400 ms is dropped unheard; nothing is heard while a reply plays, because
the speaker is in the same room as the microphone. The mute is remembered per browser; the lock
holds the mic without muting it and the unlock listens again; a browser that refuses the
microphone shows it muted with the refusal as the title. A held Space is still push-to-talk
wherever nothing is being typed (and the reactor's centre in Ambient), and a press is how a
running reply is cut (barge-in: under 200 ms from the keydown to the audio stopping,
`e2e/hud-barge-in.spec.ts`). What left the bar: the Panels drawer, the conversations and the settings are one
tools box at the foot's end -- a three-dot toggle that opens a small hairline box, a
row and a label per tool; choosing one opens it and folds the box, and Escape from the
drawer hands focus back to the toggle (the owner, 2026-10-08: "a collapseable tiny little
box that can open each thing in it, instead of being persistent there"; the top bar that
held them went the same day). The Voice/Chat replies choice is a group in the settings
menu, and the keys legend is gone (`e2e/hud-dock.spec.ts`; `e2e/foot.ts` opens the box
for the other specs).

The foot floats over the room's bottom edge rather than taking a row of its own (T-0325, the
owner, 2026-10-09: "the panels don't go down to the bottom because of the invisible bar"): the
dock and the tools toggle are measured into reserved rects (`MapSurface` `chrome`), a side column
runs to the window's foot unless the dock sits under it, the reactor rests in the room above the
dock, and on two columns the toggle sits at the dock's end instead of the window's
(`node scripts/verify/console-live.mjs <url> --width 1440 --reach-bottom`).

**Personas.** Any of the four can be primary (ADR-0043). The console derives everything from one
name — `hud.persona`, from `persona.switch` on the socket or `GET /api/status` — and no surface
keeps its own: the wordmark, the three accent tokens (`--jv-accent` and its deep and lifted
forms, mixed by the browser from the persona's hue), the orb's hue while the room is calm and its
blade density always, the voice a reply is spoken in, the companion pool (the four less the
primary), and the transcript's speaker label. The switch itself is the plugin's (`persona_switch`,
`/persona`), reached from the settings menu's picker; a ▶ beside each name previews its voice.
A finished subagent reports in the voice of the persona its sphere wears, two sentences, queued
behind the reply at companion rank (`src/lib/hermes/speech.ts` carries a voice and a rank per
line). The `tts` readout in the top bar names the voice and the engine (`ada · cosy`, or
`ada · fallback` when the adapter had to go through hermes serve).

### 7.2 The shelf, and the lock (2026-09-10)

**The shelf.** Settings → Panels: *Orbit* or *Shelf*. On the shelf every glyph is pinned in rows
under the top bar, across the whole width, at scale 1 whatever the zoom, in one stable order
(open panels first, then by band, then by name) so a tile keeps its place as neighbours open and
close; the rings are not drawn. The glyphs are **flat squares** — the front face alone, no turn
— because nothing on a shelf is in the room. The dock columns hang **under** the last row
(`shelfBottom`, the same formula the solver places by), so a column opening never shifts the row.
A hover raises no card there; it puts the panel's **empty frame in the dock, at the place it
would take** (`data-ghost-of`, dashed, inert), and a click or a double-click makes it real — the
same tile, with the body. A drag of the room previews nothing. The reactor and the crew keep the
room below the rows. Remembered per browser (`jarvis:shelf`). It is the solver's `shelf` option
(`src/lib/hud/map/solve.ts`), not a second layout; Orbit is the room as before. Proof:
`e2e/hud-shelf.spec.ts`.

**The lock.** Settings → Lock: after 1, 5, 15, 30 or 60 quiet minutes, or never; *voice unlock*;
*lock now* (alt-k). When the allowance runs out the room stays drawn, dimmed and blurred, under a
pane that says who is primary and *Locked*, and takes the console password (the same check as the
pairing panel, with its refusals shown) or, with voice unlock on and someone enrolled, three seconds
of speech to the speaker verifier — *opened*, *refused*, or *say it again* when it could not judge.
Without a console password the pane says so and the lock cannot hold. It is the lock on the
study door, not a security boundary — the tailnet is that (`src/lib/hud/lock.ts` says this in
full). Proof: `e2e/hud-lock.spec.ts`.

### 7.3 Tabs (2026-09-24)

Settings → Panels: *Orbit*, *Shelf* or *Tabs* ([ADR-0046](../adr/0046-panels-may-be-tabbed-in-a-dock.md)).
Tabs keeps the room as Orbit draws it and changes only the docks: each dock column is one tab
stack showing one panel at the column's full height. Above it, a strip of sentence-case labels sits
on a hairline; the active label is `--jv-text-bright` with a 2 px `--jv-accent` underline that
slides to it over `--jv-dur-base` (transform only, so nothing re-lays out). The incoming panel
fades in and moves a `--jv-space-3` step from the side it came from; under reduced motion it only
fades. A strip shows the tabs that fit whole, the active one always among them, and ends in a
"+N" listing the rest; it never scrolls and never fades or cuts a label (2026-10-08).

Since 2026-10-08 ([ADR-0052](../adr/0052-the-room-has-no-top-bar-and-docks-hold-groups.md)) a
dock column is **groups**: a group of one is a tile with its own title bar, a group of several is
tabs (with no second title under the tab). Carry a tab or a title bar over a group's strip or
middle to join it as a tab, over its top or bottom quarter to make a tile there, past the last
group or to the other half of the room for a tile at that column's end; the group lights, or a
slot opens, before the release, and the groups glide into place after it. The dock's Tabs/Tiles
switch is in the Panels drawer. Proof: `e2e/hud-drag.spec.ts`.

Closing the active tab activates the tab to its right, else to its left. A panel JARVIS opens
arrives as the active tab of the dock the solver chose, once. Inactive panels stay mounted and
hidden, so a draft or a scroll position survives a switch. Keyboard: arrow keys, Home and End
along the strip, Enter or Space to activate, Ctrl+Tab and Ctrl+Shift+Tab to cycle, middle-click to
close. Proof: `e2e/hud-tabs.spec.ts`.

The room's arrangement is two choices in Settings (2026-09-25, `arrangement.ts`): **Panels** --
Tabs (the default) or Tiles, a column's panels stacked -- and **Selection**, how the panels that
are not open are offered: **Launcher only** (`none`, the default since 2026-10-08: the Panels
drawer in the foot's corner); **Scroll** (the default until then), a character-select row of cards along the
bottom or top edge ("Scroll at"), the middle card full size in the accent and each step out
smaller, dimmer and turned a little away (`map/PanelScroll.svelte`, `map/scrollLook.ts`); a side
card tapped comes to the middle, the middle one tapped opens (or brings forward) its panel, and a
wheel, a swipe or the arrow keys move the row. The rings then carry no glyph and the reactor is
centred in the room above (or below) the row. **Orbit** is the glyphs on the rings, **Shelf** the
glyphs in a row under the top bar. The phone draws the same choices natively (`glass/Selection.kt`)
and the car, the desktop app and the console are this page; the glyphs, the defaults and the
Scroll look are one table, `tests/contracts/panel-selection.json`. Proof: `e2e/hud-scroll.spec.ts`,
`SelectionContractTest`.

The **Panels drawer** arranges the tabs: the button before the wordmark slides
a sheet in from the left edge listing each dock's tabs in strip order, the one in front marked
with the accent rule; a name shows that tab, the arrows move it one place along its strip, the
cross closes it, and an Add list below opens any panel kind (an open one is shown instead of
duplicated). Escape or a press outside closes the drawer. Proof: `e2e/hud-drawer.spec.ts`.

A waiting approval is a card over the room, just above the dock, in every arrangement and in
the desktop popup: `ApprovalsBody` itself, so it reads and answers like the panel -- Approve
the one filled control, Deny outlined in the danger colour, "1 of N waiting" and All approvals
when more wait. It replaced bringing the Approvals panel forward, which could leave it behind
another tab. Proof: `e2e/hud-live.spec.ts`.

**What Jarvis does pulls up its panel** (2026-10-08, `hud/pullUp.ts`): a call by Hermes' own tool
name opens the panel its work shows in -- `ha_*`/`house_*` the house, `house_automation_*` and
`n8n_*` the automations, `cronjob` the jobs, `goal_*` the goal, `kanban_*` the tasks,
`memory`/`session_search` the memory, `llama_swap_*` the model, `gpu_*`/`grafana_*` the telemetry
-- as the shown tab of its group, and folds it `FOLD_MS` (20 s) after the turn unless the person
focused it. A panel the room already had is never folded; tools with no panel (web, terminal)
pull nothing up and the reactor's caption names them. Not on the popup, in the car or in Ambient.
The approvals and notices feeds are live on every surface, so a phone's or the house's request
reaches the waiting card whatever is open, and a notice that arrives slides in at the top of the
room for `NOTICE_MS`. Proof: `e2e/hud-live.spec.ts`.

**On a phone** (one column, under 720 px) the column stands above the reactor, not over it: as
tall as its panels up to 62 % of the room, and the reactor shrinks into the room under it
(`phoneBand` in `map/MapSurface.svelte`). Proof: `e2e/hud-overlap.spec.ts`.

**Settings** (2026-10-08) is sections with a heading and a lede each, and a jump list beside
the rows (a scrolling row on a phone) that lands on a section and lights the one on show: This
phone (in the phone app), Access (password, lock, people), On this screen (replies, reactor,
panels, selection), Persona and routines, then the catalogue's own groups. The phone's native
screen has the same idea: Server (with Tailscale), Voice, Listen, Headset, Actions, Car,
Display, Permissions, More, Updates. Proof: `e2e/hud-settings.spec.ts`,
`android-app/tools/settings_screens_test.py`.

---

## 8. Reactor state table

One instrument, eleven states, each distinct enough to learn and to read from across a room.

| State | Colour | Motion | Its own mark (2026-10-08) |
|---|---|---|---|
| idle | `accent-deep` | slow breathing, 4 s period | none: the rings at rest |
| listening | `accent` | core scales with microphone level | a ring of 48 level bars inside the bezel, a wave round it in silence, lengthened by the voice |
| thinking | `accent` | rotational energy through the rings | a comet with a tail sweeping the coil (`rx.comet`), the core beating twice and resting (`rx.heartbeat`) |
| speaking | `accent-lift` | pulses on the TTS audio envelope | three ripples leaving the core for the bezel (`rx.ripple`), brighter with the voice |
| tool running | `accent` | ring segments light in sequence, one per active tool | a light chasing round the bezel, the loading dial (`rx.chase`) |
| goal active | `accent-deep` | a slow orbit, one lap per turn | as thinking |
| attention | `warn` | shift toward attention, a held pulse | a beacon on the rim with a halo leaving it (`rx.beacon`) |
| warming | `warming` | dim to bright as the cold start progresses | none: the level arc filling is the mark |
| alert | `danger` | fast pulse | the rim strobing, two flashes and a rest (`rx.strobe`, under 3 Hz) |
| kill switch | `#e0344b` | rings dark, one red ring | the one red ring, still |
| offline | `tick` | grey, no motion | the outer ring broken, still |

The marks are drawn by both renderers -- `reactor/Reactor.svelte` and the orb (`orb/scene.ts`
`buildEffects`, stepped by `orb/effects.ts`) -- each one draw call, only the worn state's visible.
Under reduced motion every mark stays on screen and nothing in it moves.

**It is the light source.** The reactor's current state colour and its screen position drive panel
contour hue, the ambient halo on the void, and each band's atmospheric tint. One value changes and
the room changes with it.

**It is the C2 centre.** Focusing it opens command and control, not a status page: active goals with
subgoals and turn counts, running tools with elapsed time, subagents and what each was delegated,
queued and scheduled jobs, pending approvals, the model in use and its cold-start state — each with
its control in reach. Ring segments map to live activity, so load is readable at a glance and detail
is one interaction away.

**One source for the marks.** The app icons (Android adaptive, desktop, favicon), the splash, the
boot sequence, the notification icon and the home-screen widget are all generated from the reactor.

---

## 9. Copy

JARVIS's interface voice is calm, precise and brief. Dry wit belongs in its replies, never in system
copy.

- Buttons name the action, and the same word carries through the toast and the history: **Approve**
  → "Approved"; **Lock layout** → "Layout locked".
- Errors say what happened and what to do next, in the interface's voice: "The model server did not
  answer in 900 s. It is probably cold — try again, or switch to qwen3.6-35b."
- Empty panels invite an action: "No tool calls yet. Ask for something and they land here."
- Never "Submit", never "Oops", never an apology, never a word the person did not use first.

---

## 10. The generic-default review

The design plan checked against the tells that mark generated work, and what was revised.

| Tell | Present? | What was done |
|---|---|---|
| Identical rounded cards, one radius, the same grey shadow | **Was present in v2** | Panels have no shadow at all in v3. Depth is scale, falloff and focus. Radius is two values by role (`md` for controls, `lg` for panels), not one for everything. |
| Tracked-out all-caps eyebrow labels | **Was present in v2** (`TRANSCRIPT`, `THIS TURN`) | Sentence case, no eyebrow. A panel's name is its title. §3. |
| Meta strings joined with middle dots | **Was present in v2** (`READY · CHAT`, the chord strip) | The mode is a control, not prose; the chord list moved into the command palette. |
| An arrow appended to links and buttons | No | Kept out. |
| A hover animation on everything | **Risk** | Only Controls respond to hover. A Panel responds to *focus*, which hover can cause after 120 ms of intent — not to the pointer passing over it. |
| Warm cream + serif + terracotta | No | The brief pins a dark volumetric room. |
| Near-black + one acid accent | **Adjacent** | This is the shape of the palette, but it is the brief's own pinned direction and the console's existing identity, not a default reached for. What was revised is the *discipline*: two accents with a stated budget (`accent` rare, `accent-deep` carrying the load) rather than one bright colour sprayed on every interactive thing. |
| Monospace for small data labels | **Deliberate, and narrowed** | Mono is for values, never for labels. A label is Barlow at `dense`; the number beside it is JetBrains Mono at `micro`. |
| Gradient washes as decoration | No | The only gradient in the system is the reactor's halo on the void, which is a light, not a decoration. |
| Numbered markers (01 / 02 / 03) | No | The only numbers on screen are counts and measurements. |

**The one thing removed.** After the first build of the map, the band rules — three faint concentric
circles drawn under the panels — were removed. They explained the model to someone reading a design
document and did nothing for someone using it: the panels' own arrangement already shows the rings,
and three extra circles put six more edges on a screen whose whole point is that light is arranged
in space. What is left is the arrangement itself.

---

## 11. Gates

No screen ships without all five green.

| Gate | Command |
|---|---|
| Overlap: zero intersecting panel rects across viewport x count x zoom, at rest **and mid-animation** | `npx playwright test e2e/hud-overlap.spec.ts` |
| Frame time: 20 glyphs + 3 open panels, p95 ≤ 16.7 ms web/desktop, ≤ 8.3 ms Pixel | `npx playwright test e2e/hud-frametime.spec.ts` |
| Accessibility: axe per screen, plus contrast measured against the **rendered** backdrop | `npx playwright test e2e/a11y-axe.spec.ts e2e/a11y-contrast.spec.ts` |
| Visual regression: a snapshot per panel type in each of its three states | `npx playwright test e2e/gallery.spec.ts` |
| Size: bundle and APK budgets | `npx playwright test e2e/hud-size.spec.ts` |

Reduced motion, reduced transparency and high contrast are honoured on every surface; keyboard
navigation has a visible focus ring that reads against an emissive surface; screen readers announce
panels, their state and every layout change; touch targets are 48 dp.
