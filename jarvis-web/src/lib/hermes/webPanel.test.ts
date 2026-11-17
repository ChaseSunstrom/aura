import { describe, it, expect } from 'vitest';
import { mayEmbed, originOf, parseOrigins, webPanelOrigins } from './webPanel';

describe('originOf and parseOrigins', () => {
	it('reduces a URL to its origin, lower-cased', () => {
		expect(originOf('https://N8N.example.ts.net/path?q=1')).toBe('https://n8n.example.ts.net');
		expect(originOf('not a url')).toBe('');
	});

	it('splits a comma- or space-separated list into origins', () => {
		expect(parseOrigins('https://a.example , https://b.example:8080 junk')).toEqual([
			'https://a.example',
			'https://b.example:8080'
		]);
		expect(parseOrigins(['https://c.example/x', 'nope'])).toEqual(['https://c.example']);
		expect(parseOrigins(undefined)).toEqual([]);
	});
});

describe('mayEmbed', () => {
	const allowed = ['https://n8n.example.ts.net'];
	it('embeds an allow-listed origin and only that', () => {
		expect(mayEmbed('https://n8n.example.ts.net/workflows', allowed)).toBe(true);
		expect(mayEmbed('https://evil.example/login', allowed)).toBe(false);
		expect(mayEmbed('', allowed)).toBe(false);
	});
});

describe('webPanelOrigins', () => {
	it('reads the meta tag and the global, de-duplicated', () => {
		const host = {
			document: {
				querySelector: () => ({ getAttribute: () => 'https://a.example, https://b.example' })
			},
			__JARVIS_HUD_WEB_ORIGINS__: ['https://b.example', 'https://c.example']
		};
		expect(webPanelOrigins(host).sort()).toEqual([
			'https://a.example',
			'https://b.example',
			'https://c.example'
		]);
	});

	it('is empty with no meta tag and no global — every web panel is then a link card', () => {
		expect(webPanelOrigins({ document: { querySelector: () => null } })).toEqual([]);
	});
});
