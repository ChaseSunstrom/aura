# ADR-0042: the 2D reactor is kept as the fallback, and the two renderers are bound by one contract

- **Status:** Accepted (2026-09-09)
- **Comes from:** the overhaul task list B1a — *"decide and record whether the 2D `$lib/reactor` is retired or kept as the reduced-motion/fallback path"*
- **Builds on:** [ADR-0037](0037-orb-rendering-architecture.md) (the orb is WebGL, the panels are DOM), [ADR-0038](0038-one-reactor-component.md) (one reactor component, three configurations)

## Context

Once the orb renders the instrument in WebGL, the SVG `Reactor.svelte` looks like the previous
version of something. The tidy instinct is to delete it. That instinct is wrong here, for three
reasons that are all measured rather than argued.

**It is what shows while three.js downloads.** three.js is 145 kB gzipped on its own. Imported
statically it took the console's first download from 201 kB to 357 kB against a 205 kB budget, so the
orb is a dynamic `import()` and something has to occupy the centre of the HUD until it lands. The
SVG is a few kB, is already in the bundle, and *is the same instrument* — so the first paint is
correct rather than empty.

**It is what shows when WebGL is not available.** Not a hypothetical: the e2e runner rasterises
WebGL in software, and a browser can refuse a context for reasons the page does not get to see. The
orb's loader catches and leaves the reactor up. A console whose centre is a blank rectangle when a
GPU is missing is a worse console than one that falls back to a smaller instrument.

**It is the reduced-motion path.** The orb honours reduced motion by freezing its fields, but a flat
SVG that never had a camera is a better answer for someone who asked for less motion than a still
3D scene.

## Decision

**The 2D reactor is kept, permanently, as the fallback and reduced-motion renderer. It is not
legacy, and it is not scheduled for deletion.**

**Both renderers read one contract.** `$lib/reactor/spec.ts` holds the shared rules — `wornState`,
`isPlan`, `arcValue`, `bladeTone`, `ignitionLayer`, `IGNITION_LAYERS`, `BLADE_TONE_TOKEN` — and
neither component may hold an instrument rule of its own. This is ADR-0038's reasoning applied one
level up: a companion is the orb with different parameters, and the SVG is the orb in a different
raster.

**Every instrument parameter must be honoured by both.** A prop one renderer accepts and the other
ignores is the failure this arrangement invites, and it is not theoretical — it happened. `boot`,
the ignition's 0–1, was passed by the HUD to both renderers at all four call sites, assembled the
SVG across `ignitionLayer(boot, i)`, and was declared and dropped by the orb. For as long as that
shipped, the console's orb appeared fully formed while its own fallback assembled beside it.

## What caught it, and what now catches it

It was caught by `eslint`'s `no-unused-vars` — reported as a style nit in CI, and the actual content
of the report was *this instrument does not implement its own contract*. That is luck, not a
process: an unused prop is only a lint error while nothing else touches the variable.

So the binding is now a test. `instrument.test.ts` reads both components, takes every prop the SVG
declares, and fails if the orb declares the same prop without referencing it anywhere else in the
file. It is a scanner, so it strips comments and strings first — this repository has produced three
tests that read their own prose as code, and the fix is always the same.

The side-by-side page stays the human check: `/gallery/orb?view=side&boot=0.35` freezes both halves
at one instant of the ignition. That is how the layer mapping was found to be wrong — five layer
names against ten orb members, with the SVG's `core layer` group holding the core, both irises, the
think ring and the hot dot with its glow.

## What this rules out

- Deleting `Reactor.svelte`, or letting it rot while the orb moves on.
- An instrument rule written in one renderer. It goes in `spec.ts` or it does not exist.
- A static `import` of the orb from the HUD, which would put three.js back in the first download.

## The cost, stated plainly

Two renderers is two implementations of one drawing, and the second one drifts. The mitigation is a
shared contract, a test that fails on a dropped parameter, and a page that photographs both at the
same instant — not a comment asking the next person to remember. Whether that is enough is a
question the next divergence answers; the honest position today is that the first divergence was
found by a linter, and it should have been found by a test.
