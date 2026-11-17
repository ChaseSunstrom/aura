import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { THEMES } from '$lib/themes';
import { TOKENS } from '$lib/tokens';
import { baseOf, lerpColour, packVars, parseColour, validPack, type Pack } from './pack';

const css = readFileSync(new URL('../styles/themes.css', import.meta.url), 'utf8');

/** One generated block of themes.css as name → value. */
function block(id: string): Record<string, string> {
	const start = css.indexOf(`:root[data-theme='${id}'] {`);
	const body = css.slice(start, css.indexOf('}', start));
	return Object.fromEntries(
		[...body.matchAll(/(--jv-[a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()])
	);
}

/** Every colour in a value as rounded rgba, so `#b79cff14` and `rgba(183,156,255,.08)` agree. */
function norm(value: string): string {
	return value
		.replace(/#[0-9a-f]{6}(?:[0-9a-f]{2})?|rgba?\([^)]*\)/gi, (c) => {
			const p = parseColour(c);
			return p ? `[${p[0]},${p[1]},${p[2]},${Math.round(p[3] * 100)}]` : c;
		})
		.replace(/\s+/g, '');
}

const pack = (id: 'aura' | 'jarvis') => THEMES[id] as unknown as Pack;

describe('a pack worked out in the page', () => {
	it.each(['aura', 'jarvis'] as const)('matches what themes.py wrote for %s', (id) => {
		const built = block(id);
		const vars = packVars(pack(id));
		for (const [name, value] of Object.entries(vars)) {
			const want = built[name] ?? (TOKENS as Record<string, string>)[name];
			expect(want, name).toBeDefined();
			expect(norm(value), name).toBe(norm(want));
		}
		// Every extra themes.css carries is computed here, bar the duration a draft keeps.
		const extras = Object.keys(built).filter((n) => n.startsWith('--jv-theme-'));
		expect(extras.filter((n) => !(n in vars))).toEqual(['--jv-theme-state-cross']);
	});

	it('admits a drafted pack and nothing that could carry more than a colour', () => {
		const draft = structuredClone(THEMES.aura) as unknown as Record<string, unknown> & Pack;
		draft.id = 'sunrise';
		draft.name = 'Sunrise';
		(draft.color as Record<string, unknown>).accent = '#ff8a3d';
		expect(validPack(draft)?.id).toBe('sunrise');
		expect(packVars(validPack(draft)!)['--jv-accent']).toBe('#ff8a3d');
		expect(baseOf(draft)).toBe('aura');
		expect(baseOf(THEMES.jarvis as unknown as Pack)).toBe('jarvis');
		// A pack with no panel fill: the fill leads a background list (`fill, surface`), where
		// `transparent` is a colour in a non-final layer and voids the whole declaration (T-0898).
		delete (draft.color as Record<string, unknown>)['panel.fill'];
		expect(packVars(validPack(draft)!)['--jv-theme-panel-fill']).toBe('none');

		const bad = (patch: (d: Record<string, any>) => void) => {
			const d = structuredClone(draft) as Record<string, any>;
			patch(d);
			return validPack(d);
		};
		expect(bad((d) => (d.color.accent = 'url(https://example.invalid/x)'))).toBeNull();
		expect(bad((d) => (d.color.accent = '#ff8a3d; background: red'))).toBeNull();
		expect(bad((d) => (d.color['panel.fill'] = ['#000000', 'var(--x)']))).toBeNull();
		expect(bad((d) => delete d.color.accent)).toBeNull();
		expect(bad((d) => (d.id = '../aura'))).toBeNull();
		expect(bad((d) => (d.background.nebulae[0].size = ['1e3px', 2]))).toBeNull();
		// A list where one colour belongs, or a label fill that is not words, threw in packVars.
		expect(bad((d) => (d.color.accent = ['#ff8a3d', '#000000']))).toBeNull();
		expect(bad((d) => (d.typography.roles.label.fill = 7))).toBeNull();
		expect(validPack(null)).toBeNull();
	});

	it('tweens between two colours, alpha included', () => {
		const a = parseColour('#000000')!;
		const b = parseColour('rgba(255, 255, 255, 0.5)')!;
		expect(lerpColour(a, b, 0)).toBe('#000000');
		expect(lerpColour(a, b, 0.5)).toBe('#808080bf');
		expect(lerpColour(a, b, 1)).toBe('#ffffff80');
	});
});
