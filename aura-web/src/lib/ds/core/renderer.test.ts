import { describe, expect, it } from 'vitest';
import { MS } from '../motion';
import { FRAME } from './engine';
import { createCoreRenderer, rendererFor, surfaceFor } from './renderer';

type Call = [string, ...unknown[]];

/** A canvas whose context (WebGL2 or 2D, as `kinds` allows) answers everything and writes it down. */
function surfaces(kinds: string[]) {
	const calls: Call[] = [];
	const made: { kind: string; lose?: () => void }[] = [];
	const ctx = new Proxy({} as Record<string, unknown>, {
		get(target, name: string) {
			if (name === 'getShaderParameter' || name === 'getProgramParameter') return () => true;
			if (name === 'getExtension') return () => ({ loseContext: () => {} });
			if (name in target) return target[name];
			if (/^[A-Z_0-9]+$/.test(name)) return name;
			return (...args: unknown[]) => {
				calls.push([name, ...args]);
				return { addColorStop: () => {} };
			};
		},
		set(target, name: string, value) {
			target[name] = value;
			return true;
		}
	});
	const surface = () => {
		const entry: { kind: string; lose?: () => void } = { kind: '' };
		made.push(entry);
		return {
			width: 0,
			height: 0,
			getContext: (k: string) => (kinds.includes(k) ? ((entry.kind = k), ctx) : null),
			addEventListener: (name: string, fn: () => void) => {
				if (name === 'webglcontextlost') entry.lose = fn;
			},
			removeEventListener: () => {}
		} as unknown as HTMLCanvasElement;
	};
	return { surface, calls, made };
}

const size = { width: 300, height: 300, dpr: 1 };

describe('the core renderer interface', () => {
	it('lets the theme pick the core: AURA a galaxy, JARVIS the reactor', () => {
		expect(rendererFor('aura')).toBe('galaxy');
		expect(rendererFor('jarvis')).toBe('reactor');
	});

	it('draws the galaxy on WebGL2 and drops to 2D on a fresh canvas when the context is lost', async () => {
		const s = surfaces(['webgl2', '2d']);
		let swapped = 0;
		const r = (await createCoreRenderer('galaxy', {
			surface: s.surface,
			quality: 'mini',
			state: 'idle',
			onBackend: () => (swapped += 1)
		}))!;
		expect(r.core).toBe('galaxy');
		expect(r.backend).toBe('webgl');
		r.dock({ x: 0.5, y: 0.5, scale: 1 }, size);
		r.render(FRAME);
		expect(s.calls.some((c) => c[0] === 'drawArraysInstanced')).toBe(true);
		s.made[0].lose!();
		await Promise.resolve();
		expect(r.backend).toBe('canvas2d');
		expect(swapped).toBe(1);
		expect(s.made).toHaveLength(2);
		expect(s.made[1].kind).toBe('2d');
		r.dispose();
	});

	it('changes state once, counts the beat, and freezes an exact frame', async () => {
		const s = surfaces(['2d']);
		const r = (await createCoreRenderer('galaxy', { surface: s.surface, quality: 'mini', state: 'idle' }))!;
		expect(r.backend).toBe('canvas2d');
		expect(r.setState('working', { level: 0.5 })).toBe(true);
		expect(r.setState('working', { level: 0.6 })).toBe(false);
		expect(r.changes).toBe(1);
		for (let i = 0; i < Math.ceil(MS.beat / FRAME) + 1; i++) r.render(FRAME);
		expect(r.beats).toBe(1);
		r.freeze('thinking', 'idle', 1000);
		const beats = r.beats;
		r.render(MS.beat * 2);
		expect(r.beats).toBe(beats);
		// Live again: the same state still counts as a change out of the freeze.
		expect(r.setState('thinking')).toBe(true);
		r.setQuality('desktop', true);
		r.render(FRAME);
		r.dispose();
	});

	it('puts the reactor behind the same interface', async () => {
		const s = surfaces(['2d']);
		const r = (await createCoreRenderer('reactor', {
			surface: s.surface,
			prefer: 'canvas2d',
			quality: 'desktop',
			state: 'idle'
		}))!;
		expect(r.core).toBe('reactor');
		expect(r.backend).toBe('canvas2d');
		r.dock({ x: 0.5, y: 0.5, scale: 1 }, size);
		expect(r.setState('working')).toBe(true);
		expect(r.setState('working')).toBe(false);
		for (let i = 0; i < Math.ceil(MS.beat / FRAME) + 1; i++) r.render(FRAME);
		expect(r.beats).toBe(1);
		r.freeze('alert', 'idle', 500);
		r.render(0);
		r.setQuality('mini', true);
		expect(r.setState('idle')).toBe(true);
		r.render(FRAME);
		expect(s.calls.length).toBeGreaterThan(0);
		r.dispose();
	});

	it('is null where nothing can draw', async () => {
		const s = surfaces([]);
		expect(await createCoreRenderer('galaxy', { surface: s.surface, quality: 'mini', state: 'idle' })).toBeNull();
		expect(
			await createCoreRenderer('reactor', { surface: s.surface, prefer: 'canvas2d', quality: 'mini', state: 'idle' })
		).toBeNull();
	});
});

describe('a theme switched while the reactor still loads', () => {
	it('never lets the stale build put its canvas over the newer one', () => {
		// The reactor asks for its canvas after three.js loads; a switch to the galaxy in that
		// wait left the stale reactor's canvas on the page and the core blank (review, T-0868).
		const page: string[] = [];
		let built = 1;
		let n = 0;
		const reactor = surfaceFor(() => `canvas${++n}`, (c) => page.push(c), () => built === 1);
		built = 2;
		const galaxy = surfaceFor(() => `canvas${++n}`, (c) => page.push(c), () => built === 2);
		expect(galaxy()).toBe('canvas1');
		expect(reactor()).toBe('canvas2');
		expect(page).toEqual(['canvas1']);
	});
});
