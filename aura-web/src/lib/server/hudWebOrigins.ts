// The origins a web-kind panel may be framed from. Read from the environment at request
// time so a deployment sets it with the console's other origins instead of editing the
// build: empty (the default) keeps every web panel a link card, never an iframe.
export function hudWebOrigins(env: Record<string, string | undefined>): string[] {
	const raw = (env.AURA_HUD_WEB_ORIGINS ?? env.AURA_CORS_ORIGINS ?? '').trim();
	if (!raw) return [];
	const out: string[] = [];
	for (const part of raw.split(',')) {
		const v = part.trim();
		if (!v) continue;
		try {
			const u = new URL(v);
			if (u.protocol !== 'http:' && u.protocol !== 'https:') continue;
			out.push(`${u.protocol}//${u.host}`);
		} catch {
			// an unparseable entry is dropped, not guessed at: framing the wrong origin is the failure this list prevents
		}
	}
	return Array.from(new Set(out));
}

/** The value the `jarvis-hud-web-origins` meta tag carries: comma-joined, HTML-attribute safe. */
export function hudWebOriginsAttr(env: Record<string, string | undefined>): string {
	return hudWebOrigins(env).join(',').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}
