// Which origins a `web` panel may embed in a frame, and which get a link card.
//
// A `web` panel carries a URL the agent wants shown. Embedding an arbitrary
// origin in an iframe on the HUD is the kind of thing that reads as convenient
// right up until it frames a login page, so the surface embeds ONLY origins a
// deployment has named, and every other URL becomes a link card the person
// clicks through themselves — the safe default the protocol doc asks for.
//
// The list is a deployment fact, not a per-panel one, and the browser has no
// env of its own: it reads what the page was told. Two sources, in order —
// a `<meta name="jarvis-hud-web-origins">` the server may stamp from its
// `AURA_CORS_ORIGINS`, and a `window.__JARVIS_HUD_WEB_ORIGINS__` a test (or a
// runtime config) can set. Nothing is embedded when neither is present.

/** An origin (`protocol//host[:port]`) from any URL-ish string, lower-cased, or "". */
export function originOf(url: string): string {
	try {
		return new URL(url).origin.toLowerCase();
	} catch {
		return '';
	}
}

/** Parse a comma- or space-separated origin list into normalised origins. */
export function parseOrigins(raw: unknown): string[] {
	if (Array.isArray(raw)) return raw.map((one) => originOf(String(one))).filter(Boolean);
	if (typeof raw !== 'string') return [];
	return raw
		.split(/[\s,]+/)
		.map((one) => originOf(one))
		.filter(Boolean);
}

interface OriginHost {
	document?: { querySelector(sel: string): { getAttribute(name: string): string | null } | null };
	__JARVIS_HUD_WEB_ORIGINS__?: unknown;
}

/**
 * The origins a `web` panel may be framed at, from the meta tag then the global.
 *
 * Empty on the server and in any deployment that has named none, which is what
 * turns every `web` panel into a link card.
 */
export function webPanelOrigins(host: OriginHost | undefined = globalThis as OriginHost): string[] {
	const out = new Set<string>();
	try {
		const meta = host?.document?.querySelector('meta[name="jarvis-hud-web-origins"]');
		for (const origin of parseOrigins(meta?.getAttribute('content'))) out.add(origin);
	} catch {
		// No DOM (SSR): nothing from the meta tag.
	}
	for (const origin of parseOrigins(host?.__JARVIS_HUD_WEB_ORIGINS__)) out.add(origin);
	return [...out];
}

/** Whether a `web` panel's URL may be embedded, given the allowed origins. */
export function mayEmbed(url: string, allowed: string[]): boolean {
	const origin = originOf(url);
	return origin !== '' && allowed.includes(origin);
}
