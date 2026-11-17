// The hero's core: the console's own galaxy (jarvis-web/src/lib/ds/core), bundled for this page
// by site/build.mjs. Nothing here draws; it runs the same renderer the console runs, in a box
// the page gives it, and lets the page set the state.
//
// It follows what Core.svelte does around the renderer: the loop runs only while the core is on
// screen and the tab is visible, a phone gets the phone's star budget, and under reduced motion
// it stands still (each change of state is one frame).
//
// Not covered: the reactor (JARVIS's core needs three.js; this page wears AURA only), and the
// live inputs (mic level, speech envelope, music beat), which the renderer synthesizes.

import { FRAME } from '$lib/ds/core/engine';
import type { Quality } from '$lib/ds/core/galaxy';
import { createCoreRenderer, type CoreRenderer } from '$lib/ds/core/renderer';
import { CORE_STATES, type CoreState } from '$lib/ds/core/states';

export { CORE_STATES };
export type { CoreState };

export interface HeroCore {
	/** False where neither WebGL2 nor a 2D canvas would draw: the page shows its still picture. */
	readonly drawn: boolean;
	set(state: CoreState): void;
}

/** Canvas width in CSS px at or under which the core is drawn at up to 3x, as the console does. */
const SHARP_UNDER = 260;

export async function mountCore(host: HTMLElement, state: CoreState = 'idle'): Promise<HeroCore> {
	const reduce = matchMedia('(prefers-reduced-motion: reduce)');
	const coarse = matchMedia('(pointer: coarse)');
	const quality = (): Quality => (reduce.matches ? 'mini' : coarse.matches ? 'phone' : 'desktop');

	let visible = true;
	let running = false;
	let raf = 0;
	let last = 0;

	const core: CoreRenderer | null = await createCoreRenderer('galaxy', {
		surface: () => {
			// A canvas that once gave a WebGL context cannot give a 2D one: the fallback gets a new one.
			const made = document.createElement('canvas');
			made.setAttribute('aria-hidden', 'true');
			host.replaceChildren(made);
			return made;
		},
		quality: quality(),
		still: reduce.matches,
		state
	});
	if (!core) return { drawn: false, set() {} };

	function frame(now: number) {
		raf = 0;
		if (!running) return;
		core!.render(last ? now - last : FRAME);
		last = now;
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
		const width = host.offsetWidth;
		const height = host.offsetHeight;
		core!.dock(
			{ x: 0.5, y: 0.5, scale: 1 },
			{ width, height, dpr: Math.min(width <= SHARP_UNDER ? 3 : 2, devicePixelRatio || 1) }
		);
		if (!running) core!.render(0);
	}

	const requality = () => {
		core.setQuality(quality(), reduce.matches);
		sync();
		if (!running) core.render(0);
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
			if (core.setState(next) && !running) core.render(0);
		}
	};
}
