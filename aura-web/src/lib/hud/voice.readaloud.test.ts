import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';

// The owner, 2026-11-09: "instead of having it not speak by default (chat mode or whatever),
// can you have it speak by default?" Replies off the core were written unless Settings was
// switched to Read aloud, and the switch was forgotten on every reload.

const controller = async () => {
	const { VoiceController } = await import('./voice.svelte');
	return new VoiceController({ current: { hud: {} } } as unknown as HermesLink);
};

describe('replies off the core', () => {
	beforeEach(() => {
		const kept = new Map<string, string>();
		vi.stubGlobal('localStorage', { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => void kept.set(k, v) });
	});
	afterEach(() => vi.unstubAllGlobals());

	it('are read aloud by default', async () => {
		expect((await controller()).speakReplies).toBe(true);
	});

	it('stay written once this browser chose Written, and aloud again once it chose that', async () => {
		const voice = await controller();
		voice.speakReplies = false;
		expect((await controller()).speakReplies).toBe(false);
		voice.speakReplies = true;
		expect((await controller()).speakReplies).toBe(true);
	});
});
