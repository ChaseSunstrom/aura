<!--
@component
Items, one line each, with an optional note on the right; virtualized, so two
thousand cost what twelve do.
Does not cover: wrapping. A long item is cut with an ellipsis; its full text is the tooltip.
-->
<script lang="ts">
	import { toneColor } from '../tone';

	import Virtual from './Virtual.svelte';

	interface Item {
		text: string;
		meta?: string;
		tone?: string;
	}
	let { data }: { data: { title?: string; items: Item[] } } = $props();
</script>

<div class="list" data-gen="list">
	<Virtual items={data.items} label={data.title ?? 'List'}>
		{#snippet row(item, i, fresh)}
			<div
				class="item"
				role="listitem"
				data-ds-row={fresh || undefined}
				style:--i={i}
				style:--tone={toneColor(item.tone ?? 'accent')}
			>
				<i aria-hidden="true"></i>
				<span class="text" title={item.text}>{item.text}</span>
				{#if item.meta}<span class="meta">{item.meta}</span>{/if}
			</div>
		{/snippet}
	</Virtual>
</div>

<style>
	.list {
		min-width: var(--jv-measure-card);
	}
	.item {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		height: var(--jv-touch);
		border-bottom: 1px solid var(--jv-line-soft);
	}
	i {
		flex: none;
		width: var(--jv-space-1);
		height: var(--jv-space-1);
		background: var(--tone);
		box-shadow: var(--jv-glow-sm);
	}
	.text {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.meta {
		flex: none;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
</style>
