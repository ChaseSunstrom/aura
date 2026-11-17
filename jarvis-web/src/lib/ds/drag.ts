// A stage panel moved by hand. T-0887 let it float where it was dropped; T-0951 tiles it (the
// owner, 2026-11-10: "lock and tile like hyprland does instead of just being free floating, with
// nice animations"). A press that travels past the slop picks the panel up and it follows the
// finger, never past the stage's edge; on release the stage decides the tile it locks into
// (`tiling.ts`) and `glide` carries every panel that moved from where it was to its tile. A
// press that does not travel, or one on a control inside the panel, is left alone, so the
// panel's own taps still work. Does not cover the keyboard: the panels keep their reading order.

import { MS, prefersReducedMotion } from './motion';
import { TOKENS } from '$lib/tokens';

/** How far a press travels before it is a drag: a finger's wobble on a tap stays a tap. */
const SLOP = 8;
/** A glide's animation id: the panels' own animations are not ours to cancel. */
const GLIDE = 'jv-tile-glide';

/** [lo, hi] for one axis; on an axis the stage is too small for, the panel keeps its own place. */
const within = (v: number, lo: number, hi: number) => (lo > hi ? 0 : Math.min(Math.max(v, lo), hi));

interface Drag {
	/** Each move while held, with the pointer, for what follows the panel (the ghost, the lines). */
	moved?: (x: number, y: number) => void;
	/** The release: the stage locks the panel into a tile and glides it there. */
	dropped?: (x: number, y: number) => void;
}

export function draggable(node: HTMLElement, { moved, dropped }: Drag) {
	let press: { x: number; y: number; base: DOMRect; pointer: number } | null = null;
	let dragging = false;

	const stage = () => node.closest<HTMLElement>('[data-testid="stage"]')?.getBoundingClientRect();

	// A phone stacks the panels in one column that scrolls once they outgrow it (their glow past
	// it is not outgrowing it): a finger's vertical swipe there is the scroll's, and a sideways
	// one starts the drag. Everywhere else a press may go any way.
	const column = node.parentElement;
	// Layout offsets, not boxes: a held panel's translate and the column's scroll move its box.
	function fit() {
		const c = column;
		const outgrown = (p: HTMLElement) => p.offsetTop - (p.offsetParent === c ? 0 : c!.offsetTop) + p.offsetHeight > c!.clientHeight + 1;
		const full = c && getComputedStyle(c).overflowY === 'auto' && [...c.children].some((p) => outgrown(p as HTMLElement));
		node.style.touchAction = full ? 'pan-y' : 'none';
	}
	const watch = new ResizeObserver(fit);
	if (column) watch.observe(column);
	watch.observe(node);

	function down(e: PointerEvent) {
		if (e.button !== 0 || (e.target as Element).closest('button, a, input, select, textarea, [contenteditable]')) return;
		// A panel still gliding is picked up from its tile: the press measures the layout's box.
		for (const a of node.getAnimations()) if (a.id === GLIDE) a.cancel();
		press = { x: e.clientX, y: e.clientY, base: node.getBoundingClientRect(), pointer: e.pointerId };
		// Until the slop is passed there is no capture, and a quick flick leaves the panel before
		// it: the press is followed on the window, so the moves that start a drag are never lost.
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
	}

	function release() {
		press = null;
		window.removeEventListener('pointermove', move);
		window.removeEventListener('pointerup', up);
		window.removeEventListener('pointercancel', up);
	}

	function move(e: PointerEvent) {
		if (!press || e.pointerId !== press.pointer) return;
		const dx = e.clientX - press.x;
		const dy = e.clientY - press.y;
		if (!dragging) {
			if (Math.hypot(dx, dy) < SLOP) return;
			dragging = true;
			node.setPointerCapture(e.pointerId);
			node.dataset.dragging = '';
		}
		e.preventDefault();
		const s = stage();
		const b = press.base;
		const at = s ? [within(dx, s.left - b.left, s.right - b.right), within(dy, s.top - b.top, s.bottom - b.bottom)] : [dx, dy];
		node.style.translate = `${at[0]}px ${at[1]}px`;
		moved?.(e.clientX, e.clientY);
	}

	// The press ends wherever the finger is, and a press that went down on the panel and up
	// elsewhere is a click on what holds them both: one click is swallowed after a drag.
	function up(e: PointerEvent) {
		if (!press || e.pointerId !== press.pointer) return;
		release();
		if (!dragging) return;
		dragging = false;
		delete node.dataset.dragging;
		// The stage reads the panel where it was let go (its translate) before it locks it in.
		if (dropped) dropped(e.clientX, e.clientY);
		else glide(new Map([[node, node.getBoundingClientRect()]]));
		// The click comes in the same task as this pointerup, or not at all (released off the
		// page): the swallow goes on the next tick either way, so it never eats a later tap.
		const swallow = (c: MouseEvent) => (c.stopPropagation(), c.preventDefault());
		window.addEventListener('click', swallow, { capture: true, once: true });
		setTimeout(() => window.removeEventListener('click', swallow, { capture: true }), 0);
	}

	node.addEventListener('pointerdown', down);

	return {
		update(next: Drag) {
			moved = next.moved;
			dropped = next.dropped;
		},
		destroy() {
			watch.disconnect();
			node.removeEventListener('pointerdown', down);
			release();
		}
	};
}

/**
 * Each panel in [before] glides from the box it had there to where the layout now puts it (a
 * FLIP on its `translate`), on the motion tokens; [frame] runs each frame of it, for what is
 * drawn to the panels. Under reduced motion they are simply there. A panel whose box did not
 * change is left alone.
 */
export function glide(before: Map<HTMLElement, DOMRect>, frame?: () => void): void {
	const live = [...before].filter(([el]) => el.isConnected);
	// Every held offset and running glide off first, then every box read: the boxes the layout gives.
	for (const [el] of live) {
		for (const a of el.getAnimations()) if (a.id === GLIDE) a.cancel();
		el.style.translate = '';
	}
	const runs: Animation[] = [];
	if (!prefersReducedMotion()) {
		for (const [el, was] of live) {
			const now = el.getBoundingClientRect();
			const dx = was.left - now.left;
			const dy = was.top - now.top;
			if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
			runs.push(el.animate([{ translate: `${dx}px ${dy}px` }, { translate: '0px 0px' }], { id: GLIDE, duration: MS.row, easing: TOKENS['--jv-ease-quick'] }));
		}
	}
	frame?.();
	if (!runs.length || !frame) return;
	const each = () => {
		frame();
		if (runs.some((r) => r.playState === 'running')) requestAnimationFrame(each);
	};
	requestAnimationFrame(each);
}
