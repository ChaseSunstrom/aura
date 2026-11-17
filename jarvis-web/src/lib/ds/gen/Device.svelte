<!--
@component
One device and its state: the hall lights, on, at 30%.
Does not cover: control. The device's own panel switches it; this is what the
model saw when it drew it.
-->
<script lang="ts">
	import Figure from '../Figure.svelte';
	import Label from '../Label.svelte';
	import { toneColor, type Tone } from '../tone';

	import { fmt } from './draw';

	interface Data {
		name: string;
		kind?: string;
		state: string;
		value?: number;
		unit?: string;
		tone?: Tone;
	}
	let { data }: { data: Data } = $props();
</script>

<div class="device" data-gen="device">
	<Label>{data.kind ?? 'device'}</Label>
	<span class="name">{data.name}</span>
	<span class="now">
		<span class="state" style:color={toneColor(data.tone ?? 'accent')}>{data.state}</span>
		{#if data.value !== undefined}
			<Figure value={fmt(data.value)} unit={data.unit} size="md" tone={data.tone ?? 'text'} />
		{/if}
	</span>
</div>

<style>
	.device {
		display: grid;
		gap: var(--jv-space-1);
		min-width: var(--jv-measure-card);
	}
	.name {
		font-size: var(--jv-fs-md);
		color: var(--jv-text-bright);
	}
	.now {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-3);
	}
	.state {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
	}
</style>
