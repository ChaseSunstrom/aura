// One animation frame loop for every moving part of the Board (T-0733).
//
// Six panels with their own requestAnimationFrame each would be six loops waking
// the main thread; this is one, and it runs only while a mechanism that wants it
// is on screen, the page is visible and motion is allowed. A tick moves its
// elements by setting attributes on them directly: the data is drawn by Svelte
// once per refresh, and only the motion (packets, a hand, a pulse) runs per frame.
//
// Does not cover: drawing a still frame under reduced motion. A mechanism's
// markup is its still frame; nothing ticks.

import { prefersReducedMotion } from '../motion';

export type Tick = (seconds: number) => void;

const ticks = new Set<Tick>();
let raf = 0;
let listening = false;

function frame(now: number): void {
	raf = 0;
	if (document.hidden || !ticks.size) return;
	const t = now / 1000;
	for (const tick of ticks) tick(t);
	raf = requestAnimationFrame(frame);
}

function wake(): void {
	if (!raf && ticks.size && !document.hidden) raf = requestAnimationFrame(frame);
}

/** Run `tick` every frame until the returned function is called. */
export function onFrame(tick: Tick): () => void {
	if (prefersReducedMotion()) return () => {};
	ticks.add(tick);
	if (!listening) {
		listening = true;
		document.addEventListener('visibilitychange', wake);
	}
	wake();
	return () => {
		ticks.delete(tick);
		if (!ticks.size && raf) {
			cancelAnimationFrame(raf);
			raf = 0;
		}
	};
}

/** A Svelte action: tick only while the element is on screen. */
export function frames(node: Element, tick: Tick) {
	let current = tick;
	let off: (() => void) | null = null;
	const io = new IntersectionObserver((entries) => {
		const seen = entries.some((e) => e.isIntersecting);
		if (seen && !off) off = onFrame((t) => current(t));
		else if (!seen && off) {
			off();
			off = null;
		}
	});
	io.observe(node);
	return {
		update(next: Tick) {
			current = next;
		},
		destroy() {
			io.disconnect();
			off?.();
		}
	};
}
