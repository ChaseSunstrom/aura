// `panel.kanban` from jarvis-core's task rows.
//
// The board has five columns for the six task statuses: `blocked` is a task
// held for a person, and `cancelled` sits with `error` under Failed because
// both are a task that did not finish and a column called "Cancelled" would
// usually be empty. The detail line is the one sentence the old task card
// spent three rows on: how far it got, or why it stopped.

import { describeTask, isFinished, stepCount, type TaskRow, type TaskStatus } from '$lib/tasks';
import type { KanbanTask, RunState } from '$lib/glass';

export function runStateOfTask(status: TaskStatus): RunState {
	switch (status) {
		case 'queued':
			return 'queued';
		case 'running':
			return 'running';
		case 'blocked':
			return 'held';
		case 'done':
			return 'done';
		default:
			return 'failed';
	}
}

function detailOf(task: TaskRow): string | undefined {
	if (task.status === 'error' && task.error) return task.error;
	if (task.status === 'blocked') return task.detail || 'waiting on you';
	const steps = task.total_steps > 0 ? stepCount(task) : '';
	if (!isFinished(task) && steps) return steps;
	return describeTask(task) || task.detail || undefined;
}

export function kanbanTasks(rows: readonly TaskRow[]): KanbanTask[] {
	return rows.map((task) => {
		const card: KanbanTask = { id: task.id, title: task.title, state: runStateOfTask(task.status) };
		const detail = detailOf(task);
		if (detail) card.detail = detail;
		return card;
	});
}
