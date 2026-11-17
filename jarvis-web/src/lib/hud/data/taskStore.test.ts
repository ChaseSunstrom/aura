import { describe, expect, it } from 'vitest';

import type { Goal, Job, KanbanTask } from '$lib/glass';

import {
	COLUMNS,
	board,
	fromGoal,
	fromJobs,
	fromKanban,
	queue,
	sourceOf,
	taskStore,
	timeline
} from './taskStore';

const goal: Goal = {
	id: 'g1',
	title: 'Keep the study at 21°',
	state: 'running',
	turn: 3,
	turns: 20,
	subgoals: [
		{ id: 's1', label: 'Read the thermostat', state: 'done' },
		{ id: 's2', label: 'Hold it there', state: 'running' }
	]
};

const cards: KanbanTask[] = [
	{ id: 't1', title: 'Read twelve pages', state: 'queued' },
	{ id: 't2', title: 'Summarise them', state: 'held', detail: 'waiting on you' }
];

const jobs: Job[] = [
	{ id: 'core:morning', title: 'Morning brief', state: 'idle', next: 'in 4 hours' },
	{ id: 'hermes:health', title: 'Homelab health', state: 'running', last: '2 minutes ago' },
	{ id: 'core:evening', title: 'Evening summary', state: 'paused' }
];

describe('one store over five sources', () => {
	it('gives every record an id that names the source that owns it', () => {
		for (const r of taskStore({ goal, tasks: cards, jobs })) {
			expect(r.id).toMatch(/^(goal|kanban|cron|run):/);
			expect(sourceOf(r.id).source).toBe(r.source);
		}
	});

	it('makes a subgoal a record of its own, under its goal', () => {
		const out = fromGoal(goal);
		expect(out).toHaveLength(3);
		const subs = out.filter((r) => r.kind === 'subgoal');
		expect(subs.map((s) => s.parent)).toEqual(['goal:g1', 'goal:g1']);
		// Its own state, not its goal's: that is what puts it in the right column.
		expect(subs.map((s) => s.state)).toEqual(['done', 'running']);
	});

	it('is empty for no goal rather than inventing one', () => {
		expect(fromGoal(null)).toEqual([]);
		expect(fromGoal(undefined)).toEqual([]);
	});

	it('speaks one vocabulary: a paused job and a held card are both held', () => {
		const held = [...fromJobs(jobs), ...fromKanban(cards)].filter((r) => r.state === 'held');
		expect(held.map((r) => r.id).sort()).toEqual(['cron:core:evening', 'kanban:t2']);
	});
});

describe('what can be done to a record comes from its source', () => {
	it('offers pause on a running goal and resume on a held one', () => {
		expect(fromGoal(goal)[0].actions).toContain('pause');
		expect(fromGoal({ ...goal, state: 'held' })[0].actions).toContain('resume');
	});

	it('offers run on a schedule and never on a goal', () => {
		expect(fromJobs(jobs)[0].actions).toContain('run');
		expect(fromGoal(goal)[0].actions).not.toContain('run');
	});

	it('offers nothing but open on finished work', () => {
		expect(fromGoal({ ...goal, subgoals: [], state: 'done' })[0].actions).toEqual(['open']);
		expect(fromKanban([{ id: 'x', title: 'x', state: 'failed' }])[0].actions).toEqual(['open']);
	});

	it('always offers open, so every record reaches its transcript', () => {
		for (const r of taskStore({ goal, tasks: cards, jobs })) expect(r.actions).toContain('open');
	});
});

describe('three projections of the one list', () => {
	const all = taskStore({ goal, tasks: cards, jobs });

	it('loses nothing between the store and the board', () => {
		const columns = board(all);
		const seen = COLUMNS.flatMap((c) => columns[c]).map((r) => r.id);
		expect(seen.sort()).toEqual(all.map((r) => r.id).sort());
	});

	it('puts each record in the column its own state names', () => {
		const columns = board(all);
		for (const c of COLUMNS) for (const r of columns[c]) expect(r.state).toBe(c);
	});

	it('orders the timeline newest first and is stable', () => {
		const ordered = timeline(all);
		expect(ordered[0].id).toBe('goal:g1');
		expect(timeline(all).map((r) => r.id)).toEqual(ordered.map((r) => r.id));
	});

	it('keeps finished work out of the queue and dated work in front', () => {
		const q = queue(all);
		expect(q.some((r) => r.state === 'done' || r.state === 'failed')).toBe(false);
		expect(q[0].next).toBeTruthy();
	});

	it('is empty in every projection when there is no work', () => {
		const none = taskStore({});
		expect(none).toEqual([]);
		expect(timeline(none)).toEqual([]);
		expect(queue(none)).toEqual([]);
		expect(COLUMNS.every((c) => board(none)[c].length === 0)).toBe(true);
	});
});
