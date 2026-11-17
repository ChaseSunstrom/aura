import { describe, expect, it } from 'vitest';
import { DEFAULT_PREFS, LIMIT_STALE_M, SpeedWindow, WARN_EVERY_MS, bandOf, limitHere, reading, roadLimit, warning } from './speed';

describe('speeding', () => {
	it('names each band and fills the bar to twenty over, and past it', () => {
		expect(bandOf(0)).toBe('safe');
		expect(bandOf(4)).toBe('speedy');
		expect(bandOf(9)).toBe('risky');
		expect(bandOf(15)).toBe('dangerous');
		expect(bandOf(22)).toBe('deadly');
		const r = reading(95, 70);
		expect(r.over).toBe(25);
		expect(r.fill).toBeCloseTo(1.25);
		expect(reading(40, null).band).toBe('safe');
		// The bar's full point is a setting, and the bands scale with it.
		expect(reading(80, 70, 10)).toMatchObject({ fill: 1, band: 'deadly', full: 10 });
		expect(bandOf(15, 40)).toBe('risky');
	});

	it('judges the average of the last seconds, not one fix', () => {
		const w = new SpeedWindow();
		w.add(0, 30);
		w.add(1000, 30);
		w.add(2000, 40); // one quick moment
		expect(w.mph).toBeLessThan(40 * 2.237);
		w.add(20_000, 10);
		expect(Math.round(w.mph)).toBe(22); // the old fixes fell out of the window
		const long = new SpeedWindow();
		long.add(0, 30, 30_000);
		long.add(20_000, 10, 30_000);
		expect(Math.round(long.mph)).toBe(45); // a thirty-second window keeps both
	});

	it('says nothing under fifteen over, then not again inside the interval unless worse', () => {
		const at = (over: number) => reading(55 + over, 55);
		expect(warning(at(12), 'jarvis', 0, null)).toBeNull(); // risky, but under fifteen
		expect(warning(at(15), 'jarvis', 0, null, DEFAULT_PREFS, () => 0)).toEqual({ text: 'Sir, you are well over the limit.', band: 'dangerous' });
		expect(warning(at(16), 'jarvis', 10_000, { at: 0, band: 'dangerous' })).toBeNull();
		expect(warning(at(22), 'jarvis', 10_000, { at: 0, band: 'dangerous' }, DEFAULT_PREFS, () => 0)?.text).toBe('Sir, this is dangerous. Please slow down.');
		expect(warning(at(16), 'jarvis', WARN_EVERY_MS + 1, { at: 0, band: 'dangerous' })).not.toBeNull();
		// A persona with no lines of her own (AURA) says JARVIS's.
		expect(warning(at(22), 'aura', 0, null, DEFAULT_PREFS, () => 0)?.text).toBe('Sir, this is dangerous. Please slow down.');
		// A threshold set into a mild band says the risky words.
		expect(warning(at(5), 'jarvis', 0, null, { warnOver: 5, warnEveryMs: 1 }, () => 0)).toEqual({ text: 'You should slow down, sir.', band: 'risky' });
		expect(warning(reading(90, null), 'jarvis', 0, null, { warnOver: 0, warnEveryMs: 1 })).toBeNull();
	});
});

// T-0755: the limit was Overpass's, asked round one point from the console, which this host
// cannot reach, and a failed ask left the last one standing for the rest of the drive.
describe('the road limit, from the house', () => {
	it("reads the house's map-match answer as the limit where the car asked", () => {
		const body = { lat: 40.0001, lon: -90, street: 'Main St', limit_mph: 35, limit_source: 'posted', road: 'secondary' };
		expect(roadLimit(body, 40, -90)).toEqual({ lat: 40, lon: -90, limit_mph: 35, source: 'posted' });
		expect(roadLimit({ limit_mph: null, limit_source: 'none' }, 40, -90)).toEqual({ lat: 40, lon: -90, limit_mph: null, source: 'none' });
		// A house too old to say a limit, or no answer: nothing to show, not a zero.
		expect(roadLimit({ lat: 40, lon: -90, street: 'Main St' }, 40, -90)).toBeNull();
		expect(roadLimit(null, 40, -90)).toBeNull();
	});

	it('drops a limit once the car is past where it was asked', () => {
		const asked = roadLimit({ limit_mph: 70, limit_source: 'posted' }, 40, -90)!;
		expect(LIMIT_STALE_M).toBeGreaterThan(120); // the car asks every 120 m
		expect(limitHere(asked, 40.001, -90)).toBe(asked); // about 111 m on
		expect(limitHere(asked, 40.003, -90)).toBeNull(); // about 333 m on: another road's
		expect(limitHere(null, 40, -90)).toBeNull();
	});
});
