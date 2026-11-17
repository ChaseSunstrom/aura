<!--
@component
Columns and rows, virtualized, with the header kept in view. Numbers are set
right and tabular so a column can be read down.
Does not cover: sorting, or a row with more cells than columns (the extra cells are not drawn).
-->
<script lang="ts">
	import { fmt } from './draw';
	import Virtual from './Virtual.svelte';

	let { data }: { data: { title?: string; columns: string[]; rows: (number | string)[][] } } = $props();

	const cell = (v: number | string | undefined) => (typeof v === 'number' ? fmt(v) : (v ?? ''));
</script>

<div class="table" data-gen="table" style:--cols={data.columns.length}>
	<Virtual items={data.rows} role="table" label={data.title ?? 'Table'}>
		{#snippet head()}
			<div class="tr head" role="row" aria-rowindex={1}>
				{#each data.columns as c, j (j)}<span role="columnheader" title={c}>{c}</span>{/each}
			</div>
		{/snippet}
		{#snippet row(r, i, fresh)}
			<div class="tr" role="row" aria-rowindex={i + 2} data-ds-row={fresh || undefined} style:--i={i}>
				{#each data.columns as _, j (j)}
					<span role="cell" class:num={typeof r[j] === 'number'} title={String(cell(r[j]))}>{cell(r[j])}</span>
				{/each}
			</div>
		{/snippet}
	</Virtual>
</div>

<style>
	.table {
		min-width: var(--jv-measure-panel-min);
	}
	.tr {
		display: grid;
		grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
		gap: var(--jv-space-3);
		align-items: center;
		height: var(--jv-touch);
		border-bottom: 1px solid var(--jv-line-soft);
	}
	.head {
		position: sticky;
		top: 0;
		z-index: 1;
		background: var(--jv-bg);
		border-bottom-color: var(--jv-tick);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	span {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
</style>
