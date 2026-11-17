// `panel.task` from one task row, its log and — for a coding job — its result.
//
// The plan is the task's steps in order; the output is the log's `output` and
// `note` lines verbatim; the diff is the coding job's, when it has one; the
// timeline is the log's tool lines as rows. Nothing is summarised: this is the
// panel a person opens to see exactly what ran.

import type { CodeResult } from '$lib/code';
import type { LogEntry } from '$lib/taskEvents';
import type { TaskRow } from '$lib/tasks';
import type { TaskStep, TimelineCall } from '$lib/glass';
import { runStateOfTask } from './kanban';
import { clockOf } from './time';

export interface TaskView {
	title: string;
	steps: TaskStep[];
	output: string;
	diff: string;
	calls: TimelineCall[];
}

export function taskSteps(task: TaskRow): TaskStep[] {
	return task.steps.map((step, index) => ({
		id: `${task.id}:${index}`,
		label: step.detail ? `${step.title} — ${step.detail}` : step.title,
		state: runStateOfTask(step.status)
	}));
}

/** The lines a person reads as the task's output, in order. */
export function taskOutput(log: readonly LogEntry[], result?: CodeResult | null): string {
	const lines = log.filter((e) => e.kind === 'output' || e.kind === 'note').map((e) => e.text);
	if (result?.summary) lines.push(result.summary);
	return lines.join('\n');
}

/** The log's tool lines as timeline rows: the task's own calls, in order. */
export function taskCalls(task: TaskRow, log: readonly LogEntry[]): TimelineCall[] {
	return log
		.map((entry, index) => ({ entry, index }))
		.filter(({ entry }) => entry.kind === 'tool')
		.map(({ entry, index }) => ({
			id: `${task.id}:log:${index}`,
			name: entry.text,
			state: 'ok' as const,
			ms: null,
			at: clockOf(entry.at * 1000)
		}));
}

export function taskView(task: TaskRow, log: readonly LogEntry[] = [], result: CodeResult | null = null): TaskView {
	const steps = taskSteps(task);
	// A coding job's plan is its result's, when the row carried none of its own.
	if (!steps.length && result?.plan?.length) {
		steps.push(
			...result.plan.map((label, index) => ({
				id: `${task.id}:plan:${index}`,
				label,
				state: 'done' as const
			}))
		);
	}
	return {
		title: task.title,
		steps,
		output: taskOutput(log, result),
		diff: result?.diff ?? '',
		calls: taskCalls(task, log)
	};
}
