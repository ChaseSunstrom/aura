import { describe, expect, it } from 'vitest';
import { hudWebOrigins, hudWebOriginsAttr } from './hudWebOrigins';

describe('hudWebOrigins', () => {
	it('is empty by default: every web panel stays a link card', () => {
		expect(hudWebOrigins({})).toEqual([]);
		expect(hudWebOriginsAttr({})).toBe('');
	});
	it('prefers the dedicated variable over the CORS list and normalises to protocol//host', () => {
		expect(hudWebOrigins({ AURA_CORS_ORIGINS: 'https://jarvis.example.ts.net,http://jarvis.example.ts.net:8199' }))
			.toEqual(['https://jarvis.example.ts.net', 'http://jarvis.example.ts.net:8199']);
		expect(hudWebOrigins({ AURA_HUD_WEB_ORIGINS: 'https://n8n.example.ts.net/path', AURA_CORS_ORIGINS: 'https://x' }))
			.toEqual(['https://n8n.example.ts.net']);
	});
	it('drops what it cannot parse or frame, and escapes the attribute', () => {
		expect(hudWebOrigins({ AURA_HUD_WEB_ORIGINS: 'ftp://a, not a url ,https://ok' })).toEqual(['https://ok']);
		expect(hudWebOriginsAttr({ AURA_HUD_WEB_ORIGINS: 'https://a"b' })).not.toContain('"');
	});
});
