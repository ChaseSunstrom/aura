// The orb's shells, from the 2D reactor's own numbers.
//
// V3_PLAN §13 inventoried the instrument and the orb brief makes that inventory
// the specification: someone who knows the current HUD has to look at this and
// say "that's the same reactor, in 3D". So nothing here is a new proportion —
// every radius is read from `tests/contracts/reactor_geometry.json`, the same
// file `reactor.test.ts` holds `Reactor.svelte` to. Retuning the instrument
// moves both renderers or neither.
//
// **Extrude, don't replace.** Each flat ring becomes a real torus at the same
// radius with genuine thickness, not a screen-facing sprite: a billboard looks
// right head-on and gives the game away on the first drag, which is the one
// failure the brief calls out by name.
//
// Which axis each shell sits on is the one judgement call, and it is made to
// serve two requirements that pull against each other. "Preserve the
// silhouette" wants every band coplanar, because a tilted ring projects to an
// ellipse and head-on would no longer read as the reactor. "Rings on different
// axes cross each other visibly" wants tilts. The resolution: the four outer
// bands — bezel, blades, coil, level — stay in the XY plane, so head-on is the
// instrument unchanged; the two iris rings and the think ring, which live
// *inside* the core and already read as arcs rather than full circles, are the
// ones tilted onto their own axes. Head-on they still read as the arcs they are
// today. Rotate, and they are revealed as real rings crossing on different
// axes, which is what the third dimension was for.

/** The contract, typed to the fields the orb reads. */
export interface ReactorGeometry {
	ticks: number;
	long_tick_every: number;
	long_tick_len: number;
	short_tick_len: number;
	blades: number;
	blade_gap_deg: number;
	r_blade: number;
	r_coil: number;
	coil_dash: [number, number];
	r_level: number;
	r_core: number;
	iris_a_r: number;
	iris_a_sweep: number;
	iris_b_r: number;
	iris_b_sweep: number;
	r_think: number;
	think_dash: [number, number];
	breathe_scale: number;
}

/**
 * The contract's numbers, typed here rather than imported.
 *
 * `reactor.test.ts` says the rule: *"`Reactor.svelte` types the same numbers as
 * constants (it ships no JSON)"*, and the phone types them a third time. This
 * file used to `import … from '../../../../tests/contracts/reactor_geometry.json'`
 * instead, which is a *bundled* import reaching outside `jarvis-web/` — and
 * `build:web` builds with `CONTEXT: jarvis-web`, so in the image that path does
 * not exist and rollup stopped with "Could not resolve". The web image could not
 * be built at all; nobody saw it because the build stage had not run on this
 * branch since before the orb landed.
 *
 * `geometry.test.ts` reads the contract and fails if any number here drifts from
 * it, which is the same binding the SVG and the phone have.
 */
export const GEOMETRY: ReactorGeometry = {
	ticks: 120,
	long_tick_every: 10,
	long_tick_len: 0.07,
	short_tick_len: 0.032,
	blades: 36,
	blade_gap_deg: 3,
	r_blade: 0.85,
	r_coil: 0.74,
	coil_dash: [2, 6],
	r_level: 0.65,
	r_core: 0.56,
	iris_a_r: 0.9,
	iris_a_sweep: 1.25,
	iris_b_r: 0.82,
	iris_b_sweep: 1.1,
	r_think: 0.47,
	think_dash: [1, 5],
	breathe_scale: 1.025
};

/**
 * How thick a shell is, as a fraction of the orb's radius.
 *
 * The 2D instrument draws strokes in px, which does not survive the trip to a
 * torus — a 3 px stroke is a different object at 120 px and at 560 px, and the
 * orb has no single size. These are the stroke weights read as *proportions* of
 * the drawable radius at the size the instrument was designed at (560 px, the
 * 4x reference shot), so the relative weights hold: the bezel's ticks are the
 * finest marks, the blades are the heaviest band, the level arc is a hair
 * thicker than the coil.
 */
export const TUBE = {
	bezel: 0.013,
	blade: 0.012,
	coil: 0.008,
	level: 0.011,
	iris: 0.007,
	think: 0.005
} as const;

/**
 * Where each shell sits along the view axis, as a fraction of the radius.
 *
 * Zero would put every band in one plane, which is correct head-on and reads as
 * a flat disc the moment it turns. A small spread gives the assembly depth —
 * "which ring is nearer, which is further, how far apart they sit" is one of
 * the brief's acceptance criteria — without moving anything in the head-on
 * projection, because a shift along the view axis is invisible from the front.
 */
export const DEPTH = {
	bezel: -0.1,
	blade: -0.04,
	coil: 0.02,
	level: 0.07
} as const;

/**
 * The tilts, in radians, for the shells that are allowed one.
 *
 * Only the core's own rings tilt (see the header). The angles are chosen so the
 * two iris rings cross at a visible angle rather than nearly coinciding — two
 * rings 6° apart read as one fat ring with a moiré, which is worse than either.
 */
export const TILT = {
	irisA: { x: 0.20, y: 0.07 },
	irisB: { x: -0.15, y: 0.24 },
	// The think ring is **not** tilted, and the first side-by-side is why. It is
	// a full dashed circle, so any tilt projects it to an obvious ellipse
	// head-on — at 0.9 rad it read as a dotted oval lying across the core and
	// broke the silhouette outright, which is the one thing the brief says the
	// head-on view may not do. The iris rings can carry the axis crossing
	// because they are *arcs*: a 0.2 rad tilt is invisible in a 200° sweep
	// head-on and unmistakable the moment the orb turns.
	think: { x: 0, y: 0 }
} as const;

/**
 * How each ring precesses about the two axes across the view, as multiples of
 * the `--jv-rx-tumble` period, with sign for direction.
 *
 * This is the orb-only motion. The 2D instrument's rings turn in their plane
 * and that is all a plane can do; in three dimensions a ring can also swing its
 * *axis*, and a set of rings whose axes precess at different rates in different
 * directions reads as rings in space rather than as discs spinning on a dial —
 * the armillary rather than the clock face. The x and y here are added to the
 * z spin the rings already have.
 *
 * The rule the table follows: no two rings share a rate or a direction on
 * either axis, so the set never phase-locks into a single tilting disc. The
 * level arc is **absent on purpose** — it is a gauge, and a gauge on a swinging
 * axis cannot be read.
 *
 * This is a decision against the earlier one recorded on `TILT`, which kept the
 * outer bands coplanar so head-on matched the SVG at every instant. It now
 * matches on average and not at every instant: each ring's tilt swings through
 * zero, and the ensemble's outline stays circular. The side-by-side page is
 * where to judge it.
 */
export const TUMBLE = {
	bezel: { x: 0.35, y: -0.22 },
	blades: { x: -0.5, y: 0.31 },
	coil: { x: 0.27, y: 0.61 },
	irisA: { x: 1.15, y: -0.7 },
	irisB: { x: -0.9, y: 1.0 },
	think: { x: 0.55, y: 0.44 }
} as const;

/**
 * How far a ring's axis swings from the view axis, in radians.
 *
 * A precession is a *cone*: the axis circles the view axis at this half-angle.
 * The multipliers above set how fast and which way each ring goes round it;
 * the angle is the same for all, so the ensemble keeps one silhouette. A full
 * flip was the first thing tried and it is wrong on sight — a bezel edge-on is
 * a line across the core, and for that moment there is no reactor. At 0.45 rad
 * (~26°) a ring is unmistakably in space and is never a line.
 */
export const TUMBLE_CONE = 0.45;

/**
 * How much of an outer shell's light survives where it passes BEHIND the glass core.
 *
 * Every shell is additive, so a ring behind the core used to be exactly as bright as the
 * same ring in front of it: nothing was ever occluded, and the owner read the orb as "flat
 * and with no depth" (2026-09-24). A ring now dims to this where the view ray reaches it
 * only through the core -- the strongest depth cue there is -- and keeps its full light in
 * front. The core's own rings, the dot and its glow live inside the glass and are exempt.
 */
export const GLASS_THROUGH = 0.3;

/**
 * Where the HUD's orb is seen from at rest, in radians, and its slow drift.
 *
 * Head-on, every ring is a circle on the screen and the instrument reads as a flat
 * graphic; a three-quarter view turns them into ellipses stacked in depth. The drift is a
 * slow Lissajous of the camera about that rest -- `sin` of the clock, so a pinned `t` is
 * one reproducible frame -- which is the parallax that sells a solid in motion.
 */
export const REST_VIEW = { yaw: -0.42, pitch: 0.3 } as const;
export const DRIFT = { yaw: 0.22, pitch: 0.06, period_s: 48, pitch_rate: 1.37 } as const;

/** The key light on the glass core, a direction in view space (up, left, toward the viewer). */
export const KEY_LIGHT = [-0.45, 0.62, 0.64] as const;

/** A capability node's radius on the core: small enough to read as a mark, not a bubble. */
export const NODE_RADIUS = 0.018;

/** The default camera distance, in orb radii. Frames the bezel with the halo's room around it. */
export const CAMERA_DISTANCE = 2.82;
/** Dolly bounds, in orb radii (the brief's "close and far bound"). */
export const DOLLY = { near: 1.9, far: 6.0 } as const;
/** Pitch is clamped short of the poles so the world up stays up and nobody gets lost. */
export const PITCH_LIMIT = 1.35;

/**
 * One tick's angle and length on the bezel, for all 120 of them.
 *
 * Returned as data rather than drawn here so the geometry can be built once
 * into a single merged buffer: 120 separate meshes would be 120 draw calls for
 * the thing that is least interesting to look at.
 */
export function bezelTicks(): { angle: number; length: number }[] {
	const out: { angle: number; length: number }[] = [];
	for (let i = 0; i < GEOMETRY.ticks; i++) {
		const long = i % GEOMETRY.long_tick_every === 0;
		out.push({
			angle: (i / GEOMETRY.ticks) * Math.PI * 2,
			length: long ? GEOMETRY.long_tick_len : GEOMETRY.short_tick_len
		});
	}
	return out;
}

/**
 * The 36 blades as (start, sweep) pairs in radians.
 *
 * The gap is the contract's 3°, taken off the end of each blade rather than
 * shared between neighbours, which is what the 2D renderer does — halving it
 * would make the ring read as solid at small sizes.
 */
export function bladeArcs(count = GEOMETRY.blades): { start: number; sweep: number }[] {
	// The count is a parameter because a companion's identity depends on it.
	// V3_PLAN §21: under deuteranopia the three companion hues are literally
	// the same colour, so blade density is not a decorative second cue — it is
	// the only thing that tells them apart, and it has to be a number this
	// function will accept rather than a constant it owns.
	const step = (Math.PI * 2) / count;
	const gap = (GEOMETRY.blade_gap_deg * Math.PI) / 180;
	return Array.from({ length: count }, (_, i) => ({
		start: i * step,
		sweep: step - gap
	}));
}

/**
 * A dashed ring's segments, from a CSS-style `[dash, gap]` pair.
 *
 * The 2D coil is `stroke-dasharray: 2 6` in user units on a circle of radius
 * `r_coil × R`. The same rhythm on a torus means working out how many whole
 * dash+gap cycles fit the circumference and distributing them evenly, so the
 * pattern closes rather than leaving a short segment at the seam — a visible
 * join is exactly the kind of artefact that reads as a bug when the orb turns.
 */
export function dashedRing(
	radius: number,
	dash: readonly [number, number]
): { start: number; sweep: number }[] {
	const circumference = 2 * Math.PI * radius;
	const cycle = dash[0] + dash[1];
	const count = Math.max(8, Math.round(circumference / (cycle / 100)));
	const step = (Math.PI * 2) / count;
	const lit = step * (dash[0] / cycle);
	return Array.from({ length: count }, (_, i) => ({ start: i * step, sweep: lit }));
}

/** The level arc's sweep for a 0..1 progress, starting at twelve o'clock like the 2D one. */
export const levelSweep = (progress: number): number =>
	Math.max(0.001, Math.min(1, progress)) * Math.PI * 2;
