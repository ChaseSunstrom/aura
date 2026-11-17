// The segmented control under a round pack (T-0900): AURA's --jv-radius-sm is a pill, and the
// owner saw a square pressed segment cut out of the pill track. Reads the component's own CSS;
// does not cover how it renders (the console's screenshots do).

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
const css = /<style>([\s\S]*)<\/style>/.exec(read('./Segmented.svelte'))![1];
const rule = (selector: string) => new RegExp(`(^|\\n)\\t${selector.replace(/[[\]+().'*]/g, '\\$&')} \\{([^}]*)\\}`).exec(css)?.[2] ?? '';

describe('Segmented under a round pack', () => {
	it('gives each segment the track’s corners, so the pressed one is a pill in a pill', () => {
		expect(rule('.seg')).toContain('border-radius: var(--jv-radius-sm)');
		expect(rule('button')).toContain('border-radius: var(--jv-radius-sm)');
	});

	it('draws the rules between segments as straight lines that step aside for the pressed one', () => {
		// A border on a round segment bends into an arc.
		expect(rule('button + button')).not.toContain('border');
		expect(rule('button + button::before')).toContain('border-inline-start: 1px solid var(--jv-line-hair)');
		expect(css).toMatch(/button\[aria-pressed='true'\]::before,\s*button\[aria-pressed='true'\] \+ button::before \{\s*content: none;/);
	});

	it('is round only where the pack is: AURA’s small radius is a pill, JARVIS’s is not', () => {
		const themes = read('../../styles/themes.css');
		const radius = (pack: string) => new RegExp(`\\[data-theme='${pack}'\\][^{]*\\{[^}]*--jv-radius-sm: ([^;]+);`).exec(themes)?.[1];
		expect(radius('aura')).toBe('999px');
		expect(radius('jarvis')).not.toBe('999px');
	});
});
