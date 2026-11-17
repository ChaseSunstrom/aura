<!--
@component
Two to four options side by side, row by row; the `picked` one is lit.
Does not cover: choosing. It shows the recommendation; Sir says which.
-->
<script lang="ts">
	interface Row {
		label: string;
		values: (number | string)[];
	}
	let { data }: { data: { title?: string; options: string[]; rows: Row[]; picked?: number } } = $props();
</script>

<div
	class="comparison"
	data-gen="comparison"
	role="table"
	aria-label={data.title ?? 'Comparison'}
	style:--n={data.options.length}
>
	<div class="tr head" role="row">
		<span role="columnheader"></span>
		{#each data.options as o, j (j)}
			<span role="columnheader" class:picked={j === data.picked}>{o}</span>
		{/each}
	</div>
	{#each data.rows as r, i (i)}
		<div class="tr" role="row" data-ds-row style:--i={i}>
			<span role="rowheader">{r.label}</span>
			{#each data.options as _, j (j)}
				<span role="cell" class:picked={j === data.picked}>{r.values[j] ?? ''}</span>
			{/each}
		</div>
	{/each}
</div>

<style>
	.comparison {
		display: grid;
		min-width: var(--jv-measure-panel-min);
	}
	.tr {
		display: grid;
		grid-template-columns: minmax(0, 1.2fr) repeat(var(--n), minmax(0, 1fr));
		border-bottom: 1px solid var(--jv-line-soft);
	}
	span {
		padding: var(--jv-space-2) var(--jv-space-2);
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.head span {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	[role='rowheader'] {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	.picked {
		background: var(--jv-wash);
	}
	.head .picked {
		color: var(--jv-accent);
		border-top: 1px solid var(--jv-accent);
	}
</style>
