// The particle sphere round the reactor (BUILD_BRIEF §1 "Core visual: the
// JARVIS orb", part 1; the Build Brief's Main board `startSphere`): about 1,400
// Fibonacci-distributed points, rotating slowly, depth-shaded, additive, with a
// slow ripple and a few amber "data" points. Pure: the points and what each
// reactor state does to them, so the behaviour table is tested without a GPU.
// `ParticleSphere.svelte` draws it with WebGL.

/** The board's density. */
export const POINTS = 1400;
/** One point in this many is an amber data point (the board's ~3 %). */
export const DATA_EVERY = 33;
/** The board's tilt of the spin axis toward the viewer, radians. */
export const TILT = 0.38;

/** x, y, z on the unit sphere and 1 for a data point, 0 otherwise: four floats a point. */
export function fibonacci(n = POINTS, dataEvery = DATA_EVERY): Float32Array {
	const out = new Float32Array(n * 4);
	const golden = Math.PI * (3 - Math.sqrt(5));
	for (let i = 0; i < n; i++) {
		const y = n === 1 ? 0 : 1 - (i / (n - 1)) * 2;
		const r = Math.sqrt(Math.max(0, 1 - y * y));
		const th = golden * i;
		out.set([Math.cos(th) * r, y, Math.sin(th) * r, i % dataEvery === dataEvery - 1 ? 1 : 0], i * 4);
	}
	return out;
}

export type SphereHue = 'accent' | 'warn' | 'warming' | 'danger';

/** What a state does to the sphere (BUILD_BRIEF §1's state table). */
export interface SphereLook {
	/** Radians a second round the tilted axis. */
	spin: number;
	/** The ripple's depth, as a fraction of the radius. */
	ripple: number;
	/** A whole-sphere swell, fraction of the radius: the idle breath, the speech pulse. */
	swell: number;
	hue: SphereHue;
	/** 1 full colour, 0 grey: offline is desaturated. */
	saturation: number;
	/** 0–1, the points' brightness. */
	light: number;
}

/**
 * The state's look at time `t` (seconds) with the live `level` (0–1): the mic's
 * while listening, the speech envelope while speaking.
 *
 * Does not cover tokens/sec: nothing in the console measures a token rate, so
 * thinking spins at one faster speed rather than one tied to the rate.
 */
export function lookFor(state: string, level: number, t: number): SphereLook {
	const lv = Math.max(0, Math.min(1, level));
	const breath = 0.018 * Math.sin(t * ((2 * Math.PI) / 5));
	const base: SphereLook = { spin: 0.18, ripple: 0.045, swell: breath, hue: 'accent', saturation: 1, light: 0.85 };
	switch (state) {
		case 'listening':
			return { ...base, ripple: 0.045 + lv * 0.22, light: 0.95 };
		case 'thinking':
		case 'tool':
		case 'goal':
			return { ...base, spin: 0.62, light: 0.95 };
		case 'speaking':
			return { ...base, spin: 0.3, swell: lv * 0.09, light: 0.8 + lv * 0.2 };
		case 'attention':
			return { ...base, hue: 'warn' };
		case 'warming':
			return { ...base, spin: 0.1, hue: 'warming', light: 0.7 };
		case 'alert':
			return { ...base, hue: 'danger', light: 1 };
		case 'kill':
			return { ...base, spin: 0, ripple: 0, swell: 0, hue: 'danger', saturation: 0.25, light: 0.5 };
		case 'offline':
			return { ...base, spin: 0.05, ripple: 0.02, swell: 0, hue: 'warn', saturation: 0.15, light: 0.55 };
		default:
			return base;
	}
}

/** The error flash (BUILD_BRIEF §1: "brief pink flash, then a readable message"): 1 at the start, 0 by `ms`. */
export function flashAt(sinceMs: number, ms = 620): number {
	if (sinceMs < 0 || sinceMs >= ms) return 0;
	return 1 - sinceMs / ms;
}

/** `#rrggbb` → three 0–1 channels for a shader uniform. */
export function rgb01(hex: string): [number, number, number] {
	const h = hex.replace('#', '');
	return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number];
}
