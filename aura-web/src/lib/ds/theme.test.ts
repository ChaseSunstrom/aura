import { describe, expect, it, vi } from 'vitest';
import { DEFAULT_THEME, THEMES } from '$lib/themes';
import { packVars, parseColour, type Pack } from './pack';
import {
	askDraft,
	chooseTheme,
	DRAFT_RETRY_MS,
	deviceTheme,
	deviceVoice,
	loadDraft,
	setDeviceTheme,
	setDeviceVoice,
	setTheme,
	theme,
	tweenFrame,
	VOICE_CHOICES,
	voiceFor,
	wearTheme,
	type Draft,
	type SwitchEnv
} from './theme.svelte';

function memoryStore() {
	const m = new Map<string, string>();
	return {
		getItem: (k: string) => m.get(k) ?? null,
		setItem: (k: string, v: string) => void m.set(k, v),
		removeItem: (k: string) => void m.delete(k)
	};
}

/** A :root whose computed colours are its pack's, under whatever is set inline. */
function fakeRoot() {
	const inline = new Map<string, string>();
	const root = {
		dataset: {} as DOMStringMap,
		style: {
			setProperty: (n: string, v: string) => void inline.set(n, v),
			removeProperty: (n: string) => (inline.delete(n), '')
		}
	};
	const read = () => ({
		...packVars(THEMES[(root.dataset.theme ?? DEFAULT_THEME) as 'aura'] as unknown as Pack),
		...Object.fromEntries(inline)
	});
	return { root, inline, read };
}

const sunrise = (): Draft => {
	const pack = structuredClone(THEMES.aura) as unknown as Pack;
	pack.id = 'sunrise';
	pack.name = 'Sunrise';
	(pack.color as Record<string, string>).accent = '#ff8a3d';
	return { id: 'sunrise', name: 'Sunrise', pack };
};

describe('the theme the console wears', () => {
	it('starts on the default pack and puts the persona’s on :root', () => {
		expect(theme.id).toBe(DEFAULT_THEME);
		const root = { dataset: {} as DOMStringMap };
		setTheme('jarvis', root);
		expect(theme.id).toBe('jarvis');
		expect(root.dataset.theme).toBe('jarvis');
		expect([root.dataset.marker, root.dataset.label, root.dataset.ground]).toEqual([
			'bracket',
			'plain',
			'radial'
		]);
		setTheme('aura', root);
		expect([theme.id, root.dataset.theme]).toEqual(['aura', 'aura']);
		// AURA's look pieces hang off these: the star marker, gradient labels, the starfield.
		expect([root.dataset.marker, root.dataset.label, root.dataset.ground]).toEqual([
			'star',
			'gradient',
			'starfield'
		]);
	});

	it('keeps the worn pack in a cookie for the server to stamp, from the page’s own root only', () => {
		const documentElement = { dataset: {} as DOMStringMap };
		const doc = { documentElement, cookie: '' };
		vi.stubGlobal('document', doc);
		try {
			setTheme('jarvis', { dataset: {} as DOMStringMap });
			expect(doc.cookie).toBe('');
			setTheme('jarvis', documentElement);
			expect(doc.cookie).toBe('jv_theme=jarvis; path=/; max-age=31536000; SameSite=Lax');
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it('lets this device’s choice beat the theme the server synced', () => {
		const s = memoryStore();
		expect(deviceTheme(s)).toBeNull();
		setDeviceTheme('jarvis', s);
		expect(deviceTheme(s)).toBe('jarvis');
		const synced = { id: 'aura' as const, draft: sunrise() };
		expect(chooseTheme(synced, deviceTheme(s))).toEqual({ id: 'jarvis', draft: null });
		// "Follow persona" clears it; an unknown id stored by an older console is ignored.
		setDeviceTheme(null, s);
		expect(chooseTheme(synced, deviceTheme(s))).toBe(synced);
		s.setItem('jv.theme', 'neon');
		expect(deviceTheme(s)).toBeNull();
	});

	it('wears a draft over its base: the base’s fonts, the draft’s colours inline', () => {
		const { root, inline } = fakeRoot();
		setTheme('aura', root, sunrise());
		expect(root.dataset.theme).toBe('aura');
		expect(theme.draft?.id).toBe('sunrise');
		expect(inline.get('--jv-accent')).toBe('#ff8a3d');
		setTheme('jarvis', root);
		expect(inline.size).toBe(0);
		expect(theme.draft).toBeNull();
	});
});

describe('the live switch', () => {
	it('tweens only the colours that change, eased from one end to the other', () => {
		const aura = packVars(THEMES.aura as unknown as Pack);
		const jarvis = packVars(THEMES.jarvis as unknown as Pack);
		expect(tweenFrame(aura, jarvis, 0)['--jv-accent']).toBe(aura['--jv-accent']);
		expect(tweenFrame(aura, jarvis, 1)['--jv-accent']).toBe(jarvis['--jv-accent']);
		const mid = parseColour(tweenFrame(aura, jarvis, 0.5)['--jv-accent'])!;
		const [a, j] = [parseColour(aura['--jv-accent'])!, parseColour(jarvis['--jv-accent'])!];
		expect(mid[0]).toBeGreaterThan(Math.min(a[0], j[0]));
		expect(mid[0]).toBeLessThan(Math.max(a[0], j[0]));
		// Same in both packs: not touched.
		expect(tweenFrame(aura, aura, 0.5)).toEqual({});
	});

	it('swaps the pack inside the dissolve, tweens about 600 ms of frames, then lets go', () => {
		const { root, inline, read } = fakeRoot();
		const frames: ((now: number) => void)[] = [];
		const transition = vi.fn((run: () => void) => run());
		const env: SwitchEnv = {
			read,
			frame: (cb) => void frames.push(cb),
			now: () => 1000,
			reduced: false,
			transition
		};
		wearTheme('aura', null, root, env); // the first wear is instant: nothing to switch from
		expect(transition).not.toHaveBeenCalled();
		expect(inline.size).toBe(0);

		wearTheme('jarvis', null, root, env);
		expect(transition).toHaveBeenCalledOnce();
		expect(root.dataset.theme).toBe('jarvis'); // the font swap rides on data-theme
		const auraAccent = packVars(THEMES.aura as unknown as Pack)['--jv-accent'];
		expect(inline.get('--jv-accent')).toBe(auraAccent); // frame 0 still shows where it came from
		frames.shift()!(1300);
		const half = inline.get('--jv-accent');
		expect(half).not.toBe(auraAccent);
		frames.shift()!(1000 + 590);
		expect(frames).toHaveLength(1); // still running just under the token's 620 ms
		frames.shift()!(1000 + 620);
		expect(inline.size).toBe(0); // done: the stylesheet's own jarvis block answers
		expect(frames).toHaveLength(0);
	});

	it('a switch back before the dissolve lands wears where it was last asked to go', () => {
		const { root, read } = fakeRoot();
		const swaps: (() => void)[] = [];
		const env: SwitchEnv = { read, frame: () => {}, now: () => 0, reduced: false, transition: (run) => void swaps.push(run) };
		wearTheme('aura', null, root, { ...env, reduced: true });
		wearTheme('jarvis', null, root, env); // its swap waits on the view transition
		wearTheme('aura', null, root, env); // and back, before it lands
		swaps.forEach((swap) => swap());
		expect(root.dataset.theme).toBe('aura');
	});

	it('switches at once under reduced motion', () => {
		const { root, inline, read } = fakeRoot();
		const env: SwitchEnv = { read, frame: vi.fn(), now: () => 0, reduced: true };
		wearTheme('aura', null, root, env);
		wearTheme('jarvis', null, root, env);
		expect(root.dataset.theme).toBe('jarvis');
		expect(inline.size).toBe(0);
		expect(env.frame).not.toHaveBeenCalled();
	});
});

describe('a drafted theme from the house', () => {
	it('fetches /api/themes/<id> through the console’s proxy and admits only a valid pack', async () => {
		const pack = sunrise().pack;
		const fetchFn = vi.fn(async () => new Response(JSON.stringify(pack), { status: 200 }));
		const draft = await loadDraft('sunrise', fetchFn as unknown as typeof fetch);
		expect(fetchFn).toHaveBeenCalledWith('/hermes/api/themes/sunrise');
		expect(draft?.pack.color.accent).toBe('#ff8a3d');

		const missing = vi.fn(async () => new Response('{}', { status: 404 }));
		expect(await loadDraft('gone', missing as unknown as typeof fetch)).toBeNull();
		const hostile = { ...pack, color: { ...pack.color, accent: 'url(x)' } };
		const bad = vi.fn(async () => new Response(JSON.stringify(hostile), { status: 200 }));
		expect(await loadDraft('sunrise', bad as unknown as typeof fetch)).toBeNull();
		// A pack that answers under another id is not the one asked for.
		const other = vi.fn(async () => new Response(JSON.stringify(pack), { status: 200 }));
		expect(await loadDraft('dusk', other as unknown as typeof fetch)).toBeNull();
		const down = vi.fn(async () => {
			throw new TypeError('offline');
		});
		// The house could not answer: undefined, so the caller asks again (a 404 above is final).
		expect(await loadDraft('sunrise', down as unknown as typeof fetch)).toBeUndefined();
		const busy = vi.fn(async () => new Response('', { status: 503 }));
		expect(await loadDraft('sunrise', busy as unknown as typeof fetch)).toBeUndefined();
	});

	it('asks again until the house answers, and stops once it does or the caller leaves', async () => {
		vi.useFakeTimers();
		try {
			let calls = 0;
			const flaky = vi.fn(async () =>
				++calls < 3 ? new Response('', { status: 502 }) : new Response(JSON.stringify(sunrise().pack))
			);
			const got: (Draft | null)[] = [];
			askDraft('sunrise', (d) => got.push(d), flaky as unknown as typeof fetch);
			await vi.advanceTimersByTimeAsync(0);
			expect(got).toEqual([]);
			await vi.advanceTimersByTimeAsync(DRAFT_RETRY_MS * 2);
			expect(flaky).toHaveBeenCalledTimes(3);
			expect(got.map((d) => d?.pack.color.accent)).toEqual(['#ff8a3d']);
			await vi.advanceTimersByTimeAsync(DRAFT_RETRY_MS * 3);
			expect(flaky).toHaveBeenCalledTimes(3);

			// A 404 is an answer: null, once, no retry.
			const gone = vi.fn(async () => new Response('{}', { status: 404 }));
			const none: (Draft | null)[] = [];
			askDraft('gone', (d) => none.push(d), gone as unknown as typeof fetch);
			await vi.advanceTimersByTimeAsync(DRAFT_RETRY_MS * 2);
			expect(none).toEqual([null]);
			expect(gone).toHaveBeenCalledTimes(1);

			// Left (the id changed, the shell unmounted): no more asking, no late answer.
			const down = vi.fn(async () => new Response('', { status: 500 }));
			const late: (Draft | null)[] = [];
			const stop = askDraft('sunrise', (d) => late.push(d), down as unknown as typeof fetch);
			await vi.advanceTimersByTimeAsync(0);
			stop();
			await vi.advanceTimersByTimeAsync(DRAFT_RETRY_MS * 3);
			expect(down).toHaveBeenCalledTimes(1);
			expect(late).toEqual([]);
		} finally {
			vi.useRealTimers();
		}
	});
});

describe('the voice A/B', () => {
	it('offers three voices for AURA and keeps this device’s pick as an override', () => {
		expect(VOICE_CHOICES.aura).toHaveLength(3);
		expect(VOICE_CHOICES.aura).toContain(THEMES.aura.voice.voiceId);
		const s = memoryStore();
		expect(deviceVoice(s)).toBeNull();
		const pick = VOICE_CHOICES.aura[1];
		setDeviceVoice(pick, s);
		expect(deviceVoice(s)).toBe(pick);
		expect(voiceFor('aura', 'aura', deviceVoice(s))).toBe(pick);
		// Not one of the worn theme's: the persona's own voice answers.
		expect(voiceFor('jarvis', 'jarvis', deviceVoice(s))).toBe('jarvis');
		setDeviceVoice(null, s);
		expect(voiceFor('aura', 'aura', deviceVoice(s))).toBe('aura');
	});
});
