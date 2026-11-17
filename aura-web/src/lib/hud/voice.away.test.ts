import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';

// The open mic lets go while the page is away (on the car, with the phone in the car, or
// hidden on Android): in an Android web view a held microphone is call audio, and the car
// showed a call on every connect with the music muted (the owner, 2026-10-06).

let opened = 0;
let live = 0;

function fakeStream(): MediaStream {
	opened++;
	live++;
	let stopped = false;
	const track = {
		stop: () => {
			if (!stopped) live--;
			stopped = true;
		}
	};
	return { getTracks: () => [track] } as unknown as MediaStream;
}

beforeEach(() => {
	opened = 0;
	live = 0;
	vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => fakeStream() }, userAgent: 'test' });
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal('requestAnimationFrame', () => 1);
	vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => vi.unstubAllGlobals());

describe('VoiceController.setAway', () => {
	it('releases a held open mic and takes it back when the page returns', async () => {
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		await voice.setOpenMic(true);
		expect(live).toBe(1);
		voice.setAway(true);
		expect(live).toBe(0);
		voice.setAway(false);
		await vi.waitFor(() => expect(live).toBe(1));
		expect(opened).toBe(2);
	});

	// T-0993: the page asked for the mic, the car check said "away" before the grant came back,
	// and the late stream was kept for the whole drive: the car showed a call (2026-11-10).
	it('closes a mic that arrives after the page went away', async () => {
		let grant: (s: MediaStream) => void = () => {};
		vi.stubGlobal('navigator', {
			mediaDevices: { getUserMedia: () => new Promise<MediaStream>((ok) => (grant = ok)) },
			userAgent: 'test'
		});
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		const asked = voice.setOpenMic(true);
		voice.setAway(true);
		grant(fakeStream());
		await asked;
		await vi.waitFor(() => expect(opened).toBe(1));
		expect(live).toBe(0);
	});

	it('keeps one mic when the page leaves and returns before a grant arrives', async () => {
		const grants: ((s: MediaStream) => void)[] = [];
		vi.stubGlobal('navigator', {
			mediaDevices: { getUserMedia: () => new Promise<MediaStream>((ok) => grants.push(ok)) },
			userAgent: 'test'
		});
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		const asked = voice.setOpenMic(true);
		voice.setAway(true);
		voice.setAway(false);
		await vi.waitFor(() => expect(grants.length).toBe(2));
		grants.forEach((g) => g(fakeStream()));
		await asked;
		await vi.waitFor(() => expect(opened).toBe(2));
		expect(live).toBe(1);
	});

	it('never opens the mic while away, even when asked', async () => {
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		voice.setAway(true);
		await voice.setOpenMic(true);
		expect(opened).toBe(0);
		expect(voice.openMic).toBe(true);
	});
});
