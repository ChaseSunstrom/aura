import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';
import { initialHud } from '$lib/hermes/hud';
import { stateFrom } from '$lib/reactor';

// Instant visual acknowledgement (voi-08): the reactor leaves "listening" for "thinking"
// on the release itself, before the recorder hands over its blob and before the
// transcriber answers. Before this the reactor sat at rest through the whole
// transcription round trip, which read as "it didn't hear me".

beforeEach(() => {
	vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) }, userAgent: 'test' });
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal('requestAnimationFrame', () => 1);
	vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => vi.unstubAllGlobals());

describe('releasing the microphone', () => {
	it('shows thinking before the recording or any request resolves', async () => {
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		// The recorder never hands over its blob: nothing after the release can resolve.
		const ptt = { stop: () => new Promise<Blob>(() => {}) };
		Object.assign(voice, { ptt, recording: true });
		const reactor = () =>
			stateFrom({ hud: initialHud(), link: 'connected', recording: voice.recording, transcribing: voice.transcribing }).state;
		expect(reactor()).toBe('listening');

		void voice.stop();

		expect(voice.recording).toBe(false);
		expect(voice.transcribing).toBe(true);
		expect(voice.stamps['ptt-end']).toBeTypeOf('number');
		expect(reactor()).toBe('thinking');
	});

	it("keeps the turn's stages, first audio from the speaker itself", async () => {
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		voice.stamp('ptt-end', 1000);
		voice.stamp('stt-end', 1400);
		voice.stamp('first-token', 2000);
		vi.spyOn(performance, 'now').mockReturnValue(2300);
		(voice as unknown as { player: { onSound: () => void } }).player.onSound();
		expect(voice.latency).toEqual([{ at: 1000, transcript: 400, firstToken: 600, firstAudio: 300 }]);
	});
});
