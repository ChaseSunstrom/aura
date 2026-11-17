<!--
@component
One number and what it is: the label, the figure, the change since last time.
Does not cover: what "better" is. The delta is an arrow and a number, not a
judgement in green or red.
-->
<script lang="ts">
	import Figure from '../Figure.svelte';
	import Label from '../Label.svelte';
	import type { Tone } from '../tone';

	import { fmt } from './draw';

	interface Data {
		label: string;
		value: number | string;
		unit?: string;
		delta?: number;
		caption?: string;
		tone?: Tone;
	}
	let { data }: { data: Data } = $props();

	const value = $derived(typeof data.value === 'number' ? fmt(data.value) : data.value);
</script>

<div class="stat" data-gen="stat">
	<Label>{data.label}</Label>
	<Figure {value} unit={data.unit} size="display" tone={data.tone ?? 'text'} />
	{#if data.delta !== undefined}
		<span class="delta">
			{data.delta > 0 ? '▲' : data.delta < 0 ? '▼' : '±'}
			{fmt(Math.abs(data.delta))}{data.unit ?? ''}
		</span>
	{/if}
	{#if data.caption}<span class="caption">{data.caption}</span>{/if}
</div>

<style>
	.stat {
		display: grid;
		justify-items: start;
		gap: var(--jv-space-2);
	}
	.delta {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		font-variant-numeric: tabular-nums;
	}
	.caption {
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-xs);
	}
</style>
