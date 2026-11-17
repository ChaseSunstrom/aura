import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';
import { LEARN_MS, TAIL_MS } from './bargeIn';
import { AWAKE_MS } from './voice.svelte';

// The open mic heard Jarvis's own voice as the person: in the gap while the model wrote the next
// sentence, and in the tail of the last clip, the plain detector took the echo for an onset, and
// the onset stopped the reply and interrupted the turn (the owner, 2026-11-08: "it pauses a bunch
// when it is trying to speak, and doesn't output all of the text"; a temperature question that
// never answered: adapter turn 11, outcome=interrupted after 29958 ms). These drive the open
// mic's frame with levels and a fake link, so the rule is pinned without a microphone.

const FRAME = 16;

function fakeLink(running: boolean) {
	const calls: string[] = [];
	const link = {
		current: { hud: { turn: { running } } },
		interrupt: vi.fn(async () => void calls.push('interrupt')),
		submit: vi.fn(async () => {
			calls.push('submit');
			return null;
		})
	};
	return { link, calls };
}

async function controller(running: boolean) {
	const { VoiceController } = await import('./voice.svelte');
	const { link, calls } = fakeLink(running);
	const voice = new VoiceController(link as unknown as HermesLink);
	await voice.setOpenMic(true);
	// The recorder is not under test: start() only marks the utterance as recording.
	const v = voice as unknown as {
		ptt: { start: () => Promise<void>; stop: () => Promise<Blob>; mediaStream: MediaStream | null };
		readLevel: () => number;
		openMicFrame: (now: number) => void;
		speaking: boolean;
		recording: boolean;
		openUtterance: boolean;
		heard: (blob: Blob) => Promise<void>;
	};
	v.ptt.start = async () => {};
	v.ptt.stop = async () => new Blob(['x']);
	return { voice, v, link, calls };
}

/** start() is async: let its awaits settle before reading what it set. */
const settle = () => new Promise((r) => setTimeout(r, 0));

/** Feed `ms` of one level, a frame at a time, from `t0`; returns the time after. */
function feed(v: { readLevel: () => number; openMicFrame: (now: number) => void }, level: number, t0: number, ms: number): number {
	v.readLevel = () => level;
	let t = t0;
	for (; t < t0 + ms; t += FRAME) v.openMicFrame(t);
	return t;
}

beforeEach(() => {
	vi.stubGlobal('navigator', {
		mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop: () => {} }] }) },
		userAgent: 'test'
	});
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal('requestAnimationFrame', () => 1);
	vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => vi.unstubAllGlobals());

describe('the open mic while a reply is live', () => {
	it('takes the echo between two sentences for the speaker, not the person', async () => {
		const { v, link } = await controller(true);
		let t = feed(v, 0.01, 0, 1000); // the room, before the reply
		v.speaking = true;
		t = feed(v, 0.08, t, 2000); // a sentence plays; the mic hears it
		v.speaking = false; // the queue drained: the model is still writing the next one
		feed(v, 0.06, t, 600); // the sentence's tail in the room
		await settle();
		expect(v.recording).toBe(false);
		expect(link.interrupt).not.toHaveBeenCalled();
	});

	it('takes the tail of the last clip for the speaker, after the turn has ended', async () => {
		const { v, link } = await controller(false);
		let t = feed(v, 0.01, 0, 1000);
		v.speaking = true;
		t = feed(v, 0.08, t, 2000);
		v.speaking = false;
		feed(v, 0.06, t, TAIL_MS - FRAME);
		await settle();
		expect(v.recording).toBe(false);
		expect(link.interrupt).not.toHaveBeenCalled();
	});

	it('lets a voice clearly over the echo cut in, without interrupting the turn before its words are in', async () => {
		const { v, link } = await controller(true);
		let t = feed(v, 0.01, 0, 1000);
		v.speaking = true;
		t = feed(v, 0.05, t, LEARN_MS + 200);
		feed(v, 0.3, t, 600); // the person, well over the echo
		await settle();
		expect(v.recording).toBe(true);
		expect(v.openUtterance).toBe(true);
		expect(link.interrupt).not.toHaveBeenCalled();
	});

	it('hears the person at a plain level mid-task, while the turn works with nothing playing (T-0902)', async () => {
		// "I tried talking to it mid task, and I couldn't" (the owner, 2026-11-09): with nothing
		// playing there is no echo to be heard over, so the plain detector is the one that listens.
		const { v, link } = await controller(true);
		let t = feed(v, 0.01, 0, 1000); // the room while the turn works
		v.speaking = true;
		t = feed(v, 0.08, t, 1500); // an earlier sentence
		v.speaking = false;
		t = feed(v, 0.01, t, TAIL_MS + 300); // its tail is over; the tools run on
		feed(v, 0.09, t, 600); // the person, at a plain speaking level: under BargeIn's edge
		await settle();
		expect(v.recording).toBe(true);
		expect(link.interrupt).not.toHaveBeenCalled();
	});

	it('interrupts nothing on an onset while the turn works with nothing said yet', async () => {
		const { v, link } = await controller(true);
		const t = feed(v, 0.01, 0, 1000);
		feed(v, 0.3, t, 1200); // a cough, or the person
		await settle();
		expect(link.interrupt).not.toHaveBeenCalled();
	});
});

describe('an open-mic utterance, once its words are accepted', () => {
	it('interrupts the running turn, then submits', async () => {
		const { v, calls } = await controller(true);
		vi.stubGlobal(
			'FileReader',
			class {
				result = 'data:audio/webm;base64,eA==';
				onload: (() => void) | null = null;
				onerror: (() => void) | null = null;
				readAsDataURL() {
					queueMicrotask(() => this.onload?.());
				}
			}
		);
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response(JSON.stringify({ ok: true, transcript: 'stop', speaker: null }), { status: 200 }))
		);
		v.openUtterance = true;
		await v.heard(new Blob(['x']));
		expect(calls).toEqual(['interrupt', 'submit']);
	});

	it('folds anything but a stop into the running turn: submitted, not interrupted (T-0794)', async () => {
		const { v, calls } = await controller(true);
		vi.stubGlobal(
			'FileReader',
			class {
				result = 'data:audio/webm;base64,eA==';
				onload: (() => void) | null = null;
				onerror: (() => void) | null = null;
				readAsDataURL() {
					queueMicrotask(() => this.onload?.());
				}
			}
		);
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response(JSON.stringify({ ok: true, transcript: 'and check the garage too', speaker: null }), { status: 200 }))
		);
		v.openUtterance = true;
		await v.heard(new Blob(['x']));
		expect(calls).toEqual(['submit']);
	});

	it('leaves the turn alone when the gate refuses the voice', async () => {
		const { v, calls } = await controller(true);
		vi.stubGlobal(
			'FileReader',
			class {
				result = 'data:audio/webm;base64,eA==';
				onload: (() => void) | null = null;
				readAsDataURL() {
					queueMicrotask(() => this.onload?.());
				}
			}
		);
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response(JSON.stringify({ ok: true, transcript: '', speaker: { dropped: true } }), { status: 200 }))
		);
		v.openUtterance = true;
		await v.heard(new Blob(['x']));
		expect(calls).toEqual([]);
	});
});

// The owner, 2026-11-09: "I say the wake word on the console and it doesn't go into a listening
// state". The phone wakes on its own model; the console's open mic sent "Hey Jarvis" as a turn.
describe('the wake phrase on the open mic (T-0769)', () => {
	function hears(transcript: string) {
		vi.stubGlobal(
			'FileReader',
			class {
				result = 'data:audio/webm;base64,eA==';
				onload: (() => void) | null = null;
				readAsDataURL() {
					queueMicrotask(() => this.onload?.());
				}
			}
		);
		vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, transcript, speaker: null }), { status: 200 })));
	}

	it('listens for the command instead of sending the name, and sends the command', async () => {
		const { voice, v, calls } = await controller(false);
		hears('Hey, Jarvis.');
		v.openUtterance = true;
		await v.heard(new Blob(['x']));
		expect(calls).toEqual([]);
		expect(voice.awake).toBe(true);
		hears('Turn on the lights.');
		v.openUtterance = true;
		await v.heard(new Blob(['x']));
		expect(calls).toEqual(['submit']);
		expect(voice.awake).toBe(false);
	});

	it('stops listening for the command when none comes', async () => {
		vi.useFakeTimers();
		try {
			const { voice, v } = await controller(false);
			hears('Jarvis');
			v.openUtterance = true;
			await v.heard(new Blob(['x']));
			expect(voice.awake).toBe(true);
			vi.advanceTimersByTime(AWAKE_MS + 1);
			expect(voice.awake).toBe(false);
		} finally {
			vi.useRealTimers();
		}
	});

	it('keeps listening through a cough after the name, and stops when the mic is held', async () => {
		const { voice, v } = await controller(false);
		hears('Hey, Jarvis.');
		v.openUtterance = true;
		await v.heard(new Blob(['x']));
		// A cough: its onset hands the ambient stream to the recorder, which ended the wake.
		const t = feed(v, 0.01, 0, 1000);
		feed(v, 0.3, t, 200);
		await settle();
		expect(v.recording).toBe(true);
		expect(voice.awake).toBe(true);
		voice.holdOpenMic();
		expect(voice.awake).toBe(false);
	});

	// T-0730: the owner waited 30 s on a temperature question and called the name; the name
	// interrupted the turn and, being only the name, sent nothing in its place, so the answer
	// never came (Hermes, 2026-11-08 10:06:13 CST: interrupted after 32.4 s, next word 21 min later).
	it('wakes without interrupting a turn that is still working, and its answer still comes', async () => {
		const { voice, v, calls } = await controller(true);
		hears('Jarvis?');
		v.openUtterance = true;
		await v.heard(new Blob(['x']));
		expect(calls).toEqual([]);
		expect(voice.awake).toBe(true);
	});

	it('sends the name as it is when it was pushed to talk, not the open mic', async () => {
		const { voice, v, calls } = await controller(false);
		hears('Jarvis.');
		v.openUtterance = false;
		await v.heard(new Blob(['x']));
		expect(calls).toEqual(['submit']);
		expect(voice.awake).toBe(false);
	});
});

// The lock and the enrolment sheet hold the open mic (Shell.svelte). The sheet's phrases went to
// JARVIS as turns through the open mic, and one set a real timer (the owner, 2026-11-09, T-0768).
describe('the hold (the lock, the enrolment sheet)', () => {
	it('throws away the utterance it was recording instead of sending it', async () => {
		const { voice, v, link } = await controller(false);
		const t = feed(v, 0.01, 0, 1000);
		feed(v, 0.3, t, 600); // the person starts reading a phrase
		await settle();
		expect(v.recording).toBe(true);
		voice.holdOpenMic();
		await settle();
		expect(v.recording).toBe(false);
		expect(link.submit).not.toHaveBeenCalled();
	});

	it('opens a stream again only when a hold is undone, never a second one beside the first', async () => {
		const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop: () => {} }] }));
		vi.stubGlobal('navigator', { mediaDevices: { getUserMedia }, userAgent: 'test' });
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController(fakeLink(false).link as unknown as HermesLink);
		// The shell's hold effect runs at mount, unheld, while the open mic's first stream is
		// still being opened: a resume then must not open another.
		void voice.setOpenMic(true);
		voice.resumeOpenMic();
		await settle();
		expect(getUserMedia).toHaveBeenCalledTimes(1);
		voice.holdOpenMic();
		voice.resumeOpenMic();
		await settle();
		expect(getUserMedia).toHaveBeenCalledTimes(2);
	});
});
