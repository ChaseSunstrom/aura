// The acknowledgement earcon: what the console plays the instant a prompt is
// submitted while replies are spoken. Between the request and the first spoken
// word there is model time plus ~1.3 s of CosyVoice first byte, in silence, and
// silence after speaking reads as "not heard" (T-0022). Every value is a
// `sound.ack.*` token; this file only turns them into a schedule.

import { TOKENS, tokenMs } from '$lib/tokens';
import type { Note } from '$lib/audio/playback';

/** The two notes of the acknowledgement, starting at `at` on the audio clock (seconds). */
export function ackNotes(at: number): Note[] {
	const note = tokenMs('--jv-sound-ack-note') / 1000;
	const gap = tokenMs('--jv-sound-ack-gap') / 1000;
	const gain = Number(TOKENS['--jv-sound-ack-gain']);
	const second = at + note + gap;
	return [
		{ freq: Number(TOKENS['--jv-sound-ack-low']), start: at, end: at + note, gain },
		{ freq: Number(TOKENS['--jv-sound-ack-high']), start: second, end: second + note, gain }
	];
}
