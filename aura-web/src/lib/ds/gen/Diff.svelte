<!--
@component
A unified diff: added lines in green, removed in pink, hunks in the accent.
Does not cover: more than 400 lines. The rest is counted, not drawn, so a patch
at the 20 000-character cap cannot put ten thousand nodes on the HUD.
-->
<script lang="ts">
	import Label from '../Label.svelte';

	import { diffLines } from './draw';

	let { data }: { data: { patch: string; file?: string } } = $props();

	const MAX_LINES = 400;
	const lines = $derived(diffLines(data.patch));
</script>

<div class="diff" data-gen="diff">
	{#if data.file}<Label>{data.file}</Label>{/if}
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<pre tabindex="0">{#each lines.slice(0, MAX_LINES) as l, i (i)}<span class={l.kind}>{l.text}</span>{/each}</pre>
	{#if lines.length > MAX_LINES}<Label>{lines.length - MAX_LINES} more lines</Label>{/if}
</div>

<style>
	.diff {
		display: grid;
		gap: var(--jv-space-2);
		min-width: var(--jv-measure-panel-min);
	}
	pre {
		margin: 0;
		max-height: calc(var(--jv-touch) * 8);
		overflow: auto;
		padding: var(--jv-space-2) 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		text-shadow: none;
	}
	pre:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	span {
		display: block;
		padding: 0 var(--jv-space-3);
		min-height: 1lh;
		color: var(--jv-text-dim);
	}
	.add {
		color: var(--jv-ok);
		background: var(--jv-wash);
	}
	.del {
		color: var(--jv-danger-text);
	}
	.hunk {
		color: var(--jv-accent);
	}
	.file {
		color: var(--jv-text-faint);
	}
</style>
