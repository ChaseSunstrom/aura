# ADR-0038: one reactor component, three configurations

- **Status:** Accepted (2026-09-08)
- **Comes from:** the scene brief Part 3, the v3 plan §21–§22
- **Builds on:** ADR-0037 (the orb is WebGL, the panels are DOM)

## Context

When JARVIS delegates to a subagent, a smaller reactor spawns and lives for that subagent's
lifetime. Three of them, distinguished by hue: Ultron orange, Ada pink, Friday green.

The obvious implementation is a `Companion` component beside `Orb`. It would work, ship quickly, and
be wrong within a month — because the two would then be separate objects that merely resemble each
other. Every subsequent change to the instrument would have to be made twice, and the second copy
would be the one that got missed. This project already has that failure written down twice: the
reactor "was retuned on the web and not on the phone, and nothing anywhere said the two were
supposed to agree, so they did not" (`reactor/spec.ts`), and `spec.ts` still exports a `RINGS` table
describing an instrument nothing renders.

## Decision

**A companion is the orb with different parameters. There is no second implementation.**

Concretely, `buildOrb()` already takes an accent colour, `Orb.svelte` already takes `state`, `size`,
`level`, `yaw` and `pitch`, and the geometry already comes from one contract. What a companion adds
is:

- `hue` — already the `accent` argument; a companion passes its own instead of `colorOf(state)`.
- `scale` — already `size`; a companion is instantiated at roughly a third.
- `bladeDensity` — **new**, and the only genuinely new parameter. It is the secondary cue V3_PLAN
  §21 shows is mandatory: under deuteranopia the three companion hues have a worst pairwise contrast
  of **1.00**, meaning identical, so hue alone identifies nothing for a deuteranope.

That is the whole of it. If a companion turns out to need behaviour the orb has not got, the brief's
instruction is to generalize the orb rather than fork it, and this ADR is the place that decision
gets recorded rather than made silently in a new file.

## What this rules out

- A `Companion.svelte` with its own geometry, shaders or animation loop.
- A copy of `scene.ts` with the radii changed.
- A second frame loop. One animation clock drives everything, which is also what the performance
  budget assumes.

## Consequences

**Good.** Retuning the instrument moves the orb and every companion together, because there is one
of it. The shared shader library, instanced-node system, batched line system and projection pass the
performance section requires fall out of this rather than having to be arranged.

**Good.** `bladeDensity` is a parameter the orb itself can use — the LOD tier needs to shed blade
count under pressure, and that is the same knob. Generalizing for companions produced the mechanism
performance needed anyway, which is the usual sign the generalization was real.

**Bad.** The orb's props grow. A component with a `bladeDensity` is slightly less obvious than one
without, and there is a limit to how many parameters can accumulate before "one component,
configured" stops being readable. The limit is not reached at three.

**Bad.** A companion inherits the orb's open problems as well as its geometry — the head-on
brightness gap recorded in the September 2026 build log will be three-quarters as visible on a ⅓-scale companion, and
fixing it fixes all four instruments at once, which is the point.

## How the absence of a duplicate is proved

The brief asks for it to be *shown*, not asserted. A test asserts that exactly one file under
`src/lib/orb` constructs ring or sphere geometry: a companion implementation containing a
`TorusGeometry` or an `IcosahedronGeometry` of its own would fail it the moment it appeared, which
is a stronger guarantee than a code review that has to remember to look.
