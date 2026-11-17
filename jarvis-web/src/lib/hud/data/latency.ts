// Where a voice turn's time goes (voi-08): release → transcript → first token →
// first audio, kept for the last turns so the voice panel can show the last and
// the median of each stage.
//
// The marks are the controller's `performance.now()` stamps (`hud/voice.svelte.ts`):
// `ptt-end` is the release, `stt-end` the transcriber's answer, `first-token` the
// reply's first token arriving and `first-audio` the first reply sample handed to
// the speaker. This does not cover the server's split of a stage (the adapter's
// own transcribe/speak timings), nor a typed turn, which has no release.

import type { VoiceStamps } from './voice';

/** How many turns are kept: enough for a median that one slow turn cannot move. */
export const LATENCY_TURNS = 20;

/** One voice turn's stages, each in milliseconds from the stage before; null if it did not happen. */
export interface TurnLatency {
	/** The release stamp: which turn this is. */
	at: number;
	transcript: number | null;
	firstToken: number | null;
	firstAudio: number | null;
}

type StageKey = 'transcript' | 'firstToken' | 'firstAudio';

const STAGES: [StageKey, string, string, string][] = [
	['transcript', 'Release → transcript', 'ptt-end', 'stt-end'],
	['firstToken', 'Transcript → first token', 'stt-end', 'first-token'],
	['firstAudio', 'First token → first audio', 'first-token', 'first-audio']
];

function span(stamps: VoiceStamps, from: string, to: string): number | null {
	const a = stamps[from];
	const b = stamps[to];
	return a !== undefined && b !== undefined && b >= a ? Math.round(b - a) : null;
}

/** The turn the stamps describe, or null for one with no release (typed). */
export function turnLatency(stamps: VoiceStamps): TurnLatency | null {
	const at = stamps['ptt-end'];
	if (at === undefined) return null;
	const turn: TurnLatency = { at, transcript: null, firstToken: null, firstAudio: null };
	for (const [key, , from, to] of STAGES) turn[key] = span(stamps, from, to);
	return turn;
}

/**
 * The history with this turn in it: the same release replaces its own entry as
 * later marks land, a new one is appended, and only the last `LATENCY_TURNS` stay.
 */
export function recordTurn(history: TurnLatency[], stamps: VoiceStamps): TurnLatency[] {
	const turn = turnLatency(stamps);
	if (!turn) return history;
	const last = history[history.length - 1];
	const kept = last?.at === turn.at ? history.slice(0, -1) : history;
	return [...kept, turn].slice(-LATENCY_TURNS);
}

/** The middle value, the mean of the two middles for an even count; null for none. */
export function median(values: number[]): number | null {
	if (!values.length) return null;
	const sorted = [...values].sort((a, b) => a - b);
	const mid = sorted.length >> 1;
	return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

export interface LatencyRow {
	key: StageKey;
	label: string;
	last: number | null;
	median: number | null;
}

/** One row per stage: the last turn's figure and the median over the kept turns. */
export function latencyRows(history: TurnLatency[]): LatencyRow[] {
	const last = history[history.length - 1];
	return STAGES.map(([key, label]) => ({
		key,
		label,
		last: last?.[key] ?? null,
		median: median(history.map((t) => t[key]).filter((v): v is number => v !== null))
	}));
}
