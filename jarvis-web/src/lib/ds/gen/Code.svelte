<!--
@component
Code, as text, in the chrome face; it scrolls past eight rows.
Does not cover: highlighting. The language is named, not coloured.
-->
<script lang="ts">
	import Label from '../Label.svelte';

	let { data }: { data: { code: string; language?: string } } = $props();
</script>

<div class="code" data-gen="code">
	{#if data.language}<Label>{data.language}</Label>{/if}
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<pre tabindex="0"><code>{data.code}</code></pre>
</div>

<style>
	.code {
		display: grid;
		gap: var(--jv-space-2);
		min-width: var(--jv-measure-panel-min);
	}
	pre {
		margin: 0;
		max-height: calc(var(--jv-touch) * 8);
		overflow: auto;
		padding: var(--jv-space-2) var(--jv-space-3);
		border-left: 1px solid var(--jv-accent);
		background: var(--jv-wash);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text);
		text-shadow: none;
	}
	pre:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
</style>
