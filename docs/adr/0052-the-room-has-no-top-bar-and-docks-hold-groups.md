# ADR-0052: the room has no top bar, and each dock column holds groups a person drags between

- **Status:** Accepted (2026-10-08, on the owner's request)
- **Reference:** the owner, 2026-10-08, with a screenshot of the console: "the topbar looks weird,
  honestly remove it to make it look clean and have the reactor indicate the state jarvis is in
  with different new effects/looks", "I cant' drag and drop like hyprland anywhere", "empty
  panels being weird with text", "the mobile ui/console UI cutoff is weird"; T-0134.
- **Amends:** [ADR-0032](0032-the-room-has-no-tabs.md) (the top bar it kept is gone),
  [ADR-0046](0046-panels-may-be-tabbed-in-a-dock.md) (a dock is no longer all tabs or all tiles),
  [ADR-0049](0049-panels-tile-in-tab-stacks-around-the-orb.md) (its i3-style idea returns inside
  the docks, without the overlap that retired it)

## Context

The console's chrome was a top bar (the Panels launcher, the wordmark and state word, the goal's
line, the link/model/kill readouts, Chat and Settings) and a dock at the foot. Each dock column
beside the reactor was either one tab stack or a stack of tiles, switched by a button at its head;
a tab could be dragged along its strip or to the other side, a tile up and down its column. A
panel could not leave a tab stack to become a tile, nor a tile join a stack, and nothing showed
where a carried tab would land. The free tiling of ADR-0049 was retired on 2026-09-25 because its
tiles overlapped; the owner kept the tabs.

## Decision

- **No top bar.** The reactor's caption carries the name, the state word, the goal's line and,
  only when something is wrong, the link, model or kill switch (the model and link are its
  tooltip; the link is `data-link` on the root). Every state wears a mark of its own on both
  renderers (HUD.md §8). The launcher, Conversations and Settings are quiet glyphs in the foot's
  corners either side of the dock, which stays the mic and the box.
- **A dock column is groups** (`hud/map/groups.ts`). A group of one is a tile with its own title
  bar; a group of several is tabs over the one that shows, with no second title under the tab.
  Carrying a tab or a title bar: over a group's strip or middle it joins as a tab; over a
  group's top or bottom quarter it becomes a tile there; past the last group, or over the other
  half of the room, a tile at that column's end. The group it would join lights, or a slot opens
  at the edge it would tile at; the column's groups glide to their new places after the drop.
  Touch is a hold, then a drag. Groups are remembered per surface, closed panels beside the
  group they left.
- The dock's Tabs/Tiles switch moves into the Panels drawer (one group of tabs / a tile each);
  Settings' Panels choice decides where a newly opened panel goes and regroups every column.
- Nothing in the room offers the panels that are not open unless the person chooses a Selection;
  the default is `none`, the launcher (`tests/contracts/panel-selection.json`).

## Consequences

- Nothing overlaps: groups are flex items of the column, each holding at least its shown
  panel's floor, and the column scrolls; `hud-overlap` holds it.
- A panel moved between groups is remounted, as a move between columns always was: a draft in
  its box does not survive the move (a tab switch within a group still keeps it).
- Not covered: a split side by side inside a column (columns stack vertically), moving a panel
  between groups by keyboard (the drawer's up and down move it within its group, or its tile
  past the next), and the phone's native HUD, which keeps its own tab and tile choice.
- Proof: `e2e/hud-drag.spec.ts`, `e2e/hud-tabs.spec.ts`, `e2e/hud-car.spec.ts`,
  `src/lib/hud/map/groups.test.ts`.
