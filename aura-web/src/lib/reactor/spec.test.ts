import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import * as spec from './spec';

/**
 * The console's half of the reactor contract.
 *
 * `tests/contracts/reactor.json` is the one geometry the four renderers share
 * (this, the Canvas fallback, the phone's AGSL and Canvas, and the icon
 * generator). This file reads it and refuses `spec.ts` drifting from it, which
 * is the whole reason the contract exists: the previous instrument was retuned
 * on the web and not on the phone, and nothing anywhere said the two were
 * supposed to agree.
 */

const CONTRACT = JSON.parse(
	readFileSync(new URL('../../../../tests/contracts/reactor.json', import.meta.url), 'utf8')
) as {
	geometry: Record<string, never> & {
		margin: number;
		rings: { name: string; r: number; width: number }[];
		core_r: number;
		chromatic_offset_px: number;
		canvas_offset_px: number;
		segment_gap_deg: number;
		max_segments: number;
		satellite_r: number;
		attention_gap_deg: number;
		think_arc_deg: number;
		alert_bar: { length: number; thickness: number };
		speak_swell: number;
		ignite_ms: string;
	};
	boot: Record<string, number>;
	states: Record<string, { color: string; word: string; period?: string; luminance?: [number, number] }>;
	precedence: string[];
	icons: { still_luminance: number };
};

describe('the geometry is the contract’s', () => {
	const g = CONTRACT.geometry;

	it('keeps the margin, the core and the three rings', () => {
		expect(spec.MARGIN).toBe(g.margin);
		expect(spec.CORE_R).toBe(g.core_r);
		expect(spec.RINGS.map((r) => ({ name: r.name, r: r.r, width: r.width }))).toEqual(g.rings);
	});

	it('keeps every figure the states are drawn with', () => {
		expect(spec.CHROMATIC_OFFSET_PX).toBe(g.chromatic_offset_px);
		expect(spec.CANVAS_OFFSET_PX).toBe(g.canvas_offset_px);
		expect(spec.SEGMENT_GAP_DEG).toBe(g.segment_gap_deg);
		expect(spec.MAX_SEGMENTS).toBe(g.max_segments);
		expect(spec.SATELLITE_R).toBe(g.satellite_r);
		expect(spec.ATTENTION_GAP_DEG).toBe(g.attention_gap_deg);
		expect(spec.THINK_ARC_DEG).toBe(g.think_arc_deg);
		expect(spec.ALERT_BAR_LENGTH).toBe(g.alert_bar.length);
		expect(spec.ALERT_BAR_THICKNESS).toBe(g.alert_bar.thickness);
		expect(spec.SPEAK_SWELL).toBe(g.speak_swell);
	});

	it('keeps the ignition’s timings', () => {
		expect(spec.BOOT.coreFrom).toBe(CONTRACT.boot.core_from);
		expect(spec.BOOT.coreTo).toBe(CONTRACT.boot.core_to);
		expect(spec.BOOT.ringsFrom).toBe(CONTRACT.boot.rings_from);
		expect(spec.BOOT.ringStagger).toBe(CONTRACT.boot.ring_stagger);
		expect(spec.BOOT.ringEach).toBe(CONTRACT.boot.ring_each);
		expect(spec.BOOT.overshoot).toBe(CONTRACT.boot.overshoot);
	});

	it('is an instrument: the rings nest, and nothing reaches past the outer edge', () => {
		const [outer, middle, inner] = spec.RINGS;
		expect(inner.r + inner.width / 2).toBeLessThan(middle.r - middle.width / 2);
		expect(middle.r + middle.width / 2).toBeLessThan(outer.r - outer.width / 2);
		// The core sits inside the inner ring, with air between them.
		expect(spec.CORE_R).toBeLessThan(inner.r - inner.width / 2);
		// R is half the element minus the margin, so the outer ring's outer edge
		// has to stay inside 1 or the glow would be clipped by the element.
		expect(outer.r + outer.width / 2).toBeLessThanOrEqual(1);
	});

	it('splits into segments a person can count', () => {
		// Twelve segments with an 8° gap each still leaves 22° of lit arc.
		const step = 360 / spec.MAX_SEGMENTS;
		expect(step - spec.SEGMENT_GAP_DEG).toBeGreaterThan(15);
	});
});

describe('the eleven states', () => {
	it('are the contract’s, with the contract’s words', () => {
		expect(Object.keys(CONTRACT.states).sort()).toEqual([...spec.STATES].sort());
		for (const [name, row] of Object.entries(CONTRACT.states)) {
			expect(spec.WORDS[name as spec.ReactorStateName]).toBe(row.word);
		}
	});

	it('resolve in the contract’s precedence, and goal never wins', () => {
		expect([...spec.PRECEDENCE]).toEqual(CONTRACT.precedence);
		expect(spec.PRECEDENCE).not.toContain('goal');
		// Every state but the overlay has a rank, so nothing can tie.
		expect(new Set(spec.PRECEDENCE).size).toBe(spec.STATES.length - 1);
	});

	it('take C from the generated token for that state, never a typed hue', () => {
		for (const name of spec.STATES) {
			const colour = spec.colorOf(name);
			expect(colour, name).toMatch(/^#[0-9a-f]{6}$/);
			// The contract names the token; the token table holds the value.
			expect(CONTRACT.states[name].color).toBe(`color.reactor.${name}`);
		}
	});

	it('stop moving only in kill and offline', () => {
		const still = spec.STATES.filter(spec.isStill);
		expect(still).toEqual(['kill', 'offline']);
	});

	it('read their halo from the light tokens, with a peak for the two that move', () => {
		for (const name of spec.STATES) {
			expect(Number.isFinite(spec.haloOf(name)), name).toBe(true);
			expect(spec.haloPeakOf(name)).toBeGreaterThanOrEqual(spec.haloOf(name));
		}
		// Speaking and warming are the only two whose halo travels (§10 rows 4, 8),
		// and `haloMoves` is what the reactor asks rather than carrying its own
		// copy of that pair — so the two have to agree by construction.
		const moving = spec.STATES.filter((s) => spec.haloPeakOf(s) > spec.haloOf(s));
		expect(moving.sort()).toEqual(['speaking', 'warming']);
		expect(spec.STATES.filter(spec.haloMoves).sort()).toEqual(moving.sort());
		// Every other row rests on one token, and publishing a moving value for
		// one of them would leave the room bright after the reply had finished.
		for (const state of spec.STATES) {
			if (state === 'speaking' || state === 'warming') continue;
			expect(spec.haloMoves(state), state).toBe(false);
		}
		// The two dark states light nothing.
		expect(spec.haloOf('kill')).toBe(0);
		expect(spec.haloOf('offline')).toBe(0);
	});
});

describe('the clock', () => {
	it('takes every period from a motion token', () => {
		// A period the token table does not carry parses as NaN, which is the
		// failure this catches: a hand-typed 5000 would pass a smoke test.
		for (const [name, ms] of Object.entries(spec.PERIOD)) {
			expect(Number.isFinite(ms), name).toBe(true);
			expect(ms, name).toBeGreaterThan(0);
		}
		// The ignition is `motion.dur.enter` (900 ms), as §8 says.
		expect(spec.IGNITE_MS).toBe(900);
		expect(CONTRACT.geometry.ignite_ms).toBe('motion.dur.enter');
	});

	it('eases on the standard curve, parsed from the token', () => {
		expect(spec.EASE_STANDARD).toEqual([0.2, 0.8, 0.2, 1]);
		expect(spec.ease(0)).toBe(0);
		expect(spec.ease(1)).toBe(1);
		// Monotonic, and decelerating: past halfway it is more than half done.
		let previous = 0;
		for (let t = 0; t <= 1.0001; t += 0.05) {
			const v = spec.ease(t);
			expect(v).toBeGreaterThanOrEqual(previous - 1e-9);
			previous = v;
		}
		expect(spec.ease(0.5)).toBeGreaterThan(0.5);
	});

	it('breathes between the turnarounds without overshooting', () => {
		for (let t = 0; t < 10; t += 0.13) {
			const b = spec.breath(t, spec.PERIOD.breathe);
			expect(b).toBeGreaterThanOrEqual(-1e-9);
			expect(b).toBeLessThanOrEqual(1 + 1e-9);
		}
		expect(spec.breath(0, 1000)).toBeCloseTo(0, 6);
		expect(spec.breath(0.5, 1000)).toBeCloseTo(1, 6);
	});
});

describe('the icons', () => {
	it('are rendered at the still luminance the contract names', () => {
		expect(spec.STILL_LUMINANCE).toBe(CONTRACT.icons.still_luminance);
		// It is the middle of the idle breath, which is what makes it the resting
		// picture rather than an arbitrary bright frame.
		const [lo, hi] = CONTRACT.states.idle.luminance!;
		expect(spec.STILL_LUMINANCE).toBe((lo + hi) / 2);
	});
});

describe('the instrument rules both renderers share', () => {
	// These moved out of Reactor.svelte when the orb became a second renderer of
	// the same instrument (ADR-0038). The reactor's own tests prove the SVG did
	// not change; these pin the rules themselves so the orb inherits them.

	it('wears goal when a plan runs under an idle instrument', () => {
		expect(spec.wornState('idle', true)).toBe('goal');
		expect(spec.wornState('idle', false)).toBe('idle');
		// A real state outranks the goal badge: a tool call is more urgent news.
		expect(spec.wornState('tool', true)).toBe('tool');
		expect(spec.wornState('alert', true)).toBe('alert');
	});

	it('only calls it a plan when there are steps and a reason to show them', () => {
		expect(spec.isPlan(4, 'tool', false)).toBe(true);
		expect(spec.isPlan(4, 'goal', false)).toBe(true);
		expect(spec.isPlan(0, 'tool', false)).toBe(false);
		expect(spec.isPlan(4, 'idle', false)).toBe(false);
	});

	describe('the level arc', () => {
		const base = { plan: false, litSegments: 0, segments: 0, level: 0.5 };

		it('gives a cold start precedence over everything', () => {
			// Until the model is up the plan is not moving, so warming wins.
			expect(
				spec.arcValue({ ...base, worn: 'warming', warmingProgress: 0.3, plan: true, segments: 4, litSegments: 2 })
			).toBeCloseTo(0.3, 5);
		});

		it('gives a plan precedence over the voice level', () => {
			expect(spec.arcValue({ ...base, worn: 'tool', plan: true, segments: 4, litSegments: 1 })).toBeCloseTo(0.25, 5);
		});

		it('falls back to the voice level', () => {
			expect(spec.arcValue({ ...base, worn: 'listening' })).toBeCloseTo(0.5, 5);
		});

		it('clamps, and never returns NaN from a zero-step plan', () => {
			expect(spec.arcValue({ ...base, worn: 'idle', level: 5 })).toBe(1);
			expect(spec.arcValue({ ...base, worn: 'idle', level: -2 })).toBe(0);
			expect(spec.arcValue({ ...base, worn: 'tool', plan: true, segments: 0, litSegments: 0 })).toBe(0);
			expect(spec.arcValue({ ...base, worn: 'warming', warmingProgress: null })).toBe(0);
		});
	});

	describe('blade tones', () => {
		it('dims every third blade off-plan, which is what gives the ring its rhythm', () => {
			const off = { plan: false, segments: 0, litSegments: 0 };
			expect([0, 1, 2, 3].map((i) => spec.bladeTone(i, 36, off))).toEqual(['idle', 'idle', 'soft', 'idle']);
		});

		it('walks done → running → pending across the plan', () => {
			const tones = Array.from({ length: 36 }, (_, i) =>
				spec.bladeTone(i, 36, { plan: true, segments: 4, litSegments: 1 })
			);
			expect(new Set(tones.slice(0, 9))).toEqual(new Set(['done']));
			expect(new Set(tones.slice(9, 18))).toEqual(new Set(['running']));
			expect(new Set(tones.slice(18))).toEqual(new Set(['pending']));
		});

		it('has nothing running once every step is done', () => {
			const tones = Array.from({ length: 36 }, (_, i) =>
				spec.bladeTone(i, 36, { plan: true, segments: 4, litSegments: 4 })
			);
			expect(tones.every((t) => t === 'done')).toBe(true);
		});

		it('does not over-light when litSegments exceeds segments', () => {
			const tones = Array.from({ length: 36 }, (_, i) =>
				spec.bladeTone(i, 36, { plan: true, segments: 3, litSegments: 99 })
			);
			expect(tones.every((t) => t === 'done')).toBe(true);
		});
	});

	describe('the ignition', () => {
		it('is fully assembled when nothing is booting', () => {
			expect(spec.IGNITION_LAYERS.map((_, i) => spec.ignitionLayer(null, i))).toEqual([1, 1, 1, 1, 1]);
		});

		it('starts with nothing and ends with everything', () => {
			expect(spec.IGNITION_LAYERS.map((_, i) => spec.ignitionLayer(0, i))).toEqual([0, 0, 0, 0, 0]);
			expect(spec.IGNITION_LAYERS.map((_, i) => spec.ignitionLayer(1, i))).toEqual([1, 1, 1, 1, 1]);
		});

		it('assembles in order, each layer behind the last', () => {
			const mid = spec.IGNITION_LAYERS.map((_, i) => spec.ignitionLayer(0.5, i));
			for (let i = 1; i < mid.length; i++) expect(mid[i]).toBeLessThanOrEqual(mid[i - 1]);
			// The last layer completes exactly at the end, not before.
			expect(spec.ignitionLayer(0.99, spec.IGNITION_LAYERS.length - 1)).toBeLessThan(1);
		});
	});
});
