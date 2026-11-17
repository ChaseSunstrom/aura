// The wake word on the console's open mic (T-0875): the phone's openWakeWord chain
// (OnDeviceWakeWord.kt) on onnxruntime-web, and its fire rule (WakeScore.kt).
//
//   1280 samples (80 ms) + the 480 before them -> melspectrogram -> 8 mel frames of 32
//   76 mel frames                              -> embedding      -> one 96-dim vector
//   16 embeddings                              -> the theme's head -> a score in 0..1
//
// The chain is bound to the phone's by a fixture of its scores (fixtures/wake.json, from
// scripts/wake-fixture.py), the rule by tests/contracts/wake_score.json. The models come from the
// house (the phone's ModelStore source), the runtime's wasm from this build: nothing leaves.

export const SAMPLE_RATE = 16_000;
export const CHUNK_SAMPLES = 1280;
export const MEL_OVERLAP = 480;
export const MEL_BINS = 32;
export const MEL_WINDOW = 76;
export const EMBEDDING_SIZE = 96;
export const EMBEDDING_WINDOW = 16;
/** One score per step. */
export const STEP_MS = 80;
/** WakeScore.kt's: two frames in a row, then two seconds before it fires again. */
export const DEFAULT_FRAMES = 2;
export const DEFAULT_REFRACTORY_MS = 2_000;

/** One model's forward pass: flat float32 in, flat float32 out. */
export type Run = (data: Float32Array, dims: number[]) => Promise<Float32Array>;

function flat(rows: Float32Array[], width: number): Float32Array {
	const out = new Float32Array(rows.length * width);
	rows.forEach((r, i) => out.set(r, i * width));
	return out;
}

export class WakeChain {
	private readonly mels: Float32Array[] = [];
	private readonly embeddings: Float32Array[] = [];
	/** The last MEL_OVERLAP samples of the previous step; silence at the start. */
	private tail = new Float32Array(MEL_OVERLAP);
	private readonly pending = new Float32Array(CHUNK_SAMPLES);
	private held = 0;

	constructor(
		private readonly mel: Run,
		private readonly embedding: Run,
		private readonly head: Run
	) {}

	/**
	 * Feed 16 kHz mono samples in -1..1, in any lengths. Returns the head's score for each 80 ms
	 * step this completed: none until both windows fill, two seconds in. Not reentrant: the caller
	 * awaits one push before the next.
	 */
	async push(samples: Float32Array): Promise<number[]> {
		const scores: number[] = [];
		for (let i = 0; i < samples.length; ) {
			const n = Math.min(CHUNK_SAMPLES - this.held, samples.length - i);
			this.pending.set(samples.subarray(i, i + n), this.held);
			this.held += n;
			i += n;
			if (this.held < CHUNK_SAMPLES) break;
			this.held = 0;
			const score = await this.step(this.pending);
			if (score !== null) scores.push(score);
		}
		return scores;
	}

	private async step(chunk: Float32Array): Promise<number | null> {
		// The step's samples and the 480 before them, as openWakeWord streams: alone, 1280 samples
		// make 5 mel frames where 80 ms needs 8, and the phone's takes fired about half as often.
		const input = new Float32Array(MEL_OVERLAP + CHUNK_SAMPLES);
		input.set(this.tail);
		input.set(chunk, MEL_OVERLAP);
		this.tail = chunk.slice(CHUNK_SAMPLES - MEL_OVERLAP);
		const mel = await this.mel(input, [1, input.length]);
		for (let at = 0; at + MEL_BINS <= mel.length; at += MEL_BINS)
			// openWakeWord's own scaling between the stages: without it the head never fires.
			this.mels.push(Float32Array.from(mel.subarray(at, at + MEL_BINS), (v) => v / 10 + 2));
		if (this.mels.length > MEL_WINDOW) this.mels.splice(0, this.mels.length - MEL_WINDOW);
		if (this.mels.length < MEL_WINDOW) return null;

		const e = await this.embedding(flat(this.mels, MEL_BINS), [1, MEL_WINDOW, MEL_BINS, 1]);
		this.embeddings.push(e.slice(0, EMBEDDING_SIZE));
		if (this.embeddings.length > EMBEDDING_WINDOW) this.embeddings.shift();
		if (this.embeddings.length < EMBEDDING_WINDOW) return null;

		return (await this.head(flat(this.embeddings, EMBEDDING_SIZE), [1, EMBEDDING_WINDOW, EMBEDDING_SIZE]))[0];
	}
}

/** Scores to one detection: WakeScore.kt, case for case (tests/contracts/wake_score.json). */
export class WakeScore {
	private above = 0;
	private lastFiredAt = 0;

	constructor(
		private readonly threshold: number,
		private readonly frames = DEFAULT_FRAMES,
		private readonly refractoryMs = DEFAULT_REFRACTORY_MS
	) {}

	/** True once per utterance: on the frame that completes the run, not again until the refractory period has passed. */
	onScore(now: number, score: number): boolean {
		if (score < this.threshold) {
			this.above = 0;
			return false;
		}
		this.above++;
		if (this.above < this.frames) return false;
		// A clock that went back would read as inside the refractory period until it caught up.
		if (now < this.lastFiredAt) {
			this.lastFiredAt = now;
			return false;
		}
		// The run keeps counting here, so a new phrase fires the moment the period ends.
		if (this.lastFiredAt !== 0 && now - this.lastFiredAt < this.refractoryMs) return false;
		this.lastFiredAt = now;
		this.above = 0;
		return true;
	}
}

/** The file the house serves for a theme's wake model (`themes/<id>/theme.json` wake.model), or null. */
export function headFile(model: string): string | null {
	if (model.startsWith('builtin:')) return `${model.slice('builtin:'.length)}.onnx`;
	return model.split('/').pop() || null;
}

/** The house's model mirror, through the console's proxy. */
const MODELS = '/hermes/api/models/';

/**
 * Listen on `stream` for a theme's wake word until the returned stop is called. Rejects when the
 * runtime or a model cannot load, and calls `onFail` (once, then stops scoring) when a step throws
 * while running, so the caller can fall back to listening without it. Does not verify the
 * speaker: the house's speaker gate does that for the turn the wake opens.
 */
export async function startWake(
	stream: MediaStream,
	wake: { model: string; threshold: number },
	onWake: () => void,
	onFail: (e: unknown) => void,
	fetchFn: typeof fetch = fetch
): Promise<() => void> {
	const file = headFile(wake.model);
	if (!file) throw new Error('this theme has no wake model');
	// The models first: a house without them fails here, before the 14 MB runtime is fetched.
	const bytes = async (name: string) => {
		const res = await fetchFn(MODELS + encodeURIComponent(name));
		if (!res.ok) throw new Error(`the house answered ${res.status} for ${name}`);
		return new Uint8Array(await res.arrayBuffer());
	};
	const models = await Promise.all(['melspectrogram.onnx', 'embedding_model.onnx', file].map(bytes));
	const [ort, { default: wasm }] = await Promise.all([
		import('onnxruntime-web/wasm'),
		import('onnxruntime-web/ort-wasm-simd-threaded.wasm?url')
	]);
	ort.env.wasm.wasmPaths = { wasm };
	ort.env.wasm.numThreads = 1;
	// ponytail: the chain runs on the main thread beside the core (~5.7 ms of each 80 ms step);
	// ort.env.wasm.proxy moves it to a worker if the frame-budget gates ever say so.
	const sessions = await Promise.all(models.map((m) => ort.InferenceSession.create(m)));
	// Each session holds its model in the wasm heap until released: a theme switch makes three more.
	const release = () => sessions.forEach((s) => void s.release().catch(() => {}));
	const [mel, embedding, head] = sessions.map(
		(s): Run =>
			async (data, dims) =>
				(await s.run({ [s.inputNames[0]]: new ort.Tensor('float32', data, dims) }))[s.outputNames[0]]
					.data as Float32Array
	);
	const chain = new WakeChain(mel, embedding, head);
	const rule = new WakeScore(wake.threshold);

	// The context resamples the mic to 16 kHz itself (Chromium and WebKit both do).
	const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
	let source: MediaStreamAudioSourceNode;
	try {
		source = ctx.createMediaStreamSource(stream);
	} catch (e) {
		void ctx.close().catch(() => {});
		release();
		throw e;
	}
	// ponytail: ScriptProcessorNode is deprecated but runs in every webview the desktop app uses;
	// move to an AudioWorklet when one drops it.
	const tap = ctx.createScriptProcessor(2048, 1, 1);
	let queue = Promise.resolve();
	let steps = 0;
	tap.onaudioprocess = (e) => {
		const samples = e.inputBuffer.getChannelData(0).slice();
		// One push at a time (the chain is not reentrant); a step is about 6 ms of an 80 ms budget.
		queue = queue
			.then(async () => {
				for (const score of await chain.push(samples)) if (rule.onScore(steps++ * STEP_MS, score)) onWake();
			})
			.catch((e) => {
				// A step that threw leaves the chain's windows half-filled: stop scoring, say so.
				if (!tap.onaudioprocess) return;
				tap.onaudioprocess = null;
				onFail(e);
			});
	};
	source.connect(tap);
	// A ScriptProcessor runs only while connected to the output; it writes silence there.
	tap.connect(ctx.destination);
	return () => {
		tap.onaudioprocess = null;
		source.disconnect();
		tap.disconnect();
		void ctx.close().catch(() => {});
		void queue.then(release);
	};
}
