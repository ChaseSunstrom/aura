/**
 * Scheduled jobs, as the console understands them.
 *
 * jarvis-core owns the arithmetic (`integrations/schedule/plan.py`) and sends
 * `describes` already written — "every day at 07:30" — precisely so two
 * surfaces cannot disagree about what a schedule means. Nothing here recomputes
 * that: this is the shape `jarvis/schedule/list` answers, and only the shape.
 * The form that once lived beside it went with the old schedule screen; the
 * HUD's jobs panel (`hud/data/jobs.ts`) reads, it does not write.
 */

export type Mode = 'once' | 'daily' | 'weekly' | 'every';
export type JobKind = 'notify' | 'research' | 'service' | 'code';

export interface ScheduledJob {
	id: string;
	title: string;
	kind: JobKind;
	when: { mode: Mode; at: string; days: string[]; minutes: number };
	/** Written by jarvis-core so both surfaces say the same thing. */
	describes: string;
	payload: Record<string, unknown>;
	enabled: boolean;
	/** Epoch seconds, or null once a spent one-shot has nowhere left to go. */
	next_at: number | null;
	last_at: number;
	last_result: string;
	missed: number;
	created: number;
	source: string;
	/** False for jobs from configuration.yaml — the file owns those. */
	editable: boolean;
}
