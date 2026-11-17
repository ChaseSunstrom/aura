// `panel.voice` from the microphone controller, the turn's clock and the
// speaker gate's status.
//
// The stages are the old voice screen's four timings — listen, transcribe,
// first token, speak — as stage rows (no v2 screen draws them yet, T-0665); the sentence about who is
// enrolled comes from `/api/voice/speaker`. The controller (`hud/voice.svelte.ts`)
// owns the recorder and the speech queue; this only shapes what it reports.

export interface VoiceStamps {
	[key: string]: number | undefined;
}

export interface Stage {
	key: string;
	label: string;
	ms: number;
}

function between(stamps: VoiceStamps, from: string, to: string): number | null {
	const a = stamps[from];
	const b = stamps[to];
	return a !== undefined && b !== undefined && b >= a ? Math.round(b - a) : null;
}

/** The four stages that have a measurement, in order; one without is left out. */
export function voiceStages(stamps: VoiceStamps): Stage[] {
	const rows: [string, string, string, string][] = [
		['listen', 'Listening', 'ptt-start', 'ptt-end'],
		['transcribe', 'Transcribing', 'ptt-end', 'stt-end'],
		['first-token', 'First token', 'submit', 'first-token'],
		['speak', 'Speaking', 'speak-start', 'speak-end']
	];
	const out: Stage[] = [];
	for (const [key, label, from, to] of rows) {
		const ms = between(stamps, from, to);
		if (ms !== null) out.push({ key, label, ms });
	}
	return out;
}

