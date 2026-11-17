// What the ring says while JARVIS works, and when it says nothing at all.
//
// The failure this file exists to catch is a ring that lies: 0 % on a finished
// task, "step 0 of 0" on a goal with no budget, or a figure that keeps moving
// after the work stopped. A person watching the instrument to know whether to
// wait cannot check any of that against anything else on the screen.

import { describe, expect, it } from 'vitest';

import { runningGoal, runningTask, runningView } from './running';
import type { Goal, TaskStep } from '$lib/glass';
import type { TaskView } from './task';

const step = (label: string, state: TaskStep['state']): TaskStep => ({ id: label, label, state });
const task = (steps: TaskStep[]): TaskView => ({ title: 'Wire the offline state', steps, output: '', diff: '', calls: [] });

describe('the ring, while a task runs', () => {
	it('names the step that is running and counts the ones that are done', () => {
		const view = runningTask(task([step('read', 'done'), step('wrap', 'running'), step('wire', 'queued')]));
		expect(view).toMatchObject({ step: 2, steps: 3, label: 'wrap', percent: 33, kind: 'task' });
	});

	it('between steps, reads what has finished rather than guessing at the next', () => {
		const view = runningTask(task([step('read', 'done'), step('wrap', 'queued'), step('wire', 'queued')]));
		expect(view).toMatchObject({ step: 2, steps: 3, percent: 33 });
	});

	it('says nothing about a plan that has finished', () => {
		expect(runningTask(task([step('read', 'done'), step('wrap', 'done')]))).toBeNull();
	});

	it('says nothing about a task with no plan, rather than 0 of 0', () => {
		expect(runningTask(task([]))).toBeNull();
		expect(runningTask(null)).toBeNull();
	});

	it('starts at the first step before anything has run', () => {
		expect(runningTask(task([step('read', 'queued'), step('wrap', 'queued')]))).toMatchObject({ step: 1, percent: 0 });
	});
});

describe('the ring, while a goal runs', () => {
	const goal = (over: Partial<Goal> = {}): Goal => ({ id: 'goal', title: 'make two files exist', state: 'running', turn: 1, turns: 20, ...over });

	it('counts the judge s turns against the budget', () => {
		expect(runningGoal(goal())).toMatchObject({ step: 1, steps: 20, percent: 5, kind: 'goal' });
	});

	it('says the judge s reason when there is one, and the goal when there is not', () => {
		expect(runningGoal(goal({ reason: 'only a.txt exists' }))?.label).toBe('only a.txt exists');
		expect(runningGoal(goal())?.label).toBe('make two files exist');
	});

	it('says nothing about a goal that is not running', () => {
		expect(runningGoal(goal({ state: 'done' }))).toBeNull();
		expect(runningGoal(goal({ state: 'held' }))).toBeNull();
	});

	it('says nothing about a goal with no budget, rather than dividing by zero', () => {
		expect(runningGoal(goal({ turns: undefined }))).toBeNull();
	});
});

describe('what the stage draws', () => {
	it('prefers the task the person has open: the goal s line is already in the top bar', () => {
		const g: Goal = { id: 'goal', title: 'a goal', state: 'running', turn: 4, turns: 20 };
		const t = task([step('read', 'running'), step('wrap', 'queued')]);
		expect(runningView(t, g)?.kind).toBe('task');
		expect(runningView(null, g)?.kind).toBe('goal');
		expect(runningView(null, null)).toBeNull();
	});
});
