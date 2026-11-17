// A subagent's reactor: the orb, with three numbers changed.
//
// ADR-0038 and the scene brief agree on the rule — "companions must be
// parameterizations of the orb, never a second implementation" — and
// `one-reactor.test.ts` enforces it: nothing in this file constructs geometry.
// It describes configurations, and `buildOrb` builds them.
//
// V3_PLAN §21 is why the configuration has a *shape* in it and not only a hue.
// Simulated with the Brettel/Viénot LMS transform, the worst pairwise contrast
// between the companion hues under deuteranopia is 1.00 — not "hard to
// tell apart", the same colour. So the blade density is not a nicety to add if
// there is time; it is the only thing that distinguishes companions for a
// deuteranope, and it ships with the hue or the hue means nothing.

import { TOKENS } from '$lib/tokens';

import { IDENTITIES, PERSONAS } from './personas';

export interface CompanionSpec {
	/** What it is called, in the tray and in the C2 list. */
	name: string;
	/** Its light. An existing token; no hue is typed here. */
	accent: string;
	/** Blades in the ring — the cue that survives every CVD simulation. */
	blades: number;
	/** Multiplier on the coil's counter-rotation: a second cue, by rhythm. */
	coilRate: number;
	/** Fraction of the orb's radius. §19: "companions are ⅓-scale instances of it". */
	scale: number;
}

/**
 * The two personas: a companion is any persona that is not primary (ADR-0043),
 * so whichever of AURA and JARVIS you are talking to, the other is the worker.
 */
export const COMPANIONS: readonly CompanionSpec[] = PERSONAS.map((p) => ({
	name: p,
	accent: TOKENS[IDENTITIES[p].accent],
	// Each persona's own ring density: the cue that is not colour.
	blades: IDENTITIES[p].blades,
	coilRate: 1,
	scale: 1 / 3
}));

/**
 * Which companion a subagent gets.
 *
 * "Hue-assigned by a stable hash of the subagent's id so the same kind of work
 * gets the same companion within a session" (§21). Stable rather than
 * round-robin: a person learns that the orange one is the one doing the search,
 * and a counter would break that the moment a job finished out of order.
 */
export function companionFor(id: string, primary = 'JARVIS'): CompanionSpec {
	// The pool is the two less the primary: "there are never two of the same
	// identity on screen" (V3 PART 2). Expressed as a function of the primary
	// rather than a list somebody maintains, because that is how you get two of one.
	const pool = COMPANIONS.filter((c) => c.name !== primary);
	let h = 2166136261;
	for (let i = 0; i < id.length; i++) {
		h ^= id.charCodeAt(i);
		h = Math.imul(h, 16777619);
	}
	return pool[(h >>> 0) % pool.length];
}

/**
 * How long a companion takes to arrive and to leave, in seconds.
 *
 * Arriving is slower than leaving on purpose: a new worker appearing is
 * information a person should notice, and one finishing is information they
 * have already had from its telemetry. The brief's reduced-motion path replaces
 * both with a crossfade, which is what `phaseAt` produces when `reduced` is set
 * — the same two numbers, no scale.
 */
export const CHOREOGRAPHY = { spawn: 0.65, despawn: 0.4 } as const;

export interface Phase {
	/** 0..1. Multiplies every shell's opacity, and the tether's. */
	opacity: number;
	/** Multiplies the companion's own scale. 1 once it has arrived. */
	scale: number;
	/** False once a despawn has finished: the caller may drop it. */
	alive: boolean;
}

/**
 * Where a companion is in its life at `now`.
 *
 * Pure and time-driven, like the memory activity, so the choreography is
 * testable without a GPU and a screenshot of it is reproducible.
 */
export function phaseAt(
	{ spawnedAt, endedAt }: { spawnedAt: number; endedAt?: number },
	now: number,
	reduced = false
): Phase {
	if (endedAt !== undefined && now >= endedAt) {
		const t = (now - endedAt) / CHOREOGRAPHY.despawn;
		if (t >= 1) return { opacity: 0, scale: 1, alive: false };
		// Leaving does not shrink, even when arriving grew: a companion that
		// collapsed to a point would read as being destroyed rather than as
		// having finished.
		return { opacity: 1 - t, scale: 1, alive: true };
	}
	const t = (now - spawnedAt) / CHOREOGRAPHY.spawn;
	if (t >= 1) return { opacity: 1, scale: 1, alive: true };
	if (t <= 0) return { opacity: 0, scale: reduced ? 1 : 0.6, alive: true };
	// Decelerating, so it settles rather than arriving at speed.
	const eased = 1 - (1 - t) ** 3;
	return { opacity: eased, scale: reduced ? 1 : 0.6 + 0.4 * eased, alive: true };
}

/** A companion in the scene: which one, and where. */
export interface PlacedCompanion {
	/** The subagent's id. What the spawn and despawn are keyed on. */
	id: string;
	spec: CompanionSpec;
	/** Seconds on the scene clock when it appeared. */
	spawnedAt?: number;
	/** When it finished, if it has. */
	endedAt?: number;
	/** Position in orb radii, relative to the scene origin. */
	x: number;
	y: number;
	z: number;
}

/**
 * Where `count` companions sit around the orb.
 *
 * On a ring in the subject plane rather than scattered: they are peers of one
 * instrument and reading them as a set is the point, so they share a radius and
 * a plane. Deterministic in the count, so a companion does not jump when
 * another one finishes — the brief's crowding case is a later problem, but a
 * layout that reshuffled on every despawn would make it unsolvable.
 */
export function ringOf(
	ids: readonly string[],
	radius = 1.55,
	lives: Readonly<Record<string, { spawnedAt: number; endedAt?: number }>> = {},
	primary = 'JARVIS'
): PlacedCompanion[] {
	// 1.55 comes out of the frustum, not out of taste. A companion is a third
	// of the orb, so the ring has to clear the instrument (1 + ⅓ = 1.33) and
	// still fit inside the cone: at the camera's 6 radii through a 38° lens the
	// visible half-width is 6·tan(19°) = 2.06, leaving room for 1.55 + ⅓. The
	// first version used 2.1 and the screenshot showed two of the three
	// companions cut in half by the edge of the canvas.
	return ids.map((id, i) => {
		// Start at the top and go round; an odd count still reads as balanced
		// because the first one is always in the same place.
		const angle = -Math.PI / 2 + (i / Math.max(1, ids.length)) * Math.PI * 2;
		return {
			id,
			spec: companionFor(id, primary),
			spawnedAt: lives[id]?.spawnedAt ?? 0,
			endedAt: lives[id]?.endedAt,
			x: radius * Math.cos(angle),
			y: radius * Math.sin(angle) * 0.55,
			// Slightly behind the orb, so a companion never occludes the
			// instrument a person is reading.
			z: -0.6
		};
	});
}
