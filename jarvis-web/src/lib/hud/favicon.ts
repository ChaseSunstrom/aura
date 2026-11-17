// The tab's favicon and title carry the reactor's state, so a glance at the tab
// bar (or the taskbar) says whether Jarvis is listening, thinking or waiting on
// an approval without bringing the console forward.
//
// One SVG per state, built once and cached: the state changes a few times a
// turn, never per frame, so there is no canvas and no animation here. Colour
// and glyph both come from the generated token table — the colour alone is not
// enough (listening, thinking and tool share a hue), the glyph alone is not
// either (idle and warming share one); the pair is distinct for every state.
//
// Not covered: Safari ignores SVG favicons and keeps the static `.ico`.

import { STATE_ACCENT, STATE_GLYPH, TOKENS } from '$lib/tokens';
import type { ReactorStateName } from '$lib/reactor';
import { DEFAULT_THEME, THEME_IDS, type ThemeId } from '$lib/themes';

const cache = new Map<ReactorStateName, string>();

/** The favicon for a reactor state, as an SVG data URL (the same string each call). */
export function faviconFor(state: ReactorStateName): string {
	let url = cache.get(state);
	if (url) return url;
	const c = STATE_ACCENT[state];
	const svg =
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">` +
		`<rect width="64" height="64" rx="13" fill="${TOKENS['--jv-bg']}"/>` +
		`<circle cx="32" cy="32" r="26" fill="none" stroke="${c}" stroke-width="6"/>` +
		`<text x="32" y="33" font-size="34" text-anchor="middle" dominant-baseline="central" fill="${c}">${STATE_GLYPH[state]}</text>` +
		`</svg>`;
	url = `data:image/svg+xml,${encodeURIComponent(svg)}`;
	cache.set(state, url);
	return url;
}

// Every pack's favicon, as the URL the build gives it. In-package copies (`make icons` writes
// them from each pack's `icons.favicon`): the image builds from jarvis-web/ alone, where a glob
// into ../themes matched nothing and the tab asked for "undefined" (T-0883).
const ICONS = import.meta.glob('../theme-icons/*.ico', {
	eager: true,
	query: '?url',
	import: 'default'
}) as Record<string, string>;

/**
 * The worn theme's icon at rest (its pack's `icons.favicon`). Not covered: a drafted
 * pack's own icon; drafts change colours only and wear their base's.
 */
export function themeIcon(id: string): string {
	const known = (THEME_IDS as readonly string[]).includes(id) ? (id as ThemeId) : DEFAULT_THEME;
	const at = (t: ThemeId) => ICONS[`../theme-icons/${t}.ico`];
	return at(known) ?? at(DEFAULT_THEME);
}

/** The activity's title at rest; the state word in front of it otherwise. */
export function titleFor(base: string, state: ReactorStateName, word: string): string {
	return state === 'idle' ? base : `${word} · ${base}`;
}
