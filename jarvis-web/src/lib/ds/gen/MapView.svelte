<!--
@component
Places and a route on a graticule, fitted to what the data holds. No tiles, no
map library and no network: the streets are not drawn, the shape of the trip is.
Does not cover: a route over the antimeridian (see `mapFit`).
-->
<script lang="ts">
	import { toneColor } from '../tone';

	import { mapFit } from './draw';

	interface Place {
		lat: number;
		lon: number;
		label?: string;
		tone?: string;
	}
	let { data }: { data: { points?: Place[]; route?: [number, number][] } } = $props();

	// The box is 5:3 in CSS and the route's viewBox is 500 × 300: one percent each way is 5 and 3.
	const points = $derived(data.points ?? []);
	const at = $derived(
		mapFit([...points.map((p) => [p.lat, p.lon] as [number, number]), ...(data.route ?? [])], 5 / 3)
	);
	const route = $derived(
		(data.route ?? [])
			.map((c) => at(c))
			.map(([x, y]) => `${(x * 5).toFixed(1)},${(y * 3).toFixed(1)}`)
			.join(' ')
	);
	const said = $derived(points.map((p) => p.label).filter(Boolean).join(', ') || 'A route');
</script>

<div class="map" data-gen="map" role="img" aria-label={said}>
	{#if route}
		<svg viewBox="0 0 500 300" preserveAspectRatio="none" aria-hidden="true">
			<polyline points={route} />
		</svg>
	{/if}
	{#each points as p, i (i)}
		{@const xy = at([p.lat, p.lon])}
		<!-- A place in the east half names itself to its left: at the box's edge the name was cut. -->
		<span
			class="place"
			class:east={xy[0] > 50}
			style:left="{xy[0]}%"
			style:top="{xy[1]}%"
			style:--tone={toneColor(p.tone ?? 'accent')}
		>
			<i aria-hidden="true"></i>
			{#if p.label}<span class="name">{p.label}</span>{/if}
		</span>
	{/each}
</div>

<style>
	.map {
		position: relative;
		aspect-ratio: 5 / 3;
		min-width: var(--jv-measure-panel-min);
		overflow: hidden;
		background:
			linear-gradient(90deg, var(--jv-line) 1px, transparent 1px) 0 0 / 10% 100%,
			linear-gradient(0deg, var(--jv-line) 1px, transparent 1px) 0 0 / 100% calc(100% / 6);
		border: 1px solid var(--jv-line-soft);
	}
	svg {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
	}
	polyline {
		fill: none;
		stroke: var(--jv-accent);
		stroke-width: 1px;
		vector-effect: non-scaling-stroke;
		stroke-linejoin: round;
		filter: drop-shadow(var(--jv-glow-sm));
	}
	.place {
		position: absolute;
		display: flex;
		align-items: center;
		gap: var(--jv-space-1);
		transform: translate(calc(-1 * var(--jv-space-1)), -50%);
		white-space: nowrap;
	}
	.place.east {
		flex-direction: row-reverse;
		transform: translate(calc(var(--jv-space-1) - 100%), -50%);
	}
	.place i {
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		border-radius: var(--jv-radius-pill);
		background: var(--tone);
		box-shadow: var(--jv-glow-sm);
	}
	.name {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--tone);
	}
</style>
