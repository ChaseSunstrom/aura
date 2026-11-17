<!--
@component
The default "make me a visual": the model's own vector picture, lines,
polygons, circles, text and paths in a width × height box, flat or through a
camera.
Does not cover: animation or interaction. A moving picture is custom_html's job;
this one is drawn once, whole, and appears with the projection.
-->
<script lang="ts">
	import { toneColor } from '../tone';

	import { sceneLayout, type Camera, type SceneShape } from './draw';

	let {
		data
	}: { data: { width: number; height: number; camera?: Camera; shapes: SceneShape[] } } =
		$props();

	const laid = $derived(sceneLayout(data.width, data.height, data.shapes, data.camera));
	const flat = (at: [number, number][]) => at.map((p) => p.join(',')).join(' ');
</script>

<div
	class="scene"
	data-gen="scene"
	role="img"
	aria-label={data.shapes.flatMap((s) => (s.text ? [s.text] : [])).join(', ') || 'A picture'}
	style:aspect-ratio="{data.width} / {data.height}"
	style:width="min(100%, calc(var(--jv-measure-card-height) * {(3 * data.width) / data.height}))"
>
	<svg viewBox="0 0 {data.width} {data.height}" aria-hidden="true">
		{#each laid as s, i (i)}
			{#if s.kind === 'line' && s.at.length > 1}
				<polyline
					points={flat(s.at)}
					style:stroke={toneColor(s.tone ?? 'accent')}
					style:stroke-width="{s.width ?? 1}px"
				/>
			{:else if s.kind === 'polygon' && s.at.length > 2}
				<polygon
					points={flat(s.at)}
					class:filled={s.fill}
					style:stroke={toneColor(s.tone ?? 'accent')}
					style:fill={s.fill ? toneColor(s.tone ?? 'accent') : 'none'}
					style:stroke-width="{s.width ?? 1}px"
				/>
			{:else if s.kind === 'circle' && s.at.length}
				<circle
					cx={s.at[0][0]}
					cy={s.at[0][1]}
					r={s.radius}
					class:filled={s.fill}
					style:stroke={toneColor(s.tone ?? 'accent')}
					style:fill={s.fill ? toneColor(s.tone ?? 'accent') : 'none'}
					style:stroke-width="{s.width ?? 1}px"
				/>
			{:else if s.kind === 'path' && s.d}
				<path
					d={s.d}
					class:filled={s.fill}
					style:stroke={toneColor(s.tone ?? 'accent')}
					style:fill={s.fill ? toneColor(s.tone ?? 'accent') : 'none'}
					style:stroke-width="{s.width ?? 1}px"
				/>
			{/if}
		{/each}
	</svg>
	{#each laid as s, i (i)}
		{#if s.kind === 'text' && s.text && s.at.length}
			<span
				class="text"
				style:left="{(s.at[0][0] / data.width) * 100}%"
				style:top="{(s.at[0][1] / data.height) * 100}%"
				style:color={toneColor(s.tone ?? 'text')}>{s.text}</span
			>
		{/if}
	{/each}
</div>

<style>
	.scene {
		/* Width, not max-height, caps a tall scene: the text overlay is placed in
		   percent of the box, so the box must keep the scene's aspect. */
		position: relative;
		justify-self: center;
		/* The model's coordinates are not trusted to stay in the box: a shape or a
		   label outside it painted over the projection's own title. */
		overflow: hidden;
	}
	svg {
		display: block;
		width: 100%;
		height: 100%;
		overflow: visible;
		filter: drop-shadow(var(--jv-glow-sm));
	}
	svg * {
		vector-effect: non-scaling-stroke;
		stroke-linejoin: round;
		stroke-linecap: round;
		fill: none;
	}
	.filled {
		fill-opacity: 0.14;
	}
	.text {
		position: absolute;
		transform: translate(-50%, -50%);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		white-space: nowrap;
	}
</style>
