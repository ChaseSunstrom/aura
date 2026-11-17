<!--
@component
02 TURNS: the last 24 hours as bars, one an hour, stacked by the model that
answered (violet, each model a step fainter). The hour now is capped in the
accent and breathes: it is still filling.

Does not cover: tokens per hour (Hermes records them per session, not per
turn); the panel's footer gives the day's totals instead.
-->
<script lang="ts">
	import { MS } from '../motion';
	import { frames } from './clock';

	interface Props {
		hours: { at: number; stacks: { model: string; n: number }[] }[];
		series: string[];
		peak: number;
	}
	let { hours, series, peak }: Props = $props();

	const W = 320;
	const TOP = 22;
	const BASE = 110;
	const step = W / 24;
	const SHADE = [1, 0.55, 0.32, 0.18];
	const shade = (model: string) => SHADE[Math.min(SHADE.length - 1, Math.max(0, series.indexOf(model)))];

	const bars = $derived(
		hours.map((h, i) => {
			let y = BASE;
			const parts = h.stacks.map((s) => {
				const hh = peak ? (s.n / peak) * (BASE - TOP) : 0;
				y -= hh;
				return { model: s.model, n: s.n, y, h: hh };
			});
			return { x: i * step + 1.5, top: y, parts, total: h.stacks.reduce((s, x) => s + x.n, 0) };
		})
	);
	const short = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

	let cap: SVGRectElement | undefined = $state();
	function tick(t: number): void {
		cap?.setAttribute('opacity', (0.55 + 0.45 * Math.sin(t * 3)).toFixed(2));
	}
</script>

<svg viewBox="0 0 {W} 128" role="img" aria-label="Turns per hour by model, last 24 hours" use:frames={tick}>
	{#each series.slice(0, 3) as name, i (name)}
		<rect class="bar" x={i * 108} y="2" width="8" height="8" fill-opacity={SHADE[i]} />
		<text class="legend" x={i * 108 + 12} y="10"><title>{name}</title>{short(name, 13)}</text>
	{/each}
	<line class="rule" x1="0" x2={W} y1={BASE + 0.5} y2={BASE + 0.5} />
	{#each bars as b, i (i)}
		<g class="rise" style:animation-delay="{i * MS.rowStagger}ms">
			{#each b.parts as p (p.model)}
				<rect class="bar" x={b.x} y={p.y} width={step - 3} height={p.h} fill-opacity={shade(p.model)}><title>{p.n} · {p.model}</title></rect>
			{/each}
		</g>
	{/each}
	<rect class="now" bind:this={cap} x={bars[23]?.x ?? 0} y={(bars[23]?.top ?? BASE) - 2} width={step - 3} height="1.5" />
	<text class="axis" x="0" y="124">−24 h</text>
	<text class="axis" x={W / 2} y="124" text-anchor="middle">−12 h</text>
	<text class="axis" x={W} y="124" text-anchor="end">now</text>
</svg>

<style>
	svg {
		display: block;
		width: 100%;
		height: auto;
	}
	.bar {
		fill: var(--jv-violet);
	}
	.rise {
		transform-box: fill-box;
		transform-origin: bottom;
		animation: grow var(--jv-dur-bar) var(--jv-ease-appear) both;
	}
	.now {
		fill: var(--jv-accent);
	}
	.rule {
		stroke: var(--jv-line);
	}
	text {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		fill: var(--jv-text-faint);
	}
	.legend {
		fill: var(--jv-text-dim);
	}
	@keyframes grow {
		from {
			transform: scaleY(0);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.rise {
			animation: none;
		}
	}
</style>
