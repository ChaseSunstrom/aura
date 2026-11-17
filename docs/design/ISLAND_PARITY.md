# The island, phone and desktop (T-0696)

The island is Jarvis over someone else's screen: one shape that is a pill, an activity's pill or a card.
The phone draws it in `AssistOverlay` and the desktop in the popup (`/?popup=1`); both choose the state
from `tests/contracts/island.json`. This table is the check on "everything on the phone island exists
on the desktop": one row for each input and state in that contract and for each other thing the phone's
island does. A desktop cell starts with `Has:` (and `Where` names the files that do it) or `Gap:` (and
says why and what would close it). `deploy/tests/test_island_parity.py` fails a contract input or
state with no row, a `Where` path that is gone, and a cell that is neither.

Does not cover how a state looks or moves (the Android goldens and `e2e/v2-island.spec.ts`), or the
console's own Ctrl+K, which is a console feature the desktop reaches by opening the console.

| Row | Phone | Desktop | Where |
|---|---|---|---|
| input.turn | a voice or typed turn opens the card | Has: the card opens while a turn is live, its reply stays until the app lets the idle popup go | `aura-web/src/lib/ds/shell/Popup.svelte` |
| input.ask | an approval is its own card, never flicked away | Has: the approval card, answered in the popup | `aura-web/src/lib/ds/shell/ApprovalCard.svelte` |
| input.notice | a notice shows on the card for its moment | Has: a fresh notice is the card's line | `aura-web/src/lib/ds/shell/Popup.svelte` |
| input.activity | a route held off the car is an activity pill with End route and Open maps | Gap: the desktop holds no activity (no route is opened on it); the card is the answer a route question gets | `aura-web/src/lib/ds/shell/island.ts` |
| input.expanded | a tap or pull on the pill | Has: a click into the window, a draft in the line, and the typing key (Ctrl+Shift+K by default, `AURA_PALETTE_KEY`) which opens the card with the line focused from any app | `aura-desktop-app/src-tauri/logic/src/palette.rs` |
| input.collapsed | a flick up puts a turn in its pill and anything else away | Has: Escape, the core, or going to another window; with nothing going on Escape puts the island away (the title says ` · away` and the app hides the window) | `aura-web/src/lib/hud/popupMood.ts` |
| input.working | a turn at work on a task steps out of the way to its pill | Gap: the popup never sets it, so a working turn keeps its card until Escape; it needs the console's tool state carried to the popup page | `aura-web/src/lib/ds/shell/island.ts` |
| input.touched | the phone used behind a turn puts it in its pill | Has: going back to another window collapses a turn to its pill, which is the desktop's form of touching what is behind it | `aura-web/src/lib/ds/shell/Popup.svelte` |
| input.on | the assistant on rests as a pill on the camera | Gap: a desktop has no camera to rest on and the popup goes when idle by design | `aura-web/src/lib/ds/shell/Popup.svelte` |
| state.hidden | nothing shows | Has: the popup window is hidden by the app | `aura-desktop-app/src-tauri/src/shell.rs` |
| state.pill | the core and one word, sized by tokens | Has: the pill at the same token sizes, the window following it (` · pill` in the title) | `aura-web/src/lib/ds/shell/island.ts` |
| state.activity | the activity's pill: glyph, label, figure | Gap: see input.activity | `aura-web/src/lib/ds/shell/island.ts` |
| state.card | the core, the line, tools, notice and the input | Has: the card with the core, the line, the input and the mic | `aura-web/src/lib/ds/shell/Popup.svelte` |
| state.approval | its own card with the answers | Has: the same, with Approve all for a batch | `aura-web/src/lib/ds/shell/ApprovalCard.svelte` |
| state.rest | the resting pill on the camera | Gap: see input.on | `aura-web/src/lib/ds/shell/island.ts` |
| morph | the pill and the card morph on the glass spring, the core moves between slots | Has: the same spring (`springEasing`), one core moving between the pill's slot and the card's | `aura-web/src/lib/ds/shell/island.ts` |
| gesture.flick | a flick up to put away, a tap or pull to open | Has: Escape to put away, a click or the typing key to open; a pointer flick is not drawn because a desktop has Escape | `aura-web/src/lib/ds/shell/Popup.svelte` |
| place | the reactor at the bottom while it talks, the island at the top once it works | Has: one place, the top centre; the bottom-while-talking place is the phone's | `aura-desktop-app/src-tauri/src/shell.rs` |
| dim | a dim, and a blur where the phone allows it, only behind the card | Gap: a window cannot reach the pixels under it, so only a veil dims round the card | `aura-web/src/lib/ds/shell/Popup.svelte` |
| worn | wears the worn pack's fill, edge, accent and name | Has: the same, from the theme | `aura-web/src/lib/ds/theme.svelte.ts` |
| summon.type | the wake word or the assist gesture opens the card for a typed or spoken turn | Has: the typing key from anywhere; a key another app owns is logged and the tray says so, and it never takes the push-to-talk key | `aura-desktop-app/src-tauri/logic/src/palette.rs` |
| summon.talk | the wake word | Has: the push-to-talk key (held to talk) | `aura-desktop-app/src-tauri/src/shell.rs` |
