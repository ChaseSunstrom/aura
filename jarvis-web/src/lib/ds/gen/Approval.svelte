<!--
@component
What an action waiting on Sir will do, in amber. It shows; it does not ask.
Does not cover: approving. The approval itself is the guard's frame
(`approval.request`), answered in its own card or by voice; this is the picture
the model draws beside it.
-->
<script lang="ts">
	import Label from '../Label.svelte';

	interface Row {
		label: string;
		value: string;
	}
	let { data }: { data: { action: string; detail?: string; rows?: Row[] } } = $props();
</script>

<div class="approval" data-gen="approval">
	<Label tone="warn">Waiting on you</Label>
	<p class="action">{data.action}</p>
	{#if data.detail}<p class="detail">{data.detail}</p>{/if}
	{#if data.rows?.length}
		<dl>
			{#each data.rows as r, i (i)}
				<div data-ds-row style:--i={i}><dt>{r.label}</dt><dd>{r.value}</dd></div>
			{/each}
		</dl>
	{/if}
</div>

<style>
	.approval {
		display: grid;
		gap: var(--jv-space-2);
		padding-left: var(--jv-space-3);
		border-left: 1px solid var(--jv-warn);
		min-width: var(--jv-measure-card);
	}
	p {
		margin: 0;
		overflow-wrap: anywhere;
	}
	.action {
		font-size: var(--jv-fs-md);
		color: var(--jv-text-bright);
	}
	.detail {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	dl {
		margin: 0;
		display: grid;
		gap: var(--jv-space-1);
	}
	dl div {
		display: flex;
		gap: var(--jv-space-3);
		justify-content: space-between;
	}
	dt {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	dd {
		margin: 0;
		color: var(--jv-warn);
		text-align: right;
	}
</style>
