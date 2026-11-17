// The page's cores: the console's own renderers (jarvis-web/src/lib/ds/core), bundled for this
// page by site/build.mjs. Nothing here draws; it runs the renderer the console runs, in a box the
// page gives it, lets the page set the state, and swaps the galaxy (AURA) for the reactor
// (JARVIS) when the page changes face.
//
// It follows what Core.svelte does around the renderer: the loop runs only while the core is on
// screen and the tab is visible, a phone gets the phone's star budget, a docked core the mini
// budget, and under reduced motion it stands still (each change of state is one frame).
//
// Not covered: the reactor's GPU drawer (three.js would be most of the download; the page draws
// the reactor's 2D drawer, the one the console falls back to), and the live inputs (mic level,
// speech envelope, music beat), which the renderer synthesizes.

import { tokenMs } from '$lib/tokens';
import { FRAME } from '$lib/ds/core/engine';
import type { Quality } from '$lib/ds/core/galaxy';
import { createCoreRenderer, surfaceFor, type CoreKind, type CoreRenderer } from '$lib/ds/core/renderer';
import { CORE_STATES, type CoreState } from '$lib/ds/core/states';

export { CORE_STATES };
export type { CoreKind, CoreState };

export interface LiveCore {
	/** False where neither WebGL2 nor a 2D canvas would draw: the page keeps its still picture. */
	readonly drawn: boolean;
	set(state: CoreState): void;
	/** Wear the other core: the old one goes back into the dark and the new one comes out of it. */
	wear(kind: CoreKind): Promise<void>;
}

/** Canvas width in CSS px at or under which the core is drawn at up to 3x, as the console does. */
const SHARP_UNDER = 260;
/** How long the old core takes to leave before the new one is drawn. */
const LEAVE = tokenMs('--jv-dur-exit');

export async function mountCore(
	host: HTMLElement,
	kind: CoreKind,
	{ state = 'idle', docked = false }: { state?: CoreState; docked?: boolean } = {}
): Promise<LiveCore> {
	const reduce = matchMedia('(prefers-reduced-motion: reduce)');
	const coarse = matchMedia('(pointer: coarse)');
	const quality = (): Quality => (reduce.matches || docked ? 'mini' : coarse.matches ? 'phone' : 'desktop');

	let visible = true;
	let running = false;
	let raf = 0;
	let last = 0;
	let now: CoreState = state;
	let wearing = kind;
	let build = 0;

	async function make(which: CoreKind): Promise<CoreRenderer | null> {
		const mine = ++build;
		const made = await createCoreRenderer(which, {
			// A canvas that once gave a WebGL context cannot give a 2D one: each call gets a new one,
			// shown only while this build is still the newest (a quick second switch wins).
			surface: surfaceFor(
				() => {
					const canvas = document.createElement('canvas');
					canvas.setAttribute('aria-hidden', 'true');
					return canvas;
				},
				(canvas) => host.replaceChildren(canvas),
				() => mine === build
			),
			// The reactor's GPU drawer is not in this page's bundle: go straight to its 2D one.
			prefer: which === 'reactor' ? 'canvas2d' : 'auto',
			quality: quality(),
			still: reduce.matches,
			state: now
		});
		if (made && mine !== build) {
			made.dispose();
			return null;
		}
		return made;
	}

	let core = await make(kind);
	if (!core) return { drawn: false, set() {}, async wear() {} };

	function frame(at: number) {
		raf = 0;
		if (!running || !core) return;
		core.render(last ? at - last : FRAME);
		last = at;
		raf = requestAnimationFrame(frame);
	}

	function sync() {
		const on = !reduce.matches && visible && !document.hidden;
		if (on && !running) last = 0; // a paused core resumes where it was, it does not leap
		running = on;
		if (on && !raf) raf = requestAnimationFrame(frame);
		if (!on && raf) {
			cancelAnimationFrame(raf);
			raf = 0;
		}
	}

	function resize() {
		if (!core) return;
		const width = host.offsetWidth;
		const height = host.offsetHeight;
		if (!width || !height) return;
		core.dock(
			{ x: 0.5, y: 0.5, scale: 1 },
			{ width, height, dpr: Math.min(width <= SHARP_UNDER ? 3 : 2, devicePixelRatio || 1) }
		);
		if (!running) core.render(0);
	}

	const requality = () => {
		core?.setQuality(quality(), reduce.matches);
		sync();
		if (!running) core?.render(0);
	};
	reduce.addEventListener('change', requality);
	coarse.addEventListener('change', requality);
	document.addEventListener('visibilitychange', sync);
	new ResizeObserver(resize).observe(host);
	new IntersectionObserver((entries) => {
		visible = entries.some((entry) => entry.isIntersecting);
		sync();
	}).observe(host);

	core.setState(state);
	resize();
	core.render(0);
	sync();

	return {
		drawn: true,
		set(next) {
			now = next;
			if (core?.setState(next) && !running) core.render(0);
		},
		async wear(next) {
			if (next === wearing) return;
			wearing = next;
			const old = core;
			if (!reduce.matches) {
				host.dataset.leaving = '';
				await new Promise((done) => window.setTimeout(done, LEAVE));
			}
			const fresh = await make(next);
			// A later switch overtook this one: it owns the box now.
			if (wearing !== next) {
				fresh?.dispose();
				return;
			}
			if (!fresh) {
				delete host.dataset.leaving;
				return; // the old core keeps drawing rather than leave the box empty
			}
			old?.dispose();
			core = fresh;
			resize();
			core.render(0);
			sync();
			// One frame drawn first, so the new core fades up from itself and not from an empty box.
			requestAnimationFrame(() => delete host.dataset.leaving);
		}
	};
}
