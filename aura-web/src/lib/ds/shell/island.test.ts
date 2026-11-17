// The island's states are the phone's (T-0695): tests/contracts/island.json is the one table
// the popup and the phone's overlay both read, each case's state from what is going on, and
// each state's size as tokens. Does not cover how a state is drawn (e2e/v2-island.spec.ts).

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { ISLAND, islandState, springEasing, type IslandInputs, type IslandState } from './island';

interface Contract {
	inputs: Record<keyof IslandInputs, string>;
	states: Record<IslandState, { width?: string; height?: string; focus: boolean }>;
	radius: string;
	cases: { name: string; in: Partial<IslandInputs>; state: IslandState }[];
}

const PATH = fileURLToPath(new URL('../../../../../tests/contracts/island.json', import.meta.url));
const contract = JSON.parse(readFileSync(PATH, 'utf8')) as Contract;

describe('the island, as both surfaces choose it (tests/contracts/island.json)', () => {
	it.each(contract.cases)('$name', ({ in: given, state }) => {
		expect(islandState(given)).toBe(state);
	});

	it('sizes each state by the table’s tokens, and takes the focus only as a card', () => {
		for (const [state, row] of Object.entries(contract.states) as [IslandState, Contract['states'][IslandState]][]) {
			expect(ISLAND[state], state).toEqual({ width: row.width ?? null, height: row.height ?? null, focus: row.focus });
		}
		expect(Object.keys(ISLAND).sort()).toEqual(Object.keys(contract.states).sort());
	});

	it('reads only the inputs the table names', () => {
		expect(() => islandState({ nope: true } as unknown as Partial<IslandInputs>)).not.toThrow();
		expect(Object.keys(contract.inputs).sort()).toEqual(['activity', 'ask', 'collapsed', 'expanded', 'notice', 'on', 'touched', 'turn', 'working']);
	});
});

describe('the island’s morph on the desktop moves on the phone’s spring', () => {
	it('is the glass spring over its settle time: quick off the mark, a small overshoot, then at rest', () => {
		const { easing, ms } = springEasing('glass');
		expect(ms).toBe(380);
		const points = /^linear\((.*)\)$/.exec(easing)![1].split(', ').map(Number);
		expect(points[0]).toBe(0);
		expect(points.at(-1)).toBe(1);
		expect(points.length).toBe(33);
		// Past halfway a third of the way in, as a spring is and an ease-in-out is not.
		expect(points[11]).toBeGreaterThan(0.5);
		expect(Math.max(...points)).toBeGreaterThan(1);
		expect(Math.max(...points)).toBeLessThan(1.05);
		expect(Math.abs(points.at(-2)! - 1)).toBeLessThan(0.02);
	});
});
