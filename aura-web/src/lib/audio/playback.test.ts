import { describe, expect, it } from 'vitest';

import { samplesOf } from './playback';

describe('samplesOf', () => {
	it('cuts pieces at whole samples and carries the odd byte to the next', () => {
		const carry = { odd: null as Uint8Array | null };
		// Three samples' worth of bytes, split so the second piece starts mid-sample.
		const a = new Uint8Array([1, 0, 2, 0, 3]);
		const b = new Uint8Array([0, 4, 0]);
		const first = [...samplesOf(carry, a)];
		expect(first.map((s) => [...s])).toEqual([[1, 2]]);
		expect(carry.odd && [...carry.odd]).toEqual([3]);
		const second = [...samplesOf(carry, b)];
		expect(second.map((s) => [...s])).toEqual([[3, 4]]);
		expect(carry.odd).toBeNull();
		// A lone odd byte yields nothing and waits.
		expect([...samplesOf(carry, new Uint8Array([9]))]).toEqual([]);
		expect(carry.odd && [...carry.odd]).toEqual([9]);
	});
});
