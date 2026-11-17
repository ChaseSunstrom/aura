// TTS playback through an AnalyserNode (orb levels) with hard stop for
// barge-in. Browser-only.

/**
 * A line of speech arriving as it is made: 16-bit little-endian mono PCM at
 * `rate`, in pieces. `pieces` is read once, in order; the player schedules
 * each piece to start where the last one ends, so the start of the line is
 * heard while the end of it does not exist yet.
 */
export interface PcmStream {
	rate: number;
	pieces: AsyncIterable<Uint8Array>;
}

/** Cut a PCM stream at a whole sample: a piece may end mid-sample, and the odd byte carries. */
export function* samplesOf(carry: { odd: Uint8Array | null }, piece: Uint8Array): Generator<Int16Array> {
	let bytes = piece;
	if (carry.odd) {
		bytes = new Uint8Array(carry.odd.length + piece.length);
		bytes.set(carry.odd);
		bytes.set(piece, carry.odd.length);
		carry.odd = null;
	}
	const whole = bytes.length - (bytes.length % 2);
	if (whole < bytes.length) carry.odd = bytes.slice(whole);
	if (!whole) return;
	// A copy, aligned: an Int16Array view needs an even byte offset and a
	// fetched chunk's underlying buffer gives no such promise.
	const aligned = new ArrayBuffer(whole);
	new Uint8Array(aligned).set(bytes.subarray(0, whole));
	yield new Int16Array(aligned);
}

/** One synthesised sine note, on the audio clock (seconds). */
export interface Note {
	freq: number;
	start: number;
	end: number;
	gain: number;
}

export class Player {
	private ctx: AudioContext | null = null;
	private analyser: AnalyserNode | null = null;
	private sources = new Set<AudioBufferSourceNode>();
	/** The PCM streams in flight, so a barge-in can end them mid-line. */
	private streams = new Set<{ stop: () => void }>();
	// `Uint8Array<ArrayBuffer>`, not a bare `Uint8Array`: since TypeScript 5.7
	// the array is generic in its buffer, a bare one widens to ArrayBufferLike,
	// and `getByteTimeDomainData` will not take a view that might be over a
	// SharedArrayBuffer.
	private timeData: Uint8Array<ArrayBuffer> | null = null;
	/** Called as each piece of speech is started (not an earcon): the latency clock's first audio. */
	onSound: () => void = () => {};

	private ensure(): AudioContext {
		if (!this.ctx) {
			this.ctx = new AudioContext();
			this.analyser = this.ctx.createAnalyser();
			this.analyser.fftSize = 256;
			this.analyser.connect(this.ctx.destination);
			this.timeData = new Uint8Array(this.analyser.fftSize);
		}
		return this.ctx;
	}

	/**
	 * Play sine notes built from `notes(now)`, fire and forget. They go to the
	 * speakers, not through the analyser, so an earcon does not light the
	 * reactor as if Jarvis were speaking. A context the browser will not
	 * resume (no gesture yet) stays silent rather than queueing the sound for
	 * later, when it would no longer mean anything.
	 */
	async tones(notes: (now: number) => Note[]): Promise<void> {
		const ctx = this.ensure();
		if (ctx.state === 'suspended') await ctx.resume().catch(() => undefined);
		if (ctx.state !== 'running') return;
		for (const n of notes(ctx.currentTime)) {
			const osc = ctx.createOscillator();
			const env = ctx.createGain();
			osc.frequency.value = n.freq;
			// A triangle envelope: a sine cut on and off clicks.
			const mid = (n.start + n.end) / 2;
			env.gain.setValueAtTime(0, n.start);
			env.gain.linearRampToValueAtTime(n.gain, mid);
			env.gain.linearRampToValueAtTime(0, n.end);
			osc.connect(env).connect(ctx.destination);
			osc.start(n.start);
			osc.stop(n.end);
		}
	}

	/** Fetch (via the /api/tts proxy), decode and play. Resolves when done. */
	async play(url: string): Promise<void> {
		const ctx = this.ensure();
		if (ctx.state === 'suspended') await ctx.resume();
		const res = await fetch(url);
		if (!res.ok) throw new Error(`tts fetch failed: ${res.status}`);
		const bytes = await res.arrayBuffer();
		const buffer = await ctx.decodeAudioData(bytes);
		return new Promise<void>((resolve) => {
			const source = ctx.createBufferSource();
			source.buffer = buffer;
			source.connect(this.analyser!);
			this.sources.add(source);
			source.onended = () => {
				this.sources.delete(source);
				resolve();
			};
			source.start();
			this.onSound();
		});
	}

	/**
	 * Decode and play bytes already in hand — a `data:` URL's payload, which
	 * the page's CSP (`connect-src 'self'`) will not let `fetch` read.
	 */
	async playBytes(bytes: ArrayBuffer): Promise<void> {
		const ctx = this.ensure();
		if (ctx.state === 'suspended') await ctx.resume();
		const buffer = await ctx.decodeAudioData(bytes.slice(0));
		return new Promise<void>((resolve) => {
			const source = ctx.createBufferSource();
			source.buffer = buffer;
			source.connect(this.analyser!);
			this.sources.add(source);
			source.onended = () => {
				this.sources.delete(source);
				resolve();
			};
			source.start();
			this.onSound();
		});
	}

	/**
	 * Play a PCM stream as it arrives. Resolves when the last piece has been
	 * heard; rejects only if nothing at all could be played.
	 *
	 * Each piece becomes one buffer scheduled at the end of the previous one,
	 * a few milliseconds ahead of the clock when the queue has run dry, so a
	 * piece that lands late after a stall joins with a gap rather than a
	 * glitch. `stopAll` ends the stream: pieces still arriving are dropped.
	 */
	async playPcm(stream: PcmStream): Promise<void> {
		const ctx = this.ensure();
		if (ctx.state === 'suspended') await ctx.resume();
		const LEAD = 0.03;
		let nextAt = 0;
		let stopped = false;
		let played = 0;
		let last: AudioBufferSourceNode | null = null;
		const handle = { stop: () => (stopped = true) };
		this.streams.add(handle);
		const carry = { odd: null as Uint8Array | null };
		try {
			for await (const piece of stream.pieces) {
				if (stopped) break;
				for (const samples of samplesOf(carry, piece)) {
					const buffer = ctx.createBuffer(1, samples.length, stream.rate);
					const channel = buffer.getChannelData(0);
					for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
					const source = ctx.createBufferSource();
					source.buffer = buffer;
					source.connect(this.analyser!);
					this.sources.add(source);
					source.onended = () => this.sources.delete(source);
					const at = Math.max(ctx.currentTime + LEAD, nextAt);
					source.start(at);
					this.onSound();
					nextAt = at + buffer.duration;
					last = source;
					played += 1;
				}
			}
		} finally {
			this.streams.delete(handle);
		}
		if (!played) throw new Error('the stream carried no audio');
		if (stopped || !last) return;
		const tail = last as AudioBufferSourceNode;
		await new Promise<void>((resolve) => {
			if (!this.sources.has(tail)) return resolve();
			const done = tail.onended;
			tail.onended = (e) => {
				if (typeof done === 'function') done.call(tail, e);
				resolve();
			};
		});
	}

	/** Current output level 0..1 (RMS of the analyser time-domain data). */
	level(): number {
		if (!this.analyser || !this.timeData) return 0;
		this.analyser.getByteTimeDomainData(this.timeData);
		let sum = 0;
		for (let i = 0; i < this.timeData.length; i++) {
			const v = (this.timeData[i] - 128) / 128;
			sum += v * v;
		}
		return Math.sqrt(sum / this.timeData.length);
	}

	get playing(): boolean {
		return this.sources.size > 0;
	}

	/** Barge-in: stop every scheduled source immediately, and every stream still arriving. */
	stopAll(): void {
		for (const st of this.streams) st.stop();
		this.streams.clear();
		for (const s of this.sources) {
			try {
				s.stop();
			} catch {
				// already stopped
			}
		}
		this.sources.clear();
	}
}
