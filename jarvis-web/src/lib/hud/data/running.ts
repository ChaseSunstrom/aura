// What the room draws while JARVIS is doing something (ADR-0032): the mockup's
// task view, read off whatever is actually running.
//
// Two things can be running and they are not the same shape. A GOAL has turns
// and a judge's reason; a TASK has a plan of steps, one of which is current.
// The ring says the same four things about either — which step of how many, how
// far through, what that step is, and how long it has been going — because a
// person watching the instrument is asking the same question in both cases.
//
// Null when nothing is running, and the stage draws the exchange instead. A
// ring at 0 % with "step 0 of 0" is worse than no ring: it says work is
// happening when none is.

import type { Goal } from '$lib/glass';
import type { TaskView } from './task';

export interface RunningView {
	/** 1-based, for `STEP n OF m`. */
	step: number;
	steps: number;
	/** The step itself, in the words whoever is running it used. */
	label: string;
	/** 0–100, already rounded: the ring's figure and its arc. */
	percent: number;
	/** What kind of work it is, for the line under the figure. */
	kind: string;
	/** Where it is happening — the task's title, the goal's own words. */
	where: string;
}

const pct = (done: number, total: number): number => (total > 0 ? Math.round((done / total) * 100) : 0);

/**
 * The task's own progress: the steps that are done, and the one running.
 * A plan with nothing marked running is between steps, and the figure is what
 * has finished rather than a guess at the next one.
 */
export function runningTask(task: TaskView | null | undefined): RunningView | null {
	const steps = task?.steps ?? [];
	if (!task || steps.length === 0) return null;
	const done = steps.filter((s) => s.state === 'done').length;
	const at = steps.findIndex((s) => s.state === 'running');
	if (at === -1 && done === steps.length) return null; // finished: the ring has nothing to say
	const index = at === -1 ? Math.min(done, steps.length - 1) : at;
	return {
		step: index + 1,
		steps: steps.length,
		label: steps[index]?.label ?? '',
		percent: pct(done, steps.length),
		kind: 'task',
		where: task.title
	};
}

/** The goal's: the judge counts turns, and its reason is what this turn is for. */
export function runningGoal(goal: Goal | null | undefined): RunningView | null {
	if (!goal || goal.state !== 'running') return null;
	const turns = goal.turns ?? 0;
	const turn = goal.turn ?? 0;
	if (turns <= 0) return null;
	return {
		step: Math.max(1, turn),
		steps: turns,
		label: goal.reason || goal.title,
		percent: pct(turn, turns),
		kind: 'goal',
		where: goal.title
	};
}

/**
 * What the stage draws. A task the person has open wins over a goal: it is the
 * more specific thing, and the goal's own line is already in the top bar.
 */
export function runningView(task: TaskView | null | undefined, goal: Goal | null | undefined): RunningView | null {
	return runningTask(task) ?? runningGoal(goal);
}
