/**
 * Dragging a risen menu down to close it, on a phone (the owner, 2026-11-11: "I can't drag
 * the menu down to close it"). The menu follows the finger from its grab handle or cap; let
 * go past a quarter of its height, or on a flick, and it closes; let go early and it settles
 * back.
 *
 * Does not cover: a drag that starts in the menu's body (that is the body's own scroll), a
 * wide window (the drawer at the side closes by its cross, Escape or Back), or a mouse.
 */

/** A phone: the width under which a menu rises from the foot instead of sliding from the side. */
export const RISES = '(max-width: 720px)';

/** Past this share of the menu's height, letting go closes it. */
const FAR = 0.25;
/** A flick: this many heights a second downward, once it has moved at all (a twentieth). */
const FAST = 1.2;
const MOVED = 0.05;

/**
 * Whether letting go closes the menu: `dy` px dragged down, `vy` px/ms at release, on a menu
 * `height` px tall. Pure, for its test.
 */
export function dismisses(dy: number, vy: number, height: number): boolean {
	if (!(height > 0) || !(dy > 0)) return false;
	const share = dy / height;
	return share >= FAR || (share >= MOVED && (vy * 1000) / height >= FAST);
}

/**
 * Makes `node` follow a downward drag that starts on a `[data-drag]` part of it, and calls
 * `onclose` when [dismisses] says so. The offset is left on the node when it closes, so an
 * exit animation starts from where the finger left it; `settled()` clears it.
 */
export function dragToClose(node: HTMLElement, onclose: () => void): () => void {
	let from: number | null = null;
	let dy = 0;
	let last = { y: 0, t: 0 };
	let vy = 0;

	const settled = (): void => {
		node.style.translate = '';
		node.style.transition = '';
	};
	function down(e: PointerEvent): void {
		if (e.pointerType === 'mouse' || !matchMedia(RISES).matches) return;
		if (!(e.target instanceof Element) || !e.target.closest('[data-drag]')) return;
		from = e.clientY;
		dy = 0;
		vy = 0;
		last = { y: e.clientY, t: e.timeStamp };
		node.style.transition = '';
	}
	function move(e: PointerEvent): void {
		if (from === null) return;
		dy = Math.max(0, e.clientY - from);
		const dt = e.timeStamp - last.t;
		if (dt > 0) vy = (e.clientY - last.y) / dt;
		last = { y: e.clientY, t: e.timeStamp };
		node.style.translate = `0 ${dy}px`;
	}
	function up(): void {
		if (from === null) return;
		from = null;
		if (dismisses(dy, vy, node.getBoundingClientRect().height)) {
			onclose();
			return;
		}
		if (dy === 0) return settled();
		node.style.transition = 'translate var(--jv-dur-exit) var(--jv-ease-exit)';
		node.style.translate = '0 0';
	}
	node.addEventListener('pointerdown', down);
	// On the window: the finger leaves the handle as soon as it moves.
	addEventListener('pointermove', move);
	addEventListener('pointerup', up);
	addEventListener('pointercancel', up);
	node.addEventListener('transitionend', settled);
	return () => {
		node.removeEventListener('pointerdown', down);
		removeEventListener('pointermove', move);
		removeEventListener('pointerup', up);
		removeEventListener('pointercancel', up);
		node.removeEventListener('transitionend', settled);
		settled();
	};
}
