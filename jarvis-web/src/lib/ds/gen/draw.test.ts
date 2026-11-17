// The layout arithmetic the projections draw with: the parts that go wrong at
// an edge (one point, a flat line, a scrolled list, a camera behind the scene).
import { describe, expect, it } from 'vitest';

import { clock, diffLines, fmt, initials, lineChart, mapFit, sceneLayout, windowOf } from './draw';

describe('draw', () => {
	it('draws only the rows in view and a few either side', () => {
		expect(windowOf(0, 352, 44, 2000)).toEqual({ start: 0, end: 12 });
		expect(windowOf(44 * 100, 352, 44, 2000)).toEqual({ start: 96, end: 112 });
		expect(windowOf(44 * 2000, 352, 44, 2000)).toEqual({ start: 1996, end: 2000 });
		expect(windowOf(0, 352, 44, 3)).toEqual({ start: 0, end: 3 });
		expect(windowOf(-50, 352, 0, 5)).toEqual({ start: 0, end: 5 });
	});

	it('writes numbers as they are read', () => {
		expect(fmt(0.1 + 0.2)).toBe('0.3');
		expect(fmt(14)).toBe('14');
		expect(clock(95)).toBe('1:35');
		expect(clock(3725)).toBe('1:02:05');
		expect(clock(Number.NaN)).toBe('0:00');
	});

	it('keeps a flat line in the middle and stands an area on zero', () => {
		const flat = lineChart([{ points: [3, 3, 3] }]);
		expect(flat.paths[0].line).toBe('M0.00,50.00L50.00,50.00L100.00,50.00');
		const area = lineChart([{ points: [2, 4] }], true);
		expect(area.lo).toBe(0);
		expect(area.paths[0].fill.endsWith('L100.00,100.00L0.00,100.00Z')).toBe(true);
		expect(lineChart([{ points: [] }]).paths[0].line).toBe('');
	});

	it('puts one place in the middle of the map and a route inside it', () => {
		expect(mapFit([[59.33, 18.06]], 5 / 3)([59.33, 18.06])).toEqual([50, 50]);
		const at = mapFit(
			[
				[59.33, 18.06],
				[59.34, 18.09]
			],
			5 / 3
		);
		for (const p of [at([59.33, 18.06]), at([59.34, 18.09])]) {
			expect(p[0]).toBeGreaterThan(0);
			expect(p[0]).toBeLessThan(100);
			expect(p[1]).toBeGreaterThan(0);
			expect(p[1]).toBeLessThan(100);
		}
		// North is up: the northern point is higher on the page.
		expect(at([59.34, 18.09])[1]).toBeLessThan(at([59.33, 18.06])[1]);
	});

	it('fits a scene seen through a camera inside its box, far shapes first', () => {
		const shapes = [
			{ kind: 'line' as const, points: [[0, 0, 5], [0, 1, 5]] },
			{ kind: 'line' as const, points: [[0, 0, -5], [1, 0, -5]] },
			{ kind: 'path' as const, d: 'M0 0L10 10' }
		];
		const laid = sceneLayout(200, 100, shapes, { yaw: 0, pitch: 0, distance: 6 });
		expect(laid[0].kind).toBe('path');
		expect(laid[1].points?.[0][2]).toBe(-5);
		for (const s of laid) {
			for (const [x, y] of s.at) {
				expect(x).toBeGreaterThanOrEqual(0);
				expect(x).toBeLessThanOrEqual(200);
				expect(y).toBeGreaterThanOrEqual(0);
				expect(y).toBeLessThanOrEqual(100);
				expect(Number.isFinite(x) && Number.isFinite(y)).toBe(true);
			}
		}
		// Without a camera the points are the box's own.
		expect(sceneLayout(10, 10, [{ kind: 'circle', points: [[4, 5]], r: 2 }])[0]).toMatchObject({
			at: [[4, 5]],
			radius: 2
		});
	});

	it('reads a diff line by line', () => {
		expect(diffLines('--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n z\n').map((l) => l.kind)).toEqual([
			'file', 'file', 'hunk', 'del', 'add', 'same'
		]);
		expect(initials(' ana  lind karlsson')).toBe('AL');
		expect(initials('😀x')).toBe('😀');
	});
});
