import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';

// A transcription that worked and a send the link refused: the note said "Could not transcribe
// that", which was false (the words were heard) and hid the refusal the person needs to see
// (T-1210). The transcriber's own failure keeps its line; the send gets its own.

function refusingLink(reason: string) {
	return {
		current: { hud: { turn: { running: false } } },
		interrupt: vi.fn(async () => {}),
		submit: vi.fn(async () => {
			throw new Error(reason);
		})
	};
}

async function controller(link: ReturnType<typeof refusingLink>) {
	const { VoiceController } = await import('./voice.svelte');
	const voice = new VoiceController(link as unknown as HermesLink);
	await voice.setOpenMic(true);
	const v = voice as unknown as {
		ptt: { start: () => Promise<void>; stop: () => Promise<Blob>; mediaStream: MediaStream | null };
		heard: (blob: Blob) => Promise<void>;
		openUtterance: boolean;
	};
	v.ptt.start = async () => {};
	v.ptt.stop = async () => new Blob(['x']);
	return { voice, v };
}

function hears(transcript: string) {
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
		vi.fn(async () => new Response(JSON.stringify({ ok: true, transcript, speaker: null }), { status: 200 }))
	);
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

describe('a send refused after a good transcription (T-1210)', () => {
	it('says the words were heard and the send failed, with the reason, not that transcription failed', async () => {
		const link = refusingLink('the house refused the turn');
		const { voice, v } = await controller(link);
		hears('Turn on the lights.');
		v.openUtterance = false;
		await v.heard(new Blob(['x']));
		expect(link.submit).toHaveBeenCalledTimes(1);
		expect(voice.notice).toBe('Heard that, but could not send it: the house refused the turn');
		expect(voice.notice).not.toContain('Could not transcribe');
	});
});
