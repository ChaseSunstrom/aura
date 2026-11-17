import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import type { BackgroundRow } from '$lib/hermes/hud';
import { CREW_LIMITS, crewFrom, workerType, TAG } from './crew';

const contract = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../../../tests/contracts/crew-budget.json', import.meta.url)), 'utf-8')
);

const row = (id: string, label: string, at: number, extra: Partial<BackgroundRow> = {}): BackgroundRow => ({
	id,
	kind: 'subagent',
	label,
	state: 'running',
	detail: '',
	at,
	...extra
});

describe('crew contract', () => {
	it('carries the plugin limits and tag from the shared table', () => {
		expect(CREW_LIMITS).toEqual({ helpers: contract.spawns, calls: contract.calls, minutes: contract.minutes });
		expect(TAG.source).toBe(contract.tag);
	});
	it.each(contract.types as { goal: string; type: string | null; rest: string }[])('types $goal', (c) => {
		expect(workerType(c.goal)).toEqual({ type: c.type, goal: c.rest });
	});
});

describe('crewFrom', () => {
	it('is empty with no subagents, ignoring processes', () => {
		const crew = crewFrom([{ id: 'p', kind: 'process', label: 'npm test', state: 'running', detail: '', at: 0 }], 0);
		expect(crew).toEqual({ nodes: [], helpers: 0, calls: 0, minutes: 0, running: 0 });
	});

	it('builds the tree with types, tools, state and budget used', () => {
		const t0 = 1_000_000;
		const bg = [
			row('b', '[planner] plan the move', t0 + 1000, { tools: 4 }),
			row('a', '[researcher] read the manual', t0, { tools: 10, state: 'done' }),
			row('a1', 'look up the model number', t0 + 2000, { parent: 'a', tools: 3 }),
			row('orphan', '[researcher] child of a pruned row', t0 + 3000, { parent: 'gone', tools: 1 })
		];
		const crew = crewFrom(bg, t0 + 5 * 60_000);
		expect(crew.helpers).toBe(4);
		expect(crew.calls).toBe(18);
		expect(crew.minutes).toBe(5);
		expect(crew.running).toBe(3);
		expect(crew.nodes.map((n) => n.id)).toEqual(['a', 'b', 'orphan']);
		const [a, b] = crew.nodes;
		expect(a).toMatchObject({ type: 'researcher', goal: 'read the manual', tools: 10, state: 'done' });
		expect(a.children).toHaveLength(1);
		expect(a.children[0]).toMatchObject({ id: 'a1', type: null, goal: 'look up the model number', parent: 'a', tools: 3 });
		expect(b).toMatchObject({ type: 'planner', tools: 4, state: 'running', children: [] });
	});

	it('counts a row with no tool count as none', () => {
		expect(crewFrom([row('x', 'go', 0)], 0).calls).toBe(0);
	});
});
