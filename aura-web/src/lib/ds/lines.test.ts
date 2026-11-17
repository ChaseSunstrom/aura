import { describe, expect, it } from 'vitest';

import { cleanPoints, connectorCurve, edgePoint, onStage, relativeTo } from './lines';
import { toneColor } from './tone';

describe('stage lines', () => {
	it('drops points that cannot be drawn and clamps the rest into the box', () => {
		expect(cleanPoints([[10, 20], [Number.NaN, 4], [120, -5], [3, Infinity]])).toEqual([
			[10, 20],
			[100, 0]
		]);
	});

	it('a connector starts and ends on its points and passes through the middle', () => {
		const c = connectorCurve([10, 50], [90, 20]);
		expect(c.at(0)).toEqual([10, 50]);
		expect(c.at(1)).toEqual([90, 20]);
		const [x, y] = c.at(0.5);
		expect(x).toBeCloseTo(50, 9);
		expect(y).toBeCloseTo(35, 9);
		expect(c.d.startsWith('M10 50 C50 50 50 20 90 20')).toBe(true);
	});

	it('bends along the longer axis', () => {
		expect(connectorCurve([50, 10], [60, 90]).d).toBe('M50 10 C50 50 60 50 60 90');
	});
});

describe('line anchors', () => {
	it('puts a client rect in the stage own coordinates', () => {
		const rect = { left: 140, top: 260, width: 300, height: 80 };
		expect(relativeTo(rect, { left: 40, top: 60 })).toEqual({ left: 100, top: 200, width: 300, height: 80 });
	});

	it('lands on the facing edge midpoint', () => {
		const b = { left: 100, top: 200, width: 300, height: 80 };
		expect(edgePoint(b, 'right')).toEqual([400, 240]);
		expect(edgePoint(b, 'left')).toEqual([100, 240]);
		expect(edgePoint(b, 'top')).toEqual([250, 200]);
	});

	it('hides a target outside the stage, or whose line would end outside it', () => {
		expect(onStage({ left: 1000, top: 0, width: 50, height: 50 }, 1000, 600, 'left')).toBe(false);
		expect(onStage({ left: -60, top: 0, width: 50, height: 50 }, 1000, 600, 'right')).toBe(false);
		expect(onStage({ left: 0, top: 0, width: 0, height: 50 }, 1000, 600, 'left')).toBe(false);
		// Partly in, but the point the line ends on is below the stage.
		expect(onStage({ left: 0, top: 590, width: 50, height: 50 }, 1000, 600, 'left')).toBe(false);
		expect(onStage({ left: 0, top: 560, width: 50, height: 50 }, 1000, 600, 'left')).toBe(true);
		// Facing the other way, the same box's edge is off to the left.
		expect(onStage({ left: -40, top: 100, width: 100, height: 50 }, 1000, 600, 'left')).toBe(false);
		expect(onStage({ left: -40, top: 100, width: 100, height: 50 }, 1000, 600, 'right')).toBe(true);
	});
});

describe('tones', () => {
	it('names a token for every tone and falls back to text for anything else', () => {
		expect(toneColor('violet')).toBe('var(--jv-violet)');
		expect(toneColor('danger')).toBe('var(--jv-danger-text)');
		expect(toneColor('constructor')).toBe('var(--jv-text)');
		expect(toneColor(undefined)).toBe('var(--jv-text)');
	});
});
