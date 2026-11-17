import { describe, expect, it } from 'vitest';

import { CAPTURE_RATE, durationMs, resample, toInt16, wavBytes } from './pcm';

describe('pcm', () => {
	it('writes a WAV the gate and the transcriber both read: RIFF, 16-bit mono at the capture rate', () => {
		const pcm = new Int16Array([0, 1000, -1000, 32767, -32768]);
		const bytes = wavBytes(pcm);
		const text = (at: number, n: number) => String.fromCharCode(...bytes.subarray(at, at + n));
		const v = new DataView(bytes.buffer);
		expect(text(0, 4)).toBe('RIFF');
		expect(text(8, 4)).toBe('WAVE');
		expect(text(12, 4)).toBe('fmt ');
		expect(v.getUint16(20, true)).toBe(1); // PCM
		expect(v.getUint16(22, true)).toBe(1); // mono
		expect(v.getUint32(24, true)).toBe(CAPTURE_RATE);
		expect(v.getUint16(34, true)).toBe(16);
		expect(text(36, 4)).toBe('data');
		expect(v.getUint32(40, true)).toBe(pcm.length * 2);
		expect(v.getUint32(4, true)).toBe(36 + pcm.length * 2);
		expect(Array.from(new Int16Array(bytes.buffer, 44))).toEqual(Array.from(pcm));
	});

	it('clips float samples to 16-bit and brings a foreign rate down to the capture rate', () => {
		const pcm = toInt16([new Float32Array([0, 0.5, 2, -2])]);
		expect(Array.from(pcm)).toEqual([0, 16384, 32767, -32768]);
		// A ramp at 48 kHz (kept inside 16-bit) comes down to a third as many
		// samples that still climb from the first to the last.
		const at48k = new Int16Array(48000);
		for (let i = 0; i < at48k.length; i++) at48k[i] = Math.floor(i / 2);
		const down = resample(at48k, 48000, CAPTURE_RATE);
		expect(down.length).toBe(CAPTURE_RATE);
		expect(down[0]).toBe(0);
		expect(down[CAPTURE_RATE - 1]).toBeGreaterThan(23990);
		expect(resample(pcm, CAPTURE_RATE, CAPTURE_RATE)).toBe(pcm);
		expect(durationMs(CAPTURE_RATE * 3)).toBe(3000);
	});
});
