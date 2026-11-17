// One interface for every core a theme can wear (the AURA package's renderer
// contract): `setState(state, inputs)`, `render(dt)`, `setQuality(level)`,
// `dock(position, size)` and `dispose()`. The theme pack names its core
// (`THEMES[id].core.renderer`); Core.svelte asks this module for that renderer and
// drives it the same way whichever it is, so a theme switch changes no screen code.
//
// Each renderer keeps its own clock (render(dt) moves it), picks WebGL2 where it
// can and Canvas2D otherwise, and drops to Canvas2D on a fresh canvas when the GPU
// context is lost, telling its owner through `onBackend`.
//
// Does not cover: placing the reactor off-centre (its drawers centre it; `dock`'s
// position only moves the galaxy), and the persona tint on the galaxy (galaxy.ts).

import { THEMES, type ThemeId } from '$lib/themes';
import { createCanvas2d } from './canvas2d';
import {
	createCore,
	fibonacci,
	frozenAt,
	setState as setReactorState,
	step,
	type Inputs,
	type Renderer
} from './engine';
import {
	CENTRED,
	createDrawList,
	createGalaxy,
	galaxyAt,
	galaxyInto,
	requalify,
	setGalaxyState,
	stepGalaxy,
	type Placement,
	type Quality
} from './galaxy';
import { createGalaxy2d, createGalaxyGl, pixels, type GalaxyDrawer } from './galaxy-draw';
import type { CoreState } from './states';

export type CoreKind = 'galaxy' | 'reactor';

export interface Size {
	/** CSS pixels, and the device pixel ratio (already capped). */
	width: number;
	height: number;
	dpr: number;
}

export interface CoreRenderer {
	readonly core: CoreKind;
	readonly backend: 'webgl' | 'canvas2d';
	/** State changes so far (each fired the theme's onStateChange), and working beats. */
	readonly changes: number;
	readonly beats: number;
	/**
	 * Move to `state`, tweening, and take the live inputs (mic level, speech envelope,
	 * music beat; missing ones are synthesized). The same state only updates the inputs.
	 * Returns whether the state changed (leaving a freeze always counts).
	 */
	setState(state: CoreState, inputs?: Inputs): boolean;
	/** Advance the clock `dt` ms and draw; 0 redraws as is. A frozen core never advances. */
	render(dt: number): void;
	/** The star or point budget (desktop, phone, docked mini) and reduced motion. */
	setQuality(level: Quality, still?: boolean): void;
	/** Where the core sits on its surface, and the surface's size. */
	dock(position: Placement, size: Size): void;
	/** One exact frame, `at` ms after a change from `from` to `state`; no clock until setState. */
	freeze(state: CoreState, from: CoreState, at: number): void;
	dispose(): void;
}

export interface CoreOptions {
	/** A fresh canvas in the core's box each call: a canvas that held WebGL cannot give 2D. */
	surface: () => HTMLCanvasElement;
	/** 'canvas2d' skips the GPU (the gallery's comparison). */
	prefer?: 'auto' | 'canvas2d';
	quality: Quality;
	still?: boolean;
	state: CoreState;
	/** The live inputs, read every frame: the owner may keep and mutate this object. */
	inputs?: Inputs;
	/** The backend changed (a lost GPU context fell back to 2D). */
	onBackend?: () => void;
}

/** The core a theme wears. */
export const rendererFor = (theme: ThemeId): CoreKind => THEMES[theme].core.renderer;

/**
 * One build's `surface`: a new canvas, put on the page only while that build is still the
 * newest. The reactor asks for its canvas after three.js loads; a switch to the galaxy in
 * that wait used to have the stale reactor replace the galaxy's canvas, leaving the core
 * blank until the next switch. A stale build draws to a canvas nobody sees, then is disposed.
 */
export function surfaceFor<C>(make: () => C, show: (canvas: C) => void, current: () => boolean): () => C {
	return () => {
		const canvas = make();
		if (current()) show(canvas);
		return canvas;
	};
}

/** The renderer for `kind`, or null where neither WebGL2 nor 2D will draw. */
export async function createCoreRenderer(
	kind: CoreKind,
	opts: CoreOptions
): Promise<CoreRenderer | null> {
	return kind === 'galaxy' ? galaxyRenderer(opts) : reactorRenderer(opts);
}

const forward = (dt: number) => (Number.isFinite(dt) ? Math.max(0, dt) : 0);

/** Swap a lost GPU drawer for 2D on a fresh canvas; a disposed owner swaps nothing. */
function fallbackTo<D extends { dispose(): void; resize(w: number, h: number, dpr: number): void }>(
	make2d: (c: HTMLCanvasElement) => D | null,
	opts: CoreOptions,
	get: () => { drawer: D | null; size: Size; disposed: boolean },
	set: (d: D) => void,
	redraw: () => void
) {
	return () =>
		queueMicrotask(() => {
			const { drawer, size, disposed } = get();
			if (disposed) return;
			try {
				drawer?.dispose();
			} catch {
				// A lost context may refuse to be let go of; it is gone either way.
			}
			const next = make2d(opts.surface());
			if (!next) return;
			next.resize(size.width, size.height, size.dpr);
			set(next);
			opts.onBackend?.();
			redraw();
		});
}

function galaxyRenderer(opts: CoreOptions): CoreRenderer | null {
	let still = !!opts.still;
	let g = createGalaxy(opts.quality, opts.state, still);
	let frozen = false;
	let disposed = false;
	let position: Placement = CENTRED;
	let size: Size = { width: 1, height: 1, dpr: 1 };
	const inputs = opts.inputs ?? {};
	const list = createDrawList();
	let drawer: GalaxyDrawer | null = null;
	const draw = () => {
		if (!drawer) return;
		galaxyInto(g, list, pixels(size.width, size.dpr), pixels(size.height, size.dpr), position);
		drawer.draw(list);
	};
	const lost = fallbackTo(
		createGalaxy2d,
		opts,
		() => ({ drawer, size, disposed }),
		(d) => (drawer = d),
		draw
	);
	if (opts.prefer !== 'canvas2d') drawer = createGalaxyGl(opts.surface(), lost);
	drawer ??= createGalaxy2d(opts.surface());
	if (!drawer) return null;
	return {
		core: 'galaxy',
		get backend() {
			return drawer!.kind;
		},
		get changes() {
			return g.changes;
		},
		get beats() {
			return g.beats;
		},
		setState(state, next) {
			if (next) Object.assign(inputs, next);
			if (frozen) {
				frozen = false;
				const { changes, beats } = g;
				g = { ...createGalaxy(g.quality, state, still), changes: changes + 1, beats };
				return true;
			}
			return setGalaxyState(g, state, { snap: still });
		},
		render(dt) {
			if (!frozen) stepGalaxy(g, forward(dt), inputs);
			draw();
		},
		setQuality(level, nextStill = false) {
			still = nextStill;
			g = requalify(g, level);
			g.still = still;
		},
		dock(at, box) {
			position = at;
			size = box;
			drawer?.resize(box.width, box.height, box.dpr);
		},
		freeze(state, from, at) {
			frozen = true;
			const { changes, beats } = g;
			g = { ...galaxyAt(state, from, at, g.quality, { ...inputs }), changes, beats };
		},
		dispose() {
			disposed = true;
			drawer?.dispose();
			drawer = null;
		}
	};
}

/** Points in the reactor's shell, and the fewer drawn docked, on battery saver or under reduced motion. */
const FULL = 1500;
const LIGHT = 600;

async function reactorRenderer(opts: CoreOptions): Promise<CoreRenderer | null> {
	let still = !!opts.still;
	let clock = 0;
	let model = createCore(opts.state, clock, still);
	let shell = fibonacci(opts.quality === 'mini' ? LIGHT : FULL);
	let frozen = false;
	let disposed = false;
	let size: Size = { width: 1, height: 1, dpr: 1 };
	const inputs = opts.inputs ?? {};
	let drawer: Renderer | null = null;
	const draw = () => drawer?.draw(model, shell);
	const lost = fallbackTo(
		createCanvas2d,
		opts,
		() => ({ drawer, size, disposed }),
		(d) => (drawer = d),
		draw
	);
	if (opts.prefer !== 'canvas2d') {
		try {
			// three.js loads only for a theme that wears the reactor.
			const { createWebgl } = await import('./webgl');
			drawer = createWebgl(opts.surface(), FULL, lost);
		} catch {
			drawer = null;
		}
	}
	drawer ??= createCanvas2d(opts.surface());
	if (!drawer) return null;
	return {
		core: 'reactor',
		get backend() {
			return drawer!.kind;
		},
		get changes() {
			return model.changes;
		},
		get beats() {
			return model.beats;
		},
		setState(state, next) {
			if (next) Object.assign(inputs, next);
			if (frozen) {
				frozen = false;
				const { changes, beats } = model;
				model = createCore(state, clock, still);
				model.changes = changes + 1;
				model.beats = beats;
				return true;
			}
			return setReactorState(model, state, clock, { snap: still });
		},
		render(dt) {
			if (!frozen) {
				clock += forward(dt);
				step(model, clock, inputs);
			}
			draw();
		},
		setQuality(level, nextStill = false) {
			still = nextStill;
			model.still = still;
			const n = level === 'mini' ? LIGHT : FULL;
			if (shell.n !== n) shell = fibonacci(n);
		},
		dock(_at, box) {
			size = box;
			drawer?.resize(box.width, box.height, box.dpr);
		},
		freeze(state, from, at) {
			frozen = true;
			const { changes, beats } = model;
			model = frozenAt(state, from, at, { ...inputs });
			model.changes = changes;
			model.beats = beats;
		},
		dispose() {
			disposed = true;
			drawer?.dispose();
			drawer = null;
		}
	};
}
