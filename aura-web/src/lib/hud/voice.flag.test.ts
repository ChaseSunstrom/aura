import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';

// A spoken turn says it is one (T-0644): `voice: true` on prompt.submit from the mic, whatever
// the speaker gate said, and never from the text box. The row is shared with the phone and
// the adapter (`tests/contracts/voice-flag.json`); the car's /car page is this console in a
// web view, so it rides the same code.

const root = new URL('../../../../', import.meta.url);
const contract = JSON.parse(readFileSync(new URL('tests/contracts/voice-flag.json', root), 'utf8')) as {
	param: string;
	senders: Record<string, { file: string | null; needle: string | null }>;
};

let calls: unknown[][];

beforeEach(() => {
	calls = [];
	vi.stubGlobal('window', new EventTarget());
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal('FileReader', class {
		result = 'data:audio/wav;base64,UklGRg==';
		onload: (() => void) | null = null;
		readAsDataURL() {
			queueMicrotask(() => this.onload?.());
		}
	});
});
afterEach(() => vi.unstubAllGlobals());

async function controller(body: Record<string, unknown>) {
	vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ ok: true, ...body })));
	const { VoiceController } = await import('./voice.svelte');
	const link = { submit: async (...args: unknown[]) => (calls.push(args), { kind: 'sent', notice: '' }) };
	return new VoiceController(link as unknown as HermesLink) as unknown as {
		heard(b: Blob): Promise<void>;
		submit(text: string): Promise<string>;
	};
}

describe('the voice flag', () => {
	it('the mic sends it with no ticket (the gate off or refused), and with one', async () => {
		let voice = await controller({ transcript: 'lights off', speaker: null });
		await voice.heard(new Blob(['x'], { type: 'audio/wav' }));
		expect(calls[0]).toEqual(['lights off', null, null, true]);
		calls = [];
		voice = await controller({ transcript: 'lights off', speaker_ticket: 't-1' });
		await voice.heard(new Blob(['x'], { type: 'audio/wav' }));
		expect(calls[0]).toEqual(['lights off', 't-1', null, true]);
	});

	it('a typed line never sends it', async () => {
		const voice = await controller({});
		await voice.submit('lights off');
		expect(calls[0]).toEqual(['lights off', null, null, false]);
	});

	it('every console sender in the contract still contains its text', () => {
		const mine = Object.values(contract.senders).filter((s) => s.file?.startsWith('aura-web/'));
		expect(mine.length).toBeGreaterThanOrEqual(2);
		for (const s of mine) {
			expect(readFileSync(new URL(s.file as string, root), 'utf8')).toContain(s.needle as string);
		}
	});
});
