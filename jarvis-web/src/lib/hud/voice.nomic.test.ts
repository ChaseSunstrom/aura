import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';

// A desktop with no microphone: the open mic restoring itself at load must not raise an error
// toast every time the page opens (T-0321); pressing the mic button still says what is wrong.

beforeEach(() => {
	const noMic = Object.assign(new Error('Requested device not found'), { name: 'NotFoundError' });
	vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => Promise.reject(noMic) }, userAgent: 'test' });
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal('requestAnimationFrame', () => 1);
	vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => vi.unstubAllGlobals());

describe('the open mic with no microphone', () => {
	it('restores quietly: no error, the reason kept for the mic button', async () => {
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		await voice.restoreOpenMic();
		expect(voice.openMicError).toBe('');
		expect(voice.micUnavailable).toBe('No microphone was found.');
		expect(voice.openMic).toBe(false);
	});

	it('says so when the person presses the mic', async () => {
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		await voice.setOpenMic(true);
		expect(voice.openMicError).toBe('No microphone was found.');
	});
});
