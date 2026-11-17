# ADR-0046: Panels may be tabbed inside a dock

- **Status:** Accepted (2026-09-24, on the owner's request)
- **Amends:** [ADR-0032](0032-the-room-has-no-tabs.md) — for the panels inside a dock column only; the room still has no route tabs
- **Reference:** `docs/design/HUD.md` §7.3, the C2 reactor reference (the tab underline)

## Context

ADR-0032 removed the six route tabs: one surface, one route, and JARVIS pulls panels up. The
owner, 2026-09-24: *"instead of flat/orbit, allow for a panel mode that has tabs, etc. in a nice
way … clean it up make it look nicer"*. With four or five panels open, the two dock columns
stacked them into slivers a few rows tall each, and every open panel paid for its own header.

## Decision

A third arrangement, **Tabs**, beside Orbit and Shelf in Settings → Panels. It changes the docks
and nothing else: each dock column becomes one tab stack that shows one panel at the column's
full height, under a strip of uppercase tab labels on a hairline with the sliding accent
underline. The glyphs, the rings and the reactor are the same as in Orbit. The arrangement is a
per-browser preference, `jarvis:arrangement`; the old `jarvis:shelf` flag is read once and
carried over.

The stack's order and its active tab are one pure model (`src/lib/hud/layout/tabstack.ts`): the
solver still decides which dock a panel goes to, and the stack only follows. Closing the active
tab activates its right neighbour, else its left; a panel JARVIS opens arrives as the active tab.

## Consequences

- An inactive tab's body stays mounted and hidden, so a chat draft and a scroll position survive
  a switch. Its feeds keep running while hidden — bounded by how many panels are open, which the
  docks already bound.
- There are still no route tabs and no tabs in the top bar; ADR-0032 holds for both.
- Keyboard: arrow keys, Home and End move along a strip; Ctrl+Tab and Ctrl+Shift+Tab cycle;
  middle-click closes. The strip is a WAI-ARIA tablist.
