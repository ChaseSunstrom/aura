import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';

// T-0807: a persona whose voice the house has no reference clip for was a 404 and silence.
// The adapter now says it in JARVIS's voice and names the voice it used (x-jarvis-voice);
// the console says so once per voice, so a pink core speaking as JARVIS is not a mystery.

beforeEach(() => {
	vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) }, userAgent: 'test' });
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal('requestAnimationFrame', () => 1);
	vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => vi.unstubAllGlobals());

const pcm = (used: string) =>
	new Response(new Uint8Array([0, 0, 0, 0]), { status: 200, headers: { 'x-sample-rate': '24000', 'x-jarvis-voice': used } });

async function synthWith(used: string) {
	vi.stubGlobal('fetch', vi.fn(async () => pcm(used)));
	const { VoiceController } = await import('./voice.svelte');
	const voice = new VoiceController({} as HermesLink);
	const queue = (voice as unknown as { newSpeech: () => { synth: (t: string, v?: string) => Promise<unknown> } }).newSpeech();
	return { voice, say: (voiceName: string) => queue.synth('Good evening.', voiceName) };
}

describe('a voice the house lacks', () => {
	it('is said once, naming both voices, and not again for the same voice', async () => {
		const { voice, say } = await synthWith('jarvis');
		await say('aura');
		expect(voice.notice).toMatch(/aura/i);
		expect(voice.notice).toMatch(/jarvis/i);
		voice.notice = '';
		await say('aura');
		expect(voice.notice).toBe('');
	});

	it('says nothing when the voice used is the one asked for', async () => {
		const { voice, say } = await synthWith('aura');
		await say('aura');
		expect(voice.notice).toBe('');
	});
});
