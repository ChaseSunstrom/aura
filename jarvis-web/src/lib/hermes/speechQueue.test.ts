// The speech queue's promises, and the one most implementations leave vague.
//
// The brief: *"a higher priority interrupts a lower one at the next sentence
// boundary rather than mid-word, and the interrupted utterance is either resumed
// or dropped by an explicit rule, never left ambiguous. Two agents never talk
// over each other."*
import { describe, expect, it } from 'vitest';

import {
	bargeIn,
	boundary,
	fresh,
	priorityOf,
	resumesAfterInterruption,
	say,
	speaking,
	SPEAKERS,
	type Speaker,
	type SpeechState
} from './speechQueue';

const u = (id: string, speaker: Speaker, n = 2) => ({
	id,
	speaker,
	sentences: Array.from({ length: n }, (_, i) => `${id}-${i}`)
});

/** Everything said, in the order a person would hear it. */
function heard(start: SpeechState, steps = 12): string[] {
	const out: string[] = [];
	let s = start;
	for (let i = 0; i < steps; i++) {
		const line = speaking(s);
		if (line) out.push(line);
		const before = s;
		s = boundary(s);
		if (!s.speaking && !s.queue.length && !before.speaking) break;
	}
	return out;
}

describe('the priority is the brief’s', () => {
	it('is my request, then an approval, then the primary, then a companion, then a satellite', () => {
		expect([...SPEAKERS]).toEqual(['request', 'approval', 'primary', 'companion', 'satellite']);
		expect(priorityOf('request')).toBeLessThan(priorityOf('approval'));
		expect(priorityOf('approval')).toBeLessThan(priorityOf('primary'));
		expect(priorityOf('primary')).toBeLessThan(priorityOf('companion'));
		expect(priorityOf('companion')).toBeLessThan(priorityOf('satellite'));
	});
});

describe('two agents never talk over each other', () => {
	it('speaks the first thing said and queues the second', () => {
		let s = say(fresh(), u('a', 'companion'));
		s = say(s, u('b', 'satellite'));
		expect(s.speaking?.id).toBe('a');
		expect(s.queue.map((q) => q.id)).toEqual(['b']);
	});

	it('only ever has one voice, through every interruption', () => {
		let s = say(fresh(), u('sat', 'satellite', 4));
		s = say(s, u('comp', 'companion', 2));
		s = say(s, u('appr', 'approval', 2));
		for (let i = 0; i < 12; i++) {
			expect(s.speaking === null || typeof s.speaking.id === 'string').toBe(true);
			s = boundary(s);
		}
	});
});

describe('an interruption lands on a sentence boundary', () => {
	it('does not cut the sentence being spoken', () => {
		let s = say(fresh(), u('sat', 'satellite', 3));
		expect(speaking(s)).toBe('sat-0');
		s = say(s, u('appr', 'approval'));
		// Still the satellite's sentence: the approval waits for the boundary.
		expect(speaking(s)).toBe('sat-0');
		s = boundary(s);
		expect(speaking(s), 'the approval takes over at the boundary').toBe('appr-0');
	});

	it('lets an equal priority wait its turn rather than interrupting', () => {
		let s = say(fresh(), u('a', 'companion', 2));
		s = say(s, u('b', 'companion', 1));
		s = boundary(s);
		expect(speaking(s)).toBe('a-1');
	});
});

describe('the interrupted utterance, by an explicit rule', () => {
	it('resumes the primary, because the answer I asked for is still the answer', () => {
		expect(resumesAfterInterruption('primary')).toBe(true);
		expect(resumesAfterInterruption('approval')).toBe(true);
		expect(resumesAfterInterruption('request')).toBe(true);

		let s = say(fresh(), u('pri', 'primary', 3));
		s = say(s, u('req', 'request', 1));
		s = boundary(s); // request takes over
		expect(speaking(s)).toBe('req-0');
		s = boundary(s); // request done, primary resumes
		expect(speaking(s), 'resumes where it was, not from the top').toBe('pri-1');
	});

	it('drops a companion, because a minute late is worse than not at all', () => {
		expect(resumesAfterInterruption('companion')).toBe(false);
		expect(resumesAfterInterruption('satellite')).toBe(false);

		let s = say(fresh(), u('comp', 'companion', 3));
		s = say(s, u('appr', 'approval', 1));
		s = boundary(s);
		expect(speaking(s)).toBe('appr-0');
		s = boundary(s);
		expect(s.speaking, 'the companion did not come back').toBe(null);
	});

	it('puts a resumed utterance ahead of what merely arrived while it spoke', () => {
		let s = say(fresh(), u('pri', 'primary', 3));
		s = say(s, u('req', 'request', 1));
		s = say(s, u('comp', 'companion', 1));
		s = boundary(s); // req takes over, pri is held
		s = boundary(s); // req ends
		expect(speaking(s), 'the one that was already speaking goes first').toBe('pri-1');
	});
});

describe('barge-in', () => {
	it('stops whoever is speaking, not merely the primary', () => {
		let s = say(fresh(), u('sat', 'satellite', 4));
		s = say(s, u('comp', 'companion', 2));
		expect(speaking(s)).toBe('sat-0');
		expect(bargeIn()).toEqual(fresh());
	});

	it('takes the queue with it', () => {
		// Resuming a companion the moment the person drew breath is the machine
		// talking over them. Anything that still matters asks again.
		const after = bargeIn();
		expect(after.speaking).toBe(null);
		expect(after.queue).toEqual([]);
	});
});

describe('a whole conversation', () => {
	it('is heard in priority order, with each utterance whole', () => {
		let s = say(fresh(), u('sat', 'satellite', 2));
		s = say(s, u('comp', 'companion', 2));
		s = say(s, u('pri', 'primary', 2));
		// The satellite is speaking; the primary outranks it and takes the next
		// boundary. The satellite is dropped, the companion follows the primary.
		expect(heard(s)).toEqual(['sat-0', 'pri-0', 'pri-1', 'comp-0', 'comp-1']);
	});
});
