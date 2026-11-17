import { describe, expect, it } from 'vitest';
import { THEMES } from '$lib/themes';
import { MS } from '../motion';
import { FRAME } from './engine';
import {
	createDrawList,
	createGalaxy,
	galaxyAt,
	galaxyInto,
	GALAXY_STATE,
	LINE,
	LOOKS,
	requalify,
	setGalaxyState,
	STARS,
	stepGalaxy,
	STRIDE,
	type DrawList
} from './galaxy';
import { CORE_STATES } from './states';

const W = 640;
const H = 480;

function frame(state: Parameters<typeof galaxyAt>[0], at = 2000, quality: 'desktop' | 'mini' = 'desktop') {
	const list = createDrawList();
	galaxyInto(galaxyAt(state, 'idle', at, quality), list, W, H);
	return list;
}

function finite(list: DrawList): boolean {
	for (let i = 0; i < list.n * STRIDE; i++) if (!Number.isFinite(list.data[i])) return false;
	return true;
}

/** Lines that fade out to nothing: the four diffraction spikes, plus a beam per pole while jets run. */
function fading(list: DrawList): number {
	let n = 0;
	for (let i = 0; i < list.n; i++) {
		const o = i * STRIDE;
		if (list.data[o + 5] === LINE && list.data[o + 9] > 0 && list.data[o + 10] === 0) n++;
	}
	return n;
}

describe('the galaxy core', () => {
	it('builds the pack’s star budgets: 2100 desktop, 1200 phone, 360 docked', () => {
		expect(STARS).toEqual({ desktop: 2100, phone: 1200, mini: 360 });
		expect(createGalaxy('desktop').stars).toHaveLength(2100);
		expect(createGalaxy('phone').stars).toHaveLength(1200);
		const mini = createGalaxy('mini');
		expect(mini.stars).toHaveLength(360);
		expect(mini.dust).toHaveLength(0);
		expect(mini.knots).toHaveLength(0);
	});

	it('maps every console state onto one of the theme’s eight', () => {
		const eight = Object.keys(THEMES.aura.core.states).sort();
		expect(eight).toHaveLength(8);
		expect([...new Set(Object.values(GALAXY_STATE))].sort()).toEqual(eight);
		expect(Object.keys(GALAXY_STATE).sort()).toEqual([...CORE_STATES].sort());
	});

	it.each(CORE_STATES)('draws %s: a full frame of finite instances', (state) => {
		const list = frame(state);
		expect(list.n).toBeGreaterThan(2100);
		expect(finite(list)).toBe(true);
		const counted = list.batches.reduce((n, b) => n + b.count, 0);
		expect(counted).toBe(list.n);
	});

	it('keeps each state’s signature: jets thinking, none idle', () => {
		expect(fading(frame('idle'))).toBe(4);
		expect(fading(frame('thinking'))).toBe(6);
		// Work draws comets: 3 orbits of 18 tail segments; idle draws none.
		expect(frame('working').n - frame('idle').n).toBeGreaterThan(54);
	});

	it('darkens the dust lanes source-over and adds every other light', () => {
		const list = frame('idle');
		expect(list.batches.map((b) => b.over)).toEqual([false, true, false]);
		expect(frame('idle', 2000, 'mini').batches.every((b) => !b.over)).toBe(true);
	});

	it('tweens toward a new state at 0.035 per 60Hz frame', () => {
		const g = createGalaxy('mini', 'idle');
		const from = g.look.spin;
		const to = LOOKS.think.spin;
		setGalaxyState(g, 'thinking');
		stepGalaxy(g, FRAME);
		expect(g.look.spin).toBeCloseTo(from + (to - from) * THEMES.aura.core.tween, 6);
		// The same distance in two half frames: frame-rate independent.
		const h = createGalaxy('mini', 'idle');
		setGalaxyState(h, 'thinking');
		stepGalaxy(h, FRAME / 2);
		stepGalaxy(h, FRAME / 2);
		expect(h.look.spin).toBeCloseTo(g.look.spin, 6);
	});

	it('snaps under reduced motion and fires no flare', () => {
		const g = createGalaxy('mini', 'idle', true);
		expect(setGalaxyState(g, 'alert')).toBe(true);
		expect(g.look.tilt).toBe(LOOKS.alert.tilt);
		expect(g.kickAt).toBe(Number.NEGATIVE_INFINITY);
		expect(setGalaxyState(g, 'alert')).toBe(false);
		expect(g.changes).toBe(1);
	});

	it('beats only while working, on the motion token’s grid', () => {
		const g = createGalaxy('mini', 'idle');
		setGalaxyState(g, 'working');
		for (let i = 0; i < Math.ceil((MS.beat * 3) / FRAME) + 1; i++) stepGalaxy(g, FRAME);
		expect(g.beats).toBe(3);
		const a = createGalaxy('mini', 'idle');
		setGalaxyState(a, 'acting');
		for (let i = 0; i < Math.ceil((MS.beat * 3) / FRAME) + 1; i++) stepGalaxy(a, FRAME);
		expect(a.beats).toBe(0);
	});

	it('draws the same frozen frame every load', () => {
		const a = frame('speaking', 1234);
		const b = frame('speaking', 1234);
		expect(a.n).toBe(b.n);
		expect(Array.from(a.data.subarray(0, a.n * STRIDE))).toEqual(Array.from(b.data.subarray(0, b.n * STRIDE)));
	});

	it('survives garbage time and inputs', () => {
		const g = createGalaxy('mini', 'listening');
		stepGalaxy(g, Number.NaN, { level: Number.NaN });
		stepGalaxy(g, -5, { level: 7 });
		stepGalaxy(g, 1e9);
		const list = createDrawList(1);
		galaxyInto(g, list, 0, 0);
		galaxyInto(g, list, 300, 200);
		expect(finite(list)).toBe(true);
		expect(g.t).toBeLessThanOrEqual(100);
	});

	it('requalifies without losing the motion', () => {
		const g = galaxyAt('working', 'idle', 3000, 'desktop');
		const m = requalify(g, 'mini');
		expect(m.stars).toHaveLength(360);
		expect(m.t).toBe(g.t);
		expect(m.beats).toBe(g.beats);
		expect(m.mode).toBe('working');
	});
});
