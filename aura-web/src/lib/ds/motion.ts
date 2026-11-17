// The v2 timings, read from design/tokens.json and nowhere else.
//
// CSS reaches the same values as `var(--jv-dur-*)` / `var(--jv-ease-*)`; this
// module is for the code that has to know a number: the stage machine deciding
// when the outgoing scene has gone, the core tweening its position, a row's
// stagger. A token retuned in tokens.json lands in both at once, which is the
// point — a duration typed twice is two durations the day one of them changes.
//
// Reduced motion is the house rule (`$lib/motion`): every duration collapses to
// zero, so a transition still happens and still ends in the same place, it just
// arrives at once.

import { prefersReducedMotion, watchReducedMotion } from '$lib/motion';
import { TOKENS, tokenMs, type TokenName } from '$lib/tokens';

export { prefersReducedMotion, watchReducedMotion };

/** Every v2 duration, in milliseconds. */
export const MS = {
	/** A projection comes out of the core: point → travel → line → full height. */
	appear: tokenMs('--jv-dur-appear'),
	/** The reverse, a little quicker: what is leaving should not hold the stage. */
	exit: tokenMs('--jv-dur-exit'),
	/** A bar rises. */
	bar: tokenMs('--jv-dur-bar'),
	/** One row slides in. */
	row: tokenMs('--jv-dur-row-in'),
	/** Rows start this far apart. */
	rowStagger: tokenMs('--jv-stagger-project'),
	/** A connector or leader line draws in. */
	connector: tokenMs('--jv-dur-connector'),
	/** The working beat: a pulse down each connector, a shock ring on the core. */
	beat: tokenMs('--jv-dur-beat'),
	/** The shock ring's life. */
	shock: tokenMs('--jv-dur-shock'),
	/** The core's glow kick after a state change. */
	kick: tokenMs('--jv-dur-kick'),
	/** Subtitles fade. */
	fade: tokenMs('--jv-dur-fade'),
	/** The presence strip unrolls from the core / folds back. */
	unroll: tokenMs('--jv-dur-unroll'),
	/** Hold-to-confirm. */
	hold: tokenMs('--jv-dur-hold'),
	/** How long the shell offers Undo after a settings change. A wait: read `MS.undo`, never `ms('undo', reduced)`. */
	undo: tokenMs('--jv-dur-undo')
} as const;

export type MotionName = keyof typeof MS;

/** `MS[name]`, or 0 when the user asked for reduced motion. */
export function ms(name: MotionName, reduced = false): number {
	return reduced ? 0 : MS[name];
}

type Cubic = readonly [number, number, number, number];

/** The control points of a `cubic-bezier(…)` easing token. Throws on anything else. */
export function cubicOf(name: TokenName): Cubic {
	const value = TOKENS[name];
	const m = /^cubic-bezier\(([^)]+)\)$/.exec(value.trim());
	const n = m ? m[1].split(',').map((p) => Number(p.trim())) : [];
	if (n.length !== 4 || n.some((v) => !Number.isFinite(v))) {
		throw new Error(`${name} is not a cubic-bezier: ${value}`);
	}
	return [n[0], n[1], n[2], n[3]];
}

/** One axis of a cubic Bézier from 0 through p1, p2 to 1. */
const axis = (p1: number, p2: number, u: number): number => {
	const v = 1 - u;
	return 3 * v * v * u * p1 + 3 * v * u * u * p2 + u * u * u;
};

/**
 * `y(x)` of a cubic-bezier easing, so code can follow the same curve CSS does.
 * x is solved by bisection: twenty halvings is far under a pixel on any stage.
 * Clamps t to [0, 1]; does not model overshooting curves' x (CSS forbids them).
 */
export function easing(curve: Cubic): (t: number) => number {
	const [x1, y1, x2, y2] = curve;
	return (t: number) => {
		if (!(t > 0)) return 0;
		if (t >= 1) return 1;
		let lo = 0;
		let hi = 1;
		let u = t;
		for (let i = 0; i < 20; i++) {
			u = (lo + hi) / 2;
			if (axis(x1, x2, u) < t) lo = u;
			else hi = u;
		}
		return axis(y1, y2, u);
	};
}

/** The core's travel between scenes: the same curve the stage uses in CSS. */
export const easeTravel = easing(cubicOf('--jv-ease-in-out'));
