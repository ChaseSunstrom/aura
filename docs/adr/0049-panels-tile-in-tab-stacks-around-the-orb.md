# ADR-0049: panels tile in tab stacks around the orb, and every panel is a tab on the rail

- **Status:** Superseded (2026-09-25). The owner: "the tiles on the console are weird because they overlap, I like the tabs one, but I want like a sliding panel that I can open to click/configure the tabs". Tabs (ADR-0046) is the one layout and the Panels drawer arranges it; `TileSurface.svelte`, `tiles.ts` and their specs are deleted. Was: Accepted (2026-09-24, on the owner's request)
- **Comes from:** the owner, 2026-09-24, after using the Tabs arrangement: *"the tabs thing, I do like, and I think that should be the default for panels, but I also meant like instead of just the cubes around the orb, they are already defined in tabs in the navbar or whatever that you can drag out into panels with tabs, etc. similar to tiling window managers"*; asked where the orb goes when the room tiles: *"I like the orb as a persistent background, but also it should be able to move/animate in a nice way when tabs/nav is moved"*
- **Amends:** [ADR-0032](0032-the-room-has-no-tabs.md) (there are now tabs under the top bar — one per panel type, not per route) and [ADR-0046](0046-panels-may-be-tabbed-in-a-dock.md) (tabs were a dock option; tiles are now the default)
- **Builds on:** [ADR-0036](0036-the-map-replaces-the-tiler.md) (the map), the tiling tree of GLASS_HUD §9.2 (`hud/layout/tree.ts`), [ADR-0048](0048-the-phone-and-the-car-draw-the-consoles-orb-natively.md) (the orb)

## Decision

A fourth arrangement, **Tiles**, and the default for a browser that has not chosen one:

- **The rail.** Every panel type is a tab in a row under the top bar, in the room's own order,
  its count beside it; the open ones are lit. Clicking a closed tab opens it; clicking an open
  one shows it. The map's glyphs ("the cubes around the orb") are not drawn in this arrangement.
- **Tiles of tab stacks.** Open panels live in tab stacks, and the stacks tile the room in
  `tree.ts`'s split tree. Dragging a tab — from the rail or from a tile's strip — onto a tile's
  strip or middle joins that stack; onto an edge splits the tile there, i3-style; back onto the
  rail closes the panel. A divider between tiles is dragged to resize. While a tab is in the air
  the tiles preview where it will land, and settle there on release.
- **The orb is the persistent background and owns one tile.** The tree always has a leaf that is
  never a panel, `jarvis`, and the orb — drawn once, full size, behind everything — glides and
  rescales into it on every change with a transform (`--jv-dur-enter`, `--jv-ease-spring`), so it
  moves as the tiles move and the canvas is never resized. The orb's tile cannot be closed or
  joined; an empty room is the orb.
- **A panel is moved, not remounted.** Every open panel's body is one entry in one keyed list,
  placed over its stack's slot, so dragging a tab to another tile keeps the element and all its
  state.

Where the panels are is the person's, per browser (`jarvis:tiles`, like `jarvis:arrangement`),
the way ADR-0046's tab order was. *Which* panels are open stays the layout manager's document:
`tiles.ts` `sync` follows it, so a panel JARVIS opens arrives as the focused stack's active tab,
a reconnect's replay takes no tab, and nothing is pruned until the saved layout has arrived (the
HUD's `settled`). JARVIS's camera verbs mean the nearest thing a tiled room has: `fly` shows the
panel's tab; `fit` and home have nothing to move.

The `hud-layout` plugin's tree ops still arrange panel ids in the document; they are not mapped
onto the tiles, which are a presentation of it. Orbit, Shelf and Tabs remain in Settings → Panels.

## Consequences

- The e2e specs written against the map pin `jarvis:arrangement = orbit` in the Playwright
  storage state; `hud-tiles.spec.ts` starts from an empty store, which is what tests the default.
- `PanelFrame` gained `fill`: in a tile the tile is the size, where the map caps a panel at its
  type's envelope.
- Not done: moving a tab between tiles by keyboard (the rail and every strip are keyboard-reachable
  and a panel opens, shows and closes from them; a new tile is made by pointer); mapping the
  plugin's split and swap ops onto tiles; a phone layout for tiles (the phone draws its own HUD).
