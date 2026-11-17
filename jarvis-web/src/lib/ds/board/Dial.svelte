<!--
@component
04 TOOLS: the last hour as a clock face, one bar a minute (the newest at
twelve), as long as that minute's tool calls. The hand turns as many times a
minute as JARVIS has called tools a minute lately: still when it is idle,
spinning while it works. Beside it, the tools it reached for most today.

Does not cover: what a call did or whether it worked (03 TASKS has how turns ended).
-->
<script lang="ts">
	import { frames } from './clock';

	interface Props {
		perMinute: number[];
		peak: number;
		rate: number;
		top: { name: string; calls: number }[];
	}
	let { perMinute, peak, rate, top }: Props = $props();

	const CX = 78;
	const CY = 78;
	const R0 = 40;
	const LEN = 32;
	const ticks = $derived(
		perMinute.map((n, i) => {
			const a = ((i + 1) / perMinute.length) * 2 * Math.PI - Math.PI / 2;
			const r1 = R0 + (peak ? (n / peak) * LEN : 0);
			const c = Math.cos(a);
			const s = Math.sin(a);
			return { n, x0: CX + c * R0, y0: CY + s * R0, x1: CX + c * Math.max(R0 + 1.5, r1), y1: CY + s * Math.max(R0 + 1.5, r1) };
		})
	);
	const most = $derived(Math.max(1, ...top.map((t) => t.calls)));
	const short = (s: string, n: number) => {
		const w = s.replace(/_/g, ' ');
		return w.length > n ? `${w.slice(0, n - 1)}…` : w;
	};

	let hand: SVGLineElement | undefined = $state();
	// Turns a minute = calls a minute, capped where the hand would blur.
	const rpm = $derived(Math.min(60, rate));
	function tick(t: number): void {
		hand?.setAttribute('transform', `rotate(${((t * rpm * 6) % 360).toFixed(1)} ${CX} ${CY})`);
	}
</script>

<svg viewBox="0 0 320 160" role="img" aria-label="Tool calls a minute over the last hour" use:frames={tick}>
	<circle class="ring" cx={CX} cy={CY} r={R0 - 4} />
	{#each ticks as t, i (i)}
		<line class="tick" class:on={t.n > 0} x1={t.x0} y1={t.y0} x2={t.x1} y2={t.y1}><title>{t.n}</title></line>
	{/each}
	<line class="hand" bind:this={hand} x1={CX} y1={CY} x2={CX} y2={CY - R0 + 6} />
	<circle class="hub" cx={CX} cy={CY} r="2" />
	<text class="fig" x={CX} y={CY + 17} text-anchor="middle">{rate}</text>
	<!-- A unit apart from the figure, still inside the ring (T-0692). -->
	<text class="note" x={CX} y={CY + 30} text-anchor="middle">a min</text>
	{#each top.slice(0, 5) as t, i (t.name)}
		<text class="name" x="174" y={22 + i * 27}><title>{t.name}</title>{short(t.name, 18)}</text>
		<rect class="bar" x="174" y={27 + i * 27} width={(t.calls / most) * 116} height="4" />
		<text class="note" x="320" y={22 + i * 27} text-anchor="end">{t.calls}</text>
	{/each}
</svg>

<style>
	svg {
		display: block;
		width: 100%;
		height: auto;
	}
	.ring {
		fill: none;
		stroke: var(--jv-line);
	}
	.tick {
		stroke: var(--jv-tick);
		stroke-width: 2;
	}
	.tick.on {
		stroke: var(--jv-accent);
	}
	.hand {
		stroke: var(--jv-accent);
		stroke-width: 1.5;
	}
	.hub {
		fill: var(--jv-accent);
	}
	text {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		fill: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.fig {
		fill: var(--jv-text-bright);
		font-family: var(--jv-font-display);
		font-size: var(--jv-fs-xl);
		font-weight: var(--jv-weight-display);
	}
	.name {
		fill: var(--jv-text-dim);
	}
	.bar {
		fill: var(--jv-accent-deep);
	}
	@media (prefers-reduced-motion: reduce) {
		.hand {
			display: none;
		}
	}
</style>
