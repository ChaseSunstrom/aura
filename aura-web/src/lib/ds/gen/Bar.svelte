<!--
@component
Bars that rise from their foot (≈0.4s). A `picked` bar is the accent and the
rest step back; without one, every bar is the accent or its own tone.

Does not cover: negative values. A bar below zero is drawn empty with its
number. ponytail: a signed axis when a projection ever needs one.
-->
<script lang="ts">
	import { toneColor } from '../tone';

	import { fmt } from './draw';

	interface BarData {
		label: string;
		value: number;
		picked?: boolean;
		tone?: string;
	}
	let { data }: { data: { unit?: string; bars: BarData[] } } = $props();

	const top = $derived(Math.max(0, ...data.bars.map((b) => b.value)));
	const anyPicked = $derived(data.bars.some((b) => b.picked));
	const height = (v: number) => (top > 0 ? (Math.max(0, v) / top) * 100 : 0);
	const tone = (b: BarData) =>
		b.tone ? toneColor(b.tone) : !anyPicked || b.picked ? toneColor('accent') : toneColor('faint');
</script>

<ul class="bar" data-gen="bar">
	{#each data.bars as b, i (i)}
		<li aria-label="{b.label}: {fmt(b.value)}{data.unit ?? ''}" class:picked={b.picked}>
			<span class="value" aria-hidden="true">{fmt(b.value)}</span>
			<span class="track" aria-hidden="true">
				<span class="fill" data-ds-bar style:height="{height(b.value)}%" style:--tone={tone(b)}></span>
			</span>
			<span class="name" aria-hidden="true">{b.label}</span>
		</li>
	{/each}
</ul>

<style>
	.bar {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		gap: var(--jv-space-2);
		min-width: var(--jv-measure-card);
	}
	li {
		flex: 1 1 0;
		min-width: 0;
		display: grid;
		justify-items: center;
		gap: var(--jv-space-1);
	}
	.track {
		display: flex;
		align-items: flex-end;
		width: 100%;
		height: var(--jv-measure-card-height);
	}
	.fill {
		width: 100%;
		background: linear-gradient(180deg, var(--tone), transparent);
		border-top: 1px solid var(--tone);
		opacity: 0.85;
	}
	.picked .fill {
		box-shadow: var(--jv-glow-sm);
		opacity: 1;
	}
	.value,
	.name {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
		font-variant-numeric: tabular-nums;
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.picked .value,
	.picked .name {
		color: var(--jv-accent);
	}
</style>
