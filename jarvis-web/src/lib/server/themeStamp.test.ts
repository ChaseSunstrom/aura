import { describe, expect, it } from 'vitest';
import { THEME_IDS } from '$lib/themes';
import { lookAttrs } from '$lib/ds/pack';
import { themeStamp } from './themeStamp';

const attrs = (id: (typeof THEME_IDS)[number]) => {
	const a = lookAttrs(id);
	return ` data-theme="${a.theme}" data-marker="${a.marker}" data-label="${a.label}" data-ground="${a.ground}"`;
};

describe('themeStamp', () => {
	it('turns every shipped pack id into the four attributes setTheme sets', () => {
		for (const id of THEME_IDS) {
			expect(themeStamp(id)).toBe(attrs(id));
			expect(themeStamp(id)).toContain(`data-theme="${id}"`);
		}
	});
	it('is this text, letter for letter: the names app.html and the stylesheets read', () => {
		expect(themeStamp('jarvis')).toBe(' data-theme="jarvis" data-marker="bracket" data-label="plain" data-ground="radial"');
	});
	it('differs between the packs: the look pieces really come from the pack', () => {
		expect(new Set(THEME_IDS.map((id) => themeStamp(id))).size).toBe(THEME_IDS.length);
	});
	it('leaves the default for an absent or unknown cookie', () => {
		const d = themeStamp(undefined);
		expect(d).toContain('data-theme="aura"');
		expect(themeStamp('')).toBe(d);
		expect(themeStamp('nonesuch')).toBe(d);
		expect(themeStamp('AURA')).toBe(d);
	});
	it('never echoes cookie text into the page', () => {
		const evil = '"><script>alert(1)</script>';
		expect(themeStamp(evil)).toBe(themeStamp(undefined));
		expect(themeStamp(evil)).not.toContain('script');
		expect(themeStamp('jarvis" onload="x')).toBe(themeStamp(undefined));
		expect(themeStamp('__proto__')).toBe(themeStamp(undefined));
		expect(themeStamp('constructor')).toBe(themeStamp(undefined));
	});
});
