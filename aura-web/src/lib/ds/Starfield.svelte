<!--
@component
The ground of a pack whose background is a starfield (AURA): its nebulae and void as
`--jv-theme-backdrop`, and a seeded field of stars over them that twinkle slowly.

```svelte
{#if ground === 'starfield'}<Starfield />{/if}
```

The stars are the pack's text colour (read off :root, so a draft's or a tween's colour
lands on the next redraw). It redraws about ten times a second, not every frame: a
twinkle is slow, and the core beside it owns the frame budget. Hidden tab or reduced
motion: one still frame.

Does not cover: parallax or shooting stars; a resize re-seeds the field.
-->
<script lang="ts">
	import { onMount } from 'svelte';

	import { tokenMs } from '$lib/tokens';
	import { prefersReducedMotion, watchReducedMotion } from './motion';

	interface Props {
		/** Twinkle (the pack's `background.twinkle`); off, the stars hold still. */
		twinkle?: boolean;
	}
	let { twinkle = true }: Props = $props();

	let canvas: HTMLCanvasElement | undefined = $state();

	/** A small seeded generator, so the field is the same sky on every load. */
	function seeded(seed: number): () => number {
		return () => {
			seed = (seed + 0x6d2b79f5) | 0;
			let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
			t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
			return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
		};
	}

	// One star per this many CSS pixels of screen, and never more than the cap.
	const AREA_PER_STAR = 6000;
	const MAX_STARS = 420;

	onMount(() => {
		const el = canvas!;
		const ctx = el.getContext('2d');
		if (!ctx) return;
		const period = tokenMs('--jv-dur-blink');
		const every = tokenMs('--jv-dur-instant');
		let stars: { x: number; y: number; r: number; base: number; phase: number; speed: number }[] = [];
		let reduced = prefersReducedMotion();
		let raf = 0;
		let last = -Infinity;

		const seed = () => {
			const dpr = Math.min(2, globalThis.devicePixelRatio || 1);
			const w = el.clientWidth;
			const h = el.clientHeight;
			el.width = Math.round(w * dpr);
			el.height = Math.round(h * dpr);
			ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
			const rand = seeded(1618);
			const n = Math.min(MAX_STARS, Math.round((w * h) / AREA_PER_STAR));
			stars = Array.from({ length: n }, () => {
				const big = rand() > 0.94;
				return {
					x: rand() * w,
					y: rand() * h,
					r: big ? 0.9 + rand() * 0.6 : 0.3 + rand() * 0.5,
					base: big ? 0.7 : 0.25 + rand() * 0.45,
					phase: rand() * Math.PI * 2,
					speed: 1 + rand() * 2
				};
			});
		};

		const draw = (now: number) => {
			const colour = getComputedStyle(el).getPropertyValue('--jv-text').trim();
			ctx.clearRect(0, 0, el.clientWidth, el.clientHeight);
			ctx.fillStyle = colour;
			const still = reduced || !twinkle;
			for (const s of stars) {
				const wave = still ? 1 : 0.55 + 0.45 * Math.sin(s.phase + (now / period) * s.speed);
				ctx.globalAlpha = s.base * wave;
				ctx.beginPath();
				ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
				ctx.fill();
			}
			ctx.globalAlpha = 1;
		};

		const loop = (now: number) => {
			raf = 0;
			if (document.hidden) return;
			if (now - last >= every) {
				last = now;
				draw(now);
			}
			if (!reduced && twinkle) raf = requestAnimationFrame(loop);
		};
		const start = () => {
			if (!raf) raf = requestAnimationFrame(loop);
		};

		seed();
		draw(performance.now());
		start();
		const ro = new ResizeObserver(() => {
			seed();
			draw(performance.now());
		});
		ro.observe(el);
		const onVis = () => {
			if (!document.hidden) start();
		};
		document.addEventListener('visibilitychange', onVis);
		const stopWatch = watchReducedMotion((r) => {
			reduced = r;
			draw(performance.now());
			start();
		});
		return () => {
			cancelAnimationFrame(raf);
			ro.disconnect();
			document.removeEventListener('visibilitychange', onVis);
			stopWatch?.();
		};
	});
</script>

<canvas class="starfield" bind:this={canvas} aria-hidden="true" data-testid="starfield"></canvas>

<style>
	.starfield {
		position: absolute;
		inset: 0;
		/* The ground: behind the host's content, above the host's own background. */
		z-index: -1;
		width: 100%;
		height: 100%;
		background: var(--jv-theme-backdrop);
		pointer-events: none;
	}
</style>
