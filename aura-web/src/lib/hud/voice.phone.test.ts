import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';
import { PHONE_TRANSCRIPT_EVENT } from './phone';

// The phone writes down what is said to its console home (the owner, 2026-10-08: "on phone
// transcription isnt working"). The recording still goes with the words, for the speaker
// gate to hear: the phone's copy of the gate's mode can be stale, and words alone passed a
// stranger as the key holder (the commit review). Room tone with no words stays on it.

const WAV = 'data:audio/wav;base64,UklGRg==';
let posted: Record<string, unknown>[];
let submitted: string[];

function phone(hears: boolean, answer: { text?: string; error?: string }) {
	vi.stubGlobal('JarvisPhone', {
		event: () => undefined,
		hears: () => hears,
		transcribe: (id: string) =>
			queueMicrotask(() => window.dispatchEvent(new CustomEvent(PHONE_TRANSCRIPT_EVENT, { detail: { id, ...answer } })))
	});
}

async function hear() {
	const { VoiceController } = await import('./voice.svelte');
	const link = { submit: async (text: string) => (submitted.push(text), { kind: 'sent', notice: '' }) };
	const voice = new VoiceController(link as unknown as HermesLink);
	await (voice as unknown as { heard(b: Blob): Promise<void> }).heard(new Blob(['x'], { type: 'audio/wav' }));
	return voice;
}

beforeEach(() => {
	posted = [];
	submitted = [];
	vi.stubGlobal('window', new EventTarget());
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal(
		'FileReader',
		class {
			result = WAV;
			onload: (() => void) | null = null;
			readAsDataURL() {
				queueMicrotask(() => this.onload?.());
			}
		}
	);
	vi.stubGlobal('fetch', async (_url: string, init: { body: string }) => {
		posted.push(JSON.parse(init.body));
		return new Response(JSON.stringify({ ok: true, transcript: 'from the server', speaker: null }));
	});
});

afterEach(() => vi.unstubAllGlobals());

describe('the console home transcribing on the phone', () => {
	it("sends the audio with the phone's words, which the adapter takes instead of Whisper", async () => {
		phone(true, { text: ' lights off ' });
		await hear();
		expect(posted).toHaveLength(1);
		expect(posted[0]).toMatchObject({ data_url: WAV, transcript: 'lights off' });
	});

	it('leaves it to the server, which has the audio, when the phone fails', async () => {
		phone(true, { error: 'the speech model is not on this phone yet' });
		await hear();
		expect(posted).toHaveLength(1);
		expect(posted[0].transcript).toBeUndefined();
		expect(submitted).toEqual(['from the server']);
	});

	it('keeps room tone on the phone', async () => {
		phone(true, { text: '' });
		await hear();
		expect(posted).toEqual([]);
		expect(submitted).toEqual([]);
	});

	it('leaves it to the server without the switch, as before', async () => {
		phone(false, {});
		await hear();
		expect(posted).toHaveLength(1);
		expect(posted[0].transcript).toBeUndefined();
		expect(submitted).toEqual(['from the server']);
	});
});
