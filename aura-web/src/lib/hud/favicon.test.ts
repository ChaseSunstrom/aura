import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { STATES } from '$lib/reactor';
import { STATE_ACCENT } from '$lib/tokens';
import { DEFAULT_THEME, THEMES, THEME_IDS } from '$lib/themes';
import { faviconFor, themeIcon, titleFor } from './favicon';

describe('the tab says what Jarvis is doing, at a glance', () => {
	it('gives every reactor state its own favicon, in its token colour', () => {
		const urls = STATES.map(faviconFor);
		expect(new Set(urls).size).toBe(STATES.length);
		for (const s of STATES) {
			const svg = decodeURIComponent(faviconFor(s).replace('data:image/svg+xml,', ''));
			expect(svg).toContain(STATE_ACCENT[s]);
		}
	});

	it('hands back the same string for the same state, so a re-render does not re-set the link', () => {
		expect(faviconFor('thinking')).toBe(faviconFor('thinking'));
	});

	it('keeps the activity title at rest and prefixes the state word otherwise', () => {
		expect(titleFor('Jarvis', 'idle', 'Ready')).toBe('Jarvis');
		expect(titleFor('Music — Jarvis', 'idle', 'Ready')).toBe('Music — Jarvis');
		expect(titleFor('Jarvis', 'thinking', 'Thinking')).toBe('Thinking · Jarvis');
		expect(titleFor('Music — Jarvis', 'attention', 'Needs you')).toBe('Needs you · Music — Jarvis');
	});

	it('rests on the worn theme’s own icon: its pack’s, copied into this package', () => {
		for (const id of THEME_IDS) {
			// aura-web/ is the image's whole build context (build:web, deploy-here): an icon read from
			// ../themes resolved to nothing there, and every deployed tab asked for "undefined".
			expect(themeIcon(id)).toBe(`/src/lib/theme-icons/${id}.ico`);
			const copy = readFileSync(new URL(`../theme-icons/${id}.ico`, import.meta.url));
			const pack = readFileSync(new URL(`../../../../themes/${id}/${THEMES[id].icons.favicon}`, import.meta.url));
			expect(copy.equals(pack), `${id}'s copy is stale: run make icons`).toBe(true);
		}
		// A draft wears its base's icon; a name no pack has gets the default's.
		expect(themeIcon('sunrise')).toBe(themeIcon(DEFAULT_THEME));
	});
});
