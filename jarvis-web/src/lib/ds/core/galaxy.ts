// AURA's core: a living spiral galaxy, ported from the theme's reference engine
// (themes/aura/assets/core/galaxy_engine.js) into a pure model and a draw list.
//
// Pure: numbers in, numbers out, no DOM. `stepGalaxy` advances the model, and
// `galaxyInto` writes one frame as a flat list of instances (squares, soft blobs,
// line segments) in device pixels, in draw order, with the blend each run is drawn
// with. Both drawers (galaxy-draw.ts: WebGL2, and Canvas2D as the fallback) draw
// that one list, so the GPU galaxy and the fallback cannot drift apart, and the
// tests can count what a state draws without a GPU.
//
// The numbers are the reference's, and the per-state targets, the palette and the
// star budgets are the theme pack's (`THEMES.aura.core`): a state's look is data in
// themes/aura/theme.json, not code here. The reference's per-frame rates (tween
// 0.035, colour 0.05) are made frame-rate independent the way engine.ts does it.
//
// Does not cover: the persona tint the reactor wears (AURA's palette is already her
// hue; a persona with another hue wears the JARVIS theme and its reactor), and the
// reference's icon mode (the app icon is a file in the pack, not a live core).

import { THEMES, type CoreState as ThemeState } from '$lib/themes';
import { MS } from '../motion';
import { FRAME, rgbOf, type Inputs, type Rgb } from './engine';
import type { CoreState } from './states';

const PACK = THEMES.aura.core;
const PAL = Object.fromEntries(
	Object.entries(PACK.palette).map(([k, v]) => [k, rgbOf(v)])
) as Record<keyof typeof PACK.palette, Rgb>;

/** The longest step one frame may take: a page back after a minute away resumes, not leaps. */
const MAX_DT = 50;
/** The colour tween per 60Hz frame (the reference's). */
const EASE_COLOR = 0.05;
/** The arms' winding: a star at radius r sits `r × WIND` radians along its arm. */
const WIND = PACK.params.wind;
/** The camera distance in the perspective divide `s = CAMERA / (CAMERA − z)`. */
const CAMERA = 3.4;
/** The galaxy's radius as a share of the surface's shorter side (the reference's .36 × 1.12). */
const SPREAD = 0.36 * 1.12;
/** The board the reference's pixel sizes were drawn on: a size `w` there is `w × side/600` here. */
const BOARD = 600;

/** How the core state the console speaks (states.ts, nine) lands on the theme's eight. */
export const GALAXY_STATE: Readonly<Record<CoreState, ThemeState>> = {
	idle: 'idle',
	listening: 'listen',
	speaking: 'speak',
	thinking: 'think',
	embedding: 'embed',
	// A tool call inside a turn moves like work (comets, quick spin) but without its beat.
	acting: 'work',
	music: 'music',
	alert: 'alert',
	working: 'work'
};

export type Quality = 'desktop' | 'phone' | 'mini';

/** Stars per quality: the pack's budgets (2100 on a desktop, 1200 on a phone, 360 in a dock). */
export const STARS: Readonly<Record<Quality, number>> = {
	desktop: PACK.params.starsDesktop,
	phone: PACK.params.starsPhone,
	mini: PACK.params.starsMini
};

interface Star {
	r: number;
	th: number;
	z: number;
	c: Rgb;
	sz: number;
	b: number;
	hot: boolean;
	arm: boolean;
	tw: boolean;
	ph: number;
	sp: number;
	/** Embed: how far out the star is on its fall into the core, 1 → 0, then round again. */
	life: number;
}

interface Blob {
	r: number;
	th: number;
	s: number;
	c: Rgb;
	ph: number;
}

interface Jet {
	u: number;
	sgn: number;
	jx: number;
	jy: number;
	sp: number;
}

/** What a state looks like: the targets the galaxy tweens toward. */
export interface Look {
	spin: number;
	tilt: number;
	radius: number;
	wind: number;
	glow: number;
	comets: number;
	jets: number;
	accretion: number;
	tintAmount: number;
}

const PARAMS = [
	'spin',
	'tilt',
	'radius',
	'wind',
	'glow',
	'comets',
	'jets',
	'accretion',
	'tintAmount'
] as const;

/** The pack's row for each of the eight states, with the tint the reference fades toward. */
export const LOOKS: Readonly<Record<ThemeState, Readonly<Look & { tint: Rgb | null }>>> =
	Object.fromEntries(
		Object.entries(PACK.states).map(([s, row]) => {
			const r = row as Partial<Look> & { tint?: string };
			const look = Object.fromEntries(PARAMS.map((p) => [p, r[p] ?? 0])) as unknown as Look;
			return [s, { ...look, tint: r.tint ? rgbOf(r.tint) : null }];
		})
	) as Record<ThemeState, Look & { tint: Rgb | null }>;

export interface Galaxy {
	/** The console's state, and the theme state it is drawn as. */
	mode: CoreState;
	state: ThemeState;
	quality: Quality;
	mini: boolean;
	stars: Star[];
	nebulae: Blob[];
	dust: Blob[];
	knots: Blob[];
	jets: Jet[];
	/** The look now, part way to `LOOKS[state]`. */
	look: Look;
	tint: Rgb;
	/** The model's clock (ms) and the disc's turn. */
	t: number;
	rot: number;
	amp: number;
	kickAt: number;
	lastBeat: number;
	beats: number;
	changes: number;
	/** Reduced motion: changes snap, no flare, no ring, no beat. */
	still: boolean;
}

/** The reference's generator: Park–Miller, so every load (and every test) gets the same sky. */
function random(seed: number) {
	let s = seed;
	const rnd = () => {
		s = (s * 16807) % 2147483647;
		return s / 2147483647;
	};
	const gauss = () => Math.sqrt(-2 * Math.log(rnd() || 1e-9)) * Math.cos(6.283 * rnd());
	return { rnd, gauss };
}

const mix = (a: Rgb, b: Rgb, f: number): Rgb => [
	a[0] + (b[0] - a[0]) * f,
	a[1] + (b[1] - a[1]) * f,
	a[2] + (b[2] - a[2]) * f
];
const armAngle = (arm: number, r: number) => arm * Math.PI + r * WIND;

/** A galaxy of `STARS[quality]` stars, in `state`, its look already there. */
export function createGalaxy(
	quality: Quality = 'desktop',
	state: CoreState = 'idle',
	still = false
): Galaxy {
	const { rnd, gauss } = random(7);
	const mini = quality === 'mini';
	const n = STARS[quality];
	const { warm, pink, lavender: lav, blue, gold, ice } = PAL;
	const stars: Star[] = [];
	for (let i = 0; i < n; i++) {
		const k = rnd();
		let r: number, th: number, z: number, c: Rgb, sz: number, b: number;
		let arm = false;
		if (k < 0.2) {
			// The bulge: warm, thick, near the centre.
			r = Math.min(0.32, Math.abs(gauss()) * 0.1);
			th = rnd() * 6.283;
			z = gauss() * 0.06 * (1 - r * 2.5);
			c = mix(warm, pink, r / 0.32);
			sz = 0.7 + rnd() * 0.9;
			b = 0.55 + rnd() * 0.45;
		} else if (k < 0.9) {
			// The two arms: pink inside, lavender, then blue at the rim.
			arm = true;
			const a = Math.floor(rnd() * 2);
			r = 0.08 + Math.pow(rnd(), 0.8) * 0.95;
			th = armAngle(a, r) + gauss() * (0.12 + 0.14 * r);
			r = Math.max(0.05, r + gauss() * 0.025);
			z = gauss() * 0.022;
			c =
				r < 0.35
					? mix(pink, lav, (r - 0.08) / 0.27)
					: mix(lav, blue, Math.min(1, (r - 0.35) / 0.6) * (rnd() < 0.5 ? 1 : 0.4));
			sz = 0.5 + rnd() * 1.1;
			b = 0.35 + rnd() * 0.55;
		} else {
			// The halo: faint, scattered.
			r = 0.15 + rnd() * 0.95;
			th = rnd() * 6.283;
			z = gauss() * 0.2;
			c = mix(lav, ice, rnd());
			sz = 0.4 + rnd() * 0.6;
			b = 0.12 + rnd() * 0.25;
		}
		const hot = rnd() < 0.028;
		if (hot) {
			c = rnd() < 0.5 ? ice : gold;
			sz *= 1.9;
			b = 1;
		}
		stars.push({
			r,
			th,
			z,
			c: c.map(Math.round) as Rgb,
			sz,
			b,
			hot,
			arm,
			tw: rnd() < 0.35,
			ph: rnd() * 6.283,
			sp: 0.6 + rnd() * 0.8,
			life: rnd()
		});
	}
	const nebulae: Blob[] = [];
	for (let i = 0; i < (mini ? PACK.params.nebulaeMini : PACK.params.nebulae); i++) {
		const r = 0.2 + rnd() * 0.7;
		const th = armAngle(i % 2, r) + gauss() * 0.15;
		const s = 0.12 + rnd() * 0.2;
		nebulae.push({ r, th, s, c: [pink, lav, blue][Math.floor(rnd() * 3)], ph: 0 });
	}
	const dust: Blob[] = [];
	const knots: Blob[] = [];
	if (!mini) {
		for (let i = 0; i < PACK.params.dustLanes; i++) {
			const r = 0.14 + rnd() * 0.72;
			const th = armAngle(i % 2, r) - 0.2 - rnd() * 0.08;
			dust.push({ r, th, s: 0.04 + rnd() * 0.05, c: PAL.dust, ph: 0 });
		}
		for (let i = 0; i < PACK.params.knots; i++) {
			const r = 0.28 + rnd() * 0.62;
			const th = armAngle(i % 2, r) + gauss() * 0.07;
			knots.push({ r, th, s: 0, ph: rnd() * 6.283, c: rnd() < 0.7 ? PAL.knot : PAL.knotCool });
		}
	}
	const jets: Jet[] = [];
	for (let i = 0; i < (mini ? PACK.params.jetsMini : PACK.params.jets); i++) {
		jets.push({ u: rnd(), sgn: i % 2 ? 1 : -1, jx: gauss(), jy: gauss(), sp: 0.7 + rnd() * 0.6 });
	}
	const to = LOOKS[GALAXY_STATE[state]];
	return {
		mode: state,
		state: GALAXY_STATE[state],
		quality,
		mini,
		stars,
		nebulae,
		dust,
		knots,
		jets,
		look: Object.fromEntries(PARAMS.map((p) => [p, to[p]])) as unknown as Look,
		tint: to.tint ? [...to.tint] : [0, 0, 0],
		t: 0,
		rot: 0,
		amp: 0,
		kickAt: Number.NEGATIVE_INFINITY,
		lastBeat: 0,
		beats: 0,
		changes: 0,
		still
	};
}

/** The same galaxy at another budget: the sky is rebuilt, the motion carries on. */
export function requalify(g: Galaxy, quality: Quality): Galaxy {
	if (g.quality === quality) return g;
	const next = createGalaxy(quality, g.mode, g.still);
	const { stars, nebulae, dust, knots, jets, mini } = next;
	return { ...g, quality, mini, stars, nebulae, dust, knots, jets };
}

/**
 * Change state. A real change flares the core and fires a shock ring, then tweens;
 * with `snap` (reduced motion: `g.still` by default) it lands at once and fires
 * nothing. Returns whether the state changed.
 */
export function setGalaxyState(g: Galaxy, mode: CoreState, opts: { snap?: boolean } = {}): boolean {
	if (mode === g.mode) return false;
	g.mode = mode;
	g.state = GALAXY_STATE[mode];
	g.changes += 1;
	g.lastBeat = g.t;
	if (opts.snap ?? g.still) {
		const to = LOOKS[g.state];
		for (const p of PARAMS) g.look[p] = to[p];
		if (to.tint) g.tint = [...to.tint];
		return true;
	}
	g.kickAt = g.t;
	return true;
}

const unit = (v: number | undefined, fallback: number) =>
	v === undefined ? fallback : Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0;

/** 0..1: the mic while listening, the voice while speaking, the beat while playing. */
function ampFor(g: Galaxy, inputs: Inputs): number {
	const t = g.t;
	switch (g.state) {
		case 'listen':
			return unit(inputs.level, 0.5 + 0.5 * Math.abs(Math.sin(t * 0.006) * Math.sin(t * 0.0017)));
		case 'speak':
			return unit(inputs.envelope, 0.5 + 0.5 * Math.sin(t * 0.008) * Math.sin(t * 0.0031));
		case 'music':
			return unit(inputs.music, Math.abs(Math.sin(t * 0.0065)) ** 3);
		default:
			return 0;
	}
}

/** Advance the model `dt` ms: tween, turn, fall, beat. */
export function stepGalaxy(g: Galaxy, dt: number, inputs: Inputs = {}): void {
	const d = Number.isFinite(dt) ? Math.min(MAX_DT, Math.max(0, dt)) : 0;
	g.t += d;
	const to = LOOKS[g.state];
	const f = g.still ? 1 : 1 - (1 - PACK.tween) ** (d / FRAME);
	const fc = g.still ? 1 : 1 - (1 - EASE_COLOR) ** (d / FRAME);
	for (const p of PARAMS) g.look[p] += (to[p] - g.look[p]) * f;
	// The tint only moves toward a state that has one; leaving it, the amount fades instead.
	if (to.tint) for (let i = 0; i < 3; i++) g.tint[i] += (to.tint[i] - g.tint[i]) * fc;
	g.rot += d * 0.001 * g.look.spin;
	g.amp = ampFor(g, inputs);
	if (g.state === 'embed') {
		for (const s of g.stars) {
			s.life -= d * 0.00035 * s.sp;
			if (s.life < 0) s.life += 1;
		}
	}
	if (g.look.jets > 0.02) {
		for (const j of g.jets) {
			j.u += d * 0.0005 * j.sp * (1 + g.look.spin * 0.4);
			if (j.u > 1) j.u -= 1;
		}
	}
	if (g.mode === 'working' && !g.still && g.t - g.lastBeat >= MS.beat) {
		// On the beat's own grid, so a late frame does not push every later beat.
		g.lastBeat = g.t - ((g.t - g.lastBeat) % MS.beat);
		g.beats += 1;
		g.kickAt = g.t;
	}
}

/** The frame `at` ms after a change from `from` to `state`, stepped at 60Hz: the same picture every load. */
export function galaxyAt(
	state: CoreState,
	from: CoreState = 'idle',
	at = 0,
	quality: Quality = 'desktop',
	inputs: Inputs = {}
): Galaxy {
	const end = Number.isFinite(at) ? Math.min(60_000, Math.max(0, at)) : 0;
	const g = createGalaxy(quality, from);
	setGalaxyState(g, state);
	const n = Math.floor(end / FRAME);
	for (let i = 0; i < n; i++) stepGalaxy(g, FRAME, inputs);
	stepGalaxy(g, end - n * FRAME, inputs);
	return g;
}

// --- the draw list -------------------------------------------------------------

/** One instance: x0, y0, x1, y1, width, shape, r, g, b (0..1), alpha at start, alpha at end. */
export const STRIDE = 11;
/** A square of half-side `width`; a soft disc of radius `width`; a line `width` wide. */
export const SQUARE = 0;
export const BLOB = 1;
export const LINE = 2;

export interface Batch {
	/** Source-over (the dust lanes darken); every other run adds light. */
	over: boolean;
	start: number;
	count: number;
}

export interface DrawList {
	data: Float32Array;
	n: number;
	batches: Batch[];
	width: number;
	height: number;
}

export function createDrawList(capacity = 4096): DrawList {
	return { data: new Float32Array(capacity * STRIDE), n: 0, batches: [], width: 0, height: 0 };
}

function blend(list: DrawList, over: boolean): void {
	const last = list.batches[list.batches.length - 1];
	if (last && last.over === over) return;
	if (last && last.count === 0) last.over = over;
	else list.batches.push({ over, start: list.n, count: 0 });
}

function put(
	list: DrawList,
	shape: number,
	x0: number,
	y0: number,
	x1: number,
	y1: number,
	w: number,
	c: Rgb,
	a0: number,
	a1 = a0
): void {
	if (!(a0 > 0.002 || a1 > 0.002) || !(w > 0)) return;
	if ((list.n + 1) * STRIDE > list.data.length) {
		const grown = new Float32Array(list.data.length * 2);
		grown.set(list.data);
		list.data = grown;
	}
	const o = list.n * STRIDE;
	const d = list.data;
	d[o] = x0;
	d[o + 1] = y0;
	d[o + 2] = x1;
	d[o + 3] = y1;
	d[o + 4] = w;
	d[o + 5] = shape;
	d[o + 6] = c[0] / 255;
	d[o + 7] = c[1] / 255;
	d[o + 8] = c[2] / 255;
	d[o + 9] = Math.min(1, Math.max(0, a0));
	d[o + 10] = Math.min(1, Math.max(0, a1));
	list.n += 1;
	list.batches[list.batches.length - 1].count += 1;
}

/** Where the galaxy sits on its surface: its centre as fractions, and its scale. */
export interface Placement {
	x: number;
	y: number;
	scale: number;
}

export const CENTRED: Placement = { x: 0.5, y: 0.5, scale: 1 };

/** One frame of `g` on a `width × height` (device px) surface, into `list`. */
export function galaxyInto(
	g: Galaxy,
	list: DrawList,
	width: number,
	height: number,
	at: Placement = CENTRED
): void {
	list.n = 0;
	list.batches.length = 0;
	list.width = width;
	list.height = height;
	const side = Math.min(width, height);
	const S = side * SPREAD * at.scale;
	const sc = (side * at.scale) / BOARD;
	const cx = width * at.x;
	const cy = height * at.y;
	const { t, look: L, mini, state } = g;
	const amp = g.amp;
	const since = t - g.kickAt;
	const flareMs = PACK.params.flareMs;
	const flare = since < flareMs ? 0.55 * (1 - since / flareMs) : 0;
	const radK = L.radius * (1 + 0.012 * Math.sin(t * 0.0015));
	const ct = Math.cos(L.tilt);
	const st = Math.sin(L.tilt);
	const pa = -0.32 + 0.04 * Math.sin(t * 0.00011);
	const cp = Math.cos(pa);
	const sp = Math.sin(pa);
	// The projection writes here rather than returning an array: thousands a frame.
	let qx = 0;
	let qy = 0;
	let qz = 0;
	const P = (x: number, y: number, z: number) => {
		const y1 = y * ct - z * st;
		const z1 = y * st + z * ct;
		const s = CAMERA / (CAMERA - z1);
		qx = cx + (x * cp - y1 * sp) * S * s;
		qy = cy + (x * sp + y1 * cp) * S * s;
		qz = z1;
	};
	const D = (r: number, a: number, z = 0) => P(r * Math.cos(a), r * Math.sin(a), z);
	/** A closed ring of `n` segments at radius R in the disc; alpha may depend on depth. */
	const ring = (
		R: number,
		n: number,
		w: number,
		c: Rgb,
		alpha: (z: number) => number,
		dash = false
	) => {
		D(R, 0);
		let px = qx;
		let py = qy;
		for (let i = 1; i <= n; i++) {
			D(R, (i / n) * 6.283);
			if (!(dash && i % 2)) put(list, LINE, px, py, qx, qy, w * sc, c, alpha(qz));
			px = qx;
			py = qy;
		}
	};

	blend(list, false);
	for (const n of g.nebulae) {
		D(n.r * radK, n.th + g.rot);
		put(list, BLOB, qx, qy, qx, qy, n.s * S * (1 + 0.1 * Math.sin(t * 0.0006 + n.r * 9)), n.c, mini ? 0.1 : 0.13);
	}
	// Dark dust lanes trailing each arm.
	blend(list, true);
	for (const d of g.dust) {
		D(d.r * radK, d.th + g.rot + L.wind * d.r * 1.6);
		put(list, BLOB, qx, qy, qx, qy, d.s * S, d.c, 0.55);
	}
	blend(list, false);
	const tinted = L.tintAmount > 0.01;
	const col: Rgb = [0, 0, 0];
	for (const p of g.stars) {
		let r = p.r * radK;
		let a = p.th + g.rot + L.wind * p.r * 1.6 + 0.04 * Math.sin(g.rot * 0.6 + p.r * 7);
		let bb = p.b * (1 + flare);
		if (p.tw) bb *= 0.75 + 0.25 * Math.sin(t * 0.004 * p.sp + p.ph);
		if (state === 'listen') {
			const wave = Math.sin(p.r * 22 - t * 0.012);
			bb *= 1 + 1.6 * amp * Math.max(0, wave) ** 4;
			r *= 1 + 0.03 * amp * wave;
		} else if (state === 'speak') {
			r *= 1 + 0.03 * amp;
			bb *= 1 + amp * 0.9 * Math.max(0, 1 - p.r * 2.5);
			if (p.arm) bb *= 1 + 0.9 * amp * Math.max(0, Math.sin(p.r * 9 - t * 0.007)) ** 3;
		} else if (state === 'music') {
			const wave = Math.max(0, Math.sin(p.r * 13 - t * 0.009));
			bb *= 1 + 1.3 * amp * wave;
			r *= 1 + 0.025 * amp * wave;
		} else if (state === 'embed') {
			const life = p.life;
			r *= 0.08 + 0.92 * life;
			a += (1 - life) * 2.4;
			bb *= 0.6 + 0.7 * (1 - life);
		} else if (state === 'alert') bb *= 0.85 + 0.15 * Math.sin(t * 0.03 + p.th * 5);
		D(r, a, p.z);
		const near = (qz + 1) / 2;
		const al = Math.min(1, bb * (0.55 + 0.6 * near));
		const c = tinted ? mixInto(col, p.c, g.tint, L.tintAmount) : p.c;
		const z = p.sz * sc * (0.8 + 0.5 * near);
		put(list, SQUARE, qx, qy, qx, qy, z / 2, c, al);
		if (p.hot && !mini) {
			// The hot stars' diffraction cross.
			put(list, LINE, qx - z * 2.2, qy, qx + z * 2.2, qy, 0.7 * sc, c, 0.35 * al);
			put(list, LINE, qx, qy - z * 2.2, qx, qy + z * 2.2, 0.7 * sc, c, 0.35 * al);
		}
	}
	// Bright star-forming knots along the arms.
	for (const k of g.knots) {
		D(k.r * radK, k.th + g.rot + L.wind * k.r * 1.6);
		const f = 0.7 + 0.3 * Math.sin(t * 0.002 + k.ph);
		put(list, BLOB, qx, qy, qx, qy, 7 * sc * f, k.c, 0.55 * f);
		put(list, SQUARE, qx, qy, qx, qy, sc, PAL.spark, 0.8 * f);
	}
	// Listening: rings of light roll outward; alert: rose rings close inward.
	if (state === 'listen' || state === 'alert') {
		const alert = state === 'alert';
		for (let i = 0; i < 3; i++) {
			let f = (t * 0.00045 + i / 3) % 1;
			if (alert) f = 1 - f;
			const alpha = (alert ? 0.5 : 0.35) * Math.sin(f * Math.PI);
			ring(0.12 + f, 72, 1.2, alert ? PAL.alertRing : PAL.listenRing, () => alpha);
		}
	}
	// Polar jets while thinking.
	if (L.jets > 0.02) {
		for (const sgn of [1, -1]) {
			P(0, 0, 0.04 * sgn);
			const x0 = qx;
			const y0 = qy;
			P(0, 0, (mini ? 0.7 : 0.95) * sgn);
			put(list, LINE, x0, y0, qx, qy, 3 * sc, PAL.jet, 0.6 * L.jets, 0);
		}
		for (const j of g.jets) {
			const spread = 0.015 + j.u * 0.07;
			P(j.jx * spread, j.jy * spread, j.sgn * (0.05 + j.u * (mini ? 0.65 : 0.9)));
			put(list, SQUARE, qx, qy, qx, qy, ((1.6 - j.u) * sc) / 2, PAL.jetSpark, (1 - j.u) * L.jets);
		}
	}
	// Aura rings in the disc plane, brighter on the near side, and the sweep round the outer one.
	const [outer, inner] = [...PACK.params.auraRings].sort((a, b) => b - a);
	ring(outer, 140, 1, PAL.auraRing, (z) => 0.35 * (0.35 + 0.65 * (z + 1) / 2));
	if (!mini) ring(inner, 120, 1, PAL.auraRing, (z) => 0.28 * (0.35 + 0.65 * (z + 1) / 2), true);
	{
		const sw = t * 0.0009 * (1 + L.spin);
		D(outer, sw);
		let px = qx;
		let py = qy;
		for (let i = 1; i <= 18; i++) {
			D(outer, sw + i * 0.035);
			put(list, LINE, px, py, qx, qy, 2.4 * sc, PAL.sweep, i / 18);
			px = qx;
			py = qy;
		}
	}
	// Comets on three orbits while working.
	if (L.comets > 0.02) {
		const orbits = [
			[0.62, 1.6],
			[0.85, 1.2],
			[1.02, 0.9]
		];
		orbits.forEach(([radius, speed], i) => {
			if (mini && i === 2) return;
			const ph = t * 0.001 * speed * (1 + L.spin * 0.5) + i * 2.1;
			const c = i === 1 ? PAL.cometWarm : PAL.comet;
			const at = (j: number) => {
				const an = ph - j * 0.035;
				P(Math.cos(an) * radius, Math.sin(an) * radius, 0.08 * Math.sin(an * 2 + i));
			};
			at(0);
			const hx = qx;
			const hy = qy;
			let px = qx;
			let py = qy;
			for (let j = 1; j <= 18; j++) {
				at(j);
				put(list, LINE, px, py, qx, qy, 2.2 * (1 - j / 22) * sc, c, (1 - j / 18) * 0.9 * L.comets);
				px = qx;
				py = qy;
			}
			put(list, BLOB, hx, hy, hx, hy, 9 * sc, PAL.spark, L.comets);
		});
	}
	// The core: bulge, accretion rings, diffraction spikes, and the shock ring after a change.
	P(0, 0, 0);
	const ox = qx;
	const oy = qy;
	const r0 = S * 0.16 * (1 + 0.15 * amp + (since < MS.kick ? 0.3 * (1 - since / MS.kick) : 0));
	const gl = L.glow * (1 + flare * 0.6) * (state === 'alert' ? 0.8 + 0.2 * Math.sin(t * 0.03) : 1);
	put(list, BLOB, ox, oy, ox, oy, r0 * 3.2, PAL.glow, 0.5 * gl);
	put(list, BLOB, ox, oy, ox, oy, r0, PAL.bulge, gl);
	for (let i = 0; i < 2; i++) {
		const R = 0.055 + i * 0.03;
		D(R, 0);
		let px = qx;
		let py = qy;
		for (let j = 1; j <= 64; j++) {
			const an = (j / 64) * 6.283;
			D(R, an);
			const dop = 0.5 + 0.5 * Math.cos(an - g.rot * 6 - 1);
			const c = mixInto(col, PAL.accretion, PAL.accretionHot, dop);
			put(list, LINE, px, py, qx, qy, (i ? 1 : 1.8) * sc, c, L.accretion * (0.25 + 0.6 * dop) * (i ? 0.55 : 1));
			px = qx;
			py = qy;
		}
	}
	for (const an of [pa, pa + Math.PI / 2]) {
		const len = r0 * (3.4 + amp * 1.2);
		const dx = Math.cos(an) * len;
		const dy = Math.sin(an) * len;
		put(list, LINE, ox, oy, ox + dx, oy + dy, 1.4 * sc, PAL.spike, 0.75 * gl, 0);
		put(list, LINE, ox, oy, ox - dx, oy - dy, 1.4 * sc, PAL.spike, 0.75 * gl, 0);
	}
	if (since < MS.shock) {
		const f = since / MS.shock;
		ring(0.15 + f, 64, 2.2 - 1.6 * f, PAL.shock, () => 0.7 * (1 - f));
	}
}

function mixInto(out: Rgb, a: Rgb, b: Rgb, f: number): Rgb {
	out[0] = a[0] + (b[0] - a[0]) * f;
	out[1] = a[1] + (b[1] - a[1]) * f;
	out[2] = a[2] + (b[2] - a[2]) * f;
	return out;
}
