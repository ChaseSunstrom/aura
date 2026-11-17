// Open mic: when speech starts and when it has ended, from the level alone.
//
// The owner (2026-09-09): *"it should be open mic, I shouldn't have to hold
// space."* Push-to-talk stays -- a held Space is still the surest way to be
// heard over a running reply -- but with the mic open the room listens on its
// own: an utterance begins when the level rises clearly above the room for a
// few frames and ends after the hangover of quiet the settings name
// (`voice.vad_hangover_ms`). Energy only, no model: the transcriber decides
// whether words were said, and `transcriptIsEmpty` throws room tone away.
//
// Pure, so the thresholds are tested on a hand-cranked clock. The controller
// owns the microphone; this owns only the decision.

export interface OpenMicState {
	/** The room's own level, followed slowly while nobody speaks. */
	floor: number;
	/** Frames in a row above the onset threshold; speech begins at `ONSET_FRAMES`. */
	rising: number;
	/** True between onset and end. */
	talking: boolean;
	/** When the level last cleared the end threshold while talking (ms). */
	lastLoud: number;
	/** When talking began (ms), for the ceiling. */
	since: number;
	/** The level, smoothed over a few frames, while talking. */
	voice: number;
	/** The loudest smoothed level of this utterance: what "stopped talking" is measured against. */
	peak: number;
}

/** Frames of clear signal before an onset: a door closing is one frame, a word is several. */
export const ONSET_FRAMES = 3;
/** The least an onset needs, whatever the room: below this nothing is speech. */
export const ONSET_MIN = 0.06;
/** Onset is this many times the room's floor (or ONSET_MIN, whichever is more). */
export const ONSET_OVER_FLOOR = 3;
/** Speech is still going while the level holds this much over the floor. */
export const HOLD_OVER_FLOOR = 1.6;
/**
 * Quiet is also a fall below this share of the utterance's own peak. The floor
 * alone was learned before the person spoke, and the browser's gain control
 * turns the room up once they stop, so room tone could hold an utterance open
 * until the ceiling (the owner, 2026-09-25: "it never continues when I stop
 * talking"). A quarter of a voice is not a voice.
 */
export const HOLD_UNDER_PEAK = 0.25;
/** The voice level's smoothing, so one hard consonant does not set the peak. */
const VOICE_RATE = 0.2;
/** The floor's smoothing while quiet; it never learns from speech. */
const FLOOR_RATE = 0.02;
/** Nobody speaks to an assistant for longer than this in one breath; the tail is cut and sent. */
export const CEILING_MS = 12_000;
/** Below this an utterance is a blip -- a cough, a click -- and is dropped unheard. */
export const MIN_UTTERANCE_MS = 400;

export const fresh = (): OpenMicState => ({ floor: 0.02, rising: 0, talking: false, lastLoud: 0, since: 0, voice: 0, peak: 0 });

export type OpenMicEvent = 'onset' | 'end' | 'blip' | null;

/**
 * One frame of level. Returns the event this frame produced, if any:
 * `onset` (start recording), `end` (stop and transcribe), `blip` (stop and
 * throw it away -- too short to be words).
 */
export function step(state: OpenMicState, level: number, now: number, hangoverMs: number): { state: OpenMicState; event: OpenMicEvent } {
	if (!state.talking) {
		const onsetAt = Math.max(ONSET_MIN, state.floor * ONSET_OVER_FLOOR);
		if (level >= onsetAt) {
			const rising = state.rising + 1;
			if (rising >= ONSET_FRAMES) {
				return { state: { ...state, rising: 0, talking: true, lastLoud: now, since: now, voice: level, peak: level }, event: 'onset' };
			}
			return { state: { ...state, rising }, event: null };
		}
		// Quiet: the floor follows the room, and a lone loud frame is forgotten.
		const floor = state.floor + (level - state.floor) * FLOOR_RATE;
		return { state: { ...state, floor: Math.max(0.005, floor), rising: 0 }, event: null };
	}
	const voice = state.voice + (level - state.voice) * VOICE_RATE;
	const peak = Math.max(state.peak, voice);
	const holdAt = Math.max(ONSET_MIN * 0.6, state.floor * HOLD_OVER_FLOOR, peak * HOLD_UNDER_PEAK);
	const lastLoud = level >= holdAt ? now : state.lastLoud;
	const quietFor = now - lastLoud;
	const length = now - state.since;
	if (quietFor >= hangoverMs || length >= CEILING_MS) {
		const spoken = length - (quietFor >= hangoverMs ? quietFor : 0);
		return {
			state: { ...state, talking: false, rising: 0, lastLoud: 0, since: 0, voice: 0, peak: 0 },
			event: spoken < MIN_UTTERANCE_MS ? 'blip' : 'end'
		};
	}
	return { state: { ...state, lastLoud, voice, peak }, event: null };
}
