// One voice at a time (the v3 brief).
//
// *"A speech queue with a fixed priority — my direct request > an approval that
// is needed > the primary > a companion > a satellite — and defined interruption
// rules: a higher priority interrupts a lower one at the next sentence boundary
// rather than mid-word, and the interrupted utterance is either resumed or
// dropped by an explicit rule, never left ambiguous. Two agents never talk over
// each other."*
//
// The last clause of the first sentence is the one this module is really for.
// Most speech queues leave "what happens to the thing you interrupted" to
// whatever the code happens to do, and it comes out different on every path.

/** Who is asking to speak. The order of this list is the priority. */
export const SPEAKERS = ['request', 'approval', 'primary', 'companion', 'satellite'] as const;
export type Speaker = (typeof SPEAKERS)[number];

/** Lower number speaks first. */
export const priorityOf = (s: Speaker): number => SPEAKERS.indexOf(s);

export interface Utterance {
	id: string;
	speaker: Speaker;
	/**
	 * The utterance, already split into sentences.
	 *
	 * Sentences rather than a string because the interruption rule is stated in
	 * them: *"at the next sentence boundary rather than mid-word"*. A queue that
	 * holds one blob cannot honour that, whatever it intends.
	 */
	sentences: readonly string[];
	/** Which sentence is being spoken. Absent means it has not started. */
	at?: number;
}

export interface SpeechState {
	/** At most one, ever. That is the whole invariant. */
	speaking: Utterance | null;
	queue: readonly Utterance[];
}

export const fresh = (): SpeechState => ({ speaking: null, queue: [] });

/**
 * Whether an interrupted utterance comes back.
 *
 * The brief demands this be explicit, so: **the primary and above are resumed;
 * a companion or a satellite is dropped.** The reasoning is what the voices are
 * for. An approval that was needed is still needed after the interruption, and
 * the primary answering me is still the answer I asked for. A companion
 * narrating its own work has been overtaken by something more important, and
 * saying it a minute late — after the thing that interrupted it has changed the
 * situation — is worse than not saying it.
 */
export const resumesAfterInterruption = (s: Speaker): boolean =>
	priorityOf(s) <= priorityOf('primary');

/** Queue an utterance, or start it if nothing is speaking. */
export function say(state: SpeechState, utterance: Utterance): SpeechState {
	if (!state.speaking) return { speaking: { ...utterance, at: 0 }, queue: state.queue };
	// Never cut mid-sentence, however urgent: the swap happens at `boundary()`.
	return { speaking: state.speaking, queue: [...state.queue, utterance] };
}

/** What is waiting that outranks whoever is speaking, if anything. */
function usurper(state: SpeechState): Utterance | null {
	if (!state.speaking) return null;
	const current = priorityOf(state.speaking.speaker);
	let best: Utterance | null = null;
	for (const q of state.queue) {
		if (priorityOf(q.speaker) >= current) continue;
		if (!best || priorityOf(q.speaker) < priorityOf(best.speaker)) best = q;
	}
	return best;
}

/**
 * The current sentence has finished. Decide who speaks the next one.
 *
 * This is the only place a voice changes, which is what makes "two agents never
 * talk over each other" a property of the model rather than a hope about
 * timing.
 */
export function boundary(state: SpeechState): SpeechState {
	if (!state.speaking) return next(state);

	const taking = usurper(state);
	if (taking) {
		const at = (state.speaking.at ?? 0) + 1;
		const interrupted = { ...state.speaking, at };
		const rest = state.queue.filter((q) => q.id !== taking.id);
		const keep =
			at < interrupted.sentences.length && resumesAfterInterruption(interrupted.speaker);
		return {
			speaking: { ...taking, at: 0 },
			// Resumed utterances go to the front of their own priority, because
			// they were already speaking: they are ahead of anything that merely
			// arrived while they were.
			queue: keep ? [interrupted, ...rest] : rest
		};
	}

	const at = (state.speaking.at ?? 0) + 1;
	if (at < state.speaking.sentences.length) {
		return { speaking: { ...state.speaking, at }, queue: state.queue };
	}
	return next({ speaking: null, queue: state.queue });
}

/** Nothing is speaking: start the highest-priority thing waiting. */
function next(state: SpeechState): SpeechState {
	if (!state.queue.length) return { speaking: null, queue: [] };
	let best = state.queue[0];
	for (const q of state.queue) if (priorityOf(q.speaker) < priorityOf(best.speaker)) best = q;
	return {
		speaking: { ...best, at: best.at ?? 0 },
		queue: state.queue.filter((q) => q.id !== best.id)
	};
}

/**
 * The person started talking. Everything stops.
 *
 * *"Barge-in stops whoever is currently speaking, not merely the primary."* The
 * queue goes too, and that is the deliberate part: resuming a companion's
 * narration after the person has interrupted would be the machine talking over
 * them the moment they drew breath. Anything that still matters — an approval
 * that is still needed — is still true and will ask again.
 */
export const bargeIn = (): SpeechState => fresh();

/** The sentence being spoken right now, for a caption or a test. */
export const speaking = (state: SpeechState): string | null =>
	state.speaking ? (state.speaking.sentences[state.speaking.at ?? 0] ?? null) : null;
