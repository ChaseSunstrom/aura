// The worn pack's attributes for <html>, from the cookie `setTheme` keeps (T-0879).
//
// The CSP forbids an inline script, so a cold load used to paint the default pack and swap
// to the worn one once the bundle ran: an AURA-primary house flashed JARVIS. The server
// stamps the attributes on the page's first byte instead (hooks.server.ts, as `data-embed`).
//
// Not covered: a drafted pack's colours (they arrive with the draft's fetch, as before); a
// device that never set a theme gets the default, not the house's persona theme.

import { DEFAULT_THEME, THEME_IDS, type ThemeId } from '$lib/themes';
import { lookAttrs } from '$lib/ds/pack';

/**
 * ` data-theme="…" data-marker="…" data-label="…" data-ground="…"` for a cookie value, led by
 * a space so it follows the tag's last attribute. Only a known pack id counts; anything else
 * (absent, unknown, markup) is the default, and the cookie's text is never echoed.
 */
export function themeStamp(cookie: string | undefined): string {
	const id: ThemeId = (THEME_IDS as readonly (string | undefined)[]).includes(cookie)
		? (cookie as ThemeId)
		: DEFAULT_THEME;
	const a = lookAttrs(id);
	return ` data-theme="${a.theme}" data-marker="${a.marker}" data-label="${a.label}" data-ground="${a.ground}"`;
}
