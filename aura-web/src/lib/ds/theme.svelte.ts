// What the console wears: a shipped pack by id, or a drafted one over its base (T-0861,
// T-0870), plus this device's own choices of theme and voice.
//
// `setTheme` puts a pack on :root at once: `data-theme` (themes.css swaps the colours and
// the fonts), `data-marker` / `data-label` / `data-ground` (the look pieces in $lib/ds hang
// off those), and a draft's colours inline. `wearTheme` is the live switch around it: the
// page dissolves (a view transition, where the browser has one) while every colour tweens
// from the old pack to the new over `--jv-dur-pulse`.
//
// Not covered: a draft's derived properties (pack.ts says which); a browser without view
// transitions gets the tween and an instant core swap, not the dissolve.

import { DEFAULT_THEME, THEMES, THEME_IDS, type ThemeId } from '$lib/themes';
import { tokenMs } from '$lib/tokens';
import { prefersReducedMotion } from '$lib/motion';
import VOICES from './voices.json';
import {
	COLOUR_VARS,
	THEME_COOKIE,
	lerpColour,
	lookAttrs,
	packVars,
	parseColour,
	validPack,
	type Pack
} from './pack';

export interface Draft {
	id: string;
	name: string;
	pack: Pack;
}

/** A theme as worn: the shipped base, and a draft painted over it or null. */
export interface Worn {
	id: ThemeId;
	draft: Draft | null;
}

export const theme = $state<Worn>({ id: DEFAULT_THEME, draft: null });

/** The worn theme's name, for any text that names the assistant: AURA, or JARVIS while that theme is worn (T-0942). */
export function wornName(): string {
	return (THEMES[theme.id] ?? THEMES[DEFAULT_THEME]).persona.name;
}

interface Root {
	dataset: DOMStringMap;
	style?: Pick<CSSStyleDeclaration, 'setProperty' | 'removeProperty'>;
}

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

/** localStorage, or null where there is none (SSR) or it refuses (a locked-down profile). */
function browserStore(): Store | null {
	try {
		return globalThis.localStorage ?? null;
	} catch {
		return null;
	}
}

function read(key: string, s: Store | null): string | null {
	try {
		return s?.getItem(key) ?? null;
	} catch {
		return null;
	}
}

function write(key: string, value: string | null, s: Store | null): void {
	try {
		if (value) s?.setItem(key, value);
		else s?.removeItem(key);
	} catch {
		// A full or refused store: the choice holds for this page only (`device` below).
	}
}

const THEME_KEY = 'jv.theme';
const VOICE_KEY = 'jv.voice';
const shipped = (id: string | null): id is ThemeId =>
	!!id && (THEME_IDS as readonly string[]).includes(id);

/** The theme this device wears whatever the house says, or null to follow the persona. */
export function deviceTheme(s: Store | null = browserStore()): ThemeId | null {
	const id = read(THEME_KEY, s);
	return shipped(id) ? id : null;
}

/** Three stock voices per theme for the A/B (Settings): the pack's own first. */
export const VOICE_CHOICES: Readonly<Record<string, readonly string[]>> = VOICES;

/** The voice this device picked in the A/B, or null for the persona's. */
export function deviceVoice(s: Store | null = browserStore()): string | null {
	return read(VOICE_KEY, s);
}

/** This device's overrides, as runes the shell and Settings read. */
export const device = $state({ theme: deviceTheme(), voice: deviceVoice() });

export function setDeviceTheme(id: ThemeId | null, s: Store | null = browserStore()): void {
	write(THEME_KEY, id, s);
	device.theme = id;
}

export function setDeviceVoice(id: string | null, s: Store | null = browserStore()): void {
	write(VOICE_KEY, id, s);
	device.voice = id;
}

/** The device's pick beats the synced theme (and any draft on it); null follows the house. */
export function chooseTheme(synced: Worn, override: string | null): Worn {
	return shipped(override) ? { id: override, draft: null } : synced;
}

/** The device's voice when it is one of the worn theme's choices, else the persona's. */
export function voiceFor(themeId: string, personaVoice: string, override: string | null): string {
	return override && VOICE_CHOICES[themeId]?.includes(override) ? override : personaVoice;
}

let inlined: string[] = [];

/** Set exactly `vars` inline on root: what an earlier call set and this one does not is removed. */
function paint(root: Root, vars: Record<string, string>): void {
	if (!root.style) return;
	for (const name of inlined) if (!(name in vars)) root.style.removeProperty(name);
	for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
	inlined = Object.keys(vars);
}

/** The worn pack's ground is a starfield (AURA): the stage, the island and the gate draw it. */
export function starfieldWorn(): boolean {
	return ((theme.draft?.pack ?? THEMES[theme.id]) as Pack).background?.type === 'starfield';
}

/** Wear a pack now: the data attributes and, for a draft, its colours inline. */
export function setTheme(id: ThemeId, root: Root, draft: Draft | null = null): void {
	theme.id = id;
	theme.draft = draft;
	Object.assign(root.dataset, lookAttrs(id, draft?.pack ?? null));
	// The server stamps these same attributes on the next cold load from this cookie, so an
	// AURA-primary house never flashes JARVIS first. A draft keeps its base's id (the stamp
	// is the base's look; the draft's colours arrive with its fetch). Only the live page's
	// root writes it: a test's stand-in root must not leak into document.cookie.
	if (typeof document !== 'undefined' && root === document.documentElement)
		document.cookie = `${THEME_COOKIE}=${id}; path=/; max-age=31536000; SameSite=Lax`;
	paint(root, draft ? packVars(draft.pack) : {});
}

/** The colours `t` (0..1) of the way from `before` to `after`, eased: only those that change. */
export function tweenFrame(
	before: Record<string, string>,
	after: Record<string, string>,
	t: number
): Record<string, string> {
	const e = t * t * (3 - 2 * t);
	const out: Record<string, string> = {};
	for (const name of COLOUR_VARS) {
		if (before[name]?.trim() === after[name]?.trim()) continue;
		const a = parseColour(before[name]);
		const b = parseColour(after[name]);
		if (a && b) out[name] = lerpColour(a, b, e);
	}
	return out;
}

/** What the live switch needs from the page; the tests hand in their own. */
export interface SwitchEnv {
	/** The colour properties as computed now. */
	read: () => Record<string, string>;
	frame: (cb: (now: number) => void) => void;
	now: () => number;
	reduced: boolean;
	/** Run the swap inside a dissolve; absent, it runs bare. */
	transition?: (swap: () => void) => void;
}

function browserEnv(root: HTMLElement): SwitchEnv {
	const computed = getComputedStyle(root);
	const doc = document as Document & {
		startViewTransition?: (cb: () => void) => { ready: Promise<void>; finished: Promise<void> };
	};
	return {
		read: () => Object.fromEntries(COLOUR_VARS.map((n) => [n, computed.getPropertyValue(n)])),
		frame: (cb) => void requestAnimationFrame(cb),
		now: () => performance.now(),
		reduced: prefersReducedMotion(),
		transition: doc.startViewTransition
			? (swap) => {
					// A hidden tab skips the dissolve and rejects `ready`; the swap still runs.
					const vt = doc.startViewTransition!(swap);
					vt.ready.catch(() => {});
					vt.finished.catch(() => {});
				}
			: undefined
	};
}

let worn = false;
let run = 0;
/**
 * What the last switch asked for. The early return compares against this, not the worn
 * theme: A→B with B's swap still waiting on the view transition, then A again, used to
 * return early and let B's swap land (review of T-0870).
 */
let wanted: { id: ThemeId; pack: Pack | undefined } | null = null;

/**
 * The live switch. The first wear is instant (nothing to switch from), and so is every
 * switch under reduced motion. A switch mid-tween starts from wherever the last one got to.
 */
export function wearTheme(
	id: ThemeId,
	draft: Draft | null,
	root: Root = document.documentElement,
	env: SwitchEnv = browserEnv(root as HTMLElement)
): void {
	if (worn && wanted?.id === id && wanted.pack === draft?.pack) return;
	wanted = { id, pack: draft?.pack };
	const first = !worn;
	worn = true;
	const mine = ++run;
	if (first || env.reduced) {
		setTheme(id, root, draft);
		return;
	}
	const before = env.read();
	const swap = () => {
		if (mine !== run) return;
		setTheme(id, root, draft);
		const after = env.read();
		const rest = draft ? packVars(draft.pack) : {};
		const dur = tokenMs('--jv-dur-pulse');
		const start = env.now();
		const step = (now: number) => {
			if (mine !== run) return;
			const t = Math.min(1, Math.max(0, (now - start) / dur));
			paint(root, t < 1 ? { ...rest, ...tweenFrame(before, after, t) } : rest);
			if (t < 1) env.frame(step);
		};
		step(start);
	};
	if (env.transition) env.transition(swap);
	else swap();
}

/**
 * A drafted pack from the house (`GET /api/themes/<id>`, through the console's proxy); null when
 * the house answered there is no such pack (404, not a valid pack, a pack under another id);
 * undefined when it could not answer (unreachable, 5xx), which `askDraft` asks again about.
 */
export async function loadDraft(
	id: string,
	fetchFn: typeof fetch = fetch
): Promise<Draft | null | undefined> {
	let res: Response;
	try {
		res = await fetchFn(`/hermes/api/themes/${encodeURIComponent(id)}`);
	} catch {
		return undefined;
	}
	if (res.status >= 500) return undefined;
	if (!res.ok) return null;
	try {
		const pack = validPack(await res.json());
		return pack && pack.id === id ? { id, name: pack.name, pack } : null;
	} catch {
		return null;
	}
}

/** How long before asking a house that could not answer for a drafted pack again. */
export const DRAFT_RETRY_MS = 5_000;

/**
 * Ask for a drafted pack until the house answers, then hand the answer (the pack, or null) to
 * `onDraft` once. Without the retry a reload during a restart wore the base theme until the
 * next theme.apply. The returned stop ends the asking and drops a late answer.
 */
export function askDraft(
	id: string,
	onDraft: (d: Draft | null) => void,
	fetchFn: typeof fetch = fetch
): () => void {
	let live = true;
	let timer: ReturnType<typeof setTimeout> | undefined;
	const ask = () =>
		void loadDraft(id, fetchFn).then((d) => {
			if (!live) return;
			if (d === undefined) timer = setTimeout(ask, DRAFT_RETRY_MS);
			else onDraft(d);
		});
	ask();
	return () => {
		live = false;
		clearTimeout(timer);
	};
}
