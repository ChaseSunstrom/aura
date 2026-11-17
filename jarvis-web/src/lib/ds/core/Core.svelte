<!--
@component
The core: the assistant itself. It shows in one look what the assistant is doing
(`CORE_STATES`, nine of them); a change of state tweens there and fires a shock
ring; the mic level, the speech envelope and the music's beat move it while it
listens, speaks or plays.

```svelte
<Core state={coreStateFor(reactor)} level={mic.level} envelope={voice.envelope} onbeat={(n) => (beat = n)} />
```

The theme picks what it is (`renderer.ts`): AURA's living galaxy (raw WebGL2) or
JARVIS's reactor, a shell of light in three turning rings (three.js, loaded on
demand). Both draw in Canvas2D where there is no WebGL2 or after the GPU context
is lost. It draws only while it is on screen and the page is visible; on battery
saver (a low battery off the charger), docked and under reduced motion it draws
fewer stars or points, and under reduced motion it stands still: each change is
one frame, no ring.

`freeze` draws one exact frame (`at` ms after a change from `from`) and no
loop: the gallery's screenshots and the pixel tests are made of it.

Does not cover: sound. The inputs are levels the host already has (the voice
pipeline's), 0..1; this never opens the mic.
-->
<script lang="ts">
	import { onMount, untrack } from 'svelte';

	import { THEMES, type ThemeId } from '$lib/themes';
	import { TOKENS, type TokenName } from '$lib/tokens';
	import { prefersReducedMotion, watchReducedMotion } from '../motion';
	import { theme as worn } from '../theme.svelte';
	import { FRAME, LOOK_TOKEN, rgbOf, type Inputs } from './engine';
	import type { Quality } from './galaxy';
	import { createCoreRenderer, rendererFor, surfaceFor, type CoreKind, type CoreRenderer } from './renderer';
	import type { CoreState } from './states';

	/** A battery this low, off the charger, is the nearest thing the web has to battery saver. */
	const LOW_BATTERY = 0.2;
	/** Canvas width in CSS px at or under which the core is drawn at up to 3x: a phone's core. */
	const SHARP_UNDER = 260;

	interface Props {
		state?: CoreState;
		/** The mic level while listening, 0..1. Missing: a synthesized one. */
		level?: number;
		/** The speech envelope while speaking, 0..1. */
		envelope?: number;
		/** The music's beat strength while playing, 0..1. */
		music?: number;
		/** The primary persona's hue in place of the state's colour (personas.ts `tintFor`). */
		tint?: TokenName;
		/** The theme whose core this is; defaults to the one the console wears. */
		theme?: ThemeId;
		/** A small core (the popup's): the docked star budget. */
		docked?: boolean;
		/** 'canvas2d' skips the GPU (the gallery's comparison). */
		renderer?: 'auto' | 'canvas2d';
		/** One still frame, `at` ms after a change from `from` (idle) to `state`. */
		freeze?: { at: number; from?: CoreState };
		/** Each working beat, with the count so far (Connector's `beat`). */
		onbeat?: (beats: number) => void;
		/** What a screen reader hears; defaults to "<theme's name>, <state>". */
		label?: string;
		testid?: string;
	}
	// Not `state`: a local of that name turns the `$state` rune into a store read.
	let {
		state: mode = 'idle',
		level,
		envelope,
		music,
		tint,
		theme,
		docked = false,
		renderer: prefer = 'auto',
		freeze,
		onbeat,
		label,
		testid = 'core'
	}: Props = $props();

	interface BatteryLike extends EventTarget {
		charging: boolean;
		level: number;
	}

	const wear = $derived<ThemeId>(theme ?? worn.id);
	const want = $derived<CoreKind>(rendererFor(wear));

	let host: HTMLDivElement;
	let kind = $state<'' | 'webgl' | 'canvas2d'>('');
	let drawn = $state<'' | CoreKind>('');
	let changes = $state(0);

	let core: CoreRenderer | null = null;
	let mounted = false;
	let built = 0;
	let reduced = true;
	let saver = false;
	let coarse = false;
	let frozen = false;
	let visible = true;
	let running = false;
	let raf = 0;
	let last = 0;
	const inputs: Inputs = {};

	const quality = (): Quality => (docked || reduced || saver ? 'mini' : coarse ? 'phone' : 'desktop');

	$effect(() => {
		inputs.level = level;
		inputs.envelope = envelope;
		inputs.music = music;
		inputs.tint = tint ? rgbOf(TOKENS[tint]) : undefined;
		// A core that is not looping draws only on a change: the new hue is one.
		if (!running) untrack(() => core?.render(0));
	});

	$effect(() => {
		const next = mode;
		const still = freeze ? { at: freeze.at, from: freeze.from } : null;
		untrack(() => apply(next, still));
	});

	// A theme switch wears the other core; a docking change only changes the budget.
	$effect(() => {
		const k = want;
		void docked;
		untrack(() => {
			if (!mounted) return;
			if (k !== drawn) void build(k);
			else core?.setQuality(quality(), reduced);
		});
	});

	function apply(next: CoreState, still: { at: number; from?: CoreState } | null) {
		if (!core) return; // each build starts from the props as they are
		if (still) {
			frozen = true;
			core.freeze(next, still.from ?? 'idle', still.at);
		} else {
			// Leaving a freeze always counts as a change (renderer.ts).
			frozen = false;
			if (!core.setState(next)) return;
		}
		changes = core.changes;
		sync();
		if (!running) core.render(0);
	}

	function frame(now: number) {
		raf = 0;
		if (!running || !core) return;
		const beats = core.beats;
		core.render(last ? now - last : FRAME);
		last = now;
		if (core.beats !== beats) onbeat?.(core.beats);
		raf = requestAnimationFrame(frame);
	}

	/** Run the loop only while it can be seen and should move. */
	function sync() {
		const on = !!core && !frozen && !reduced && visible && !document.hidden;
		if (on && !running) last = 0; // a paused core resumes where it was, not leaps
		running = on;
		if (on && !raf) raf = requestAnimationFrame(frame);
		if (!on && raf) {
			cancelAnimationFrame(raf);
			raf = 0;
		}
	}

	function resize() {
		// The laid-out box, not the painted one: the shell scales the core while it glides to
		// or from a menu, and a rect read mid-glide sized the canvas to the scaled picture.
		const box = { width: host.offsetWidth, height: host.offsetHeight };
		core?.dock(
			{ x: 0.5, y: 0.5, scale: 1 },
			// A small core is drawn at the screen's own density: at 2 on a phone's 3 its
			// rings were visibly pixels (the owner, 2026-11-11); a large one keeps the cap,
			// which is what its frame budget was measured at.
			{ width: box.width, height: box.height, dpr: Math.min(box.width <= SHARP_UNDER ? 3 : 2, devicePixelRatio || 1) }
		);
	}

	function canvas() {
		const made = document.createElement('canvas');
		made.setAttribute('aria-hidden', 'true');
		return made;
	}

	function show(made: HTMLCanvasElement) {
		// A canvas that once gave a WebGL context cannot give a 2D one, so the fallback
		// needs a fresh element. `host` is an empty div Svelte renders no children into.
		// eslint-disable-next-line svelte/no-dom-manipulating
		host.replaceChildren(made);
	}

	/** Wear `k`'s core in place of the one drawn; a newer build or an unmount wins. */
	async function build(k: CoreKind) {
		const mine = ++built;
		core?.dispose();
		core = null;
		drawn = k;
		sync();
		const next = await createCoreRenderer(k, {
			surface: surfaceFor(canvas, show, () => mine === built && mounted),
			prefer,
			quality: quality(),
			still: reduced,
			state: mode,
			inputs,
			onBackend: () => (kind = core?.backend ?? '')
		});
		if (mine !== built || !mounted) {
			next?.dispose();
			return;
		}
		core = next;
		kind = next?.backend ?? '';
		if (!next) return;
		// The props may have moved while the renderer loaded: wear them as they are now.
		frozen = !!freeze;
		if (freeze) next.freeze(mode, freeze.from ?? 'idle', freeze.at);
		else next.setState(mode);
		changes = next.changes;
		resize();
		next.render(0);
		sync();
	}

	onMount(() => {
		let battery: BatteryLike | null = null;
		mounted = true;
		reduced = prefersReducedMotion();
		coarse = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;
		void build(want);

		const requality = () => {
			core?.setQuality(quality(), reduced);
			sync();
			if (!running) core?.render(0);
		};
		const stopWatching = watchReducedMotion((r) => {
			reduced = r;
			requality();
		});
		const onBattery = () => {
			saver = !!battery && !battery.charging && battery.level <= LOW_BATTERY;
			requality();
		};
		const nav = navigator as Navigator & { getBattery?: () => Promise<BatteryLike> };
		nav
			.getBattery?.()
			.then((b) => {
				if (!mounted) return;
				battery = b;
				b.addEventListener('chargingchange', onBattery);
				b.addEventListener('levelchange', onBattery);
				onBattery();
			})
			.catch(() => {});

		const ro = new ResizeObserver(() => {
			resize();
			if (!running) core?.render(0);
		});
		ro.observe(host);
		const io = new IntersectionObserver((entries) => {
			visible = entries.some((e) => e.isIntersecting);
			sync();
		});
		io.observe(host);
		document.addEventListener('visibilitychange', sync);

		return () => {
			mounted = false;
			ro.disconnect();
			io.disconnect();
			document.removeEventListener('visibilitychange', sync);
			stopWatching();
			battery?.removeEventListener('chargingchange', onBattery);
			battery?.removeEventListener('levelchange', onBattery);
			core?.dispose();
			core = null;
			sync();
		};
	});
</script>

<div
	class="core"
	role="img"
	aria-label={label ?? `${THEMES[wear].name}, ${mode}`}
	style:--tone={`var(${tint ?? LOOK_TOKEN[mode]})`}
	data-state={mode}
	data-core={drawn || undefined}
	data-renderer={kind || undefined}
	data-shocks={changes}
	data-testid={testid || undefined}
>
	<div class="surface" bind:this={host}></div>
</div>

<style>
	.core {
		position: relative;
		inline-size: min(var(--jv-core-width), 100%);
		aspect-ratio: 1;
	}
	.surface,
	.surface :global(canvas) {
		position: absolute;
		inset: 0;
		inline-size: 100%;
		block-size: 100%;
	}
	/* The floor glow: the core's light on the ground below it, in its colour. */
	.core::after {
		content: '';
		position: absolute;
		left: 12%;
		width: 76%;
		top: 93%;
		height: 12%;
		background: radial-gradient(
			closest-side,
			rgb(from var(--tone) r g b / 0.28),
			rgb(from var(--tone) r g b / 0.06) 60%,
			transparent
		);
		pointer-events: none;
	}
</style>
