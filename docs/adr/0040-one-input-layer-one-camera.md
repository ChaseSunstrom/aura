# ADR-0040: One layer decides what a gesture means, and it is the only thing that moves the camera

- **Status:** Accepted (2026-09-09)
- **Comes from:** the overhaul brief Phase 3, which asks for this decision to be recorded
- **Relates to:** [ADR-0037](0037-orb-rendering-architecture.md) (panels are DOM, not WebGL text), [ADR-0038](0038-one-reactor-component.md) (one reactor implementation)

## Context

The brief describes the symptom and diagnoses it in the same sentence: *"The scroll and zoom
behavior around the reactors is wrong, and it will stay wrong until input is handled in one place
instead of by whatever component happens to be under the pointer."*

The audit found exactly that. Two components owned `wheel` handlers and each moved a different
camera:

- `Orb.svelte` dollied an orbit camera with `dolly(orbit, Math.sign(e.deltaY) * 0.25)` — **stepped**,
  with the delta magnitude discarded, so a 3 px trackpad nudge moved the camera as far as a 120 px
  mouse detent.
- `MapSurface.svelte` scaled a second camera with `zoomAbout(cam.target, deltaY < 0 ? 1.12 : 1/1.12,
  at, viewport)` — a different model, with a fixed factor per event.

Whichever happened to be under the pointer won, the two disagreed about what a notch of scroll was
worth, and neither knew the other existed.

Three further defects were found only by driving the real page, and each is the kind that reads as a
missing feature rather than a bug:

1. **Wheel-to-dolly did nothing at all.** The ownership check walked the whole ancestor chain looking
   for something scrollable; the orb's canvas sits inside the HUD and the HUD has a scrollable
   container, so every wheel over the orb was classified as a panel scroll.
2. **The canvas never used its pixel ratio.** `<canvas width={size} height={size}>` let Svelte reset
   the drawing buffer to CSS size on every reactive update, so the console never drew at 2× on a
   retina display either.
3. **Turning the orb opened the microphone**, because the orb sits inside the push-to-talk button and
   captured the pointer on `pointerdown`.

## Decision

**One module decides what every gesture means, and components forward events to it rather than
interpreting them.** `jarvis-web/src/lib/orb/input.ts`.

1. **Normalize before deciding.** Wheel deltas arrive in three `deltaMode` units and no two devices
   agree. `notchesOf()` divides by a per-mode constant so a mouse detent is 1.0 and a trackpad glide
   sums to about the same for the same physical gesture. `Math.sign` is banned — it is the specific
   defect above, and `one-input.test.ts` fails on it.

2. **Scroll ownership is a question about direction, not about capability.** A panel owns a gesture
   only if it *could still move that way*; at its end it hands the rest to the scene, or the scene
   feels dead until the pointer moves. **The walk is bounded at the element the handler is attached
   to** — anything above that is not in the gesture's scope, and an unbounded walk is defect 1.

3. **`ctrl+wheel` and pinch belong to the scene**, with the browser default suppressed, or the page
   zooms instead of the camera.

4. **Trackpad inertia is inferred and suppressed.** There is no flag for it: events arriving faster
   than a hand can push whose magnitude only shrinks. The rule is deliberately conservative — a run
   of three shrinking deltas, cleared by any growth or any pause — because a false positive kills a
   real gesture, which is worse than a short coast.

5. **A press is provisional until the pointer moves.** `DragGate` holds the tap/turn decision at four
   pixels and reports the transition exactly once, so a host that started something on `pointerdown`
   can abandon it without debouncing. That is what lets the orb be turnable while it sits inside the
   push-to-talk button.

6. **The renderer owns the drawing buffer; the layout owns the CSS box.** Never both. Buffer size,
   pixel ratio and camera aspect are re-derived on DPR change (`viewport.ts`), because there is no
   `devicepixelratiochange` event and a media query bounded at the current ratio is one-shot, so the
   listener re-arms. An orb also supersamples on a low-DPR display: its linework was drawn for 560 px
   and the console renders it at 219, which is about a third of a pixel per bezel tick.

## Consequences

- A component may not have a zoom policy. `one-input.test.ts` scans every `.ts` and `.svelte` under
  `src/` — **with comments stripped**, because these files discuss the defect they avoid and a
  line-grep cannot tell an accurate comment from the bug it describes — and fails if any file outside
  `input.ts` reads `deltaY`, collapses a delta with `Math.sign`, handles `wheel` without calling
  `arbiter.wheel`, or calls `preventDefault` unconditionally.
- **One camera is not yet true.** The orb's `PerspectiveCamera` and the map's 2D camera are still
  two. They converge when the orb becomes the scene the map draws into, not before, and this ADR
  records that as the intended end state rather than as done.
- The decisions above are testable without a browser, which is why `input.ts` is pure apart from the
  small history momentum detection needs. The parts that are *not* testable that way — that a wheel
  actually dollies, that the page does not scroll — are the Phase 3 input matrix's job, and defect 1
  is the argument for having it: every unit test passed while the feature did nothing.

## Amendment, 2026-09-09: a frame is worth the time it took, and a loop may not depend on what it writes

Two rules learned from one CI failure, recorded here because both are properties
of the camera model rather than of any component.

**7. A frame advances the animation by the time that actually passed.** The
ceiling on that is a *stall*, not the frame budget. `MapSurface` clamped `dt` to
`--jv-budget-frame` (50 ms), which is a performance target, so a machine drawing
at 5 fps was handed 50 ms of animation per 200 ms frame. `stepAxis` substeps
internally at 1/240, so a large `dt` was always safe to pass through. The policy
is `frameDelta`/`MAX_FRAME_S` in `spring.ts`, beside the integrator both the
camera and the panel spring use — one number to get right rather than two — and
`camera.ts` re-exports it. Half a second is deliberately generous: whatever the
cap touches loses wall-clock time, so it must sit below a stall and above the
slowest rate at which a machine is still genuinely drawing.

**8. A move completes on a deadline, not only on a spring settling.**
`spring.ts` has always snapped a panel to its target once `settleMs` of wall
clock has passed; the camera had no such rule, so a move was only ever as
complete as the number of frames it happened to receive. It carries `elapsed`
now and does the same.

**9. An animation loop may not depend on the state it writes.** This is the one
that actually failed CI, and it is the one worth reading twice. The loop was an
`$effect` reading `cam.target` — and it *writes* `cam` every frame. So every
frame invalidated the effect, tore the loop down and rebuilt it with `last = 0`,
and each frame then advanced a flat `1 / 60` however long it had really taken.
The camera ran at roughly half speed at 60 fps and a quarter at 28; a 640 ms move
took over ten seconds. Measured on the page: after `r` the zoom crawled
1.48 → 1.11 across 6.5 s and `data-settled` never appeared.

It is keyed on a `moves` counter that only a retarget bumps. The general rule:
**an effect that owns a loop must depend on what should restart it and on nothing
else** — depending on the state the loop mutates is a re-entrancy bug that
presents as slowness rather than as a crash, which is why it survived three
rounds of plausible fixes. Rules 7 and 8 are both correct and neither would have
found it; only measuring the running page did.

## Addendum (2026-09-18, the completion run)

The completion brief's 2.2 asks for "one camera, one zoom, one input arbitration layer". The
input layer and the zoom are one (`lib/orb/input.ts` is the only wheel owner and
`one-input.test.ts` fails a second); the camera is still two models by this document's own
"Consequences" — the orb's orbit (`lib/orb/orbit.ts`) and the map's spring on three numbers
(`lib/hud/map/camera.ts`) — and the research of 2026-09-18 confirmed one `PerspectiveCamera`
construction site and both wheel handlers calling the same arbiter. The decision for this run:
**they stay two.** Folding the map's spring into the orbit camera is the "orb becomes the scene
the map draws into" end state, a change to how the 3D scene moves, and the owner's rule of
2026-09-18 is that the 3D UI changes only to fix perspective, fix an issue or look better. The
page-never-scrolls, anchors-at-every-zoom and input-matrix rows are proven against the layer as
it is. What this addendum does not decide: when the fold happens; it records that it is not a
defect and not this run.

## Addendum (2026-09-18): a place is solved at rest, and a gesture turns the things, not the places

The anchor rule, in the owner's words: "the boxes around jarvis move weird when the view is
rotated instead of being static in place". Both halves of the alternative had been tried and
rejected (`room.ts` records the dates): the glyphs sliding round their bands while the cubes
faced the glass, and everything turning together along three differently tilted ellipses. What
reads as a room is a set of fixed places with objects in them that turn.

**10. A slot is a place in the room; the camera's turn reaches only what stands in it.**
`cddf1dbb`: the solver and the rings take `HOME_VIEW`; the live view reaches the cube's faces
(`cubeTransform`) and the reactor, never the slot or the ring under it. A glyph's slot is the rect
`solve()` committed for its band point — `rectAt`: the centre plus `project(band, angle)` times
the scene unit — and the ring is `outline()` of the same band at the same view and the same unit,
so the slot's centre lies on its ring by construction. A dolly scales the unit and the pan
together about the reactor's centre (`sceneUnit` is the fit times the zoom; `dolly` pivots on the
instrument), so the rule holds at every zoom, not only at rest. A drag changes the yaw and moves
no slot at all.

**The budget is 2 px**, measured as the distance from every glyph slot's centre to its own
band's drawn ring at the radius the solver placed it, after one dolly of 1.2× (`=`), back (`r`),
and one of 1/1.2× (`-`). "Its own band at its radius", not "the nearest ring": a band that is full
has its glyph drawn on a smaller copy of itself (`solve`'s radii 1, 0.82, 0.64 and 0.46 of the
band's), which the slot carries as `data-ring`, and measured against the full-size rings such a
glyph read 54 px off when it was exactly where the solver put it (2026-09-22). Measured by
`e2e/input-matrix.spec.ts`, "a glyph sits on its ring at 1.2×, 1× and 1/1.2× zoom", written
into `docs/design/screens/overhaul/input-matrix.md` on every run. Two because the ring is drawn
as seventy-two chords — each sags r·(1 − cos 2.5°), about 0.7 px inside the true ellipse at the
outer band at 1.2× — plus a path rounded to 0.1 px, and a sub-pixel layout; the defect it guards
against measured 5–130 px before `cddf1dbb`. The drag half is `hud-map.spec.ts` ("dragging the
ground turns the room, and no glyph leaves its place"): the yaw changes, the first glyph's box
moves at most 1 px.

What the rule does not cover: the crew band (its satellites sit a fixed gap outside the outer
ring and no ring is drawn for them), the shelf and the flat projection (no rings, no places to
hold), and a hovered card, which is placed over its glyph's last rect rather than on the band.

