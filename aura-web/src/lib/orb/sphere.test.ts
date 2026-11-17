import { describe, expect, it } from 'vitest';
import { DATA_EVERY, POINTS, fibonacci, flashAt, lookFor, rgb01 } from './sphere';

describe('the particle sphere', () => {
	it('has the brief\'s ~1,400 points, all on the unit sphere, about 3 % of them data', () => {
		const pts = fibonacci();
		expect(pts.length).toBe(POINTS * 4);
		let data = 0;
		for (let i = 0; i < POINTS; i++) {
			const [x, y, z, d] = pts.subarray(i * 4, i * 4 + 4);
			expect(Math.hypot(x, y, z)).toBeCloseTo(1, 5);
			data += d;
		}
		expect(data).toBe(Math.floor(POINTS / DATA_EVERY));
		expect(data / POINTS).toBeGreaterThan(0.02);
		expect(data / POINTS).toBeLessThan(0.04);
	});

	it('spreads the points evenly: each hemisphere holds half', () => {
		const pts = fibonacci();
		let north = 0;
		for (let i = 0; i < POINTS; i++) if (pts[i * 4 + 1] > 0) north++;
		expect(Math.abs(north - POINTS / 2)).toBeLessThanOrEqual(1);
	});

	it('follows the brief\'s state table', () => {
		const idle = lookFor('idle', 0, 0);
		expect(idle.hue).toBe('accent');
		// listening: the ripple is the mic
		expect(lookFor('listening', 1, 0).ripple).toBeGreaterThan(lookFor('listening', 0, 0).ripple + 0.1);
		// thinking: the rings run faster
		expect(lookFor('thinking', 0, 0).spin).toBeGreaterThan(idle.spin * 2);
		// speaking: the sphere pulses with the envelope
		expect(lookFor('speaking', 1, 0).swell).toBeGreaterThan(lookFor('speaking', 0, 0).swell);
		// offline: desaturated, amber, never pretending to be idle
		const off = lookFor('offline', 0, 0);
		expect(off.hue).toBe('warn');
		expect(off.saturation).toBeLessThan(0.5);
		expect(lookFor('alert', 0, 0).hue).toBe('danger');
	});

	it('breathes at idle and stops breathing when killed', () => {
		const a = lookFor('idle', 0, 0).swell;
		const b = lookFor('idle', 0, 1.25).swell;
		expect(a).not.toBe(b);
		expect(lookFor('kill', 0, 1.25).swell).toBe(0);
	});

	it('flashes once and lets go', () => {
		expect(flashAt(0)).toBe(1);
		expect(flashAt(310)).toBeCloseTo(0.5, 5);
		expect(flashAt(700)).toBe(0);
		expect(flashAt(-1)).toBe(0);
	});

	it('reads a token colour into shader channels', () => {
		expect(rgb01('#3fd0e8')).toEqual([63 / 255, 208 / 255, 232 / 255]);
	});
});
