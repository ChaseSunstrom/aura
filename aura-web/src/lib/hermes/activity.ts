// The activity view's state (`tests/contracts/activity-frames.json`): what the
// browser or the desktop is doing right now, folded from the four `activity.*`
// events the aura-activity plugin emits on the session.
//
// One activity per session: a `start` replaces whatever was there, because the
// emitter itself keeps one per session and ends the old one before opening the
// next. The console adds one status of its own, `stopped` — set the moment the
// person takes over — which the emitter's later `end` for the same run does not
// overwrite: what the person did is the last word on that run.

import type { HermesEvent } from './protocol';

export type ActivityStatus = 'running' | 'done' | 'failed' | 'stopped';

export interface ActivityStep {
	seq: number;
	tool: string;
	/** The one line the emitter wrote for the call, already redacted. */
	action: string;
	ok: boolean;
	error?: string;
	at: string;
}

export interface ActivityState {
	id: string;
	surface: string;
	intent: string;
	startedAt: string;
	url: string;
	title: string;
	/** The reduced capture as a data URL, or null when the last frame was text-only. */
	image: string | null;
	/** The page's compact snapshot, redacted; the tile's summary. */
	text: string;
	frameSeq: number;
	frameAt: string;
	/** The console's clock when the newest frame arrived: which turn it belongs to (`ds/activity`). */
	frameAtMs: number | null;
	steps: ActivityStep[];
	status: ActivityStatus;
	endedAt: string | null;
	/** The console's clock when the run ended (or was stopped), for the tile's linger. */
	endedAtMs: number | null;
}

/** A runaway loop shows a trail, not a scrollback: the newest steps are kept. */
export const MAX_STEPS = 60;
/** How long the tile stays after the run ended, so the last frame is seen (the brief: "a few seconds"). */
export const ACTIVITY_LINGER_MS = 5_000;

const str = (value: unknown): string => (value === null || value === undefined ? '' : String(value));
const num = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0);

function fresh(p: Record<string, unknown>): ActivityState {
	return {
		id: str(p.id),
		surface: str(p.surface) || 'browser',
		intent: str(p.intent),
		startedAt: str(p.started_at),
		url: '',
		title: '',
		image: null,
		text: '',
		frameSeq: 0,
		frameAt: '',
		frameAtMs: null,
		steps: [],
		status: 'running',
		endedAt: null,
		endedAtMs: null
	};
}

/**
 * Fold one `activity.*` event; anything else returns the state as it was.
 * A step or a frame for a run the console never saw start (it joined late)
 * opens one, so the tile still shows what is going on.
 */
export function reduceActivity(state: ActivityState | null, event: HermesEvent, now: number): ActivityState | null {
	const p = event.payload ?? {};
	const id = str(p.id);
	switch (event.type) {
		case 'activity.start':
			return fresh(p);
		case 'activity.step': {
			const base = state && state.id === id ? state : fresh(p);
			if (base.status !== 'running' && base.id === id) return base;
			const step: ActivityStep = {
				seq: num(p.seq),
				tool: str(p.tool),
				action: str(p.action),
				ok: p.ok !== false,
				at: str(p.at),
				...(p.error ? { error: str(p.error) } : {})
			};
			const steps = [...base.steps.filter((s) => s.seq !== step.seq), step].sort((a, b) => a.seq - b.seq).slice(-MAX_STEPS);
			return { ...base, steps };
		}
		case 'activity.frame': {
			const base = state && state.id === id ? state : fresh(p);
			const seq = num(p.seq);
			// An older frame that arrived late must not paint over a newer one.
			if (seq < base.frameSeq) return base;
			return {
				...base,
				url: str(p.url) || base.url,
				title: str(p.title) || base.title,
				image: typeof p.image === 'string' && p.image ? p.image : null,
				text: str(p.text),
				frameSeq: seq,
				frameAt: str(p.at),
				frameAtMs: now
			};
		}
		case 'activity.end': {
			if (!state || state.id !== id) return state;
			if (state.status === 'stopped') return { ...state, endedAt: str(p.ended_at) || state.endedAt };
			const status = str(p.status);
			return {
				...state,
				status: status === 'failed' ? 'failed' : 'done',
				endedAt: str(p.ended_at),
				endedAtMs: now
			};
		}
		default:
			return state;
	}
}

/** The person took over: the run is stopped here and now, whatever the emitter says later. */
export function stopActivity(state: ActivityState | null, now: number): ActivityState | null {
	if (!state || state.status !== 'running') return state;
	return { ...state, status: 'stopped', endedAtMs: now };
}

/** Whether the tile should be on the surface: a run in progress, or one that ended within the linger. */
export function activityLive(state: ActivityState | null, now: number): boolean {
	if (!state) return false;
	if (state.status === 'running') return true;
	return state.endedAtMs !== null && now - state.endedAtMs < ACTIVITY_LINGER_MS;
}
