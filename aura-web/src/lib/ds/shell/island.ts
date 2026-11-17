// The island (T-0695): Jarvis over someone else's screen, a pill at the top that grows into a
// card and back, from what is going on. The owner's phone popup was a dark box across the lower
// half and approvals a full-screen sheet (2026-11-09: "something like a dynamic island"); the
// table is tests/contracts/island.json, which the phone's overlay (assist/island/Island.kt)
// reads too. Does not cover how a state is drawn or moves: that is Popup.svelte's.

import { token, tokenMs, type TokenName } from '$lib/tokens';

/** What is going on; each is the table's `inputs` line of the same name. */
export interface IslandInputs {
	turn: boolean;
	ask: boolean;
	notice: boolean;
	activity: boolean;
	expanded: boolean;
	collapsed: boolean;
	/** The turn is at work on a task (T-0885); the desktop popup never sets it yet. */
	working: boolean;
	/** The phone behind it was used during the turn (T-0885): the phone's alone. */
	touched: boolean;
	/** The assistant is on and its island kept showing (T-0890): the phone's alone; the desktop popup never rests. */
	on: boolean;
}

export type IslandState = 'hidden' | 'pill' | 'activity' | 'card' | 'approval' | 'rest';

/** Each state's size as tokens (`content`: as tall as what it shows) and whether it takes the focus. */
export const ISLAND: Readonly<Record<IslandState, { width: TokenName | null; height: TokenName | 'content' | null; focus: boolean }>> = {
	hidden: { width: null, height: null, focus: false },
	pill: { width: '--jv-material-floating-pill-width', height: '--jv-material-floating-pill-height', focus: false },
	activity: { width: '--jv-material-floating-activity-width', height: '--jv-material-floating-pill-height', focus: false },
	card: { width: '--jv-material-floating-card-width', height: 'content', focus: true },
	approval: { width: '--jv-material-floating-card-width', height: 'content', focus: true },
	rest: { width: '--jv-material-floating-rest-width', height: '--jv-material-floating-rest-height', focus: false }
};

/**
 * The state for what is going on. An approval is never flicked away unanswered; a flick puts a
 * live turn in its pill and anything else out of the way until something new arrives.
 */
export function islandState(i: Partial<IslandInputs>): IslandState {
	if (i.ask) return 'approval';
	// At work or with the phone in use, a turn steps out of the way to its pill until opened (T-0885).
	if (i.turn) return i.collapsed || ((i.working || i.touched) && !i.expanded) ? 'pill' : 'card';
	const idle = i.on ? 'rest' : 'hidden';
	if (i.collapsed) return idle;
	if (i.notice || i.expanded) return 'card';
	return i.activity ? 'activity' : idle;
}

/**
 * A spring's tokens (`--jv-spring-<name>-*`) as a CSS `linear()` easing over its settle time, so a
 * transition between two sizes moves the way the phone's spring does. Does not cover a morph cut
 * short: a CSS transition starts again from where it is, without the spring's speed.
 */
export function springEasing(name: 'glass' | 'snap' | 'control', steps = 32): { easing: string; ms: number } {
	const k = Number(token(`--jv-spring-${name}-stiffness`));
	const c = Number(token(`--jv-spring-${name}-damping`));
	const m = Number(token(`--jv-spring-${name}-mass`));
	const ms = tokenMs(`--jv-spring-${name}-settle`);
	const w0 = Math.sqrt(k / m);
	const zeta = c / (2 * Math.sqrt(k * m));
	const wd = w0 * Math.sqrt(Math.max(1 - zeta * zeta, 0));
	// The closed form from rest at 0 to 1; critically damped when the ratio reaches 1.
	const at = (t: number) =>
		wd > 0
			? 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + ((zeta * w0) / wd) * Math.sin(wd * t))
			: 1 - Math.exp(-w0 * t) * (1 + w0 * t);
	const points = Array.from({ length: steps - 1 }, (_, i) => Number(at((((i + 1) / steps) * ms) / 1000).toFixed(3)));
	return { easing: `linear(0, ${points.join(', ')}, 1)`, ms };
}
