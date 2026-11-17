<!--
@component
What is playing, where, and how far in.
Does not cover: controls or a moving clock. It is a picture of the moment the
model drew it; the player's own panel is live.
-->
<script lang="ts">
	import Label from '../Label.svelte';

	import { clock } from './draw';

	interface Data {
		track: string;
		artist?: string;
		device?: string;
		position_s?: number;
		duration_s?: number;
		playing?: boolean;
	}
	let { data }: { data: Data } = $props();

	const part = $derived(
		data.position_s !== undefined && data.duration_s
			? Math.min(1, Math.max(0, data.position_s / data.duration_s))
			: undefined
	);
</script>

<div class="media" data-gen="media">
	<Label tone={data.playing === false ? 'faint' : 'accent'}>
		{data.playing === false ? 'Paused' : 'Playing'}{data.device ? ` · ${data.device}` : ''}
	</Label>
	<span class="track">{data.track}</span>
	{#if data.artist}<span class="artist">{data.artist}</span>{/if}
	{#if part !== undefined}
		<span class="bar" aria-hidden="true"><span style:transform="scaleX({part})"></span></span>
		<span class="time">{clock(data.position_s ?? 0)} / {clock(data.duration_s ?? 0)}</span>
	{/if}
</div>

<style>
	.media {
		display: grid;
		gap: var(--jv-space-1);
		min-width: var(--jv-measure-card);
	}
	.track {
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-xl);
		color: var(--jv-text-bright);
		overflow-wrap: anywhere;
	}
	.artist {
		color: var(--jv-text-dim);
	}
	.bar {
		margin-top: var(--jv-space-2);
		height: 1px;
		background: var(--jv-line);
	}
	.bar span {
		display: block;
		height: 100%;
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
		transform-origin: left;
	}
	.time {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
</style>
