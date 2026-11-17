import { describe, expect, it, vi } from 'vitest';

import type { HermesLink } from '$lib/hermes/link';

import { TOKENS, tokenMs } from '$lib/tokens';

import { ackNotes } from './earcon';

describe('ackNotes', () => {
	it('schedules two rising notes from the sound tokens, a gap apart, from the given time', () => {
		const note = tokenMs('--jv-sound-ack-note') / 1000;
		const gap = tokenMs('--jv-sound-ack-gap') / 1000;
		const gain = Number(TOKENS['--jv-sound-ack-gain']);
		const notes = ackNotes(10);
		expect(notes).toEqual([
			{ freq: Number(TOKENS['--jv-sound-ack-low']), start: 10, end: 10 + note, gain },
			{ freq: Number(TOKENS['--jv-sound-ack-high']), start: 10 + note + gap, end: 10 + 2 * note + gap, gain }
		]);
		expect(notes[1].freq).toBeGreaterThan(notes[0].freq);
	});

	it('stays quiet and short enough to sit under the first spoken word', () => {
		const notes = ackNotes(0);
		for (const n of notes) {
			expect(n.gain).toBeGreaterThan(0);
			expect(n.gain).toBeLessThanOrEqual(0.2);
		}
		// The whole earcon is over well before the ~1.3 s the first word takes.
		expect(notes.at(-1)!.end).toBeLessThan(0.5);
	});
});

describe('VoiceController.submit', () => {
	it('acknowledges a submitted prompt only when replies are spoken', async () => {
		const { Player } = await import('$lib/audio/playback');
		const tones = vi.spyOn(Player.prototype, 'tones').mockResolvedValue();
		const { VoiceController } = await import('./voice.svelte');
		const link = { submit: async () => ({ kind: 'sent', notice: '' }) } as unknown as HermesLink;
		const voice = new VoiceController(link);
		await voice.submit('lights off');
		expect(tones).not.toHaveBeenCalled();
		voice.ackWanted = () => true;
		await voice.submit('   ');
		expect(tones).not.toHaveBeenCalled();
		await voice.submit('lights off');
		expect(tones).toHaveBeenCalledWith(ackNotes);
		tones.mockRestore();
	});

	it('stays quiet over a reply still being read, and for a prefill that sent nothing', async () => {
		const { Player } = await import('$lib/audio/playback');
		const tones = vi.spyOn(Player.prototype, 'tones').mockResolvedValue();
		const { VoiceController } = await import('./voice.svelte');
		let outcome: unknown = { kind: 'sent', notice: '' };
		const link = { submit: async () => outcome } as unknown as HermesLink;
		const voice = new VoiceController(link);
		voice.ackWanted = () => true;
		voice.speaking = true;
		await voice.submit('lights off');
		expect(tones).not.toHaveBeenCalled();
		voice.speaking = false;
		outcome = { kind: 'prefill', message: '/goal ' };
		await voice.submit('/goal');
		expect(tones).not.toHaveBeenCalled();
		tones.mockRestore();
	});
});
