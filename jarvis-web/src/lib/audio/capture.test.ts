import { afterEach, describe, expect, it, vi } from 'vitest';

import { PcmCapture } from './pcm';

// The owner, 2026-10-06, on the web console: transcripts came back garbled ("Kick in, you
// need 一 repeat with+…"), a 3.5 s utterance took 18 s to transcribe, and the speaker gate
// refused the owner's own voice. A capture that leaked kept writing into the next
// recording's buffer, so every later utterance carried old, interleaved audio.

/** A microphone whose permission prompt answers only when the test says so. */
function fakeAudio() {
	const nodes: { onaudioprocess: ((ev: unknown) => void) | null }[] = [];
	const contexts: { closed: boolean }[] = [];
	const streams: { stopped: boolean }[] = [];
	const pending: ((s: unknown) => void)[] = [];
	class FakeContext {
		sampleRate = 16000;
		closed = false;
		destination = {};
		constructor() {
			contexts.push(this);
		}
		createMediaStreamSource() {
			return { connect() {}, disconnect() {} };
		}
		createScriptProcessor() {
			const node = { onaudioprocess: null, connect() {}, disconnect() {} };
			nodes.push(node);
			return node;
		}
		async close() {
			this.closed = true;
		}
	}
	vi.stubGlobal('AudioContext', FakeContext);
	vi.stubGlobal('navigator', {
		mediaDevices: {
			getUserMedia: () =>
				new Promise((resolve) => {
					const stream = { stopped: false, getTracks: () => [{ stop: () => (stream.stopped = true) }] };
					streams.push(stream);
					pending.push(() => resolve(stream));
				})
		}
	});
	const speak = (node: (typeof nodes)[number], value: number) =>
		node.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(4).fill(value) } });
	const answer = async () => {
		pending.shift()?.(undefined);
		await new Promise((r) => setTimeout(r, 0));
	};
	return { nodes, contexts, streams, speak, answer };
}

afterEach(() => vi.unstubAllGlobals());

describe('PcmCapture', () => {
	it('a second start while the first waits on the microphone opens no second recorder', async () => {
		const audio = fakeAudio();
		const capture = new PcmCapture();
		const first = capture.start();
		const second = capture.start();
		await audio.answer();
		await audio.answer();
		await Promise.all([first, second]);
		expect(audio.nodes).toHaveLength(1);
		audio.speak(audio.nodes[0], 0.5);
		const pcm = await capture.stop();
		expect(pcm).toHaveLength(4);
		expect(audio.contexts.every((c) => c.closed)).toBe(true);
		expect(audio.streams.every((s) => s.stopped)).toBe(true);
	});

	it('a stop while the start waits on the microphone leaves nothing recording into the next utterance', async () => {
		const audio = fakeAudio();
		const capture = new PcmCapture();
		const started = capture.start();
		const stopped = capture.stop();
		await audio.answer();
		await started;
		await stopped;
		expect(capture.recording).toBe(false);
		expect(audio.contexts.every((c) => c.closed)).toBe(true);
		expect(audio.streams.every((s) => s.stopped)).toBe(true);
		// Anything the abandoned recorder hears now must not reach the next utterance.
		audio.nodes.forEach((n) => audio.speak(n, 0.9));
		const next = capture.start();
		await audio.answer();
		await next;
		const fresh = audio.nodes[audio.nodes.length - 1];
		audio.speak(fresh, 0.1);
		const pcm = await capture.stop();
		expect(pcm).toHaveLength(4);
		expect(Math.max(...pcm)).toBeLessThan(0.2 * 32767);
	});
});

describe('PushToTalk', () => {
	it('a release while the microphone opens ends that recording too', async () => {
		const { PushToTalk } = await import('$lib/hermes/voice');
		const audio = fakeAudio();
		const ptt = new PushToTalk();
		const started = ptt.start();
		const blob = ptt.stop();
		await audio.answer();
		await started;
		expect((await blob).size).toBe(0);
		expect(ptt.recording).toBe(false);
		expect(audio.streams.every((s) => s.stopped)).toBe(true);
	});
});
