// The arc reactor's geometry and its eleven states, typed from
// `tests/contracts/reactor.json` (GLASS_HUD §10).
//
// Three concentric glass rings of decreasing width around a core, refracting
// the core's light: no spokes, no cut-outs, no prop geometry. Every constant
// below is a key of that contract, and `spec.test.ts` reads the JSON and
// refuses this file drifting from it. That is the whole point of the file —
// the previous instrument was retuned on the web and not on the phone, and
// nothing anywhere said the two were supposed to agree, so they did not.
//
// Radii and widths are fractions of R, the drawable radius (half the element
// minus `MARGIN` of it, left clear for the glow to bleed into). Colours are
// never here: a renderer reads `STATE_ACCENT` from the generated token table.

import { STATE_ACCENT, TOKENS, tokenMs, type AccentState } from '$lib/tokens';

/** Fraction of the element kept clear round the outer ring, for the glow. */
export const MARGIN = 0.04;

export interface Ring {
	name: 'outer' | 'middle' | 'inner';
	/** Radius, as a fraction of R. */
	r: number;
	/** Stroke width, as a fraction of R. */
	width: number;
}

/** Outermost first: the draw order that lands the core on top. */
export const RINGS: readonly Ring[] = [
	{ name: 'outer', r: 0.9, width: 0.11 },
	{ name: 'middle', r: 0.68, width: 0.075 },
	{ name: 'inner', r: 0.49, width: 0.05 }
] as const;

export const CORE_R = 0.3;

/** The shader's refraction: the ring sampled once per channel, this far apart. */
export const CHROMATIC_OFFSET_PX = 1.5;
/** The Canvas reading of the same thing: the ring stroked again, offset by this. */
export const CANVAS_OFFSET_PX = 1;

export const SEGMENT_GAP_DEG = 8;
export const MAX_SEGMENTS = 12;
export const SATELLITE_R = 0.03;
export const ATTENTION_GAP_DEG = 40;
export const THINK_ARC_DEG = 110;
export const ALERT_BAR_LENGTH = 0.5;
export const ALERT_BAR_THICKNESS = 0.06;
export const SPEAK_SWELL = 0.03;

/** The ignition (§8, the one signature moment): fractions of `motion.dur.enter`. */
export const BOOT = {
	coreFrom: 0,
	coreTo: 0.45,
	ringsFrom: 0.35,
	ringStagger: 0.15,
	ringEach: 0.4,
	overshoot: 1.06
} as const;

/** How long the ignition lasts, in ms — `motion.dur.enter`, never a number. */
export const IGNITE_MS = tokenMs('--jv-dur-enter');

/**
 * The eleven states of §10's table. `goal` is here because it has a colour, a
 * word and a glyph, but it is never a state a derivation returns: it is the
 * overlay flag drawn on top of whichever state won.
 */
export const STATES = [
	'idle',
	'listening',
	'thinking',
	'speaking',
	'tool',
	'goal',
	'attention',
	'warming',
	'alert',
	'kill',
	'offline'
] as const;

export type ReactorStateName = (typeof STATES)[number];

/**
 * Highest first (contract `precedence`). `goal` is absent on purpose — it
 * cannot win, it can only be worn.
 */
export const PRECEDENCE: readonly ReactorStateName[] = [
	'kill',
	'offline',
	'alert',
	'attention',
	'warming',
	'speaking',
	'listening',
	'tool',
	'thinking',
	'idle'
] as const;

/** Nothing moves in these two: the room has gone dark (§10 rows 10 and 11). */
export const isStill = (state: ReactorStateName): boolean => state === 'kill' || state === 'offline';

/** The state word the tray, the notification and the console strip all carry. */
export const WORDS: Record<ReactorStateName, string> = {
	idle: 'Ready',
	listening: 'Listening',
	thinking: 'Thinking',
	speaking: 'Speaking',
	tool: 'Working',
	goal: 'On a goal',
	attention: 'Needs you',
	warming: 'Warming up',
	alert: 'Alert',
	kill: 'Kill switch',
	offline: 'Offline'
};

/**
 * C for a state. An alias of the generated token, so no hue is added here and
 * the token lint has nothing to find.
 */
export const colorOf = (state: ReactorStateName): string => STATE_ACCENT[state as AccentState];

/** The clock, in ms: every period is a `motion.*` token, because a duration is a design value. */
export const PERIOD = {
	breathe: tokenMs('--jv-rx-breathe'),
	level: tokenMs('--jv-rx-level'),
	think: tokenMs('--jv-rx-think'),
	speak: tokenMs('--jv-rx-speak'),
	irisB: tokenMs('--jv-rx-iris-b'),
	base: tokenMs('--jv-dur-base'),
	pulse: tokenMs('--jv-dur-pulse'),
	blink: tokenMs('--jv-dur-blink')
} as const;

/** The halo's k for a state; speaking and warming move between two (see `frame.ts`). */
export function haloOf(state: ReactorStateName): number {
	const name = state === 'goal' ? 'idle' : state;
	return Number(TOKENS[`--jv-light-halo-${name}` as keyof typeof TOKENS]);
}

/** The peak the two moving halos reach, or the resting value for the other nine. */
export function haloPeakOf(state: ReactorStateName): number {
	if (state === 'speaking') return Number(TOKENS['--jv-light-halo-speaking-peak']);
	if (state === 'warming') return Number(TOKENS['--jv-light-halo-warming-peak']);
	return haloOf(state);
}

/**
 * Whether this state's halo travels with something the reactor measures, rather
 * than resting on one token.
 *
 * Derived from the token table, not typed: a state moves exactly when it has a
 * `-peak` token above its resting one, which today is speaking (with the TTS
 * envelope) and warming (with cold-start progress). Writing the pair out would
 * be a second list to keep in step, and the first thing to go stale if §10 ever
 * gives a third row a range.
 */
export const haloMoves = (state: ReactorStateName): boolean => haloPeakOf(state) > haloOf(state);

/**
 * `motion.ease.standard` sampled at `t`. A cubic Bézier in x has no closed
 * form for y(x), so x is solved by bisection — sixteen halvings is well under
 * a pixel at any size this draws at, and it is the same solve the phone and
 * the console do, so one curve serves all three.
 */
export function ease(t: number): number {
	const x = Math.min(1, Math.max(0, t));
	if (x <= 0) return 0;
	if (x >= 1) return 1;
	const [x1, y1, x2, y2] = EASE_STANDARD;
	let lo = 0;
	let hi = 1;
	let u = x;
	for (let i = 0; i < 16; i++) {
		u = (lo + hi) / 2;
		if (bezier(x1, x2, u) < x) lo = u;
		else hi = u;
	}
	return bezier(y1, y2, u);
}

/** One axis of a cubic Bézier from (0,0) through p1, p2 to (1,1). */
function bezier(p1: number, p2: number, u: number): number {
	const v = 1 - u;
	return 3 * v * v * u * p1 + 3 * v * u * u * p2 + u * u * u;
}

/**
 * The control points of `--jv-ease-standard`, parsed from the token rather
 * than typed: a curve retuned in `tokens.json` must reach the canvas, which
 * cannot read a CSS easing function.
 */
export const EASE_STANDARD: readonly [number, number, number, number] = parseCubic(
	TOKENS['--jv-ease-standard']
);

function parseCubic(value: string): [number, number, number, number] {
	const m = /^cubic-bezier\(([^)]+)\)$/.exec(value.trim());
	if (!m) throw new Error(`not a cubic-bezier: ${value}`);
	const n = m[1].split(',').map((p) => Number(p.trim()));
	if (n.length !== 4 || n.some((v) => !Number.isFinite(v))) throw new Error(`bad cubic-bezier: ${value}`);
	return [n[0], n[1], n[2], n[3]];
}

/** A cosine breath on `periodMs`, 0..1: soft at both turnarounds. */
export const breath = (tSeconds: number, periodMs: number): number =>
	0.5 - 0.5 * Math.cos((2 * Math.PI * tSeconds) / (periodMs / 1000));

/** Idle at the middle of its breath: the still the icons are rendered from. */
export const STILL_LUMINANCE = 0.7;


// --- what the instrument shows, independent of how it is drawn --------------
//
// These five rules decide what the reactor *means*: which state it wears, what
// the level arc reads, which blades are lit, and how the ignition assembles.
// They lived inside `Reactor.svelte` while there was one renderer. There are now
// two -- the SVG instrument and the orb (ADR-0038: the orb is the same
// instrument made dimensional, not a second one) -- and the house rule is that
// logic existing in two places is bound by a shared definition rather than by a
// comment asking someone to keep them in step.
//
// Pure and renderer-agnostic on purpose: an SVG dash offset and a torus sweep
// are different geometry for the same number, and the number belongs here.

/** Blade tones, in the order a plan walks through them. */
export type BladeTone = 'idle' | 'soft' | 'done' | 'running' | 'pending';

/**
 * The state actually worn.
 *
 * A goal running under an otherwise idle instrument is shown as `goal`, so the
 * resting state never hides that there is a plan in flight.
 */
export const wornState = (state: ReactorStateName, goal: boolean): ReactorStateName =>
	goal && state === 'idle' ? 'goal' : state;

/** Whether the blades are carrying a plan rather than idling. */
export const isPlan = (segments: number, worn: ReactorStateName, goal: boolean): boolean =>
	segments > 0 && (worn === 'tool' || worn === 'goal' || goal);

/**
 * What the level arc reads, 0..1.
 *
 * Three things compete for one arc and the precedence matters: a cold start
 * outranks a plan, because until the model is up the plan is not moving; a plan
 * outranks the voice level, because progress is the more useful of the two.
 */
export function arcValue(input: {
	worn: ReactorStateName;
	plan: boolean;
	warmingProgress?: number | null;
	litSegments: number;
	segments: number;
	level: number;
}): number {
	const raw =
		input.worn === 'warming'
			? (input.warmingProgress ?? 0)
			: input.plan
				? input.litSegments / input.segments
				: input.level;
	return Math.min(1, Math.max(0, Number.isFinite(raw) ? raw : 0));
}

/**
 * Which tone blade `index` wears.
 *
 * Off-plan, every third blade is dimmed, which is what gives the ring its
 * rhythm rather than reading as a solid band. On-plan the blades group into the
 * plan's steps: finished, the one running, and the rest.
 */
export function bladeTone(
	index: number,
	blades: number,
	opts: { plan: boolean; segments: number; litSegments: number }
): BladeTone {
	if (!opts.plan) return index % 3 === 2 ? 'soft' : 'idle';
	const slot = Math.floor((index * opts.segments) / blades);
	const done = Math.min(opts.litSegments, opts.segments);
	if (slot < done) return 'done';
	if (slot < done + 1 && done < opts.segments) return 'running';
	return 'pending';
}

/** How many layers the ignition assembles, in draw order. */
export const IGNITION_LAYERS = ['bezel', 'blades', 'coil', 'level', 'core'] as const;

/**
 * How far layer `index` has assembled, 0..1.
 *
 * Each layer takes a third of the run and the next starts a sixth later, so the
 * five overlap and the last completes exactly at 1. `null` means no boot is
 * playing and everything is fully present.
 */
export const ignitionLayer = (boot: number | null, index: number): number =>
	boot === null ? 1 : Math.min(1, Math.max(0, (boot - index * 0.16) / 0.36));
