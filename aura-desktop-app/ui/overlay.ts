/**
 * The visible path (T-0617): what the agent is about to do on this computer, drawn over every
 * screen before it does it -- the pointer gliding to the target, the target lit, a ripple on the
 * press. The app shows this window for the agent's steps and hides it on `hide`, or when the
 * agent's link drops; this page only draws. Taking over is not this page's: it never has the
 * focus or the cursor. The app's global Esc and the agent's own watch on the mouse do that.
 */
import { OVERLAY_HIDDEN, overlayFrame, type OverlayScreen, type OverlayView, type Point } from './logic.js';

const { listen } = window.__TAURI__.event;
const $ = (id: string): HTMLElement => document.getElementById(id) as HTMLElement;
/** A `hide` carries no screen; nothing it does needs one. */
const FLAT: OverlayScreen = { left: 0, top: 0, scale: 1 };
let view: OverlayView = OVERLAY_HIDDEN;

/** Put an element on a point (its centre, for `.centred`), or hide it. */
function place(el: HTMLElement, at: Point | null): void {
	el.hidden = !at;
	if (at) el.style.transform = `translate(${at[0]}px, ${at[1]}px)`;
}

/** Start an element's animation again: the same ripple twice is two presses. */
function replay(el: HTMLElement): void {
	el.classList.remove('play');
	void el.offsetWidth;
	el.classList.add('play');
}

function draw(next: OverlayView, before: OverlayView): void {
	const pointer = $('pointer');
	if (next.path && next.path !== before.path) {
		// From where the pointer is, with no transition, then glide to the target.
		pointer.classList.remove('glide');
		place(pointer, next.path.from);
		void pointer.offsetWidth;
		pointer.classList.add('glide');
	}
	const at = next.path?.to ?? null;
	place(pointer, at);

	const box = $('box');
	place(box, next.box && [next.box[0], next.box[1]]);
	if (next.box) {
		box.style.width = `${next.box[2]}px`;
		box.style.height = `${next.box[3]}px`;
	}

	place($('ripple'), next.ripple);
	if (next.ripple && next.ripple !== before.ripple) replay($('ripple'));
	place($('caret'), next.caret);

	const scroll = $('scroll');
	place(scroll, next.scroll && (next.scroll.at ?? at));
	scroll.textContent = next.scroll?.direction === 'up' ? '↑' : '↓';

	// The badge rides beside the pointer, where the owner is looking; with no pointer yet it
	// waits in the corner. It glides only once shown, so it never flies in across the screen.
	const badge = $('badge');
	badge.hidden = !next.visible;
	badge.classList.toggle('glide', before.visible);
	const keys = $('keys');
	keys.hidden = !next.keys;
	keys.textContent = next.keys ?? '';
	badge.style.transform = at ? `translate(${at[0]}px, ${at[1]}px)` : '';
	// Near the right or the bottom edge it goes to the other side of the pointer, or it
	// would run off the screen. Measured untransformed: mid-glide, the box is elsewhere.
	badge.classList.remove('flip-x', 'flip-y');
	if (at && next.visible) {
		badge.classList.toggle('flip-x', at[0] + badge.offsetLeft + badge.offsetWidth > window.innerWidth);
		badge.classList.toggle('flip-y', at[1] + badge.offsetTop + badge.offsetHeight > window.innerHeight);
	}
}

void listen<{ frame?: unknown; screen?: OverlayScreen }>('overlay', ({ payload }) => {
	const next = overlayFrame(view, payload?.frame, payload?.screen ?? FLAT);
	if (next !== view) draw(next, view);
	view = next;
});
