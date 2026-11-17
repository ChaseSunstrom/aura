# ADR-0036: The HUD is a spatial map on orbital bands; the tiling window manager is retired

- **Status:** Accepted (2026-09-08)
- **Comes from:** the v3 plan §1.2 and §3, the v3 brief Part 1 ("The map — panels never overlap")
- **Supersedes:** [ADR-0030](0030-glass-hud-design-system.md) §9.2 (the tiling/floating/focus/ambient window manager)
- **Keeps:** [ADR-0031](0031-reactor-ii-returns.md) (the reactor is the instrument) and [ADR-0032](0032-the-room-has-no-tabs.md) (no tabs; JARVIS and the launcher bring panels up)

## Context

`aura-web/src/lib/hud/layout/` implements a binary space-partitioning tiler: a split tree
(`tree.ts`), tree-to-boxes arithmetic with drag-to-split, drop targets and a neighbour walk
(`rects.ts`), and four layout components (`TiledLayout`, `FloatingLayout`, `FocusLayout`,
`AmbientLayout`) selected by `HudSurface.svelte`. It works, it is well tested, and it is the wrong
shape for v3.

A BSP tiler's invariant is *the container is exactly covered*. The brief's invariant is *panels sit
at a radius and an angle around the reactor and never intersect*, with depth, parallax and
atmospheric falloff doing the work that a tiler does with edges. Those are different geometries, not
different renderings of one geometry. Keeping both would put two layout engines in one tree, which
the duplication gate exists to prevent, and would leave every panel with two contradictory notions
of where it is.

`FloatingLayout` has no successor at all: the brief explicitly forbids a free-floating z-order
pile-up.

## Decision

**One solver, two projections.** Panels are placed by band (radius) and angle; the solver guarantees
no two panels intersect. The 3D projection draws that arrangement with perspective, focus falloff
and camera moves; the flat projection draws the same angles as non-overlapping rows for reduced
motion and weak hardware. Both read the same solved output, so a panel cannot be in two places.

- Three bands: inner (what is happening now), middle (context and tasks), outer (reference and
  archives). Every panel type has a band assignment in the v3 plan §3.2.
- The solver is pure arithmetic over `(viewport, camera, panels)`, with no DOM, so the same function
  runs in vitest, in the CI overlap gate and in the Kotlin mirror on Android.
- Its four guarantees: arcs never intersect (minimum gutter scaled by radius); a panel spills
  outward, never inward, when a band is full; a manually placed panel is a pin the solver arranges
  around; and re-solving an unchanged input returns identical angles.
- Three panel states — glyph, card, open — with hover intent debounced 120 ms in / 250 ms out,
  keyboard focus doing the same without a pointer, long-press on Android and a highlight ring on the
  console.
- Modal exceptions are exhaustive and dim the map rather than tiling: the command palette, a
  destructive confirmation, and the kill-switch confirm.
- Presets (Overview, Task, Focus, Ambient) are camera and arrangement states on the one map, not
  window managers.

The tiler and its four layouts are deleted as the map lands, per the v3 plan §7.

## Consequences

- **Good.** "Panels never overlap" becomes a property of the solver rather than a hope about the
  layout, and it is checkable by a bounding-rect assertion in a real browser at any zoom, viewport
  and panel count — including mid-animation.
- **Good.** One geometry for four surfaces. The phone shows one band at a time from the same solve.
- **Bad.** Drag-to-split, drop targets and the neighbour walk go, and with them a familiar way to
  arrange a screen. The replacement is drag along a band plus pin, which is less expressive on
  purpose.
- **Bad.** A large deletion lands before the replacement is fully proven on every surface. The order
  in the v3 task list puts the overlap and frame-time gates first, so nothing is deleted before the
  thing that replaces it is measured.
- **Neutral.** Saved v2 layouts (split trees) cannot be translated into band positions and are
  dropped on first load, with the panels they named reopened on their default bands.
