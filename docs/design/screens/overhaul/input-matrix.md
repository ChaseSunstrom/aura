# Phase 3 input matrix

A record of the Phase 3 run: its writer, `e2e/input-matrix.spec.ts`, went with the legacy console's
map, panels and crew band in T-0611, so nothing regenerates this page. It measured every input kind, at three zoom levels, on three
scenes: nothing open, three panels open, and three open with a companion on the crew band.
The four assertions are the brief's: no page scroll (`window.scrollX + scrollY === 0` after
the gesture), no drift, no overlap, stable hit testing.

"camera moved" is reported, not asserted — keyboard nudges rotate without changing distance,
and a row that legitimately does not dolly is not a failure. It reads the map's zoom as well
as the orb's distance: in the console the orb is not interactive, so a wheel over it reaches
the map's camera.

| input | zoom | scene | surface | page scroll | drift px | overlaps | hit test | camera moved |
|---|---|---|---|---|---:|---:|---|---|
| wheel | near | closed | web | none | 0.06 | 0 | stable | yes |
| trackpad | near | closed | web | none | 0.04 | 0 | stable | yes |
| pinch | near | closed | web | none | 0.09 | 0 | stable | yes |
| keyboard | near | closed | web | none | 0.07 | 0 | stable | yes |
| jarvis | near | closed | web | none | 0.04 | 0 | stable | yes |
| wheel | rest | closed | web | none | 0.24 | 0 | stable | yes |
| trackpad | rest | closed | web | none | 0.10 | 0 | stable | yes |
| pinch | rest | closed | web | none | 0.18 | 0 | stable | yes |
| keyboard | rest | closed | web | none | 0.23 | 0 | stable | yes |
| jarvis | rest | closed | web | none | 0.22 | 0 | stable | no |
| wheel | far | closed | web | none | 0.03 | 0 | stable | yes |
| trackpad | far | closed | web | none | 0.24 | 0 | stable | yes |
| pinch | far | closed | web | none | 0.00 | 0 | stable | yes |
| keyboard | far | closed | web | none | 0.01 | 0 | stable | yes |
| jarvis | far | closed | web | none | 0.06 | 0 | stable | yes |
| wheel | near | open | web | none | 0.24 | 0 | stable | yes |
| trackpad | near | open | web | none | 0.23 | 0 | stable | yes |
| pinch | near | open | web | none | 0.16 | 0 | stable | yes |
| keyboard | near | open | web | none | 0.25 | 0 | stable | yes |
| jarvis | near | open | web | none | 0.07 | 0 | stable | yes |
| wheel | rest | open | web | none | 0.00 | 0 | stable | no |
| trackpad | rest | open | web | none | 0.00 | 0 | stable | no |
| pinch | rest | open | web | none | 0.09 | 0 | stable | yes |
| keyboard | rest | open | web | none | 0.00 | 0 | stable | no |
| jarvis | rest | open | web | none | 0.00 | 0 | stable | no |
| wheel | far | open | web | none | 0.00 | 0 | stable | no |
| trackpad | far | open | web | none | 0.00 | 0 | stable | no |
| pinch | far | open | web | none | 0.05 | 0 | stable | yes |
| keyboard | far | open | web | none | 0.00 | 0 | stable | no |
| jarvis | far | open | web | none | 0.00 | 0 | stable | no |
| wheel | near | open + crew | web | none | 0.25 | 0 | stable | yes |
| trackpad | near | open + crew | web | none | 0.25 | 0 | stable | yes |
| pinch | near | open + crew | web | none | 0.20 | 0 | stable | yes |
| keyboard | near | open + crew | web | none | 0.24 | 0 | stable | yes |
| jarvis | near | open + crew | web | none | 0.25 | 0 | stable | yes |
| wheel | rest | open + crew | web | none | 0.00 | 0 | stable | no |
| trackpad | rest | open + crew | web | none | 0.00 | 0 | stable | no |
| pinch | rest | open + crew | web | none | 0.16 | 0 | stable | yes |
| keyboard | rest | open + crew | web | none | 0.00 | 0 | stable | no |
| jarvis | rest | open + crew | web | none | 0.00 | 0 | stable | no |
| wheel | far | open + crew | web | none | 0.00 | 0 | stable | no |
| trackpad | far | open + crew | web | none | 0.00 | 0 | stable | no |
| pinch | far | open + crew | web | none | 0.10 | 0 | stable | yes |
| keyboard | far | open + crew | web | none | 0.00 | 0 | stable | no |
| jarvis | far | open + crew | web | none | 0.00 | 0 | stable | no |

## A wheel over an open panel

A real wheel over an open code panel's body, four hundred lines tall (`page.mouse.wheel`, so
the browser's own scroll runs): the panel scrolls, the page does not, the room does not dolly.

The panel scrolled 300 px; page scroll none; room zoom unchanged.

## The room's places at three zooms

The anchor rule (ADR-0040, second addendum): a slot is solved at rest and a gesture turns the
things in the room, not the places. Measured as the distance from every glyph slot's centre to
its own band's ring at the radius the solver placed it (`data-ring`), after `=`, `r` and `-` —
one dolly of 1.2, back, and one of 1/1.2 about the reactor.

| dolly | glyphs | max off ring px | budget px |
|---|---:|---:|---:|
| 1.00 | 27 | 0.28 | 2 |
| 1.20 | 20 | 0.34 | 2 |
| 1.00 | 27 | 0.30 | 2 |
| 0.83 | 24 | 0.25 | 2 |

## Surfaces

| surface | measured | how |
|---|---|---|
| web | yes — every row above | Playwright over the built console |
| desktop | not measured here | `jarvis-desktop-app` loads the same console URL in Electron, so the rows above cover its renderer and not its frame |
| phone | not measured here | the phone's HUD is Compose; its input is pinned by `android-app/tools/*.py`, which Playwright cannot drive |
