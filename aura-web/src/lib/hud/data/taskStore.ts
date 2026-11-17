// One model of work, projected from the sources Hermes already owns (ADR-0034).
//
// Goals, kanban cards, cron jobs, scheduled jobs and runs are five different
// shapes with five different vocabularies, and the room used to draw each in its
// own panel with its own words. They are one thing to a person: work, at some
// stage, that they may want to look at or stop. `TaskRecord` is that thing.
//
// **It is a read model, not a database.** Nothing here stores anything. Every
// record is derived from a feed that is already on screen somewhere, and every
// action names the source's own verb, so the board and `hermes cron list` can
// never disagree — there is one writer per fact and it is not this file. A
// JARVIS-owned table would be a second writer, which is exactly the duplication
// v2 spent a phase deleting.
//
// The three projections are the same list read three ways: **timeline** (what
// happened, newest first), **board** (what is in flight, by state) and **queue**
// (what is scheduled, by next fire). A panel picks a projection; none of them
// filters anything the others can see, so a task cannot be missing from one
// surface and present on another.

import type { Goal, Job, KanbanTask, RunState } from '$lib/glass';

/** Which subsystem owns the record, and therefore which verb acts on it. */
export type TaskSource = 'goal' | 'kanban' | 'cron' | 'run';

export type TaskKind = 'goal' | 'subgoal' | 'card' | 'schedule' | 'run';

/** What a surface may offer for a record; the source decides which apply. */
export type TaskAction = 'open' | 'pause' | 'resume' | 'cancel' | 'run';

export interface TaskRecord {
	/** `<source>:<native id>` — stable, and obvious in a URL and in a log. */
	id: string;
	source: TaskSource;
	kind: TaskKind;
	title: string;
	state: RunState;
	/** A subgoal's goal, a card's epic; null at the top. */
	parent: string | null;
	/** One line of context: how far it got, or why it stopped. */
	detail?: string;
	/** When it is next due, in the source's own words ("in 4 minutes"). */
	next?: string;
	/** When it last ran, in the source's own words ("2 minutes ago"). */
	last?: string;
	/** Position in the timeline: higher is more recent. Absent means undated. */
	at?: number;
	actions: TaskAction[];
}

/** `id` back to the source and the id that source knows it by. */
export function sourceOf(id: string): { source: TaskSource; nativeId: string } {
	const cut = id.indexOf(':');
	if (cut < 0) return { source: 'run', nativeId: id };
	return { source: id.slice(0, cut) as TaskSource, nativeId: id.slice(cut + 1) };
}

const FINISHED: readonly RunState[] = ['done', 'failed'];

/** What can be done to a record, from its source and its state. */
function actionsFor(source: TaskSource, state: RunState): TaskAction[] {
	if (source === 'goal') {
		if (FINISHED.includes(state)) return ['open'];
		return state === 'held' ? ['open', 'resume', 'cancel'] : ['open', 'pause', 'cancel'];
	}
	if (source === 'cron') return state === 'held' ? ['open', 'resume', 'run'] : ['open', 'pause', 'run'];
	if (source === 'kanban') return FINISHED.includes(state) ? ['open'] : ['open', 'cancel'];
	return FINISHED.includes(state) ? ['open'] : ['open', 'cancel'];
}

const record = (r: Omit<TaskRecord, 'actions'>): TaskRecord => ({
	...r,
	actions: actionsFor(r.source, r.state)
});

/**
 * The standing goal and its subgoals.
 *
 * A goal is one record and each subgoal is another with `parent` set, rather
 * than a goal carrying a list — so the board can put a subgoal in the column its
 * own state belongs in, which is the whole reason a person looks at a board.
 */
export function fromGoal(goal: Goal | null | undefined): TaskRecord[] {
	if (!goal) return [];
	const id = `goal:${goal.id}`;
	const turns = goal.turn != null && goal.turns != null ? `${goal.turn}/${goal.turns} turns` : undefined;
	const out: TaskRecord[] = [
		record({
			id,
			source: 'goal',
			kind: 'goal',
			title: goal.title,
			state: goal.state,
			parent: null,
			detail: goal.reason || turns,
			at: Number.MAX_SAFE_INTEGER
		})
	];
	for (const sub of goal.subgoals ?? []) {
		out.push(
			record({
				id: `goal:${sub.id}`,
				source: 'goal',
				kind: 'subgoal',
				title: sub.label,
				state: sub.state,
				parent: id
			})
		);
	}
	return out;
}

/** The board's cards, which are already one shape. */
export function fromKanban(tasks: readonly KanbanTask[]): TaskRecord[] {
	return tasks.map((task) =>
		record({
			id: `kanban:${task.id}`,
			source: 'kanban',
			kind: 'card',
			title: task.title,
			state: task.state,
			parent: null,
			detail: task.detail
			// No `at`: a `KanbanTask` carries no time, so a card is undated on the
			// timeline rather than given a made-up one. The board and the queue,
			// which are what a card is for, do not need it.
		})
	);
}

/**
 * Cron and the house's scheduler, which `jobs.ts` has already made one list.
 *
 * A job's `state` is `running`, `paused` or `idle` in the panel's vocabulary;
 * here it joins everything else's, so "paused" and "held for a person" are the
 * same column on the board because they are the same thing to the person
 * looking at it.
 */
export function fromJobs(jobs: readonly Job[]): TaskRecord[] {
	return jobs.map((job) =>
		record({
			id: `cron:${job.id}`,
			source: 'cron',
			kind: 'schedule',
			title: job.title,
			state: job.state === 'running' ? 'running' : job.state === 'paused' ? 'held' : 'queued',
			parent: null,
			next: job.next,
			last: job.last
		})
	);
}

/** Everything, in one list. The projections below are the only readers. */
export function taskStore(input: {
	goal?: Goal | null;
	tasks?: readonly KanbanTask[];
	jobs?: readonly Job[];
}): TaskRecord[] {
	return [...fromGoal(input.goal), ...fromKanban(input.tasks ?? []), ...fromJobs(input.jobs ?? [])];
}

/** What happened: newest first, undated last, ties broken by id so it is stable. */
export function timeline(records: readonly TaskRecord[]): TaskRecord[] {
	return [...records].sort((a, b) => (b.at ?? -1) - (a.at ?? -1) || a.id.localeCompare(b.id));
}

/** The board's five columns, in the order the kanban panel already draws them. */
export const COLUMNS: readonly RunState[] = ['queued', 'running', 'held', 'done', 'failed'];

/** What is in flight: every record in the column its own state names. */
export function board(records: readonly TaskRecord[]): Record<RunState, TaskRecord[]> {
	const out = Object.fromEntries(COLUMNS.map((c) => [c, [] as TaskRecord[]])) as Record<
		RunState,
		TaskRecord[]
	>;
	for (const r of [...records].sort((a, b) => a.id.localeCompare(b.id))) out[r.state].push(r);
	return out;
}

/**
 * What is scheduled: anything with a next fire, and anything still waiting.
 *
 * A finished record is not in the queue however recently it ran — the queue
 * answers "what is coming", and a done job's next run is a different record's
 * question.
 */
export function queue(records: readonly TaskRecord[]): TaskRecord[] {
	return records
		.filter((r) => !FINISHED.includes(r.state))
		.sort((a, b) => Number(!a.next) - Number(!b.next) || a.id.localeCompare(b.id));
}
