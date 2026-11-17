import { afterEach, describe, expect, it, vi } from 'vitest';

import { SLOTS, projectionsFor } from './projections';
import { loadTiles, place, saveTiles, swap } from './tiling';

const sky = [{ id: 'moon', label: 'Moon', value: 98.14, unit: '%' }];
const jobs = [{ id: 'j', title: 'Water the garden', next: '07:00', state: 'idle' as const }];

/** A `localStorage` that is only a Map. */
function storage(seed: Record<string, string> = {}) {
	const m = new Map(Object.entries(seed));
	const s = {
		getItem: (k: string) => m.get(k) ?? null,
		setItem: (k: string, v: string) => void m.set(k, v),
		removeItem: (k: string) => void m.delete(k),
		key: (i: number) => [...m.keys()][i] ?? null,
		get length() {
			return m.size;
		}
	};
	vi.stubGlobal('localStorage', s);
	return m;
}

afterEach(() => vi.unstubAllGlobals());

describe('place', () => {
	it('with nothing kept, each panel takes the scene’s tiles in order: the board’s look is unchanged', () => {
		expect(place(['a', 'b', 'c'], 3)).toEqual([0, 1, 2]);
	});

	it('a kept tile is honoured, and the others take the first free tiles', () => {
		expect(place(['a', 'b', 'c'], 3, { b: 0 })).toEqual([1, 0, 2]);
		expect(place(['a', 'b'], 3, { a: 2 })).toEqual([2, 0]);
	});

	it('never puts two panels in one tile, whatever was kept', () => {
		const kept = { a: 1, b: 1, c: 7, d: -1, e: 0.5 } as Record<string, number>;
		for (const ids of [['a', 'b'], ['a', 'b', 'c'], ['c', 'd', 'e'], ['b', 'a', 'e']]) {
			const tiles = place(ids, 3, kept);
			expect(new Set(tiles).size).toBe(ids.length);
			expect(tiles.every((t) => t >= 0 && t < 3)).toBe(true);
		}
	});
});

describe('swap', () => {
	it('a panel dropped on another’s tile takes it, and the other takes the panel’s old one', () => {
		expect(swap({ a: 0, b: 1, c: 2 }, 'a', 2)).toEqual({ a: 2, b: 1, c: 0 });
	});

	it('a panel dropped on an empty tile moves there and nothing else moves', () => {
		expect(swap({ a: 0, b: 1 }, 'a', 2)).toEqual({ a: 2, b: 1 });
	});
});

describe('kept tiles', () => {
	it('are kept per scene on this device, and the old free-drag offsets are cleared', () => {
		const m = storage({ 'jv-drag:moon': '[10,20]', other: 'x' });
		expect(loadTiles()).toEqual({});
		expect(m.has('jv-drag:moon')).toBe(false);
		expect(m.get('other')).toBe('x');
		saveTiles({ idle: { moon: 1, j: 0 } });
		expect(loadTiles()).toEqual({ idle: { moon: 1, j: 0 } });
	});

	it('anything unreadable is nothing kept', () => {
		storage({ 'jv-tiles': '{not json' });
		expect(loadTiles()).toEqual({});
		storage({ 'jv-tiles': '[1,2]' });
		expect(loadTiles()).toEqual({});
		storage({ 'jv-tiles': '{"idle":3,"answer":{"a":1}}' });
		expect(loadTiles()).toEqual({ answer: { a: 1 } });
		vi.stubGlobal('localStorage', undefined);
		expect(loadTiles()).toEqual({});
		expect(() => saveTiles({ idle: {} })).not.toThrow();
	});
});

describe('projectionsFor places through the tiles', () => {
	it('a kept arrangement puts each panel on its tile, in tile order', () => {
		const shown = projectionsFor('idle', { sky, jobs }, { 'reading:moon': 1, 'job:j': 0 });
		expect(shown.map((s) => [s.id, s.tile, s.at, s.edge])).toEqual([
			['job:j', 0, SLOTS.idle[0], 'right'],
			['reading:moon', 1, SLOTS.idle[1], 'left']
		]);
	});

	it('one panel kept on the far tile stands there alone', () => {
		const [only] = projectionsFor('idle', { sky }, { 'reading:moon': 1 });
		expect(only.at).toEqual(SLOTS.idle[1]);
		expect(only.edge).toBe('left');
	});
});
