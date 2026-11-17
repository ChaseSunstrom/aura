// Four things this file will not let slide.
//
//  1. tokens.ts and tokens.css drifting apart. They are the same table written
//     twice (one for the cascade, one for JavaScript), and "I only changed the
//     CSS" is exactly how a HUD accent ends up not matching its own glow.
//  2. Text that cannot be read. The palette is bright-on-near-black, which is
//     flattering right up until a 0.55-opacity caption drops under 4.5:1.
//  3. Text that is too small to read, which is the same failure by another
//     route and is what the scale used to be.
//  4. A colour written anywhere but here. tokens.css has always SAID that
//     nothing else may write a raw hex; nothing checked, and by the time
//     anybody looked there were eleven of them.
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { STATE_ACCENT, TOKENS, accentFor, cssVar, token, tokenMs, type TokenName } from './tokens';

const cssPath = fileURLToPath(new URL('./styles/tokens.css', import.meta.url));
const css = readFileSync(cssPath, 'utf8');
/** The source of truth every generated file is checked against. */
const source = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../design/tokens.json', import.meta.url)), 'utf8')
);

/** The `--name: value` pairs declared on `:root`, in file order. */
function parseRootTokens(source: string): Record<string, string> {
	const block = source.slice(source.indexOf(':root'));
	const body = block.slice(block.indexOf('{') + 1, block.indexOf('}'));
	const out: Record<string, string> = {};
	for (const line of body.split('\n')) {
		const trimmed = line.trim();
		if (!trimmed.startsWith('--')) continue;
		const colon = trimmed.indexOf(':');
		out[trimmed.slice(0, colon).trim()] = trimmed.slice(colon + 1).replace(/;$/, '').trim();
	}
	return out;
}

const fromCss = parseRootTokens(css);

// --- WCAG contrast ---------------------------------------------------------

function channel(v: number): number {
	const s = v / 255;
	return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
	const h = hex.replace('#', '');
	const n = parseInt(h.length === 3 ? h.replace(/(.)/g, '$1$1') : h, 16);
	// Blue went through no `channel()` for a long time — a raw 0–255 weighted at
	// 0.0722 — so every ratio here was fiction that happened to clear 4.5. The
	// numbers now match GLASS_HUD §3.2's table and WCAG's formula.
	return (
		0.2126 * channel((n >> 16) & 255) +
		0.7152 * channel((n >> 8) & 255) +
		0.0722 * channel(n & 255)
	);
}

/** Contrast ratio of `fg` over `bg`, optionally with `fg` at partial opacity. */
export function contrast(fg: string, bg: string, alpha = 1): number {
	const lf = luminance(fg) * alpha + luminance(bg) * (1 - alpha);
	const lb = luminance(bg);
	const [hi, lo] = lf > lb ? [lf, lb] : [lb, lf];
	return (hi + 0.05) / (lo + 0.05);
}

describe('design tokens', () => {
	it('exports every custom property tokens.css declares, with the same value', () => {
		expect(Object.keys(TOKENS).sort()).toEqual(Object.keys(fromCss).sort());
		for (const [name, value] of Object.entries(TOKENS)) {
			expect(fromCss[name], name).toBe(value);
		}
	});

	it('is generated from design/tokens.json, the one place a value is typed', () => {
		for (const name of ['bg', 'accent', 'warming', 'danger', 'text', 'line'] as const) {
			expect(TOKENS[`--jv-${name}`], name).toBe(source.color[name].$value);
		}
		expect(TOKENS['--jv-wash']).toMatch(/^rgba\(/);
		expect(css.includes('@generated from design/tokens.json')).toBe(true);
	});

	it('reads a duration token as milliseconds', () => {
		expect(tokenMs('--jv-dur-base')).toBe(260);
		expect(tokenMs('--jv-rx-level')).toBe(3400);
		// A colour is a valid token name and still not a duration: the check is at runtime.
		expect(() => tokenMs('--jv-accent')).toThrow(/not a duration/);
	});

	it('names every token in the --jv- namespace', () => {
		for (const name of Object.keys(TOKENS)) expect(name.startsWith('--jv-')).toBe(true);
	});

	it('resolves tokens by name and refuses a typo', () => {
		expect(token('--jv-accent')).toBe(TOKENS['--jv-accent']);
		expect(cssVar('--jv-accent')).toBe('var(--jv-accent)');
		// @ts-expect-error — the point is that the name is not a valid token
		expect(() => token('--jv-nope')).toThrow(/unknown design token/);
	});

	it('maps every reactor state to its colour C, and anything else to idle', () => {
		// GLASS_HUD §10: eleven states, every C an alias of a base colour — no hue
		// is added, and listening/thinking/tool share Reactor light.
		expect(Object.keys(STATE_ACCENT)).toEqual([
			'idle', 'listening', 'thinking', 'speaking', 'tool', 'goal',
			'attention', 'warming', 'alert', 'kill', 'offline'
		]);
		expect(accentFor('idle')).toBe(TOKENS['--jv-accent-deep']);
		expect(accentFor('listening')).toBe(TOKENS['--jv-accent']);
		expect(accentFor('thinking')).toBe(TOKENS['--jv-accent']);
		expect(accentFor('speaking')).toBe(TOKENS['--jv-accent-lift']);
		expect(accentFor('attention')).toBe(TOKENS['--jv-warn']);
		expect(accentFor('warming')).toBe(TOKENS['--jv-warming']);
		// BUILD_BRIEF §1: offline is an amber ring, never a quiet grey idle.
		expect(accentFor('offline')).toBe(TOKENS['--jv-warn']);
		expect(accentFor('nonsense')).toBe(STATE_ACCENT.idle);
		// An error is the alert state whatever the pipeline thinks it is in.
		expect(accentFor('speaking', true)).toBe(TOKENS['--jv-danger']);
	});

	it('carries the brief language: mono UI, tracked caps labels, Space Grotesk Light figures', () => {
		// BUILD_BRIEF §1. A retired token that lingers is one a component can
		// still reach for.
		const names = Object.keys(TOKENS);
		expect(names.filter((n) => n.startsWith('--jv-orb-'))).toEqual([
			'--jv-orb-substrate', '--jv-orb-housing', '--jv-orb-hub-metal'
		]);
		expect(names).not.toContain('--jv-gold');
		expect(names).not.toContain('--jv-amber');
		// The label step is v2's .22em on UPPERCASE text (the control step stays
		// the brief's .14em); the wordmark's and the readouts' hair stay.
		expect(names.filter((n) => n.startsWith('--jv-track-')).sort()).toEqual(
			['--jv-track-control', '--jv-track-label', '--jv-track-logo', '--jv-track-snug']
		);
		const em = (n: '--jv-track-label' | '--jv-track-control') => Number(TOKENS[n].replace('em', ''));
		expect(em('--jv-track-label')).toBe(0.22);
		expect(TOKENS['--jv-font-body']).toMatch(/^['"]?JetBrains Mono['"]?,/);
		expect(TOKENS['--jv-font-display']).toMatch(/^['"]?Space Grotesk['"]?,/);
		expect(TOKENS['--jv-weight-display']).toBe('300');
		expect(TOKENS['--jv-weight-strong']).toBe('600');
		// The palette, as v2 types it.
		expect(TOKENS['--jv-bg']).toBe('#010409');
		expect(TOKENS['--jv-core-glow']).toBe('#062236');
		expect(TOKENS['--jv-text']).toBe('#f2fbff');
		expect(TOKENS['--jv-text-dim']).toBe('#a9c8d8');
		expect(TOKENS['--jv-text-faint']).toBe('#8db4ca');
		expect(TOKENS['--jv-glow-text']).toBe('0 0 12px rgba(63, 208, 232, 0.45)');
		expect(TOKENS['--jv-accent']).toBe('#3fd0e8');
		expect(TOKENS['--jv-violet']).toBe('#9b8cff');
		expect(TOKENS['--jv-material-panel-solid']).toBe('rgba(4, 11, 19, 0.78)');
		expect(TOKENS['--jv-material-panel-edge']).toBe('rgba(63, 208, 232, 0.14)');
		expect(TOKENS['--jv-material-panel-radius']).toBe('3px');
		expect(TOKENS['--jv-bracket']).toBe('12px');
	});
});

describe('the type scale', () => {
	/** The smallest rem in a token's value — a clamp's floor is what it lands on. */
	function smallestRem(value: string): number {
		const found = [...value.matchAll(/([\d.]+)rem/g)].map((m) => Number(m[1]));
		return found.length ? Math.min(...found) : NaN;
	}

	it('never asks anybody to read under eleven pixels', () => {
		// `--jv-fs-2xs` was 0.55rem — 8.8px at a default root — and it is the size
		// of every pill, every entity id and every caption in the console. This is
		// the floor, in rem, at a 16px root: 0.6875rem is 11px, the top of the
		// brief's 10–11px label range (BUILD_BRIEF §1).
		for (const [name, value] of Object.entries(TOKENS)) {
			if (!name.startsWith('--jv-fs-')) continue;
			const rem = smallestRem(value);
			expect(rem, `${name} = ${value}`).not.toBeNaN();
			expect(rem, `${name} = ${value}`).toBeGreaterThanOrEqual(0.6875);
		}
	});

	it('keeps body text at least as large as the chrome around it', () => {
		const order = ['--jv-fs-2xs', '--jv-fs-xs', '--jv-fs-sm', '--jv-fs-md', '--jv-fs-lg'] as const;
		const sizes = order.map((name) => smallestRem(TOKENS[name]));
		expect([...sizes].sort((a, b) => a - b)).toEqual(sizes);
		// The step somebody reads a sentence in, rather than a label: 14px of a
		// mono face, which sets as wide as 16px of the old proportional one.
		expect(smallestRem(TOKENS['--jv-fs-md'])).toBeGreaterThanOrEqual(0.875);
	});
});

describe('where colours may be written', () => {
	/*
	 * The rule tokens.css states, enforced.
	 *
	 * Scope: stylesheets, and the `<style>` block of every component — which is
	 * where the design system actually lives. Script bodies are deliberately not
	 * scanned: `qr.ts` emits SVG fills for a code that has to survive being
	 * photographed, a documented exception to a rule about CSS, not a quiet one
	 * about colour. (The GLSL shader that used to be the other exception is gone
	 * with M49; the reactor is an SVG whose every colour is a custom property.)
	 *
	 * Black and white pass. Neither is a palette colour: the two in the tree are
	 * a mask stencil (only its alpha is read) and the paper a QR is printed on.
	 * Everything else — including a fallback inside `var(--jv-ok, #35d08a)`, which
	 * is how a token that did not exist went unnoticed for as long as it did —
	 * has to be a token.
	 */
	const srcDir = fileURLToPath(new URL('..', import.meta.url));
	const NEUTRAL = /^#(000|fff|000000|ffffff)$/i;

	function stylesheets(): { file: string; css: string }[] {
		const out: { file: string; css: string }[] = [];
		for (const entry of readdirSync(srcDir, { recursive: true, withFileTypes: true })) {
			if (!entry.isFile()) continue;
			const path = `${entry.parentPath}/${entry.name}`;
			const rel = path.slice(srcDir.length);
			// themes.css is generated from themes/*/theme.json: the packs' values are typed there.
			if (rel.endsWith('styles/tokens.css') || rel.endsWith('styles/themes.css')) continue;
			const text = readFileSync(path, 'utf8');
			if (entry.name.endsWith('.css')) {
				out.push({ file: rel, css: text });
			} else if (entry.name.endsWith('.svelte')) {
				for (const block of text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)) {
					out.push({ file: rel, css: block[1] });
				}
			}
		}
		return out;
	}

	it('finds the stylesheets it is supposed to be checking', () => {
		// A walk that silently matches nothing is a test that passes forever.
		const files = new Set(stylesheets().map((s) => s.file));
		expect(files.size).toBeGreaterThan(4);
		expect([...files].some((f) => f.endsWith('styles/base.css'))).toBe(true);
		expect([...files].some((f) => f.endsWith('.svelte'))).toBe(true);
	});

	it('has no raw hex outside tokens.css and tokens.ts', () => {
		const offences: string[] = [];
		for (const { file, css } of stylesheets()) {
			for (const line of css.split('\n')) {
				for (const hex of line.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) {
					if (NEUTRAL.test(hex[0])) continue;
					offences.push(`${file}: ${line.trim()}`);
				}
			}
		}
		expect(offences, offences.join('\n')).toEqual([]);
	});

	it('tracks a label only when it is set in capitals (BUILD_BRIEF §1)', () => {
		// The brief's labels are UPPERCASE and tracked. Tracked sentence case is
		// the failure the owner called "weird" (2026-09-24): letters spaced out
		// one by one. So a rule that reads a label or control tracking step must
		// also set the text in capitals.
		const offences: string[] = [];
		for (const { file, css } of stylesheets()) {
			for (const rule of css.matchAll(/\{([^{}]*)\}/g)) {
				if (!/var\(--jv-track-(label|control)\)/.test(rule[1])) continue;
				if (!/text-transform:\s*uppercase/.test(rule[1])) offences.push(`${file}: ${rule[1].trim().slice(0, 80)}`);
			}
		}
		expect(offences, offences.join('\n')).toEqual([]);
	});

	/*
	 * The other half of the same rule, and the one the hex check cannot see.
	 *
	 * `var(--jv-motion-reorder, 220ms)` passes every check there is. The hex
	 * scan finds no hex; `token_lint.py` deletes `var(…)` before it looks for a
	 * raw unit, so the fallback is invisible to it too; `svelte-check` and
	 * eslint do not read custom-property names at all. And the name does not
	 * exist, so what actually renders is the fallback — a duration typed by
	 * hand, in the one place the design system says a duration may not be
	 * typed. The window manager shipped eight of them (`--jv-color-focus`,
	 * `--jv-focus-ring-width`, `--jv-ease-glass`, `--jv-lit-rim` and friends),
	 * every one drawing off its fallback, and nothing anywhere went red.
	 *
	 * So: every `--jv-*` a stylesheet reads has to be a name something
	 * declares. A property written from script counts as declared — the halo's
	 * position, the parallax offset and the stagger delay are values, not
	 * tokens, and they are set with `setProperty` or `style:--jv-…` — which is
	 * why the script side of every component is scanned for the literal name.
	 */
	function declaredNames(): Set<string> {
		const declared = new Set<string>(Object.keys(TOKENS));
		for (const { css } of stylesheets()) {
			for (const m of css.matchAll(/(--jv-[a-z0-9-]+)\s*:/g)) declared.add(m[1]);
		}
		for (const entry of readdirSync(srcDir, { recursive: true, withFileTypes: true })) {
			if (!entry.isFile()) continue;
			if (!entry.name.endsWith('.ts') && !entry.name.endsWith('.svelte')) continue;
			// Not a test: a spec naming a property does not declare it, and this
			// file's own paragraph above names five that do not exist — which is
			// exactly how the first version of this check passed on the tree it
			// was written to catch.
			if (entry.name.endsWith('.test.ts')) continue;
			const text = readFileSync(`${entry.parentPath}/${entry.name}`, 'utf8');
			// The script side only: a `<style>` block's own `var()` reads are
			// what this test is checking, and would otherwise declare themselves.
			const script = text.replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
			// Quoted, or after `style:` — never a bare occurrence. This is the
			// same rule `scripts/verify/token_lint.py` applies, and the two have
			// to agree on the STRICT reading or the looser one silently decides:
			// two implementations of one check that disagree are one check.
			for (const m of script.matchAll(/(?:style:|['"`])(--jv-[a-z0-9-]+)/g)) declared.add(m[1]);
		}
		return declared;
	}

	it('reads no custom property that nothing declares', () => {
		const declared = declaredNames();
		const offences: string[] = [];
		for (const { file, css } of stylesheets()) {
			for (const line of css.split('\n')) {
				for (const use of line.matchAll(/var\(\s*(--jv-[a-z0-9-]+)/g)) {
					if (declared.has(use[1])) continue;
					offences.push(`${file}: ${use[1]} — ${line.trim()}`);
				}
			}
		}
		expect(offences, offences.join('\n')).toEqual([]);
	});
});

describe('colour contrast', () => {
	const bg = TOKENS['--jv-bg'];

	it('clears AA for body and chrome text on the page ground', () => {
		for (const name of [
			'--jv-text',
			'--jv-text-bright',
			'--jv-text-dim',
			'--jv-text-faint',
			'--jv-accent',
			'--jv-warn',
			'--jv-warming',
			'--jv-danger-text'
		] as const) {
			expect(contrast(TOKENS[name], bg), name).toBeGreaterThanOrEqual(4.5);
		}
	});

	it('still clears AA for the dimmest text at the lowest opacity the CSS uses', () => {
		// The old chrome never dropped text below 0.75; the margin stays at 0.7.
		expect(contrast(TOKENS['--jv-text-dim'], bg, 0.7)).toBeGreaterThanOrEqual(4.5);
	});

	it('gives the accent enough contrast to be a large-text/UI colour on the panel', () => {
		expect(contrast(TOKENS['--jv-accent'], TOKENS['--jv-panel-solid'])).toBeGreaterThanOrEqual(4.5);
	});

	it('keeps the "on" button readable: dark ink on the accent fill', () => {
		expect(contrast(TOKENS['--jv-accent-ink'], TOKENS['--jv-accent'])).toBeGreaterThanOrEqual(4.5);
	});
});

describe('the material', () => {
	/** `rgba(r, g, b, a)` → the four numbers. */
	function rgba(value: string): [number, number, number, number] {
		const m = /^rgba\((\d+), (\d+), (\d+), ([\d.]+)\)$/.exec(value);
		if (!m) throw new Error(`not an rgba: ${value}`);
		return [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4])];
	}
	function hex(value: string): [number, number, number] {
		const n = parseInt(value.slice(1), 16);
		return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
	}

	it('edges every grade with the accent, deepening as the surface comes forward', () => {
		// BUILD_BRIEF §1: the edge is the accent at an alpha (the panel's .14), so
		// it is pinned as an alias of the accent and not a second hex somebody typed.
		const accent = hex(TOKENS['--jv-accent']);
		for (const [grade, alpha] of [['pane', 0.1], ['panel', 0.14], ['control', 0.35]] as const) {
			const [r, g, b, a] = rgba(TOKENS[`--jv-material-${grade}-edge`]);
			expect([r, g, b], grade).toEqual(accent);
			expect(a, grade).toBe(alpha);
		}
	});

	it('uses the panel radius for panes and panels and the control radius for controls', () => {
		// BUILD_BRIEF §1: panels 3px, the boards' buttons 2px.
		expect(TOKENS['--jv-material-pane-radius']).toBe(TOKENS['--jv-radius-md']);
		expect(TOKENS['--jv-material-panel-radius']).toBe(TOKENS['--jv-radius-md']);
		expect(TOKENS['--jv-material-control-radius']).toBe(TOKENS['--jv-radius-sm']);
	});

	it('fills each grade with a palette surface at an alpha, never a second hex', () => {
		// The panel is the brief's rgba(4,11,19,.78): the panel colour, translucent.
		const panel = hex(TOKENS['--jv-panel']);
		const [r, g, b, a] = rgba(TOKENS['--jv-material-panel-solid']);
		expect([r, g, b]).toEqual(panel);
		expect(a).toBe(0.78);
		expect(rgba(TOKENS['--jv-material-pane-solid']).slice(0, 3)).toEqual(hex(TOKENS['--jv-bg-raised']));
		expect(rgba(TOKENS['--jv-material-control-solid']).slice(0, 3)).toEqual(panel);
	});

	it('keeps no blur, tint, specular or depth table to draw glass from again', () => {
		// The names a future surface might reach for. ADR-0031 deleted them from
		// design/tokens.json; this is what stops one coming back by hand.
		for (const dead of [
			'--jv-material-pane-tint', '--jv-material-panel-blur', '--jv-material-control-specular',
			'--jv-material-blur-budget', '--jv-depth-2-shadow', '--jv-depth-4-parallax',
			'--jv-light-mix', '--jv-light-rim-idle', '--jv-light-shadow-depth-step'
		]) {
			expect(TOKENS[dead as TokenName], dead).toBeUndefined();
		}
		// The reactor's own halo is not the light layer and stays.
		expect(TOKENS['--jv-light-halo-speaking']).toBeDefined();
		// The boot traces its panels in rather than sharpening them out of a blur
		// (2026-09-24): the last blur on the console is gone.
		expect('--jv-boot-blur' in TOKENS).toBe(false);
	});

	it('derives each spring\'s damping ratio from its own physics', () => {
		// The ratio is what Compose takes; it is computed by build.py, never typed,
		// so the phone's spring is the web's spring by construction.
		for (const [name, ratio] of [['glass', 0.83], ['snap', 0.77], ['control', 0.75]] as const) {
			const k = Number(TOKENS[`--jv-spring-${name}-stiffness`]);
			const c = Number(TOKENS[`--jv-spring-${name}-damping`]);
			const m = Number(TOKENS[`--jv-spring-${name}-mass`]);
			expect(Number(TOKENS[`--jv-spring-${name}-ratio`]), name).toBe(ratio);
			expect(Math.round((c / (2 * Math.sqrt(k * m))) * 100) / 100, name).toBe(ratio);
		}
		// Settle times fall as stiffness rises: a hover must land before a panel.
		expect(tokenMs('--jv-spring-glass-settle')).toBeGreaterThan(tokenMs('--jv-spring-snap-settle'));
		expect(tokenMs('--jv-spring-snap-settle')).toBeGreaterThan(tokenMs('--jv-spring-control-settle'));
	});

	it('gives every reactor state a halo brightness, and only that', () => {
		// The reactor's own core, read by its spec on both surfaces. The rim and
		// shadow that lit panels from it are gone (ADR-0031); goal is an overlay
		// with no light of its own; kill and offline turn the halo off.
		for (const state of ['idle', 'listening', 'thinking', 'speaking', 'tool', 'attention', 'warming', 'alert', 'kill', 'offline']) {
			const value = Number(TOKENS[`--jv-light-halo-${state}` as TokenName]);
			expect(value, state).toBeGreaterThanOrEqual(0);
			expect(value, state).toBeLessThanOrEqual(1);
		}
		expect(Number(TOKENS['--jv-light-halo-kill'])).toBe(0);
		expect(Number(TOKENS['--jv-light-halo-offline'])).toBe(0);
		expect(Number(TOKENS['--jv-light-halo-alert'])).toBeGreaterThan(Number(TOKENS['--jv-light-halo-idle']));
		// The scrim is Room at half (§3.2).
		const [r, g, b, a] = rgba(TOKENS['--jv-scrim']);
		expect([r, g, b]).toEqual(hex(TOKENS['--jv-bg']));
		expect(a).toBe(0.5);
	});
});
