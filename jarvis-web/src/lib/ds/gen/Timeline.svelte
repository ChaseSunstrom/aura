<!--
@component
Events in time order: a time, a dot in the event's tone, what happens.
Does not cover: sorting. The events are drawn in the order they came.
-->
<script lang="ts">
	import { toneColor } from '../tone';

	interface Event {
		time: string;
		label: string;
		detail?: string;
		tone?: string;
	}
	let { data }: { data: { events: Event[] } } = $props();
</script>

<ol class="timeline" data-gen="timeline">
	{#each data.events as e, i (i)}
		<li data-ds-row style:--i={i} style:--tone={toneColor(e.tone ?? 'accent')}>
			<span class="time">{e.time}</span>
			<span class="dot" aria-hidden="true"></span>
			<span class="what">
				<span class="label">{e.label}</span>
				{#if e.detail}<span class="detail">{e.detail}</span>{/if}
			</span>
		</li>
	{/each}
</ol>

<style>
	.timeline {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: var(--jv-space-3);
		min-width: var(--jv-measure-card);
	}
	li {
		display: grid;
		grid-template-columns: auto auto minmax(0, 1fr);
		gap: var(--jv-space-3);
		align-items: baseline;
	}
	.time {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		font-variant-numeric: tabular-nums;
	}
	.dot {
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		border-radius: var(--jv-radius-pill);
		background: var(--tone);
		box-shadow: var(--jv-glow-sm);
		align-self: center;
	}
	.what {
		display: grid;
		gap: var(--jv-space-1);
		overflow-wrap: anywhere;
	}
	.detail {
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-xs);
	}
</style>
