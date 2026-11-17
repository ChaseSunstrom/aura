// A theme pack's colours as the console's custom properties, worked out in the page.
//
// `design/themes.py` does this at build time for every shipped pack (themes.css). A pack
// drafted by voice (`theme.preview` / `theme.apply`, hermes/plugins/aura-personas/drafts.py)
// arrives at run time, so the same role table lives here too; `pack.test.ts` holds the two
// to the same answer for both shipped packs, so a role added there and not here fails a test
// rather than leaving a draft half-painted.
//
// A draft comes off the wire: `validPack` admits only colours this module can parse back
// into numbers, so nothing but a colour (no `url(`, no `;`) reaches an inline custom property.
//
// Not covered: the derived properties themes.css computes from the token tree (reactor-*,
// material-*, emission-*, stage-ground, scrim, the glow shadows). A draft keeps its base
// pack's; drafts change colour roles only, and those are what the console paints with.

import { DEFAULT_THEME, THEMES, THEME_IDS, type ThemeId } from '$lib/themes';

export type Rgba = [number, number, number, number];

/** What the console reads of a pack: the shipped ones and a validated draft alike. */
export interface Pack {
	id: string;
	name: string;
	color: Record<string, string | readonly string[]>;
	typography?: { display?: { family?: string }; roles?: { label?: { fill?: string } } };
	background?: {
		type?: string;
		nebulae?: readonly { color: string; at: readonly number[]; size: readonly number[] }[];
	};
	shape?: { marker?: string; toggle?: string };
	core?: { renderer?: string };
	motion?: { ms?: { stateCross?: number } };
}

const HEX = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i;
const FN = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(\d*\.?\d+)\s*)?\)$/i;

/** `#rrggbb[aa]` or `rgb[a](r, g, b[, a])` as numbers; anything else is null. */
export function parseColour(value: unknown): Rgba | null {
	if (typeof value !== 'string') return null;
	const s = value.trim();
	const h = HEX.exec(s);
	if (h) {
		const n = (i: number) => parseInt(h[1].slice(i, i + 2), 16);
		return [n(0), n(2), n(4), h[2] ? parseInt(h[2], 16) / 255 : 1];
	}
	const f = FN.exec(s);
	if (!f) return null;
	const rgb = [f[1], f[2], f[3]].map(Number);
	const a = f[4] === undefined ? 1 : Number(f[4]);
	if (rgb.some((c) => c > 255) || a > 1) return null;
	return [rgb[0], rgb[1], rgb[2], a];
}

const hh = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');

/** One colour as hex, `#rrggbbaa` when it is not opaque. */
export const hexOf = ([r, g, b, a]: Rgba): string =>
	'#' + hh(r) + hh(g) + hh(b) + (a < 1 ? hh(a * 255) : '');

/** Python's round (half to even), so a mix lands on the same byte themes.py wrote. */
function pyRound(v: number): number {
	const r = Math.round(v);
	return Math.abs(v - Math.trunc(v)) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/** `a` toward `b` by `t`, per channel, the way themes.py mixes a role it has no colour for. */
function mix(a: Rgba, b: Rgba, t: number): string {
	return hexOf([0, 1, 2].map((i) => pyRound(a[i] + (b[i] - a[i]) * t)).concat(1) as Rgba);
}

/** Two colours `t` of the way between, alpha too: the switch's tween. */
export function lerpColour(a: Rgba, b: Rgba, t: number): string {
	return hexOf(a.map((x, i) => x + (b[i] - x) * t) as Rgba);
}

type Spec = string | ['opt', string, Spec] | ['mix', string, string, number] | ['alpha', string, number];

/** design/themes.py ROLES, by the property it writes. */
const ROLES: Record<string, Spec> = {
	'--jv-bg': 'bg.void',
	'--jv-bg-raised': ['opt', 'bg.raised', 'bg.deep'],
	'--jv-panel': ['opt', 'bg.panel', 'bg.deep'],
	'--jv-panel-solid': ['opt', 'bg.panel', 'bg.deep'],
	'--jv-field': ['opt', 'bg.raised', 'bg.deep'],
	'--jv-surface-2': ['opt', 'surface.2', ['mix', 'bg.deep', 'accent', 0.12]],
	'--jv-surface-sunken': ['opt', 'surface.sunken', ['mix', 'bg.void', 'bg.deep', 0.4]],
	'--jv-core-glow': 'bg.nebula',
	'--jv-accent': 'accent',
	'--jv-accent-deep': ['opt', 'accent.deep', ['mix', 'accent', 'bg.void', 0.35]],
	'--jv-accent-lift': 'accent.soft',
	'--jv-accent-ink': 'on.accent',
	'--jv-focus': 'accent',
	'--jv-warn': 'attention',
	'--jv-ok': 'ok',
	'--jv-danger': 'danger',
	'--jv-violet': 'memory',
	'--jv-text': 'text',
	'--jv-text-bright': 'text',
	'--jv-text-dim': 'text.secondary',
	'--jv-text-faint': 'text.label',
	'--jv-tick': 'off',
	'--jv-line': ['opt', 'line', ['mix', 'bg.void', 'accent', 0.16]],
	'--jv-line-soft': ['opt', 'line.soft', ['mix', 'bg.void', 'accent', 0.11]],
	'--jv-line-hair': ['opt', 'line.hair', ['mix', 'bg.void', 'accent', 0.07]],
	'--jv-wash': ['alpha', 'accent', 0.08],
	'--jv-wash-strong': ['alpha', 'accent', 0.18],
	'--jv-glow': ['alpha', 'accent', 0.35]
};

/** The plain colour properties: the ones the live switch tweens. */
export const COLOUR_VARS: readonly string[] = Object.keys(ROLES);

/** The colour roles every pack must name; the rest have a fallback. */
const REQUIRED = [
	'bg.void',
	'bg.deep',
	'bg.nebula',
	'accent',
	'accent.soft',
	'on.accent',
	'attention',
	'ok',
	'danger',
	'memory',
	'text',
	'text.secondary',
	'text.label',
	'off'
];

function rgbaOf(pack: Pack, role: string): Rgba {
	return parseColour(pack.color[role]) ?? [0, 0, 0, 1];
}

function roleValue(pack: Pack, spec: Spec): string {
	if (typeof spec === 'string') return hexOf(rgbaOf(pack, spec));
	if (spec[0] === 'opt') return roleValue(pack, spec[1] in pack.color ? spec[1] : spec[2]);
	if (spec[0] === 'alpha') {
		const [r, g, b] = rgbaOf(pack, spec[1]);
		return hexOf([r, g, b, spec[2]]);
	}
	return mix(rgbaOf(pack, spec[1]), rgbaOf(pack, spec[2]), spec[3]);
}

function labelFill(pack: Pack): string {
	const fill = pack.typography?.roles?.label?.fill ?? '';
	const [kind, spec = ''] = fill.split(':');
	const parts = spec.split('→').map((s) => s.trim());
	const known = parts.every((p) => p in pack.color);
	if (kind === 'gradient' && parts.length === 2 && known)
		return `linear-gradient(90deg, ${roleValue(pack, parts[0])}, ${roleValue(pack, parts[1])})`;
	return roleValue(pack, kind === 'color' && known ? parts[0] : 'text.label');
}

const list = (v: unknown): readonly string[] | null => (Array.isArray(v) ? v : null);

/**
 * Every custom property a pack sets: the role colours and the `--jv-theme-*` extras
 * (themes.py `extras`). Fonts, radii and motion are not here: a draft keeps its base's.
 */
export function packVars(pack: Pack): Record<string, string> {
	const out: Record<string, string> = {};
	for (const [name, spec] of Object.entries(ROLES)) out[name] = roleValue(pack, spec);
	const c = pack.color;
	const fill = list(c['panel.fill']);
	const grad = list(c['accent.gradient']) ?? [c.accent as string, c.accent as string];
	// toFixed then unary plus: 0.24 * 100 is 24.000000000000004 in a double, and 24 in themes.py's `:g`.
	const pct = (v: number) => `${+(v * 100).toFixed(4)}%`;
	const nebulae = (pack.background?.nebulae ?? []).map(
		(n) =>
			`radial-gradient(${n.size[0]}px ${n.size[1]}px at ${pct(n.at[0])} ${pct(n.at[1])}, ` +
			`${hexOf(parseColour(n.color)!)}, transparent 70%)`
	);
	out['--jv-theme-label-fill'] = labelFill(pack);
	out['--jv-theme-accent-gradient'] =
		`linear-gradient(90deg, ${grad.map((g) => hexOf(parseColour(g)!)).join(', ')})`;
	out['--jv-theme-panel-fill'] = fill
		? `linear-gradient(180deg, ${fill.map((g) => hexOf(parseColour(g)!)).join(', ')})`
		: 'none';
	out['--jv-theme-panel-edge'] = c['panel.edge']
		? hexOf(parseColour(c['panel.edge'])!)
		: roleValue(pack, ['alpha', 'accent', 0.14]);
	out['--jv-theme-glow-text'] = c['glow.text']
		? hexOf(parseColour(c['glow.text'])!)
		: roleValue(pack, ['alpha', 'accent', 0.45]);
	out['--jv-theme-nebula'] = roleValue(pack, 'bg.nebula');
	out['--jv-theme-memory'] = roleValue(pack, 'memory');
	out['--jv-theme-backdrop'] = [...nebulae, roleValue(pack, 'bg.void')].join(', ');
	return out;
}

const ID = /^[a-z][a-z0-9-]{1,23}$/;
const isObj = (v: unknown): v is Record<string, unknown> =>
	typeof v === 'object' && v !== null && !Array.isArray(v);
const finite = (v: unknown, n: number) =>
	Array.isArray(v) && v.length === n && v.every((x) => typeof x === 'number' && Number.isFinite(x));

/**
 * A pack off the wire, or null. Every colour must parse (one colour or a list of them),
 * the required roles must be there, and nebulae must be numbers and a colour: what
 * `packVars` writes into inline style is then only what it built itself.
 */
/** The colour roles a pack may give as a list (a gradient's stops); the rest are one colour. */
const LIST_ROLES = new Set(['accent.gradient', 'panel.fill']);

export function validPack(x: unknown): Pack | null {
	if (!isObj(x) || typeof x.id !== 'string' || !ID.test(x.id) || typeof x.name !== 'string')
		return null;
	const color = x.color;
	if (!isObj(color) || !REQUIRED.every((r) => r in color)) return null;
	for (const [role, v] of Object.entries(color)) {
		// Only the list roles may be lists: `packVars` reads every other role as one colour,
		// and an array there threw inside a Shell effect (review of T-0870).
		if (Array.isArray(v) && !LIST_ROLES.has(role)) return null;
		const all = Array.isArray(v) ? v : [v];
		if (all.length === 0 || !all.every((c) => parseColour(c))) return null;
	}
	const fill = (x.typography as { roles?: { label?: { fill?: unknown } } } | undefined)?.roles?.label?.fill;
	if (fill !== undefined && typeof fill !== 'string') return null;
	const bg = x.background;
	if (bg !== undefined) {
		if (!isObj(bg)) return null;
		const nebulae = bg.nebulae ?? [];
		if (!Array.isArray(nebulae)) return null;
		const ok = nebulae.every(
			(n) => isObj(n) && parseColour(n.color) && finite(n.at, 2) && finite(n.size, 2)
		);
		if (!ok) return null;
	}
	return x as unknown as Pack;
}

/** The cookie `setTheme` keeps the worn pack's id in, for the server to stamp the first byte (T-0879). */
export const THEME_COOKIE = 'jv_theme';

/**
 * The four attributes a worn pack puts on :root. Closed sets: a draft's shape comes off the
 * wire, and only these words reach the DOM. `setTheme` and the server's `themeStamp` both
 * read it, so a cold load paints what a live switch would.
 */
export function lookAttrs(id: ThemeId, draft: Pack | null = null) {
	const pack = (draft ?? THEMES[id]) as Pack;
	return {
		theme: id,
		marker: pack.shape?.marker === 'star' ? 'star' : 'bracket',
		label: (pack.typography?.roles?.label?.fill ?? '').startsWith('gradient:')
			? 'gradient'
			: 'plain',
		ground: pack.background?.type === 'starfield' ? 'starfield' : 'radial'
	};
}

/** The shipped pack a draft was cut from: drafts keep their base's fonts, so the display face says. */
export function baseOf(pack: Pack): ThemeId {
	const family = pack.typography?.display?.family;
	return THEME_IDS.find((id) => THEMES[id].typography.display.family === family) ?? DEFAULT_THEME;
}
