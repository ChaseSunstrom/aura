import { describe, expect, it } from 'vitest';

import { CEILING_MS, MIN_UTTERANCE_MS, ONSET_FRAMES, fresh, step, type OpenMicEvent } from './openmic';

/** Feed a sequence of (level, ms) frames; collect the events. */
function run(levels: number[], stepMs = 16, hangover = 550, start = fresh()) {
	let s = start;
	const events: { at: number; event: OpenMicEvent }[] = [];
	let now = 0;
	for (const level of levels) {
		now += stepMs;
		const r = step(s, level, now, hangover);
		s = r.state;
		if (r.event) events.push({ at: now, event: r.event });
	}
	return { state: s, events };
}

const frames = (level: number, ms: number, stepMs = 16) => Array(Math.round(ms / stepMs)).fill(level);

describe('open mic', () => {
	it('starts on a few frames of clear signal and ends after the hangover of quiet', () => {
		const { events } = run([...frames(0.01, 500), ...frames(0.3, 1200), ...frames(0.01, 800)]);
		expect(events.map((e) => e.event)).toEqual(['onset', 'end']);
		// Onset after ONSET_FRAMES of speech, not on the first frame.
		expect(events[0].at).toBe(Math.round(500 / 16) * 16 + ONSET_FRAMES * 16);
		// End one hangover after the last loud frame (within a frame).
		expect(events[1].at).toBeGreaterThanOrEqual(1700 + 550 - 16);
		expect(events[1].at).toBeLessThanOrEqual(1700 + 550 + 16);
	});

	it('forgets a single loud frame: a door is not a word', () => {
		const { events } = run([...frames(0.01, 300), 0.5, ...frames(0.01, 300), 0.5, 0.5, ...frames(0.01, 300)]);
		expect(events).toEqual([]);
	});

	it('throws a blip away', () => {
		const { events } = run([...frames(0.01, 300), ...frames(0.4, MIN_UTTERANCE_MS - 100), ...frames(0.01, 800)]);
		expect(events.map((e) => e.event)).toEqual(['onset', 'blip']);
	});

	it('follows the room: a noisy room needs a louder onset, and speech never raises the floor', () => {
		const quiet = run(frames(0.01, 2000)).state;
		const noisy = run(frames(0.05, 4000)).state;
		expect(noisy.floor).toBeGreaterThan(quiet.floor);
		// 0.1 is speech in a quiet room and not in this noisy one (floor ~0.05, onset 0.15).
		expect(run(frames(0.1, 200), 16, 550, quiet).events.map((e) => e.event)).toEqual(['onset']);
		expect(run(frames(0.1, 200), 16, 550, noisy).events).toEqual([]);
		const after = run(frames(0.4, 1000), 16, 550, quiet).state;
		expect(after.floor).toBe(quiet.floor);
	});

	it('cuts an utterance at the ceiling rather than listening for ever, and keeps listening', () => {
		const { events } = run(frames(0.3, CEILING_MS + 2000));
		// Cut and sent at the ceiling; the speech that goes on is a new utterance.
		expect(events.slice(0, 3).map((e) => e.event)).toEqual(['onset', 'end', 'onset']);
		expect(events[1].at).toBeLessThanOrEqual(CEILING_MS + ONSET_FRAMES * 16 + 16);
	});

	it('ends when the voice stops even if the gain lifts the room above the old floor', () => {
		// The owner, 2026-09-25: "it never continues when I stop talking". The
		// browser's gain control turns the room up once speech stops, so room
		// tone (0.05) sat over a hold threshold learned from the quiet room
		// before (0.036), and only the 12 s ceiling ended the utterance.
		const { events } = run([...frames(0.01, 500), ...frames(0.25, 1500), ...frames(0.05, 1500)]);
		expect(events.map((e) => e.event)).toEqual(['onset', 'end']);
		expect(events[1].at).toBeLessThanOrEqual(2000 + 550 + 32);
	});

	it('holds through a short pause inside a sentence', () => {
		const { events } = run([...frames(0.01, 300), ...frames(0.3, 800), ...frames(0.01, 300), ...frames(0.3, 800), ...frames(0.01, 800)]);
		expect(events.map((e) => e.event)).toEqual(['onset', 'end']);
	});
});
