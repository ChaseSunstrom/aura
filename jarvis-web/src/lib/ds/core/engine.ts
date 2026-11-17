// The core's parameter model, ported from the v2 reference (orb_engine.js).
//
// Pure: numbers in, numbers out, no DOM, no clock of its own. Both renderers
// (webgl.ts on three.js, canvas2d.ts the reference port) read the same model
// every frame, so the GPU core and the fallback cannot drift apart: what a
// state looks like, how a change travels, how loud the voice makes it, all live
// here once.
//
// The units are the reference's. Time is in milliseconds; space is in core
// radii, where the particle shell is the unit sphere and a renderer maps one
// unit to `SPREAD × min(width, height)` pixels, seen through a camera `CAMERA`
// units away and tilted `TILT` radians toward the viewer.
//
// Does not cover: smoothing the inputs. A mic level that jumps makes the shell
// jump; the voice pipeline's level is already an envelope.

import { TOKENS, type TokenName } from '$lib/tokens';
import { MS } from '../motion';
import type { CoreState } from './states';

/** One 60Hz frame, in ms: the reference's tween rates are per frame at this rate. */
export const FRAME = 1000 / 60;
/** The longest step a frame may take: a page back after a minute away resumes, not leaps. */
const MAX_DT = 100;
/** Tween rates per 60Hz frame (the reference's): parameters, and the slower colour. */
const EASE = 0.035;
const EASE_COLOR = 0.05;
/** At most this many shock rings at once, however fast the states change. */
const MAX_RINGS = 4;
/** A frozen frame is at most this far after its change (a query string is not a hang). */
const MAX_FROZEN = 60_000;

/** The view: the shell's tilt toward the viewer, the camera distance, the spread. */
export const TILT = 0.28;
export const CAMERA = 3.3;
export const SPREAD = 0.36;
/** The board the reference's pixel widths were drawn on: a line `w` there is `w × width/600` here. */
export const BOARD_WIDTH = 600;

/**
 * The three segmented rings: radius, segment count, rest angles about X (a) and Y (b), turn
 * direction, and what makes each its own ring (T-0748: they were one ring at three sizes):
 * `w` scales the segment's radial extent, `d` its depth, and `gap` is its own gap (rad).
 * Outermost is a broad, shallow plate with tight joints; innermost a fine, open band; and the
 * step between them is uneven.
 */
export const RINGS = [
	{ r: 0.58, seg: 12, a: 1.35, b: 0, dir: 1, w: 1.15, d: 0.8, gap: 0.08 },
	{ r: 0.455, seg: 10, a: 0.2, b: 0.95, dir: -1, w: 0.85, d: 1, gap: 0.12 },
	{ r: 0.35, seg: 8, a: 0.2, b: -0.95, dir: 1.4, w: 0.6, d: 1.2, gap: 0.16 }
] as const;

/** A segment before its ring's own scale: radial extent about the ring, depth, and the coil's windings. */
export const SEGMENT = { inner: 0.07, outer: 0.05, depth: 0.05, windings: 5 } as const;

/** Ring `ri`'s segment: its radial extent about the ring, its depth, and the gap to the next (rad). */
export function segmentOf(ri: number): { inner: number; outer: number; depth: number; gap: number } {
	const R = RINGS[ri];
	return { inner: SEGMENT.inner * R.w, outer: SEGMENT.outer * R.w, depth: SEGMENT.depth * R.d, gap: R.gap };
}
/** Dots flowing round each ring, their trail (rad), the comet's length in steps, and how far outside the ring's outer edge they run. */
export const FLOW = { dots: 26, trail: 0.07, comet: 14, lift: 0.05 } as const;
/** The four struts run from ring 0's inner edge to this radius, round the core. */
export const STRUT_IN = 0.2;

export type Rgb = [number, number, number];

/** What a state looks like: the targets the core tweens toward. */
export interface Look {
	/** Ring spin and gyroscope turn rates. */
	spin: number;
	gyro: number;
	/** 0: the rings turned apart; 1: lined up facing the viewer. */
	align: number;
	/** The shell's radius. */
	shell: number;
	/** How much the shell's rotation shears with latitude. */
	swirl: number;
	/** How fast light flows round the rings and the shell turns. */
	flow: number;
	/** The core's brightness. */
	glow: number;
	/** 1 while working: the rings wobble and the beat runs. */
	work: number;
	color: Rgb;
}

const PARAMS = ['spin', 'gyro', 'align', 'shell', 'swirl', 'flow', 'glow', 'work'] as const;

/** `#rrggbb` → [r, g, b]. Throws on anything else: a token that is not a hex colour is a build error. */
export function rgbOf(hex: string): Rgb {
	const m = /^#([0-9a-f]{6})$/i.exec(hex);
	if (!m) throw new Error(`not a #rrggbb colour: ${hex}`);
	const n = Number.parseInt(m[1], 16);
	return [n >> 16, (n >> 8) & 255, n & 255];
}

/** The token each state's colour comes from; Core.svelte tints the floor glow with it. */
export const LOOK_TOKEN: Readonly<Record<CoreState, TokenName>> = {
	idle: '--jv-accent',
	listening: '--jv-accent',
	speaking: '--jv-core-speak',
	thinking: '--jv-violet',
	embedding: '--jv-violet',
	acting: '--jv-accent',
	music: '--jv-accent',
	alert: '--jv-danger',
	working: '--jv-core-work'
};

const look = (state: CoreState, l: Omit<Look, 'color'>): Look => ({
	...l,
	color: rgbOf(TOKENS[LOOK_TOKEN[state]])
});

/** The nine looks (the reference's targets). */
export const LOOKS: Readonly<Record<CoreState, Readonly<Look>>> = {
	idle: look('idle', { spin: 0.18, gyro: 0.12, align: 0, shell: 1, swirl: 0, flow: 0.2, glow: 0.7, work: 0 }),
	listening: look('listening', { spin: 0.35, gyro: 0.05, align: 1, shell: 1.02, swirl: 0, flow: 0.3, glow: 0.9, work: 0 }),
	speaking: look('speaking', { spin: 0.45, gyro: 0.08, align: 0.65, shell: 1.02, swirl: 0, flow: 0.35, glow: 1.1, work: 0 }),
	thinking: look('thinking', { spin: 1.6, gyro: 1.1, align: 0, shell: 0.84, swirl: 1.4, flow: 1.6, glow: 0.85, work: 0 }),
	embedding: look('embedding', { spin: 0.9, gyro: 0.5, align: 0.2, shell: 0.9, swirl: 0.4, flow: 0.9, glow: 1, work: 0 }),
	acting: look('acting', { spin: 0.9, gyro: 0.45, align: 0.15, shell: 0.94, swirl: 0.5, flow: 0.9, glow: 0.95, work: 0 }),
	music: look('music', { spin: 0.55, gyro: 0.25, align: 0.3, shell: 1.04, swirl: 0.2, flow: 0.5, glow: 1, work: 0 }),
	alert: look('alert', { spin: 0.05, gyro: 0, align: 1, shell: 0.96, swirl: 0, flow: 0.1, glow: 0.8, work: 0 }),
	working: look('working', { spin: 1.1, gyro: 0.2, align: 0, shell: 0.9, swirl: 0.25, flow: 1.3, glow: 1, work: 1 })
};

/** What moves the core from outside, each 0..1. Missing: synthesized; not a number: silence. */
export interface Inputs {
	/** The mic level while listening. */
	level?: number;
	/** The speech envelope while speaking. */
	envelope?: number;
	/** The music's beat strength while playing. */
	music?: number;
	/** The primary persona's hue, in place of the state's own colour (personas.ts `tintFor` says when). */
	tint?: Rgb;
}

/** The model a renderer draws. Mutable: `step` advances it in place, every frame. */
export interface Core {
	state: CoreState;
	/** The look now, part way to `LOOKS[state]`. */
	look: Look;
	/** The model's clock (ms): the `now` of the last step. */
	t: number;
	/** Accumulated angles: ring spin, gyroscope, shell turn, flow. */
	spinA: number;
	gyroA: number;
	shellA: number;
	flowA: number;
	/** 0..1: how loud the mic, the voice or the music is right now. */
	amp: number;
	/** The ambient beat, 0..1. */
	beat: number;
	kickAt: number;
	/** Start times of the shock rings still on screen (or about to be pruned). */
	rings: number[];
	/** Working beats so far, and when the last one fell. */
	beats: number;
	lastBeat: number;
	/** State changes so far. */
	changes: number;
	/** Reduced motion: changes snap, no rings, no beat. */
	still: boolean;
}

const copy = (l: Readonly<Look>): Look => ({ ...l, color: [...l.color] });

export function createCore(state: CoreState = 'idle', now = 0, still = false): Core {
	return {
		state,
		look: copy(LOOKS[state]),
		t: now,
		spinA: 0,
		gyroA: 0,
		shellA: 0,
		flowA: 0,
		amp: 0,
		beat: 0.5,
		kickAt: Number.NEGATIVE_INFINITY,
		rings: [],
		beats: 0,
		lastBeat: now,
		changes: 0,
		still
	};
}

function ring(core: Core, now: number): void {
	core.rings = core.rings.filter((s) => now - s < MS.shock);
	core.rings.push(now);
	if (core.rings.length > MAX_RINGS) core.rings.shift();
}

/**
 * Change state at `now`. A real change kicks the glow and fires a shock ring,
 * then tweens; with `snap` (reduced motion; `core.still` by default) it lands
 * at once and fires nothing. Returns whether the state changed.
 */
export function setState(core: Core, state: CoreState, now: number, opts: { snap?: boolean } = {}): boolean {
	if (state === core.state) return false;
	core.state = state;
	core.changes += 1;
	core.lastBeat = now;
	if (opts.snap ?? core.still) {
		core.look = copy(LOOKS[state]);
		return true;
	}
	core.kickAt = now;
	ring(core, now);
	return true;
}

const unit = (v: number | undefined, fallback: number) =>
	v === undefined ? fallback : Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0;

function ampFor(core: Core, now: number, inputs: Inputs): number {
	switch (core.state) {
		case 'listening':
			return unit(inputs.level, 0.5 + 0.5 * Math.abs(Math.sin(now * 0.006) * Math.sin(now * 0.0017)));
		case 'speaking':
			return unit(inputs.envelope, core.beat);
		case 'music':
			return unit(inputs.music, Math.abs(Math.sin(now * 0.0065)) ** 3);
		default:
			return 0;
	}
}

/** Advance the model to `now`: tween, turn, read the inputs, beat. */
export function step(core: Core, now: number, inputs: Inputs = {}): void {
	const dt = Math.min(MAX_DT, Math.max(0, now - core.t));
	core.t = now;
	const target = LOOKS[core.state];
	const L = core.look;
	// Per-frame rates made frame-rate independent: k per 60Hz frame is
	// 1-(1-k)^(dt/frame) per dt, so 120Hz and 60Hz land in the same place.
	const f = 1 - (1 - EASE) ** (dt / FRAME);
	// Still: a tint that came with no state change lands in the one frame drawn, as a change does.
	const fc = core.still ? 1 : 1 - (1 - EASE_COLOR) ** (dt / FRAME);
	const color = inputs.tint ?? target.color;
	for (const p of PARAMS) L[p] += (target[p] - L[p]) * f;
	for (let i = 0; i < 3; i++) L.color[i] += (color[i] - L.color[i]) * fc;

	core.spinA += dt * 0.001 * L.spin;
	core.gyroA += dt * 0.001 * L.gyro;
	core.shellA += dt * 0.001 * (0.12 + L.flow * 0.25);
	core.flowA += dt * 0.001 * (0.6 + L.flow * 1.6);
	core.beat = 0.5 + 0.5 * Math.sin(now * 0.008) * Math.sin(now * 0.0031);
	core.amp = ampFor(core, now, inputs);

	if (core.state === 'working' && !core.still && now - core.lastBeat >= MS.beat) {
		// On the beat's own grid, so a late frame does not push every later beat;
		// one beat however long the gap was.
		core.lastBeat = now - ((now - core.lastBeat) % MS.beat);
		core.beats += 1;
		core.kickAt = now;
		ring(core, now);
	}
}

/** The glow kick after a change or a beat: 1 at once, 0 after the kick time. */
export function kick(core: Core, now: number): number {
	return Math.min(1, Math.max(0, 1 - (now - core.kickAt) / MS.kick));
}

/** Each live shock ring's progress, 0 (just fired) to 1 (gone). */
export function shocks(core: Core, now: number): number[] {
	return core.rings.map((s) => (now - s) / MS.shock).filter((f) => f >= 0 && f < 1);
}

/** A shock ring at progress f: radius (core radii), width (board px) and alpha. */
export function shockRing(f: number): { radius: number; width: number; alpha: number } {
	return { radius: 0.2 + 1.1 * f, width: 6 - 5 * f, alpha: 0.8 * (1 - f) };
}

/** The core's glow (brightness) and radius now. */
export function coreGlow(core: Core): { glow: number; radius: number } {
	const flick = core.state === 'alert' ? 0.8 + 0.2 * Math.sin(core.t * 0.03) : 1;
	return {
		glow: core.look.glow * flick * (1 + 0.25 * core.amp) + 0.6 * kick(core, core.t),
		radius: 0.15 * (1 + 0.12 * core.amp)
	};
}

/** The particle shell's points: positions on the unit sphere, which are hot, and each one's pace. */
export interface Shell {
	n: number;
	pos: Float32Array;
	hot: Uint8Array;
	/** 0.8..1.2: each point turns and fades at its own pace, so the shell churns. */
	speed: Float32Array;
	/** 0..1: where in its fade a point starts. */
	phase: Float32Array;
}

/** A repeatable 0..1 for point i: the shell is the same on every load and in every test. */
const hash = (i: number, salt: number) => {
	const x = Math.sin(i * 12.9898 + salt * 78.233) * 43758.5453;
	return x - Math.floor(x);
};

/** `n` points spread evenly over the unit sphere (the golden-angle spiral); about 3% hot. */
export function fibonacci(n: number): Shell {
	const count = Math.max(0, Math.floor(n));
	const pos = new Float32Array(count * 3);
	const hot = new Uint8Array(count);
	const speed = new Float32Array(count);
	const phase = new Float32Array(count);
	const ga = Math.PI * (3 - Math.sqrt(5));
	for (let i = 0; i < count; i++) {
		const y = 1 - ((i + 0.5) / count) * 2;
		const r = Math.sqrt(1 - y * y);
		pos[i * 3] = Math.cos(i * ga) * r;
		pos[i * 3 + 1] = y;
		pos[i * 3 + 2] = Math.sin(i * ga) * r;
		hot[i] = hash(i, 1) < 0.03 ? 1 : 0;
		speed[i] = 0.8 + 0.4 * hash(i, 2);
		phase[i] = hash(i, 3);
	}
	return { n: count, pos, hot, speed, phase };
}

/**
 * The shell now, into `out` (4 floats a point: x, y, z before the tilt, and an
 * alpha). The radius breathes by state: a ripple with the mic, a swell with the
 * voice, a bounce with the music, a shiver in alert; while embedding each point
 * falls in and fades, and starts again.
 */
export function shellInto(core: Core, shell: Shell, out: Float32Array): void {
	const { look: L, t, amp, state } = core;
	const { pos, speed, phase } = shell;
	for (let i = 0; i < shell.n; i++) {
		const px = pos[i * 3];
		const py = pos[i * 3 + 1];
		const pz = pos[i * 3 + 2];
		const sp = speed[i];
		let k = L.shell;
		let alpha = 1;
		if (state === 'listening') k *= 1 + 0.07 * amp * Math.sin(py * 9 - t * 0.012);
		else if (state === 'speaking') k *= 1 + 0.07 * amp;
		else if (state === 'music') k *= 1 + 0.09 * amp * (0.5 + 0.5 * Math.sin(py * 5 + px * 3));
		else if (state === 'alert') k *= 1 + 0.012 * Math.sin(t * 0.05 + px * 40);
		else if (state === 'embedding') {
			const run = t * 0.00045 * sp + phase[i];
			const life = 1 - (run - Math.floor(run));
			k *= 0.25 + 0.75 * life;
			alpha = 0.25 + 0.75 * life;
		} else k *= 1 + 0.018 * Math.sin(t * 0.002 + py * 3);
		// One speed for the whole shell: a speed a point drifted the golden spiral into clumps and
		// holes within a minute (T-0749). The swirl turns latitudes, not points, so a band stays even.
		const a = core.shellA * (1 + L.swirl * py * py) + L.swirl * py * 0.8;
		const c = Math.cos(a);
		const s = Math.sin(a);
		out[i * 4] = (px * c + pz * s) * k;
		out[i * 4 + 1] = py * k;
		out[i * 4 + 2] = (pz * c - px * s) * k;
		out[i * 4 + 3] = alpha;
	}
}

/**
 * Ring `ri` now: turned `a` about X, then `b` about Y (radians), and scaled by
 * `pulse` with the voice or the beat. Lined up, the rings face the viewer;
 * working, they wobble.
 */
export function ringPose(core: Core, ri: number): { a: number; b: number; pulse: number } {
	const R = RINGS[ri];
	const { align: al, work: wk } = core.look;
	const t = core.t;
	const a = R.a * (1 - al) + Math.sin(t * 0.0003 + ri) * 0.12 * (1 - al) + wk * Math.sin(t * 0.0016 + ri * 2.1) * 0.7;
	const b = (R.b + core.gyroA * R.dir) * (1 - al) + (ri - 1) * 0.08 * al + wk * Math.cos(t * 0.0012 + ri * 2.1) * 0.7;
	const pulse = core.state === 'speaking' || core.state === 'music' ? 1 + 0.05 * core.amp : 1;
	return { a, b, pulse };
}

/** How lit segment k is: a running wave while thinking or playing, steady otherwise. */
export function segmentLit(core: Core, k: number): number {
	if (core.state === 'music') return 0.6 + 0.8 * Math.max(0, Math.sin(core.t * 0.013 + k * 1.3));
	if (core.state === 'thinking') return 0.7 + 0.6 * Math.max(0, Math.sin(core.t * 0.02 - k));
	return 1;
}

/** The angle of flowing dot i on ring ri now. */
export function flowAngle(core: Core, ri: number, i: number): number {
	return core.flowA * RINGS[ri].dir * 0.8 + (i / FLOW.dots) * Math.PI * 2 + ri;
}

/** The comet's head on ring ri now: it runs the other way round, faster. */
export function cometAngle(core: Core, ri: number): number {
	return -core.flowA * RINGS[ri].dir * 1.3 + ri * 2;
}

/** What Core.svelte asks of a renderer (webgl.ts, canvas2d.ts): size, draw the model, let go. */
export interface Renderer {
	readonly kind: 'webgl' | 'canvas2d';
	/** CSS pixels and the device pixel ratio (already capped). */
	resize(width: number, height: number, dpr: number): void;
	/** The model as it stands; `shell` may change (battery saver), never grow past the first. */
	draw(core: Core, shell: Shell): void;
	/** Free everything: buffers, programs and, for WebGL, the context itself. */
	dispose(): void;
}

/** The palette's fixed colours, from the tokens: the core's white, its spark, halo, hot points and dark faces. */
export const PALETTE = {
	white: rgbOf(TOKENS['--jv-core-white']),
	spark: rgbOf(TOKENS['--jv-core-spark']),
	halo: rgbOf(TOKENS['--jv-core-halo']),
	hot: rgbOf(TOKENS['--jv-core-hot']),
	face: rgbOf(TOKENS['--jv-core-face'])
} as const;

/**
 * The frame `at` ms after a change from `from` to `state`, stepped at 60Hz: the
 * same picture on every load, which is what a screenshot and a pixel test need.
 * `at` is clamped to a minute; not a number is 0.
 */
export function frozenAt(state: CoreState, from: CoreState = 'idle', at = 0, inputs: Inputs = {}): Core {
	const end = Number.isFinite(at) ? Math.min(MAX_FROZEN, Math.max(0, at)) : 0;
	const core = createCore(from, 0);
	setState(core, state, 0);
	const n = Math.floor(end / FRAME);
	for (let i = 1; i <= n; i++) step(core, i * FRAME, inputs);
	if (core.t !== end) step(core, end, inputs);
	return core;
}
