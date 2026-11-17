// The microphone as 16 kHz mono 16-bit PCM, and PCM as a WAV.
//
// One capture for three mouths: push-to-talk, the lock screen's voice unlock
// and enrolment. It is PCM rather than MediaRecorder's opus because the
// speaker gate (jarvis-core `voice/speaker.py`) embeds WAV or raw PCM and
// nothing else, and the gate has to hear the SAME audio the transcriber
// hears — the adapter checks the transcribe request's audio against the
// enrolled voices before the words can become a prompt. An opus recording
// would transcribe and never be checked, which is a turn the gate cannot
// see: exactly the path the brief closes ("verification on every utterance,
// before the transcript reaches the agent").
//
// `ScriptProcessorNode` is deprecated and still the one capture path every
// browser the console targets shares without a worklet file to serve; the
// lock screen used it first. A context that will not run at 16 kHz gives its
// own rate and the samples are brought down to it — nearest-sample, which is
// enough for words and for a voiceprint (the embedder band-limits well under
// 8 kHz) and not for a recording anyone would keep.

/**
 * What the conversation's microphone asks the browser for: its noise suppressor,
 * echo canceller and gain control, stated rather than left to each browser's
 * default (the owner, 2026-09-25: "make the listener better at disregarding
 * background noise"). A room's hum and Jarvis's own voice through the speakers are
 * what the end-of-speech rule has to see past. The same for enrolment and the voice
 * unlock (PcmCapture), which is what keeps an enrolled voice and a heard one alike;
 * it is Chromium's default for `audio: true`, now said, not assumed.
 */
export const VOICE_CAPTURE: MediaTrackConstraints = { noiseSuppression: true, echoCancellation: true, autoGainControl: true };

export const CAPTURE_RATE = 16000;
export const CAPTURE_WIDTH = 2;

/** Nearest-sample resampling: enough for a verdict, not for a recording. */
export function resample(pcm: Int16Array, from: number, to: number): Int16Array {
	if (from === to) return pcm;
	const out = new Int16Array(Math.floor((pcm.length * to) / from));
	for (let i = 0; i < out.length; i++) out[i] = pcm[Math.min(pcm.length - 1, Math.round((i * from) / to))];
	return out;
}

/** Float samples in −1..1 to 16-bit, clipped. */
export function toInt16(chunks: readonly Float32Array[]): Int16Array {
	const total = chunks.reduce((n, c) => n + c.length, 0);
	const pcm = new Int16Array(total);
	let at = 0;
	for (const c of chunks) {
		for (let i = 0; i < c.length; i++) pcm[at++] = Math.max(-32768, Math.min(32767, Math.round(c[i] * 32767)));
	}
	return pcm;
}

/** A RIFF WAV around 16-bit mono PCM: the 44-byte header and the samples. */
export function wavBytes(pcm: Int16Array, rate = CAPTURE_RATE): Uint8Array<ArrayBuffer> {
	const bytes = pcm.length * CAPTURE_WIDTH;
	const out = new ArrayBuffer(44 + bytes);
	const v = new DataView(out);
	const ascii = (at: number, s: string) => {
		for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i));
	};
	ascii(0, 'RIFF');
	v.setUint32(4, 36 + bytes, true);
	ascii(8, 'WAVE');
	ascii(12, 'fmt ');
	v.setUint32(16, 16, true);
	v.setUint16(20, 1, true); // PCM
	v.setUint16(22, 1, true); // mono
	v.setUint32(24, rate, true);
	v.setUint32(28, rate * CAPTURE_WIDTH, true);
	v.setUint16(32, CAPTURE_WIDTH, true);
	v.setUint16(34, 8 * CAPTURE_WIDTH, true);
	ascii(36, 'data');
	v.setUint32(40, bytes, true);
	new Int16Array(out, 44).set(pcm);
	return new Uint8Array(out);
}

export function wavBlob(pcm: Int16Array, rate = CAPTURE_RATE): Blob {
	return new Blob([wavBytes(pcm, rate)], { type: 'audio/wav' });
}

/** Milliseconds of audio in `samples` at the capture rate. */
export function durationMs(samples: number, rate = CAPTURE_RATE): number {
	return (samples / rate) * 1000;
}

/**
 * The microphone, open between `start()` and `stop()`.
 *
 * `stop()` resolves with everything heard since `start()`, at the capture
 * rate. `mediaStream` is exposed for a level meter, as the recorder's was.
 * Nothing leaves the browser until the caller posts it: the button is the
 * privacy boundary as well as the control.
 */
export class PcmCapture {
	private ctx: AudioContext | null = null;
	private source: MediaStreamAudioSourceNode | null = null;
	private node: ScriptProcessorNode | null = null;
	private stream: MediaStream | null = null;
	private chunks: Float32Array[] = [];

	get recording(): boolean {
		return this.node !== null;
	}

	get mediaStream(): MediaStream | null {
		return this.stream;
	}

	/** How much has been heard so far, for a caller that stops on silence or length. */
	get heardMs(): number {
		const rate = this.ctx?.sampleRate ?? CAPTURE_RATE;
		return durationMs(
			this.chunks.reduce((n, c) => n + c.length, 0),
			rate
		);
	}

	/**
	 * Open the microphone and record. Safe to call again while it is opening (the same
	 * opening is awaited, no second recorder), and a {@link stop} that lands meanwhile
	 * ends this recording rather than finding nothing to stop.
	 *
	 * The failure it closes (the owner, 2026-10-06): a stop during the permission wait
	 * returned "nothing recorded", the opening then finished and recorded for good, and
	 * every later utterance came out of that same buffer -- minutes of room tone and old
	 * speech interleaved with the new, so the transcript was garbage, a 3.5 s utterance
	 * took 18 s to transcribe and the speaker gate refused the owner's own voice.
	 */
	start(): Promise<void> {
		if (this.node) return Promise.resolve();
		this.opening ??= this.open().finally(() => (this.opening = null));
		return this.opening;
	}
	private opening: Promise<void> | null = null;

	private async open(): Promise<void> {
		this.stream = await navigator.mediaDevices.getUserMedia({ audio: VOICE_CAPTURE });
		const ctx = new AudioContext({ sampleRate: CAPTURE_RATE });
		const source = ctx.createMediaStreamSource(this.stream);
		const node = ctx.createScriptProcessor(4096, 1, 1);
		// This recording's own buffer: a recorder that outlived its stop can never write
		// into the next one's.
		const chunks: Float32Array[] = [];
		this.chunks = chunks;
		node.onaudioprocess = (ev) => chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
		source.connect(node);
		// A processor with no destination is never pulled; the meter reads the
		// stream, not this node, so nothing audible comes of the connection.
		node.connect(ctx.destination);
		this.ctx = ctx;
		this.source = source;
		this.node = node;
	}

	/** End the capture; resolves with what was heard, at the capture rate. */
	async stop(): Promise<Int16Array> {
		// A stop during the opening ends that recording once it exists.
		if (this.opening) await this.opening.catch(() => {});
		const { ctx, source, node } = this;
		this.ctx = null;
		this.source = null;
		this.node = null;
		if (!ctx || !node) return new Int16Array(0);
		node.disconnect();
		source?.disconnect();
		const rate = ctx.sampleRate;
		await ctx.close().catch(() => {});
		const pcm = toInt16(this.chunks);
		this.chunks = [];
		this.release();
		return resample(pcm, rate, CAPTURE_RATE);
	}

	/** Let the microphone go. */
	release(): void {
		this.stream?.getTracks().forEach((t) => t.stop());
		this.stream = null;
	}
}
