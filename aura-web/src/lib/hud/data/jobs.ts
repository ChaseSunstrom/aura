// `panel.jobs` from both schedulers.
//
// jarvis-core keeps the house's scheduled jobs (`jarvis/schedule/list`);
// Hermes keeps the agent's cron (`GET /api/jobs`). The old console listed them
// in two panels on two screens. One list, one vocabulary; the id says which
// scheduler owns the job so a verb reaches the right one, the way approvals do.

import type { CronJob } from '$lib/hermes/protocol';
import type { ScheduledJob } from '$lib/schedule';
import type { Job } from '$lib/glass';
import { agoOf, untilOf } from './time';

export type JobSource = 'core' | 'hermes';

export const CORE_JOB = 'core:';
export const HERMES_JOB = 'hermes:';

export function jobSourceOf(id: string): { source: JobSource; jobId: string } {
	if (id.startsWith(HERMES_JOB)) return { source: 'hermes', jobId: id.slice(HERMES_JOB.length) };
	return { source: 'core', jobId: id.startsWith(CORE_JOB) ? id.slice(CORE_JOB.length) : id };
}

/** "passed", "failed: …", "missed" — the last run in one word or clause. */
function lastOfCore(job: ScheduledJob, now: number): string {
	if (!job.last_at) return '';
	const when = agoOf(job.last_at * 1000, now);
	if (job.missed) return `was missed ${when}`;
	const result = job.last_result.trim();
	if (!result) return `ran ${when}`;
	if (/^(ok|passed|told you)/i.test(result)) return `passed ${when}`;
	return `ran ${when}: ${result}`;
}

export function fromScheduled(job: ScheduledJob, now = Date.now()): Job {
	const view: Job = {
		id: `${CORE_JOB}${job.id}`,
		title: job.title,
		state: job.enabled ? 'idle' : 'paused'
	};
	if (job.enabled && job.next_at) view.next = untilOf(job.next_at * 1000, now);
	else if (job.enabled && job.when.mode === 'once') view.next = 'is done';
	const last = lastOfCore(job, now);
	if (last) view.last = last;
	return view;
}

function ms(iso: string | null | undefined): number | null {
	if (!iso) return null;
	const parsed = Date.parse(iso);
	return Number.isFinite(parsed) ? parsed : null;
}

export function fromCron(job: CronJob, now = Date.now()): Job {
	const paused = job.enabled === false || job.state === 'paused' || Boolean(job.paused_at);
	const view: Job = {
		id: `${HERMES_JOB}${job.id}`,
		title: job.name,
		state: job.state === 'running' ? 'running' : paused ? 'paused' : 'idle'
	};
	const next = ms(job.next_run_at);
	if (!paused && next) view.next = untilOf(next, now);
	else if (!paused && job.schedule_display) view.next = job.schedule_display;
	const last = ms(job.last_run_at);
	if (last) {
		const when = agoOf(last, now);
		view.last =
			job.last_status === 'ok' || job.last_status === 'success'
				? `passed ${when}`
				: job.last_status
					? `${job.last_status} ${when}${job.last_error ? `: ${job.last_error}` : ''}`
					: `ran ${when}`;
	}
	return view;
}

/** Both schedulers' jobs, soonest first, paused ones last. */
export function jobsView(scheduled: readonly ScheduledJob[], cron: readonly CronJob[] = [], now = Date.now()): Job[] {
	const rows = [
		...scheduled.map((j) => ({ at: j.enabled && j.next_at ? j.next_at * 1000 : Number.POSITIVE_INFINITY, view: fromScheduled(j, now) })),
		...cron.map((j) => ({ at: ms(j.next_run_at) ?? Number.POSITIVE_INFINITY, view: fromCron(j, now) }))
	];
	return rows
		.map((row, index) => ({ ...row, index }))
		.sort((a, b) => a.at - b.at || a.index - b.index)
		.map((row) => row.view);
}
