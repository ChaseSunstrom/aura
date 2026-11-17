<!--
@component
Up to six series over the same x, as lines or, with `area`, filled to zero.
The x labels are spread evenly under the plot, at most six of them.
Does not cover: an x axis with gaps. Every point is one even step.
-->
<script lang="ts">
	import { toneColor } from '../tone';

	import { fmt, lineChart } from './draw';

	interface Series {
		name: string;
		points: number[];
		tone?: string;
	}
	let { data }: { data: { unit?: string; area?: boolean; x?: string[]; series: Series[] } } = $props();

	// A second series that names no tone is told apart by colour, not by luck.
	const ORDER = ['accent', 'violet', 'ok', 'warn', 'danger', 'dim'];
	const tone = (s: Series, i: number) => toneColor(s.tone ?? ORDER[i % ORDER.length]);

	const chart = $derived(lineChart(data.series, data.area));
	const unit = $derived(data.unit ?? '');
	const ticks = $derived.by(() => {
		const x = data.x ?? [];
		const step = Math.max(1, Math.ceil(x.length / 6));
		return x.filter((_, i) => i % step === 0);
	});
	const said = $derived(
		data.series
			.filter((s) => s.points.length)
			.map((s) => `${s.name} ${fmt(Math.min(...s.points))} to ${fmt(Math.max(...s.points))}${unit}`)
			.join('; ')
	);
</script>

<figure class="line" data-gen="line">
	<div class="plot" role="img" aria-label={said}>
		<span class="hi">{fmt(chart.hi)}{unit}</span>
		<span class="lo">{fmt(chart.lo)}{unit}</span>
		<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
			{#each chart.paths as p, i (i)}
				{#if p.fill}<path class="fill" d={p.fill} style:fill={tone(data.series[i], i)} />{/if}
				<path class="stroke" d={p.line} style:stroke={tone(data.series[i], i)} />
			{/each}
		</svg>
	</div>
	{#if ticks.length}
		<div class="x" aria-hidden="true">{#each ticks as t, i (i)}<span>{t}</span>{/each}</div>
	{/if}
	<figcaption class="legend">
		{#each data.series as s, i (i)}
			<span><i style:background={tone(s, i)}></i>{s.name}</span>
		{/each}
	</figcaption>
</figure>

<style>
	.line {
		margin: 0;
		display: grid;
		gap: var(--jv-space-2);
		min-width: var(--jv-measure-panel-min);
	}
	.plot {
		position: relative;
		height: var(--jv-measure-card-height);
		border-left: 1px solid var(--jv-tick);
		border-bottom: 1px solid var(--jv-tick);
	}
	svg {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		overflow: visible;
	}
	.stroke {
		fill: none;
		stroke-width: 1px;
		vector-effect: non-scaling-stroke;
		filter: drop-shadow(var(--jv-glow-sm));
	}
	.fill {
		opacity: 0.14;
		stroke: none;
	}
	.hi,
	.lo {
		position: absolute;
		left: var(--jv-space-1);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.hi {
		top: 0;
	}
	.lo {
		bottom: var(--jv-space-1);
	}
	.x,
	.legend {
		display: flex;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.x {
		justify-content: space-between;
	}
	.legend {
		flex-wrap: wrap;
		gap: var(--jv-space-3);
		color: var(--jv-text-dim);
	}
	.legend span {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-1);
	}
	.legend i {
		width: var(--jv-space-3);
		height: 1px;
		box-shadow: var(--jv-glow-sm);
	}
</style>
