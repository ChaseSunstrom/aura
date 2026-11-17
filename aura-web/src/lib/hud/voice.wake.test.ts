import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';

// The wake word on the open mic (T-0875): with it on, the room's talk is thrown away where it was
// heard, and only an utterance the name opened goes to the house to be transcribed. The chain
// itself is wake.test.ts's; here startWake is a fake whose `onWake` the test calls.

const wake = vi.hoisted(() => ({
	onWake: null as (() => void) | null,
	onFail: null as ((e: unknown) => void) | null,
	stop: vi.fn(),
	fail: null as Error | null,
	/** The next start alone fails: the old theme's load, while the new one's succeeds. */
	failOnce: null as Error | null,
	/** When set, a start waits for it: the models still loading. */
	loading: null as Promise<void> | null,
	starts: [] as unknown[]
}));
vi.mock('$lib/audio/wake', () => ({
	startWake: vi.fn(async (_s: MediaStream, w: unknown, onWake: () => void, onFail: (e: unknown) => void) => {
		wake.starts.push(w);
		await wake.loading;
		if (wake.failOnce) {
			const e = wake.failOnce;
			wake.failOnce = null;
			throw e;
		}
		if (wake.fail) throw wake.fail;
		wake.onWake = onWake;
		wake.onFail = onFail;
		return wake.stop;
	})
}));

const FRAME = 16;
const AURA = { model: 'assets/wake/aura.onnx', threshold: 0.95 };

async function controller(opts: { word?: typeof AURA | null } = {}) {
	const { VoiceController } = await import('./voice.svelte');
	const link = { current: { hud: { turn: { running: false } } }, interrupt: vi.fn(), submit: vi.fn() };
	const voice = new VoiceController(link as unknown as HermesLink);
	voice.setWakeWord(opts.word === undefined ? AURA : opts.word);
	const v = voice as unknown as {
		ptt: { start: () => Promise<void>; stop: () => Promise<Blob> };
		readLevel: () => number;
		openMicFrame: (now: number) => void;
		heard: (blob: Blob) => Promise<void>;
	};
	v.ptt.start = async () => {};
	v.ptt.stop = async () => new Blob(['x']);
	const sent = vi.fn(async () => {});
	v.heard = sent;
	await voice.setOpenMic(true);
	await settle();
	return { voice, v, sent };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

function feed(v: { readLevel: () => number; openMicFrame: (now: number) => void }, level: number, t0: number, ms: number): number {
	v.readLevel = () => level;
	let t = t0;
	for (; t < t0 + ms; t += FRAME) v.openMicFrame(t);
	return t;
}

/** The room, one utterance of `ms` (calling `during` halfway through), then quiet to its end. */
async function utterance(v: Parameters<typeof feed>[0], t0: number, ms = 800, during?: () => void): Promise<number> {
	let t = feed(v, 0.01, t0, 1000);
	t = feed(v, 0.3, t, ms / 2);
	await settle();
	during?.();
	t = feed(v, 0.3, t, ms / 2);
	t = feed(v, 0.01, t, 1500);
	await settle();
	return t;
}

beforeEach(() => {
	Object.assign(wake, { onWake: null, onFail: null, fail: null, failOnce: null, loading: null, starts: [] });
	wake.stop.mockClear();
	vi.stubGlobal('navigator', {
		mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop: () => {} }] }) },
		userAgent: 'test'
	});
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal('requestAnimationFrame', () => 1);
	vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => vi.unstubAllGlobals());

describe('the wake word on the open mic', () => {
	it('throws away room talk without the name, never transcribing it', async () => {
		const { voice, v, sent } = await controller();
		expect(voice.wakeListening).toBe(true);
		await utterance(v, 0);
		expect(sent).not.toHaveBeenCalled();
	});

	it('sends the utterance the name was said in, and a long command after it', async () => {
		const { voice, v, sent } = await controller();
		const t = await utterance(v, 0, 800, () => wake.onWake?.());
		expect(sent).toHaveBeenCalledTimes(1);
		// Longer than the awake window: it lapses mid-command (AWAKE_MS), and the name opened it.
		const sleep = (voice as unknown as { sleep: () => void }).sleep.bind(voice);
		await utterance(v, t, 800, () => {
			wake.onWake?.();
			sleep();
		});
		expect(voice.awake).toBe(false);
		expect(sent).toHaveBeenCalledTimes(2);
	});

	it('sends the next utterance without the name while awake (the name said alone)', async () => {
		const { voice, v, sent } = await controller();
		wake.onWake?.();
		expect(voice.awake).toBe(true);
		await utterance(v, 0);
		expect(sent).toHaveBeenCalledTimes(1);
	});

	it('sends every utterance, as before, when the wake word is off or could not load', async () => {
		const off = await controller();
		off.voice.setWakeOn(false);
		expect(wake.stop).toHaveBeenCalled();
		expect(off.voice.wakeListening).toBe(false);
		await utterance(off.v, 0);
		expect(off.sent).toHaveBeenCalledTimes(1);

		wake.fail = new Error('the house answered 503 for aura.onnx');
		const failed = await controller();
		expect(failed.voice.wakeListening).toBe(false);
		expect(failed.voice.wakeError).toContain('503');
		await utterance(failed.v, 0);
		expect(failed.sent).toHaveBeenCalledTimes(1);
	});

	it('falls open when the running chain fails, rather than holding every word back', async () => {
		const { voice, v, sent } = await controller();
		wake.onFail?.(new Error('memory access out of bounds'));
		expect(wake.stop).toHaveBeenCalled();
		expect(voice.wakeListening).toBe(false);
		expect(voice.wakeError).toContain('out of bounds');
		await utterance(v, 0);
		expect(sent).toHaveBeenCalledTimes(1);
	});

	it('does not fetch the models again at every utterance after a failure, only after a while', async () => {
		const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
		wake.fail = new Error('the house answered 503 for aura.onnx');
		const { v } = await controller();
		const t = await utterance(v, 0);
		await utterance(v, t);
		expect(wake.starts).toHaveLength(1);
		now.mockReturnValue(1_000_000 + 61_000);
		await utterance(v, t * 2);
		expect(wake.starts).toHaveLength(2);
		now.mockRestore();
	});

	it('a theme change while the models load listens for the new name, not for none', async () => {
		let loaded!: () => void;
		wake.loading = new Promise((r) => (loaded = r));
		const { voice } = await controller();
		const JARVIS = { model: 'builtin:hey_jarvis_v0.1', threshold: 0.6 };
		voice.setWakeWord(JARVIS);
		wake.loading = null;
		loaded();
		await settle();
		await settle();
		expect(wake.starts.at(-1)).toEqual(JARVIS);
		expect(voice.wakeListening).toBe(true);
	});

	it('listens for the new name when the theme changes, and stops with the open mic', async () => {
		const { voice } = await controller();
		expect(wake.starts).toHaveLength(1);
		voice.setWakeWord({ model: 'builtin:hey_jarvis_v0.1', threshold: 0.6 });
		await settle();
		expect(wake.stop).toHaveBeenCalledTimes(1);
		expect(wake.starts).toHaveLength(2);
		await voice.setOpenMic(false);
		expect(wake.stop).toHaveBeenCalledTimes(2);
		expect(voice.wakeListening).toBe(false);
	});

	it('a theme change while the old name loads, and that load fails, still listens for the new name at once', async () => {
		let loaded!: () => void;
		wake.loading = new Promise((r) => (loaded = r));
		const { voice } = await controller();
		const JARVIS = { model: 'builtin:hey_jarvis_v0.1', threshold: 0.6 };
		voice.setWakeWord(JARVIS);
		wake.loading = null;
		wake.failOnce = new Error('the house answered 503 for aura.onnx');
		loaded();
		await settle();
		await settle();
		// The failure belongs to the name no longer worn: it must not hold the new one back for WAKE_RETRY_MS.
		expect(wake.starts.at(-1)).toEqual(JARVIS);
		expect(voice.wakeListening).toBe(true);
		expect(voice.wakeError).toBe('');
	});

	it('a push the house refuses keeps the name listening: the next room talk is still thrown away', async () => {
		// The real push path: the recording is read, transcribed, then submitted to the link.
		vi.stubGlobal(
			'FileReader',
			class {
				result: string | null = null;
				onload: (() => void) | null = null;
				onerror: (() => void) | null = null;
				readAsDataURL() {
					this.result = 'data:audio/wav;base64,eA==';
					this.onload?.();
				}
			}
		);
		const transcribe = vi.fn(async () => new Response(JSON.stringify({ ok: true, transcript: 'what time is it' })));
		vi.stubGlobal('fetch', transcribe);
		const { voice, v } = await controller();
		const link = (voice as unknown as { link: { submit: () => Promise<unknown> } }).link;
		link.submit = vi.fn(async () => {
			throw new Error('the house refused the turn');
		});
		const { VoiceController } = await import('./voice.svelte');
		v.heard = (VoiceController.prototype as unknown as { heard: (b: Blob) => Promise<void> }).heard;

		const t = await utterance(v, 0, 800, () => wake.onWake?.());
		expect(transcribe).toHaveBeenCalledTimes(1);
		expect(link.submit).toHaveBeenCalledTimes(1);
		expect(voice.notice).toContain('refused the turn');
		expect(voice.awake).toBe(false);
		expect(voice.wakeListening).toBe(true);

		// Room talk after the refused push: no name, so it is never transcribed.
		await utterance(v, t);
		expect(transcribe).toHaveBeenCalledTimes(1);
		expect(link.submit).toHaveBeenCalledTimes(1);
	});

	it('never runs on the phone, whose own service listens for the name', async () => {
		vi.stubGlobal('JarvisPhone', {});
		const { voice } = await controller();
		expect(voice.wakeOn).toBe(false);
		expect(wake.starts).toHaveLength(0);
	});
});
