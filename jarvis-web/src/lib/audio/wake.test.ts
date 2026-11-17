import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import * as ort from 'onnxruntime-web';
import contract from '../../../../tests/contracts/wake_score.json';
import fixture from './fixtures/wake.json';
import { head as auraHead, model } from './fixtures/models';
import { headFile, WakeChain, WakeScore, type Run } from './wake';

async function runner(bytes: Uint8Array): Promise<Run> {
	const s = await ort.InferenceSession.create(bytes);
	return async (data, dims) =>
		(await s.run({ [s.inputNames[0]]: new ort.Tensor('float32', data, dims) }))[s.outputNames[0]]
			.data as Float32Array;
}

/** A PCM16 WAV's samples in -1..1, padded with silence as the fixture's scores were. */
function clip(file: string): Float32Array {
	const b = readFileSync(new URL(`./fixtures/${file}`, import.meta.url));
	let at = 12;
	while (b.toString('ascii', at, at + 4) !== 'data') at += 8 + b.readUInt32LE(at + 4);
	const pcm = new Int16Array(b.buffer.slice(b.byteOffset + at + 8, b.byteOffset + at + 8 + b.readUInt32LE(at + 4)));
	const x = new Float32Array(fixture.padBefore + pcm.length + fixture.padAfter);
	pcm.forEach((v, i) => (x[fixture.padBefore + i] = v / 32768));
	return x;
}

describe('the wake chain', () => {
	ort.env.wasm.numThreads = 1;

	it("scores each clip as the phone's chain does, and fires only on the name", { timeout: 120_000 }, async () => {
		const [mel, embedding, aura] = await Promise.all([
			model('melspectrogram.onnx').then(runner),
			model('embedding_model.onnx').then(runner),
			runner(auraHead())
		]);
		for (const c of fixture.clips) {
			const chain = new WakeChain(mel, embedding, aura);
			const x = clip(c.file);
			// Fed in uneven pieces, as an audio callback hands them over: the chain cuts its own steps.
			const got: number[] = [];
			for (let i = 0; i < x.length; i += 1000) got.push(...(await chain.push(x.subarray(i, i + 1000))));
			const want = c.scores.filter((v): v is number => v !== null);
			expect(got).toHaveLength(want.length);
			got.forEach((v, i) => expect(Math.abs(v - want[i]), `${c.file} step ${i}`).toBeLessThan(1e-3));

			const rule = new WakeScore(0.95);
			const fired = got.some((v, i) => rule.onScore(i * 80, v));
			expect(fired, c.file).toBe(c.fires);
		}
	});

	it('cuts 80 ms steps with the 480 samples before each, and waits for both windows to fill', async () => {
		const melIn: Float32Array[] = [];
		const mel: Run = async (data, dims) => {
			expect(dims).toEqual([1, 1760]);
			melIn.push(data.slice());
			return new Float32Array(8 * 32).fill(10); // scaled by the chain to 10 / 10 + 2 = 3
		};
		const embedding: Run = async (data, dims) => {
			expect(dims).toEqual([1, 76, 32, 1]);
			expect(data.every((v) => v === 3)).toBe(true);
			return new Float32Array(96).fill(1);
		};
		const head: Run = async (_data, dims) => {
			expect(dims).toEqual([1, 16, 96]);
			return Float32Array.of(0.5);
		};
		const chain = new WakeChain(mel, embedding, head);
		const ramp = Float32Array.from({ length: 1280 * 26 }, (_, i) => i);
		const scores = await chain.push(ramp);
		// 76 mel frames take 10 steps; 16 embeddings 15 more: the 25th step is the first score.
		expect(scores).toEqual([0.5, 0.5]);
		expect(Array.from(melIn[0].subarray(0, 480))).toEqual(new Array(480).fill(0));
		expect(melIn[1][0]).toBe(800);
		expect(melIn[1][480]).toBe(1280);
	});

	it('names the file the house serves for a theme’s wake model', () => {
		expect(headFile('assets/wake/aura.onnx')).toBe('aura.onnx');
		expect(headFile('builtin:hey_jarvis_v0.1')).toBe('hey_jarvis_v0.1.onnx');
		expect(headFile('')).toBeNull();
	});
});

describe('the fire rule', () => {
	it.each(contract.cases)('$name', (c) => {
		const rule = new WakeScore(c.threshold, c.frames, c.refractoryMs);
		const fires = c.steps.flatMap(([ms, s], i) => (rule.onScore(ms, s) ? [i] : []));
		expect(fires).toEqual(c.fires);
	});
});
